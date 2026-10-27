# providers/handlers/litellm_handler.py

import logging
from typing import List, Dict, Any, AsyncGenerator, Optional
from utils.tool_normalizer import normalize_tool_arguments
import litellm

from core.types import (
    TextChunk,
    ReasoningChunk,
    ErrorChunk,
    UsageChunk,
    ToolCallChunk,
    StreamChunk,
)
from core.error_handling import classify_error
from providers.usage import make_usage_chunk

logger = logging.getLogger(__name__)


class LiteLLMHandler:
    """
    Universal LiteLLM handler (OpenAI / Groq / Mistral / Claude via LiteLLM)
    """

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config["api_key"]
        self.model = config["model"]

        self.provider_tools = config.get("provider_tools", [])
        self.tool_classes = config.get("tool_classes", {})
        self.dispatcher = config.get("dispatcher")

        # V6.4: optional max_tokens/temperature. This is the one handler
        # where LiteLLM's own get_supported_openai_params() IS the correct
        # source of truth -- this handler genuinely calls litellm.acompletion()
        # underneath, so LiteLLM performs the real-API translation itself.
        # See core/providers/param_support.py.
        self._max_tokens = config.get("max_tokens")
        self._temperature = config.get("temperature")
        self._token_limit_param = "max_tokens"
        if self._max_tokens is not None or self._temperature is not None:
            from core.providers.param_support import get_param_support  # noqa: PLC0415
            _support = get_param_support("litellm", self.model)
            self._token_limit_param = _support.token_limit_param
            if self._temperature is not None and not _support.supports_temperature:
                logger.debug(
                    "litellm_handler: temperature requested but unsupported for this model — omitting",
                    extra={"model": self.model},
                )
                self._temperature = None

    async def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        stream: bool = True,
        reasoning: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        reasoning: V6.7 -- one of core.providers.reasoning_support.
        REASONING_LEVELS, or None/"off" to disable. Passed straight through
        as litellm's own `reasoning_effort` (this handler's whole point is
        that litellm does the real per-provider translation itself, unlike
        the 5 native handlers -- no EVA-side level mapping needed here).
        drop_params=True below means an unsupported level for a specific
        model is silently dropped by litellm rather than erroring, the
        same graceful-degradation behaviour already relied on for
        max_tokens/temperature.
        """
        reasoning_enabled = reasoning not in (None, "off")

        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

        # Prompt caching: litellm DOES have a genuine, provider-agnostic
        # passthrough for this -- a message content block shaped
        # {"type":"text","text":...,"cache_control":{"type":"ephemeral"}}
        # (Anthropic's own wire format) gets auto-translated to Bedrock
        # Claude / Vertex Anthropic / Vertex Gemini context-caching calls,
        # and litellm's OpenAI transformation (litellm/llms/openai/chat/
        # gpt_transformation.py, remove_cache_control_flag_from_messages_
        # and_tools()) explicitly strips it back out before sending to
        # OpenAI/Mistral-family (OpenAI-derived) endpoints -- verified
        # against the installed litellm==1.89.3 source, not assumed.
        # NOT wired up here: this handler is a genuine catch-all for
        # whatever model string litellm is given (Cohere, dashscope, etc.),
        # and several of those provider configs do NOT subclass the
        # OpenAI-derived base that does the stripping -- so unconditionally
        # injecting cache_control risks sending an unrecognized field to a
        # provider that might reject it. Safe to add later scoped to a
        # known-supported provider allowlist; not "trivially safe" as a
        # blanket default the way it is for the dedicated anthropic_handler.
        req: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "api_key": self.api_key,
            "stream": stream,
        }

        if self.provider_tools:
            req["tools"] = self.provider_tools
            req["tool_choice"] = "auto"

        if stream:
            # Ask for usage on the stream. Providers that do not support it are
            # handled by drop_params=True below (silently dropped, not an error).
            req["stream_options"] = {"include_usage": True}

        if reasoning_enabled and litellm.supports_reasoning(model=self.model):
            req["reasoning_effort"] = reasoning

        if self._max_tokens is not None and self._token_limit_param:
            req[self._token_limit_param] = self._max_tokens
        if self._temperature is not None:
            req["temperature"] = self._temperature
        # Safety net: if a param slips through despite the capability check
        # above (e.g. a model LiteLLM has no metadata for), drop it instead
        # of raising -- LiteLLM's own built-in graceful-degradation feature.
        req["drop_params"] = True

        try:
            # ==========================
            # NON-STREAMING
            # ==========================
            if not stream:
                response = await litellm.acompletion(**req)
                choice = response.choices[0]
                msg = choice.message

                # reasoning
                rc = getattr(msg, "reasoning_content", None)
                if reasoning_enabled and rc:
                    yield ReasoningChunk(content=rc)

                # tool call?
                if getattr(msg, "tool_calls", None):
                    for c in msg.tool_calls:
                        yield ToolCallChunk(
                            call_id=c.id,
                            tool_name=c.function.name,
                            arguments=normalize_tool_arguments(c.function.arguments),
                        )
                    return

                yield TextChunk(
                    content=msg.content or "",
                    finish_reason=choice.finish_reason,
                )

                if getattr(response, "usage", None):
                    yield make_usage_chunk(response.usage, provider="litellm", model=self.model)

                return

            # ==========================
            # STREAMING
            # ==========================
            stream_resp = await litellm.acompletion(**req)

            # LiteLLM (OpenAI-compatible) fragments tool-call arguments across
            # multiple streaming deltas. Accumulate and yield complete
            # ToolCallChunks only when finish_reason == "tool_calls".
            _tc_acc: dict = {}  # index → {id, name, arguments}

            _usage_emitted = False
            async for chunk in stream_resp:
                if not chunk.choices:
                    # include_usage's final chunk carries the usage and NO
                    # choices. It used to be skipped here, so a streamed
                    # LiteLLM call (the main chat path) reported no usage.
                    if getattr(chunk, "usage", None) and not _usage_emitted:
                        _usage_emitted = True
                        yield make_usage_chunk(chunk.usage, provider="litellm", model=self.model)
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # reasoning (streamed)
                rc = getattr(delta, "reasoning_content", None)
                if reasoning_enabled and rc:
                    yield ReasoningChunk(content=rc)

                # Accumulate tool-call fragments (do NOT yield individual deltas)
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

                # text
                if getattr(delta, "content", None):
                    yield TextChunk(content=delta.content)

                # finish
                if choice.finish_reason is not None:
                    if choice.finish_reason == "tool_calls" and _tc_acc:
                        # Yield complete, fully-assembled tool calls
                        for idx in sorted(_tc_acc.keys()):
                            tc_data = _tc_acc[idx]
                            yield ToolCallChunk(
                                call_id=tc_data["id"],
                                tool_name=tc_data["name"],
                                arguments=normalize_tool_arguments(tc_data["arguments"]),
                            )
                    else:
                        yield TextChunk(content="", finish_reason=choice.finish_reason)

                # usage
                if getattr(chunk, "usage", None) and not _usage_emitted:
                    _usage_emitted = True
                    yield make_usage_chunk(chunk.usage, provider="litellm", model=self.model)

        except Exception as e:
            _err_type = classify_error(e)
            logger.error(
                "litellm_handler_exception",
                exc_info=e,
                extra={
                    "model": self.model,
                    "exc_class": type(e).__name__,
                    "eva_error_type": _err_type.value,
                    "error": str(e)[:400],
                },
            )
            yield ErrorChunk(
                message=str(e),
                provider="litellm",
                error_type=_err_type.value,
            )
