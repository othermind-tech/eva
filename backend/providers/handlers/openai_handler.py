# providers/handlers/openai_handler.py

import logging
from typing import List, Dict, Any, AsyncGenerator, Optional
from utils.tool_normalizer import normalize_tool_arguments
from openai import AsyncOpenAI

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

# V6.7 -- process-lifetime, in-memory cache of models observed to need
# max_completion_tokens instead of max_tokens. Seeded lazily from
# param_support.py's known-prefix list (o1/o3/o4/gpt-5 -- zero-latency
# fast path for today's known reasoning models) and grown at runtime when
# an unlisted model rejects the wrong name -- see _create_with_token_param_retry().
# No hardcoded list to maintain as OpenAI ships new model names: confirmed
# that neither litellm's public
# get_supported_openai_params() nor its real request-transform code has a
# reliable per-model answer for this, and litellm itself has an open,
# unresolved bug in exactly this area -- so "let the API be the source of
# truth, learn once, remember" is the more robust choice here than trying
# to out-guess OpenAI's own model naming.
_LEARNED_TOKEN_LIMIT_PARAM: Dict[str, str] = {}


def _is_wrong_token_param_error(exc: Exception) -> bool:
    """
    Detects OpenAI's specific "you sent max_tokens/max_completion_tokens
    but this model wants the other one" rejection, so create_message can
    retry once with the corrected name rather than failing the whole turn.
    Matches on the structured `.param` field (set by openai's own SDK from
    the parsed error body -- see core/error_handling.py's structured
    classifier) plus a loose message check, not a blind "any 400" catch --
    an unrelated validation error must not trigger a pointless retry.
    """
    param = getattr(exc, "param", None)
    if param not in ("max_tokens", "max_completion_tokens"):
        return False
    msg = str(exc).lower()
    return "not supported" in msg or "unsupported" in msg or "not permitted" in msg


class OpenAIHandler:
    """
    OpenAI handler
    - Supports tool calling (function calling)
    - Supports reasoning (V6.7: full level range -- see create_message)
    """

    def __init__(self, config: Dict[str, Any]):
        self.client = AsyncOpenAI(api_key=config["api_key"])
        self.model = config["model"]

        self.dispatcher = config.get("dispatcher")
        self.tool_classes = config.get("tool_classes", {})
        self.provider_tools = config.get("provider_tools", [])

        # V6.4/V6.7: optional max_tokens/temperature. token_limit_param
        # starts from param_support.py's known-prefix seed (o1/o3/o4/gpt-5)
        # but is re-checked against the runtime-learned cache on every
        # create_message() call below, since that cache can grow after
        # __init__ ran (a long-lived handler instance, or a second
        # instance for the same model in the same process).
        self._max_tokens = config.get("max_tokens")
        self._temperature = config.get("temperature")
        self._token_limit_param = "max_tokens"
        if self._max_tokens is not None or self._temperature is not None:
            from core.providers.param_support import get_param_support  # noqa: PLC0415
            _support = get_param_support("openai", self.model)
            self._token_limit_param = _support.token_limit_param
            if self._temperature is not None and not _support.supports_temperature:
                logger.debug(
                    "openai_handler: temperature requested but unsupported for this model — omitting",
                    extra={"model": self.model},
                )
                self._temperature = None

    async def _create_with_token_param_retry(self, req: Dict[str, Any]):
        """
        Calls the OpenAI API with whichever token-limit param name is
        currently in `req`; on the specific "wrong name for this model"
        rejection, swaps to the other name, remembers it in
        _LEARNED_TOKEN_LIMIT_PARAM for every future call to this model in
        this process, and retries exactly once. See module docstring for
        why this replaces trying to pre-guess every current/future
        reasoning model name.
        """
        try:
            return await self.client.chat.completions.create(**req)
        except Exception as exc:
            if not _is_wrong_token_param_error(exc):
                raise
            wrong_param = getattr(exc, "param", None)
            if wrong_param not in req:
                raise  # error wasn't actually about a param we sent -- don't loop
            other_param = "max_completion_tokens" if wrong_param == "max_tokens" else "max_tokens"
            req[other_param] = req.pop(wrong_param)
            _LEARNED_TOKEN_LIMIT_PARAM[self.model] = other_param
            logger.info(
                "openai_handler: model needs %s not %s — retrying once and remembering for future calls",
                other_param, wrong_param,
                extra={"model": self.model},
            )
            return await self.client.chat.completions.create(**req)

    async def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        stream: bool = True,
        reasoning: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        reasoning: V6.7 -- one of core.providers.reasoning_support.
        REASONING_LEVELS ("minimal"/"low"/"medium"/"high"/"xhigh"), or
        None/"off" to disable. Passed straight through as OpenAI's own
        `reasoning.effort` -- no EVA-side level mapping needed, OpenAI
        already accepts this exact vocabulary. Which levels a specific
        model actually supports isn't reliably knowable in advance (see
        module docstring); an unsupported level surfaces as a normal
        classified provider error rather than being pre-validated here.
        """
        reasoning_enabled = reasoning not in (None, "off")

        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

        token_limit_param = _LEARNED_TOKEN_LIMIT_PARAM.get(self.model, self._token_limit_param)

        # Prompt caching: no request-side config needed. OpenAI automatically
        # caches (and discounts) the longest matching prompt prefix across
        # requests to the same model, with no opt-in parameter and no
        # request-shape change required, for prompts over roughly 1024
        # tokens (verified against OpenAI's own prompt-caching docs, July
        # 2026). EVA's baseline system prompt (platform preamble + tool
        # manifest) already clears that floor, so the repeated identical
        # sends within one execution's ReAct loop (see core/agent_engine.py)
        # should already benefit here with zero code changes.
        req: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }

        if self.provider_tools:
            req["tools"] = self.provider_tools
            req["tool_choice"] = "auto"

        if reasoning_enabled:
            req["reasoning"] = {"effort": reasoning}

        if stream:
            # Without this OpenAI sends no usage at all on a streamed call.
            req["stream_options"] = {"include_usage": True}

        if self._max_tokens is not None and token_limit_param:
            req[token_limit_param] = self._max_tokens
        if self._temperature is not None:
            req["temperature"] = self._temperature

        try:
            # =============================================
            # NON-STREAMING
            # =============================================
            if not stream:
                response = await self._create_with_token_param_retry(req)
                choice = response.choices[0]
                msg = choice.message

                # reasoning
                r = getattr(msg, "reasoning", None)
                if reasoning_enabled and r:
                    yield ReasoningChunk(content=r)

                # tool call?
                if getattr(msg, "tool_calls", None):
                    for c in msg.tool_calls:
                        yield ToolCallChunk(
                            call_id=c.id,
                            tool_name=c.function.name,
                            arguments=normalize_tool_arguments(c.function.arguments),
                        )
                    return

                # text output
                yield TextChunk(
                    content=msg.content or "",
                    finish_reason=choice.finish_reason,
                )

                # usage
                if getattr(response, "usage", None):
                    yield make_usage_chunk(response.usage, provider="openai", model=self.model)
                return

            # =============================================
            # STREAMING
            # =============================================
            # In OpenAI SDK 1.x, chat.completions.create() is async def and
            # returns a coroutine.  Await it first to get the AsyncStream, then
            # use the stream as a context manager for proper resource cleanup.
            _raw_stream = await self._create_with_token_param_retry(req)
            async with _raw_stream as stream_resp:
                # OpenAI fragments tool-call arguments across multiple streaming
                # deltas. Accumulate and yield complete ToolCallChunks only when
                # finish_reason == "tool_calls".
                _tc_acc: dict = {}  # index → {id, name, arguments}

                _usage_emitted = False
                async for chunk in stream_resp:
                    if not chunk.choices:
                        # With stream_options.include_usage the final chunk
                        # carries the usage and NO choices -- it used to be
                        # skipped here, so streamed calls reported no usage.
                        if getattr(chunk, "usage", None) and not _usage_emitted:
                            _usage_emitted = True
                            yield make_usage_chunk(chunk.usage, provider="openai", model=self.model)
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta

                    # reasoning
                    if reasoning_enabled and getattr(delta, "reasoning", None):
                        yield ReasoningChunk(content=delta.reasoning)

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

                    if getattr(chunk, "usage", None) and not _usage_emitted:
                        _usage_emitted = True
                        yield make_usage_chunk(chunk.usage, provider="openai", model=self.model)

        except Exception as e:
            _err_type = classify_error(e)
            logger.error(
                "openai_handler_exception",
                exc_info=e,
                extra={
                    "model": self.model,
                    "exc_class": type(e).__name__,
                    "eva_error_type": _err_type.value,
                    "error": str(e)[:400],
                },
            )
            # Use EVA's error taxonomy so FALLBACK_ERROR_TYPES matching works.
            yield ErrorChunk(
                message=str(e),
                provider="openai",
                error_type=_err_type.value,
            )
