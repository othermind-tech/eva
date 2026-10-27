# providers/handlers/anthropic_handler.py
#
# EVA native handler for Anthropic (Claude models).
#
# Uses the anthropic Python SDK (anthropic>=0.40.0).
# Install: pip install anthropic  (or via requirements.txt)
#
# ── Key API differences from OpenAI ─────────────────────────────────────────
#
#   Tool schema:   {"name":..., "description":..., "input_schema":{...}}
#                  NOT {"type":"function","function":{...,"parameters":{...}}}
#
#   System prompt: top-level `system=` param, NOT a {"role":"system"} message.
#
#   Tool results:  must be sent as a "user" turn with content block type
#                  "tool_result", NOT as {"role":"tool", ...} messages.
#
#   Tool calls:    assistant response has a "tool_use" content block (type,
#                  id, name, input:dict), NOT a "tool_calls" field.
#
#   Usage:         input_tokens / output_tokens (not prompt/completion tokens)
#
#   Stop reason:   "end_turn" | "tool_use" | "max_tokens"
#                  (not "stop" | "tool_calls" | "length")
#
#   Extended/adaptive thinking (V6.7, reasoning=<level>):
#     Requires betas=["interleaved-thinking-2025-05-14"] and a "thinking"
#     param in the request -- shape depends on model generation, resolved
#     dynamically via core/providers/reasoning_support.py (borrows
#     litellm's own AnthropicConfig, not a hand-maintained model list):
#       Legacy (Opus 4.5, Haiku 4.5, earlier Claude 4):
#         thinking={"type":"enabled","budget_tokens":N}
#       Newer (Sonnet 5, Opus 4.7/4.8, Fable 5, Mythos 5/Preview):
#         thinking={"type":"adaptive","effort":"low"|"medium"|"high"}
#         -- and the newest of these (Fable 5, Mythos 5, Mythos Preview)
#         cannot disable thinking at all; "type":"disabled" is rejected.
#     Response contains "thinking" content blocks with streaming
#     "thinking_delta" events regardless of which shape was requested.
#
# ── Streaming event sequence ─────────────────────────────────────────────────
#   message_start        → event.message.usage.input_tokens
#   content_block_start  → event.content_block.type ("text"|"tool_use"|"thinking")
#   content_block_delta  → event.delta.type ("text_delta"|"input_json_delta"|
#                          "thinking_delta"|"signature_delta")
#   content_block_stop   → end of block; flush accumulated tool JSON here
#   message_delta        → event.delta.stop_reason + event.usage.output_tokens
#   message_stop         → stream complete (no payload needed)

from __future__ import annotations

import json
import logging

from providers import credentials as _credentials
from typing import Any, AsyncGenerator, Dict, List, Optional

from core.types import (
    ErrorChunk,
    ReasoningChunk,
    StreamChunk,
    TextChunk,
    ToolCallChunk,
    UsageChunk,
)
from core.error_handling import classify_error
from providers.usage import make_anthropic_usage_chunk
from core.providers.reasoning_support import anthropic_thinking_param
from core.providers.multimodal import data_uri_to_anthropic_source, is_multimodal_content

logger = logging.getLogger(__name__)

PROVIDER_NAME = "anthropic"
_ENV_KEY = "ANTHROPIC_API_KEY"
_DEFAULT_MAX_TOKENS = 8192
_THINKING_BETA = "interleaved-thinking-2025-05-14"


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------

def _resolve_key(config_key: Optional[str]) -> str:
    key = _credentials.resolve_api_key(PROVIDER_NAME, config_key)
    if not key:
        raise ValueError(
            "Anthropic API key not configured. "
            f"Store it via POST /secrets/anthropic (saved to Windows Credential Manager)."
        )
    return key


# ---------------------------------------------------------------------------
# Message format conversion: OpenAI-format → Anthropic-format
# ---------------------------------------------------------------------------

def _anthropic_content_blocks(content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    M-07: translate EVA's canonical (OpenAI-style) multimodal content-part list into
    Anthropic's own content-block shape -- {"type":"text",...} passes through with Anthropic's
    key name, {"type":"image_url",...} becomes {"type":"image","source":{...}}. A part this
    cannot translate (a non-data-URI image_url, or an unrecognised type -- neither of which EVA
    itself ever produces) is dropped rather than sent malformed; logged so a real gap doesn't
    go silently unnoticed.
    """
    blocks: List[Dict[str, Any]] = []
    for part in content:
        ptype = part.get("type")
        if ptype == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif ptype == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            source = data_uri_to_anthropic_source(url)
            if source is not None:
                blocks.append({"type": "image", "source": source})
            else:
                logger.warning(
                    "[anthropic] dropped an image_url content part this handler cannot "
                    "translate (not a data: URI)",
                    extra={"url_prefix": str(url)[:40]},
                )
        else:
            logger.warning(
                "[anthropic] dropped an unrecognised multimodal content part",
                extra={"part_type": ptype},
            )
    return blocks


def _merge_adjacent_same_role_turns(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Anthropic requires strict user/assistant alternation. Nothing upstream of this converter
    guarantees that two adjacent EVA-canonical messages never share a role -- the tool-result
    grouping above already handles the one case that always existed (consecutive role="tool"
    messages); M-07 added a second: an image message (role="user") immediately following the
    tool-result turn it belongs to (role="user" too, once converted). Rather than special-case
    that one pattern, this folds ANY adjacent same-role turns into one, generally and safely --
    a no-op for every sequence that already alternated (every pre-M-07 conversation), and a
    correctness fix for both the new case and any other same-role adjacency in EVA's own
    canonical history the two branches above didn't foresee.
    """
    merged: List[Dict[str, Any]] = []
    for entry in messages:
        if merged and merged[-1]["role"] == entry["role"]:
            prev_content, new_content = merged[-1]["content"], entry["content"]
            if isinstance(prev_content, str):
                prev_content = [{"type": "text", "text": prev_content}] if prev_content else []
            if isinstance(new_content, str):
                new_content = [{"type": "text", "text": new_content}] if new_content else []
            merged[-1]["content"] = prev_content + new_content
        else:
            # Copy the dict -- callers (and this function, across iterations) must not mutate
            # the list `_convert_messages` built.
            merged.append(dict(entry))
    return merged


def _convert_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert OpenAI-format conversation history to Anthropic message format.

    OpenAI → Anthropic mapping
    ──────────────────────────
    system message
        → stripped (caller passes system= separately)
    user message
        → {"role":"user", "content": str}
    assistant text
        → {"role":"assistant", "content": str}
    assistant with tool_calls
        → {"role":"assistant", "content": [
               {"type":"text", "text":...},        # optional
               {"type":"tool_use", "id":..., "name":..., "input":{...}},
               ...
           ]}
    role=="tool" result(s)
        → {"role":"user", "content": [
               {"type":"tool_result", "tool_use_id":..., "content": str},
               ...
           ]}
          Consecutive tool-result messages are grouped into ONE user turn
          because Anthropic requires alternating user/assistant roles.
    """
    result: List[Dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")

        # System messages are handled separately as `system=` param
        if role == "system":
            i += 1
            continue

        if role == "user":
            content = msg.get("content") or ""
            if is_multimodal_content(content):
                content = _anthropic_content_blocks(content)
            result.append({"role": "user", "content": content})
            i += 1
            continue

        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                content_blocks: List[Dict[str, Any]] = []
                # Preserve any text that accompanied the tool calls
                text_content = msg.get("content")
                if text_content:
                    content_blocks.append({"type": "text", "text": text_content})
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    raw_args = fn.get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            raw_args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": raw_args,
                    })
                result.append({"role": "assistant", "content": content_blocks})
            else:
                result.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                })
            i += 1
            continue

        if role == "tool":
            # Group ALL consecutive tool results into a single user turn.
            # Anthropic rejects separate tool-result messages; they must be
            # co-located in one user message when the assistant called multiple
            # tools in a single turn.
            tool_results: List[Dict[str, Any]] = []
            while i < len(messages) and messages[i].get("role") == "tool":
                tr = messages[i]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tr.get("tool_call_id", ""),
                    "content": str(tr.get("content", "")),
                })
                i += 1
            result.append({"role": "user", "content": tool_results})
            continue  # i already advanced inside the inner loop

        # Unknown role — skip silently
        i += 1

    return _merge_adjacent_same_role_turns(result)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class AnthropicHandler:
    """
    Native EVA handler for Anthropic (Claude models).

    Implements the same interface as OpenAIHandler / GroqHandler / OllamaCloudHandler:

        async def create_message(
            system_prompt: str,
            messages: List[Dict],
            stream: bool = True,
            reasoning: Optional[str] = None,
        ) -> AsyncGenerator[StreamChunk, None]

    Message history is accepted in OpenAI format and converted to Anthropic
    format internally via _convert_messages().
    Tool schemas must already be in Anthropic format — build_provider_tools()
    in core/tools/builder.py handles this when provider=="anthropic".
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.api_key: str = _resolve_key(config.get("api_key"))
        self.model: str = config["model"]
        self.provider_tools: List[Dict[str, Any]] = config.get("provider_tools", [])
        self.tool_classes: Dict[str, Any] = config.get("tool_classes", {})
        self.dispatcher = config.get("dispatcher")
        self.max_tokens: int = int(config.get("max_tokens") or _DEFAULT_MAX_TOKENS)

        # V6.4 / V6.7: optional temperature, gated by per-model capability
        # support. Claude Opus 4.7+ (including 4.8) rejects `temperature`
        # with a 400 error if set to a non-default value (except exactly
        # 1) -- checked dynamically via litellm's own request-transform
        # logic (core/providers/reasoning_support.py), not a hand-
        # maintained model list -- see that module's docstring for why.
        self._temperature: Optional[float] = None
        _requested_temperature = config.get("temperature")
        if _requested_temperature is not None:
            from core.providers.reasoning_support import anthropic_supports_temperature  # noqa: PLC0415
            if anthropic_supports_temperature(self.model, _requested_temperature):
                self._temperature = _requested_temperature
            else:
                logger.debug(
                    "[anthropic] temperature requested but unsupported for this model/value — omitting",
                    extra={"model": self.model, "temperature": _requested_temperature},
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        from anthropic import AsyncAnthropic  # noqa: PLC0415
        return AsyncAnthropic(api_key=self.api_key)

    def _build_request(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        reasoning: Optional[str],
    ) -> Dict[str, Any]:
        req: Dict[str, Any] = {
            "model":      self.model,
            "max_tokens": self.max_tokens,
            "messages":   _convert_messages(messages),
        }
        if system_prompt:
            # V6.8: prompt caching. EVA re-sends this same system_prompt
            # string, unchanged, on every turn of a single execution's
            # multi-step ReAct tool-calling loop (see core/agent_engine.py).
            # Structuring it as a one-block content list with a
            # cache_control breakpoint lets Anthropic reuse the cached
            # prefix across those repeated turns instead of reprocessing
            # the full (typically large) system prompt at full token cost
            # every time. Default ttl ("5m") is fine here -- turns within
            # one execution happen back-to-back, well under 5 minutes.
            # Not split into static/dynamic sub-blocks -- see task scope.
            req["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if self.provider_tools:
            req["tools"]       = self.provider_tools
            req["tool_choice"] = {"type": "auto"}
        if self._temperature is not None:
            req["temperature"] = self._temperature
        thinking = anthropic_thinking_param(reasoning, self.model)
        if thinking is not None:
            req["thinking"] = thinking
        return req

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        stream: bool = True,
        reasoning: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        reasoning: V6.7 -- one of core.providers.reasoning_support.
        REASONING_LEVELS ("minimal"/"low"/"medium"/"high"/"xhigh"), or
        None/"off" to disable. Caller (AgentEngine) is expected to have
        already resolved/snapped the level against this model's actual
        support via reasoning_support.anthropic_reasoning_support() --
        this method builds the request for whatever level it's given.
        """
        req = self._build_request(system_prompt, messages, reasoning)
        use_beta = req.get("thinking") is not None  # extended thinking requires the beta header

        logger.debug(
            "[anthropic] sending request",
            extra={
                "model":      self.model,
                "stream":     stream,
                "reasoning":  reasoning,
                "msg_count":  len(req["messages"]),
                "has_tools":  bool(self.provider_tools),
                "tool_count": len(self.provider_tools),
            },
        )

        try:
            client = self._get_client()

            # ==============================================================
            # NON-STREAMING
            # ==============================================================
            if not stream:
                if use_beta:
                    response = await client.beta.messages.create(
                        **req, betas=[_THINKING_BETA]
                    )
                else:
                    response = await client.messages.create(**req)

                input_tokens  = getattr(response.usage, "input_tokens",  0) or 0
                output_tokens = getattr(response.usage, "output_tokens", 0) or 0
                cache_read    = getattr(response.usage, "cache_read_input_tokens",     0) or 0
                cache_write   = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                stop_reason   = getattr(response, "stop_reason", None)

                for block in response.content:
                    btype = getattr(block, "type", "")

                    if btype == "thinking":
                        thinking_text = getattr(block, "thinking", "")
                        if thinking_text:
                            yield ReasoningChunk(content=thinking_text)

                    elif btype == "text":
                        text = getattr(block, "text", "")
                        # Only yield finish_reason on the last text block
                        yield TextChunk(content=text, finish_reason=stop_reason)

                    elif btype == "tool_use":
                        raw_input = block.input
                        args = raw_input if isinstance(raw_input, dict) else {}
                        yield ToolCallChunk(
                            call_id=block.id,
                            tool_name=block.name,
                            arguments=args,
                        )

                yield make_anthropic_usage_chunk(
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read_tokens=cache_read, cache_creation_tokens=cache_write,
                    provider=PROVIDER_NAME, model=self.model,
                )
                return

            # ==============================================================
            # STREAMING
            # ==============================================================
            # Per-block state (reset at each content_block_start)
            _current_block_type: str = ""
            _current_block_id:   str = ""
            _current_block_name: str = ""
            _tc_json_acc:        str = ""   # accumulated tool-use input JSON
            _had_text:           bool = False
            _input_tokens:       int = 0
            _cache_read_tokens:  int = 0
            _cache_write_tokens: int = 0
            _output_tokens:      int = 0

            if use_beta:
                stream_ctx = client.beta.messages.stream(
                    **req, betas=[_THINKING_BETA]
                )
            else:
                stream_ctx = client.messages.stream(**req)

            async with stream_ctx as stream_resp:
                async for event in stream_resp:
                    etype = getattr(event, "type", "")

                    # ── message_start: capture input token count ───────────
                    if etype == "message_start":
                        msg_obj = getattr(event, "message", None)
                        if msg_obj:
                            usage = getattr(msg_obj, "usage", None)
                            if usage:
                                _input_tokens = getattr(usage, "input_tokens", 0) or 0
                                # input_tokens EXCLUDES prompt-cache reads/writes
                                # (this handler sets cache_control breakpoints,
                                # so on a warm cache it is a tiny fraction of
                                # the real input) -- count them too.
                                _cache_read_tokens  = getattr(usage, "cache_read_input_tokens",     0) or 0
                                _cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0

                    # ── content_block_start: initialise per-block state ────
                    elif etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block:
                            _current_block_type = getattr(block, "type", "")
                            _current_block_id   = getattr(block, "id",   "")
                            _current_block_name = getattr(block, "name", "")
                            _tc_json_acc = ""

                    # ── content_block_delta: stream text / accumulate tool ─
                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta:
                            dtype = getattr(delta, "type", "")

                            if dtype == "text_delta":
                                txt = getattr(delta, "text", "")
                                if txt:
                                    _had_text = True
                                    yield TextChunk(content=txt)

                            elif dtype == "thinking_delta":
                                thinking = getattr(delta, "thinking", "")
                                if thinking:
                                    yield ReasoningChunk(content=thinking)

                            elif dtype == "input_json_delta":
                                # Accumulate tool-call JSON fragments —
                                # do NOT yield until the block is complete.
                                _tc_json_acc += getattr(delta, "partial_json", "")

                            # signature_delta: extended-thinking cryptographic
                            # signature — not useful for EVA, skip silently.

                    # ── content_block_stop: flush accumulated tool call ────
                    elif etype == "content_block_stop":
                        if _current_block_type == "tool_use":
                            try:
                                args = json.loads(_tc_json_acc) if _tc_json_acc else {}
                            except json.JSONDecodeError:
                                logger.warning(
                                    "[anthropic] tool-use JSON parse failed",
                                    extra={
                                        "tool": _current_block_name,
                                        "raw":  _tc_json_acc[:200],
                                    },
                                )
                                args = {}
                            yield ToolCallChunk(
                                call_id=_current_block_id,
                                tool_name=_current_block_name,
                                arguments=args,
                            )
                        # Reset per-block state
                        _current_block_type = ""
                        _current_block_id   = ""
                        _current_block_name = ""
                        _tc_json_acc        = ""

                    # ── message_delta: stop_reason + output token count ────
                    elif etype == "message_delta":
                        delta = getattr(event, "delta", None)
                        usage = getattr(event, "usage", None)
                        if usage:
                            _output_tokens = getattr(usage, "output_tokens", 0) or 0
                        if delta:
                            stop_reason = getattr(delta, "stop_reason", None)
                            # Emit a finish TextChunk for text responses.
                            # Don't emit for "tool_use" — ToolCallChunks were
                            # already yielded at content_block_stop.
                            if stop_reason and stop_reason not in ("tool_use",):
                                yield TextChunk(content="", finish_reason=stop_reason)

                    # ── message_stop: nothing to do ────────────────────────
                    elif etype == "message_stop":
                        pass

            yield make_anthropic_usage_chunk(
                input_tokens=_input_tokens, output_tokens=_output_tokens,
                cache_read_tokens=_cache_read_tokens, cache_creation_tokens=_cache_write_tokens,
                provider=PROVIDER_NAME, model=self.model,
            )

        except Exception as exc:
            _err_type = classify_error(exc)
            logger.error(
                "[anthropic] handler exception",
                exc_info=exc,
                extra={
                    "model":      self.model,
                    "exc_class":  type(exc).__name__,
                    "error_type": _err_type.value,
                    "error":      str(exc)[:400],
                },
            )
            yield ErrorChunk(
                message=str(exc),
                provider=PROVIDER_NAME,
                error_type=_err_type.value,
            )
