# providers/handlers/ollama_cloud_handler.py
#
# EVA handler for Ollama Cloud (https://ollama.com).
#
# Ollama Cloud exposes an OpenAI-compatible API at https://ollama.com/v1/.
# This handler uses the openai Python SDK with a custom base_url — NOT the
# ollama Python SDK (which targets the self-hosted /api/ endpoints).
#
# OpenAI SDK streaming contract (same as openai_handler.py):
#   chunk.choices[0].delta.content        → str | None  (text delta)
#   chunk.choices[0].delta.tool_calls     → list | None (accumulated fragments)
#   chunk.choices[0].finish_reason        → str | None  ("stop" | "tool_calls" | ...)
#   chunk.usage                           → usage object (present on last chunk
#                                           when stream_options.include_usage=True)
#
# Tool schema: uses OpenAI function-calling JSON format (same as openai_handler).
# build_provider_tools("openai", ...) output is reused directly.
#
# Cost: Ollama Cloud is subscription-based — no per-token pricing.
# UsageChunk carries real token counts; computed_cost_usd will be None
# (correct — LiteLLM has no pricing data for ollama_cloud models).

from __future__ import annotations

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
from providers.usage import make_usage_chunk
from core.providers.reasoning_support import ollama_think_value
from utils.tool_normalizer import normalize_tool_arguments

logger = logging.getLogger(__name__)

_BASE_URL = "https://ollama.com/v1"
_ENV_KEY  = "OLLAMA_CLOUD_API_KEY"


def _resolve_key(config_key: Optional[str]) -> str:
    """
    Resolve the Ollama Cloud API key.
    Priority:
      1. Key stored in handler config (explicit override)
      2. The key in the OS credential store (providers.credentials; never os.environ)
    """
    key = _credentials.resolve_api_key("ollama_cloud", config_key)
    if not key:
        raise ValueError(
            "Ollama Cloud API key not configured. "
            f"Store it via POST /secrets/ollama_cloud (saved to Windows Credential Manager)."
        )
    return key


class OllamaCloudHandler:
    """
    Provider handler for Ollama Cloud.

    Implements the same interface as OpenAIHandler / GroqHandler:
        async def create_message(system_prompt, messages, stream, reasoning)
            -> AsyncGenerator[StreamChunk, None]

    Key behavioural notes
    ----------------------
    - Uses the openai Python SDK with base_url="https://ollama.com/v1".
    - Tool arguments arrive as JSON strings (OpenAI format) — normalize_tool_arguments
      is applied exactly as in openai_handler.
    - Streaming: accumulates tool-call fragments across deltas; yields complete
      ToolCallChunks only when finish_reason == "tool_calls".
    - Reasoning / thinking control (V6.7): `reasoning` is a level string
      ("minimal"/"low"/"medium"/"high"/"xhigh") or None, mapped via
      core.providers.reasoning_support.ollama_think_value() to Ollama
      Cloud's own `reasoning_effort` value on its OpenAI-compatible
      endpoint (same level contract as local Ollama's `think` field --
      see ollama_handler.py). None/"off" sends "none" -- but this is a
      *request*, not a guarantee: confirmed empirically that gpt-oss
      reasons regardless of reasoning_effort (including "none", omitting
      the field entirely, and even Groq's separate hosting of the same
      model) -- no request-level parameter suppresses it. Reasoning
      content surfaces in two different shapes depending on the model:
      gpt-oss returns it in a dedicated `message.reasoning` /
      `delta.reasoning` field (read directly, same as openai_handler.py /
      groq_handler.py); other Ollama-hosted models (qwen3) instead embed
      it inline as literal <think>…</think> tags in `content`, parsed
      client-side below. Both paths are checked; a model could in
      principle use either.
    - Usage: real token counts come back in the response; no per-token cost
      is available (subscription pricing).
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.model: str = config["model"]
        self.api_key: str = _resolve_key(config.get("api_key"))
        self.provider_tools: List[Dict[str, Any]] = config.get("provider_tools", [])
        self.tool_classes: Dict[str, Any] = config.get("tool_classes", {})
        self.dispatcher = config.get("dispatcher")

        # V6.4: optional max_tokens/temperature -- top-level, OpenAI-style
        # keys, since this handler talks to Ollama Cloud's OpenAI-compatible
        # endpoint. No known model-specific restriction (open-weight hosted
        # models) -- see core/providers/param_support.py.
        self._max_tokens = config.get("max_tokens")
        self._temperature = config.get("temperature")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=self.api_key, base_url=_BASE_URL)

    def _build_request(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        stream: bool,
        reasoning: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Prompt caching: NOT confirmed to auto-apply here, despite this
        # handler proxying through the OpenAI-compatible SDK the same way
        # openai_handler.py does. Checked GitHub issues ollama/ollama#15600
        # and #16714 (July 2026) -- both are open feature *requests* asking
        # Ollama to add prompt/KV-cache support to Ollama Cloud, meaning it
        # is not yet documented or confirmed live. Do not assume the
        # OpenAI-SDK-shape similarity implies OpenAI's server-side caching
        # behavior transfers -- it is a different backend. Also moot for
        # the cost angle regardless: this handler's own docstring notes
        # Ollama Cloud is subscription-based with no per-token pricing, so
        # even if caching lands it would show up as latency, not $ savings.
        msg_list = (
            [{"role": "system", "content": system_prompt}] + messages
            if system_prompt
            else list(messages)
        )
        req: Dict[str, Any] = {
            "model":    self.model,
            "messages": msg_list,
            "stream":   stream,
            # V6.7: level string ("minimal"/"low"/"medium"/"high"/"xhigh")
            # mapped to Ollama's own scale via ollama_think_value(), or the
            # explicit "none" string when disabled -- always sending the
            # field (never omitting it) is more efficient than client-side
            # tag stripping, since the model never generates thinking
            # tokens at all when effort="none".
            "reasoning_effort": ollama_think_value(reasoning) or "none",
        }
        if self.provider_tools:
            req["tools"]       = self.provider_tools
            req["tool_choice"] = "auto"
        if self._max_tokens is not None:
            req["max_tokens"] = self._max_tokens
        if self._temperature is not None:
            req["temperature"] = self._temperature
        # Request usage in the final streaming chunk
        if stream:
            req["stream_options"] = {"include_usage": True}
        return req

    # ------------------------------------------------------------------
    # Main create_message
    # ------------------------------------------------------------------

    async def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        stream: bool = True,
        reasoning: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:

        req = self._build_request(system_prompt, messages, stream, reasoning)
        client = self._get_client()
        # Normalise both None and the explicit "off" string to a single
        # bool for the client-side <think> tag fallback below -- the level
        # string itself only matters for the reasoning_effort request
        # field built above.
        reasoning_enabled = reasoning not in (None, "off")

        logger.debug(
            "[ollama_cloud] sending request",
            extra={
                "model":      self.model,
                "stream":     stream,
                "msg_count":  len(req["messages"]),
                "has_tools":  bool(self.provider_tools),
            },
        )

        try:
            # ── NON-STREAMING ────────────────────────────────────────────────
            if not stream:
                response = await client.chat.completions.create(**req)
                choice = response.choices[0]
                msg    = choice.message

                # reasoning -- gpt-oss and similar models return their chain
                # of thought in a dedicated `reasoning` field (same shape as
                # openai_handler.py / groq_handler.py's OpenAI-compatible
                # endpoints), separate from the <think>-tag convention some
                # other Ollama-hosted models (qwen3) use inline in `content`
                # below. Previously never read at all -- reasoning content
                # was silently discarded rather than surfaced as a
                # ReasoningChunk, on both this path and the streaming one.
                r = getattr(msg, "reasoning", None)
                if reasoning_enabled and r:
                    yield ReasoningChunk(content=r)

                if getattr(msg, "tool_calls", None):
                    for c in msg.tool_calls:
                        yield ToolCallChunk(
                            call_id=c.id,
                            tool_name=c.function.name,
                            arguments=normalize_tool_arguments(c.function.arguments),
                        )
                    # Usage comes after tool calls too
                    if getattr(response, "usage", None):
                        u = response.usage
                        yield make_usage_chunk(u, provider=PROVIDER_NAME, model=self.model)
                    return

                raw_content = msg.content or ""
                # Always parse <think>…</think> — qwen3 / Ollama models may
                # include thinking tokens regardless of API-level settings.
                # reasoning_enabled=True  → yield ReasoningChunk for the thought
                # reasoning_enabled=False → strip the thinking block silently
                if "<think>" in raw_content:
                    t_open  = raw_content.find("<think>")
                    t_close = raw_content.find("</think>")
                    if t_open >= 0 and t_close > t_open:
                        pre     = raw_content[:t_open]
                        thought = raw_content[t_open + 7 : t_close]
                        post    = raw_content[t_close + 8:]
                        if pre.strip():
                            yield TextChunk(content=pre)
                        if thought and reasoning_enabled:
                            yield ReasoningChunk(content=thought)
                        yield TextChunk(
                            content=post,
                            finish_reason=choice.finish_reason,
                        )
                    else:
                        yield TextChunk(
                            content=raw_content,
                            finish_reason=choice.finish_reason,
                        )
                else:
                    yield TextChunk(
                        content=raw_content,
                        finish_reason=choice.finish_reason,
                    )

                if getattr(response, "usage", None):
                    u = response.usage
                    yield make_usage_chunk(u, provider=PROVIDER_NAME, model=self.model)
                return

            # ── STREAMING ────────────────────────────────────────────────────
            # IMPORTANT: In OpenAI SDK 1.x, chat.completions.create() is
            # async def — it returns a coroutine, not a context manager.
            # You must AWAIT the coroutine first to get the AsyncStream, then
            # use the stream as a context manager for proper cleanup.
            #
            #   WRONG:  async with client.chat.completions.create(**req) as s:
            #   RIGHT:  _s = await client.chat.completions.create(**req)
            #           async with _s as stream_resp:  ← context manager on stream
            #
            # Tool-call arguments arrive fragmented across streaming deltas.
            # Accumulate them and yield complete ToolCallChunks only on
            # finish_reason == "tool_calls" — identical to openai_handler.
            _tc_acc: Dict[int, Dict[str, str]] = {}  # index → {id, name, arguments}

            # <think>…</think> tag state for client-side thinking extraction.
            # Ollama-family models (qwen3 etc.) may emit thinking tags in content
            # regardless of API-level settings.  We always parse them:
            #   reasoning_enabled=True  → yield ReasoningChunk for thinking content
            #   reasoning_enabled=False → discard thinking content (keeps text output clean)
            # The buffer retains a partial tag suffix to handle chunk boundaries.
            _in_thinking: bool = False
            _think_buf:   str  = ""

            _raw_stream = await client.chat.completions.create(**req)
            async with _raw_stream as stream_resp:
                async for chunk in stream_resp:
                    if not chunk.choices:
                        # Usage-only chunk (final, with stream_options.include_usage)
                        if getattr(chunk, "usage", None):
                            u = chunk.usage
                            yield make_usage_chunk(u, provider=PROVIDER_NAME, model=self.model)
                        continue

                    choice = chunk.choices[0]
                    delta  = choice.delta

                    # reasoning (streamed) -- see the matching non-streaming
                    # comment above for why this field, not just <think> tags,
                    # needs to be read directly.
                    if reasoning_enabled and getattr(delta, "reasoning", None):
                        yield ReasoningChunk(content=delta.reasoning)

                    # Accumulate tool-call fragments
                    if getattr(delta, "tool_calls", None):
                        for c in delta.tool_calls:
                            idx = c.index if c.index is not None else 0
                            if idx not in _tc_acc:
                                _tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            if c.id:
                                _tc_acc[idx]["id"] = c.id
                            _fn = getattr(c, "function", None)
                            if _fn and _fn.name:
                                _tc_acc[idx]["name"] = _fn.name
                            if _fn and _fn.arguments:
                                _tc_acc[idx]["arguments"] += _fn.arguments

                    # Text delta — always parse <think>…</think> tags so that
                    # thinking tokens from qwen3 / similar models are extracted
                    # correctly regardless of API-level suppression support.
                    if getattr(delta, "content", None):
                        _think_buf += delta.content
                        while True:
                            if _in_thinking:
                                close_pos = _think_buf.find("</think>")
                                if close_pos >= 0:
                                    # Flush thinking content up to closing tag
                                    if close_pos > 0 and reasoning_enabled:
                                        yield ReasoningChunk(content=_think_buf[:close_pos])
                                    _think_buf   = _think_buf[close_pos + 8:]
                                    _in_thinking = False
                                else:
                                    # Keep last 8 chars buffered (partial </think>)
                                    safe = max(0, len(_think_buf) - 8)
                                    if safe > 0:
                                        if reasoning_enabled:
                                            yield ReasoningChunk(content=_think_buf[:safe])
                                        _think_buf = _think_buf[safe:]
                                    break
                            else:
                                open_pos = _think_buf.find("<think>")
                                if open_pos >= 0:
                                    if open_pos > 0:
                                        yield TextChunk(content=_think_buf[:open_pos])
                                    _think_buf   = _think_buf[open_pos + 7:]
                                    _in_thinking = True
                                else:
                                    # Keep last 7 chars buffered (partial <think>)
                                    safe = max(0, len(_think_buf) - 7)
                                    if safe > 0:
                                        yield TextChunk(content=_think_buf[:safe])
                                        _think_buf = _think_buf[safe:]
                                    break

                    # Finish
                    if choice.finish_reason is not None:
                        if choice.finish_reason == "tool_calls" and _tc_acc:
                            for idx in sorted(_tc_acc.keys()):
                                tc = _tc_acc[idx]
                                yield ToolCallChunk(
                                    call_id=tc["id"],
                                    tool_name=tc["name"],
                                    arguments=normalize_tool_arguments(tc["arguments"]),
                                )
                        else:
                            yield TextChunk(
                                content="",
                                finish_reason=choice.finish_reason,
                            )

                    # Usage (present in final chunk when include_usage=True)
                    if getattr(chunk, "usage", None):
                        u = chunk.usage
                        yield make_usage_chunk(u, provider=PROVIDER_NAME, model=self.model)

            # Flush any remaining think-buffer content after the stream ends
            if _think_buf:
                if _in_thinking:
                    if reasoning_enabled:
                        yield ReasoningChunk(content=_think_buf)
                    # else: discard stray thinking content
                else:
                    yield TextChunk(content=_think_buf)

        except Exception as exc:
            _err_type = classify_error(exc)
            logger.error(
                "[ollama_cloud] handler exception",
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


# Module-level constant — used in UsageChunk / ErrorChunk so the string
# is defined exactly once rather than scattered as literals.
PROVIDER_NAME = "ollama_cloud"
