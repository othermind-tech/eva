# providers/handlers/groq_handler.py

from typing import List, Dict, Any, AsyncGenerator, Optional
from logging import getLogger
from utils.tool_normalizer import normalize_tool_arguments
from groq import AsyncGroq
import json
from core.types import (
    TextChunk,
    ReasoningChunk,
    ErrorChunk,
    UsageChunk,
    ToolCallChunk,
    StreamChunk
)
from core.error_handling import classify_error
from providers.usage import make_usage_chunk

logger = getLogger(__name__)

def _parse_tool_arguments(raw_args):
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
    return raw_args


def _try_parse_python_tag_tool_call(text: str):
    """
    Parse Llama-format text-based tool call:
      <|python_tag|>tool_name{"arg": "value"}
    Some models on Groq emit this instead of the proper function-calling API
    format, especially in streaming mode.
    Returns (tool_name, args_dict) on success, None on failure.
    """
    text = text.strip()
    start = text.find("<|python_tag|>")
    if start == -1:
        return None
    text = text[start + len("<|python_tag|>"):].strip()

    # Strip known Llama end-of-turn / end-of-message markers
    for marker in ("%</function>", "</function>", "<|eom_id|>", "<|eot_id|>", "<|eom|>"):
        if marker in text:
            text = text[:text.index(marker)].strip()

    brace_idx = text.find("{")
    if brace_idx == -1:
        return None

    tool_name = text[:brace_idx].strip()
    args_str = text[brace_idx:].strip()

    if not tool_name:
        return None

    try:
        args = json.loads(args_str)
        if not isinstance(args, dict):
            return None
        return tool_name, args
    except json.JSONDecodeError:
        return None
class GroqHandler:
    """
    Unified Groq handler with:
    - streaming + non-streaming
    - reasoning extraction (native for Groq)
    - tool call extraction (OpenAI-compatible)
    """

    def __init__(self, config: Dict[str, Any]):
        self.client = AsyncGroq(api_key=config["api_key"])
        self.model = config["model"]

        self.provider_tools = config.get("provider_tools", [])
        self.tool_classes = config.get("tool_classes", {})
        self.dispatcher = config.get("dispatcher")

        # V6.4: optional max_tokens/temperature, gated by per-model capability
        # support — see core/providers/param_support.py.
        self._max_tokens = config.get("max_tokens")
        self._temperature = config.get("temperature")
        self._token_limit_param = "max_tokens"
        if self._max_tokens is not None or self._temperature is not None:
            from core.providers.param_support import get_param_support  # noqa: PLC0415
            _support = get_param_support("groq", self.model)
            self._token_limit_param = _support.token_limit_param
            if self._temperature is not None and not _support.supports_temperature:
                logger.debug(
                    "groq_handler: temperature requested but unsupported for this model — omitting",
                    extra={"model": self.model},
                )
                self._temperature = None


    async def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        stream: bool = True,
        reasoning: Optional[str] = None
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        reasoning: V6.7 -- one of core.providers.reasoning_support.
        REASONING_LEVELS ("minimal"/"low"/"medium"/"high"/"xhigh"), or
        None/"off" to disable. Sent as Groq's own `reasoning_effort` --
        previously not sent at all (only `include_reasoning`, which
        controls whether reasoning content is returned, not its depth).
        No EVA-side per-model-family mapping: gpt-oss (low/medium/high)
        and Qwen (none/default/low/medium/high) both accept EVA's
        low/medium/high directly; an unsupported level surfaces as a
        normal classified provider error rather than being pre-validated,
        since litellm's own reasoning_effort support for Groq has had
        open, unresolved bugs -- no
        reliable static data exists here to validate against anyway.
        """
        reasoning_enabled = reasoning not in (None, "off")

        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

        # Prompt caching: no request-side config needed. GroqCloud enabled
        # automatic prompt caching for all requests (Aug 2025) -- matching
        # prefixes (system prompts, tool definitions, etc.) from recently
        # processed requests are reused with no opt-in parameter, cutting
        # cached-token cost ~50% and not counting against rate limits.
        # Cache entries expire after 2 hours of inactivity (verified against
        # console.groq.com/docs/prompt-caching, July 2026). So the repeated
        # identical system-prompt sends within one execution's ReAct loop
        # (see core/agent_engine.py) should already benefit here too.
        # Build request
        req: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }

        if reasoning_enabled:
            req["include_reasoning"] = True
            req["reasoning_effort"] = reasoning

        if self.provider_tools:
            req["tools"] = self.provider_tools
            req["tool_choice"] = "auto"

        if self._max_tokens is not None and self._token_limit_param:
            req[self._token_limit_param] = self._max_tokens
        if self._temperature is not None:
            req["temperature"] = self._temperature

        # log raw request context before hitting Groq API
        logger.debug(
            "groq_handler — sending request to Groq API",
            extra={
                "model": self.model,
                "stream": stream,
                "reasoning": reasoning,  # level string or None
                "msg_count": len(messages),
                "has_tools": bool(self.provider_tools),
                "tool_count": len(self.provider_tools),
                "last_msg_role": messages[-1]["role"] if messages else None,
                "last_msg_preview": repr(str(messages[-1].get("content") or "")[:120]) if messages else None,
            },
        )

        try:
            # ======================================================
            # NON-STREAMING
            # ======================================================
            if not stream:
                response = await self.client.chat.completions.create(**req)

                choice = response.choices[0]
                msg = choice.message

                # reasoning
                if reasoning_enabled and getattr(msg, "reasoning", None):
                    yield ReasoningChunk(content=msg.reasoning)

                # tool call?
                if getattr(msg, "tool_calls", None):
                    for c in msg.tool_calls:
                        yield ToolCallChunk(
                            call_id=c.id,
                            tool_name=c.function.name,
                            arguments=normalize_tool_arguments(c.function.arguments)
                        )
                    return

                # text — guard against <|python_tag|> leaking in non-streaming responses
                # ONLY intercept when tools are configured; otherwise the tag is
                # used for code generation and must pass through.
                content = msg.content or ""
                if self.provider_tools and "<|python_tag|>" in content:
                    # Model used text-based tool call format without proper tool_calls field
                    parsed = _try_parse_python_tag_tool_call(content)
                    if parsed:
                        import uuid as _uuid
                        tool_name, args = parsed
                        yield ToolCallChunk(
                            call_id=f"tag-{_uuid.uuid4().hex[:12]}",
                            tool_name=tool_name,
                            arguments=args,
                        )
                        return
                    # Parsing failed — not a tool call. Strip the tag and yield as text.
                    content = content.replace("<|python_tag|>", "").strip()
                yield TextChunk(content=content, finish_reason=choice.finish_reason)

                # usage
                if getattr(response, "usage", None):
                    yield make_usage_chunk(response.usage, provider="groq", model=self.model)

                return

            # ======================================================
            # STREAMING
            # ======================================================
            logger.info(
                "groq_stream_start",
                extra={
                    "model": self.model,
                    "tools": len(self.provider_tools),
                    "msg_count": len(messages),
                },
            )
            stream_resp = await self.client.chat.completions.create(**req)
            logger.info("groq_stream_object_received")

            # Groq (OpenAI-compatible) fragments tool-call arguments across
            # multiple streaming deltas; the first delta always carries an
            # empty arguments string. Accumulate here and yield complete
            # ToolCallChunks only when finish_reason == "tool_calls".
            _tc_acc: dict = {}   # index → {id, name, arguments}  (proper function-call format)
            _tag_acc: str = ""   # accumulated <|python_tag|>-format text tool call
            _in_tag: bool = False  # are we buffering a python_tag tool call?
            _chunk_count: int = 0  # total streaming chunks received
            _empty_choices: int = 0  # chunks with no choices
            _text_yielded: bool = False  # did we yield any text?
            _usage_emitted: bool = False

            async for chunk in stream_resp:
                if not chunk.choices:
                    _empty_choices += 1
                    continue

                _chunk_count += 1
                choice = chunk.choices[0]
                delta = choice.delta

                # Log first few deltas and finish — data IN the message string
                _has_content = getattr(delta, "content", None)
                _has_tc = getattr(delta, "tool_calls", None)
                if _chunk_count <= 3 or choice.finish_reason is not None:
                    logger.info(
                        "groq_stream_delta",
                        extra={
                            "chunk_n": _chunk_count,
                            "content": repr((_has_content or "")[:60]),
                            "has_tc": bool(_has_tc),
                            "finish": choice.finish_reason,
                            "role": getattr(delta, "role", None),
                        },
                    )

                # reasoning
                if reasoning_enabled and getattr(delta, "reasoning", None):
                    yield ReasoningChunk(content=delta.reasoning)

                # Accumulate proper function-call fragments (do NOT yield individual deltas)
                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        if idx not in _tc_acc:
                            _tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc.id:
                            _tc_acc[idx]["id"] = tc.id
                        _fn = getattr(tc, "function", None)
                        if _fn and _fn.name:
                            _tc_acc[idx]["name"] = _fn.name
                        if _fn and _fn.arguments:
                            _tc_acc[idx]["arguments"] += _fn.arguments

                # text — intercept Llama's <|python_tag|> text-based tool calls
                # ONLY when tools are configured; otherwise <|python_tag|> is
                # used for code generation (e.g. fibonacci) and must pass through.
                if getattr(delta, "content", None):
                    content = delta.content
                    if self.provider_tools and (_in_tag or "<|python_tag|>" in content):
                        # Buffer without yielding — parse as tool call at finish
                        _in_tag = True
                        _tag_acc += content
                    else:
                        _text_yielded = True
                        yield TextChunk(content=content)

                # finish
                if choice.finish_reason is not None:
                    logger.info(
                        "groq_stream_finish",
                        extra={
                            "model": self.model,
                            "finish_reason": choice.finish_reason,
                            "total_chunks": _chunk_count,
                            "tc_acc_count": len(_tc_acc),
                            "tag_acc_len": len(_tag_acc),
                            "in_tag": _in_tag,
                            "text_yielded": _text_yielded,
                            "empty_choices": _empty_choices,
                        },
                    )
                    if choice.finish_reason == "tool_calls" and _tc_acc:
                        # Proper function-calling format — yield assembled tool calls
                        for idx in sorted(_tc_acc.keys()):
                            tc_data = _tc_acc[idx]
                            yield ToolCallChunk(
                                call_id=tc_data["id"],
                                tool_name=tc_data["name"],
                                arguments=normalize_tool_arguments(tc_data["arguments"]),
                            )
                    elif _tag_acc:
                        # Llama text-based format — parse and yield as a proper ToolCallChunk
                        import uuid as _uuid
                        parsed = _try_parse_python_tag_tool_call(_tag_acc)
                        if parsed:
                            tool_name, args = parsed
                            yield ToolCallChunk(
                                call_id=f"tag-{_uuid.uuid4().hex[:12]}",
                                tool_name=tool_name,
                                arguments=args,
                            )
                        else:
                            # Parsing failed — this was NOT a tool call (e.g. code
                            # generation that happens to use <|python_tag|>).
                            # Strip the tag and yield remaining content as text.
                            fallback = _tag_acc.replace("<|python_tag|>", "").strip()
                            if fallback:
                                yield TextChunk(content=fallback)
                            yield TextChunk(content="", finish_reason=choice.finish_reason)
                    else:
                        # Detect context overflow: model hit output token limit without
                        # producing any text (input was too large, left no room for output).
                        # Groq returns finish_reason="length" with 2047 empty-content chunks.
                        if choice.finish_reason == "length" and not _text_yielded:
                            logger.warning(
                                "groq_context_overflow",
                                extra={
                                    "model": self.model,
                                    "total_chunks": _chunk_count,
                                    "msg": "Input context too large — no output tokens available",
                                },
                            )
                            yield ErrorChunk(
                                error=(
                                    "The conversation context is too large for this model to respond. "
                                    "Try starting a new conversation, removing large attachments, "
                                    "or switching to a model with a larger context window."
                                )
                            )
                        else:
                            yield TextChunk(content="", finish_reason=choice.finish_reason)

                # usage
                # Groq reports usage on its final chunk by default (verified
                # live) -- no stream_options needed; asking for it would add a
                # duplicate usage-only chunk. Guarded to emit once regardless.
                if getattr(chunk, "usage", None) and not _usage_emitted:
                    _usage_emitted = True
                    yield make_usage_chunk(chunk.usage, provider="groq", model=self.model)

            # Post-loop: catch cases where stream ends without any content chunks.
            # An empty stream can occur when Groq silently drops a rate-limited or
            # auth-rejected request without raising an HTTP error in streaming mode.
            # Yielding an ErrorChunk lets the fallback chain activate; staying silent
            # would cause the agent engine to treat it as an empty response.
            if _chunk_count == 0:
                logger.warning(
                    "groq_stream_empty",
                    extra={"model": self.model, "empty_choices": _empty_choices},
                )
                yield ErrorChunk(
                    message=(
                        "Groq returned an empty streaming response with no content. "
                        "The request may have been silently rate-limited or rejected. "
                        "Trying next provider in fallback chain if configured."
                    ),
                    provider="groq",
                    error_type="provider_down",
                )

        except Exception as e:
            err_msg_lower = str(e).lower()

            # Surface a user-friendly message for known Groq tool-call
            # failures that are model-specific (e.g. llama-3.3-70b).
            _is_tool_fail = self.provider_tools and (
                "failed to call a function" in err_msg_lower
                or "tool call validation failed" in err_msg_lower
            )

            if _is_tool_fail:
                user_msg = (
                    f"Model '{self.model}' failed to generate a valid tool call. "
                    "This model has unreliable tool-calling support. "
                    "Try a different model (e.g. openai/gpt-oss-20b) or disable tools."
                )
                logger.warning(
                    "groq_tool_call_rejected_by_server",
                    extra={
                        "model": self.model,
                        "error": str(e)[:200],
                    },
                )
                yield ErrorChunk(
                    message=user_msg,
                    provider="groq",
                    error_type="tool_call_failed",
                    details={"raw_error": str(e)[:500], "model": self.model},
                )
                return

            _err_type = classify_error(e)
            logger.error(
                "groq_handler — unhandled LLM exception",
                exc_info=e,
                extra={
                    "model": self.model,
                    "exc_class": type(e).__name__,
                    "eva_error_type": _err_type.value,
                    "error": str(e)[:400],
                },
            )
            # Use EVA's error taxonomy (e.g. "rate_limit", "auth", "provider_down")
            # so the fallback chain in AgentEngine._gen_with_fallback() can match
            # against FALLBACK_ERROR_TYPES correctly.  type(e).__name__ (e.g.
            # "RateLimitError") would never match those string values.
            yield ErrorChunk(
                message=str(e),
                provider="groq",
                error_type=_err_type.value,
            )
