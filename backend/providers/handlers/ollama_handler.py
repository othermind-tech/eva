# providers/handlers/ollama_handler.py
#
# EVA handler for Ollama local LLM inference.
#
# Ollama SDK streaming contract:
#   async for chunk in await client.chat(..., stream=True):
#       chunk.message.content  → str | None   (text delta)
#       chunk.message.tool_calls → list | None
#       chunk.done             → bool          (True on final chunk)
#       chunk.prompt_eval_count / chunk.eval_count  → token counts (final chunk)
#
# Tool-call contract (non-streaming):
#   response.message.tool_calls[i].function.name      → str
#   response.message.tool_calls[i].function.arguments → Mapping[str, Any]
#   (arguments is already a dict — no JSON parsing needed)
#
# Tool schema: Ollama uses the same OpenAI function-calling JSON schema that
# build_provider_tools() already produces for "openai" / "groq".  We reuse
# that schema by requesting format "openai" in the factory.

from __future__ import annotations

import logging
import uuid
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
from core.providers.reasoning_support import ollama_think_value

logger = logging.getLogger(__name__)


def _settings_host() -> str:
    """
    Resolve OLLAMA_HOST: real OS env var or eva.env, else the local default.

    Previously a 4-tier fallback (env var -> pydantic settings -> a second,
    manual eva.env re-read -> hardcoded default) with two silent
    `except Exception: pass` blocks, working around config/env_loader.py's
    load_eva_env() not copying general eva.env config into os.environ.
    Since that gap is fixed (Step 4 in load_eva_env()), os.getenv() alone already sees
    both a real env var and an eva.env value with correct precedence,
    identically to what config.settings.settings.ollama.OLLAMA_HOST (still
    reading the same file, same os.environ) would resolve to -- so the
    other three tiers were left with nothing left to add.
    """
    import os
    return os.getenv("OLLAMA_HOST", "http://localhost:11434")


class OllamaHandler:
    """
    Provider handler for Ollama.

    Implements the same interface as GroqHandler / OpenAIHandler:
        async def create_message(system_prompt, messages, stream, reasoning)
            -> AsyncGenerator[StreamChunk, None]

    Key behavioural notes
    ----------------------
    - api_key in config is repurposed as an optional custom host URL.
      If it looks like a URL (starts with http) it is used as the host;
      otherwise it is ignored and OLLAMA_HOST / localhost fallback applies.
    - Tool arguments from Ollama are already dicts (Mapping[str, Any]),
      not JSON strings — no extra parsing required.
    - Reasoning / thinking control (V6.7): `reasoning` is a level string
      ("minimal"/"low"/"medium"/"high"/"xhigh") or None, mapped via
      core.providers.reasoning_support.ollama_think_value() to Ollama's
      own `think` value and passed to the SDK's client.chat() call. Always
      sent as a level string, never a bare bool -- gpt-oss specifically
      requires a level string and silently ignores a bool
      (docs.ollama.com/capabilities/thinking), and every other reasoning-
      capable Ollama model accepts a level string too, so one code path
      correctly covers all of them without a per-model-family list. When
      reasoning is None/"off", `think` is omitted from the request
      entirely so responses stay clean and tool-call reliability isn't
      degraded.
    - Streaming: uses the async generator form of AsyncClient.chat().
    - Non-streaming: uses the awaitable (stream=False) form.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.model: str = config["model"]

        # api_key is repurposed as optional custom host for Ollama.
        # Resolution order:
        #   1. api_key field (if it looks like a URL)
        #   2. OLLAMA_HOST from config/settings.py  (reads .env)
        #   3. OLLAMA_HOST env var bare fallback
        raw_key: str = config.get("api_key") or ""
        self.host: str = raw_key if raw_key.startswith("http") else _settings_host()

        self.provider_tools: List[Dict[str, Any]] = config.get("provider_tools", [])
        self.tool_classes: Dict[str, Any] = config.get("tool_classes", {})
        self.dispatcher = config.get("dispatcher")

        # V6.4: optional max_tokens/temperature. Always supported by Ollama,
        # but the native API nests both under an "options" dict rather than
        # as top-level request keys (num_predict is the max-tokens key) --
        # see _build_options() below and core/providers/param_support.py.
        self._max_tokens = config.get("max_tokens")
        self._temperature = config.get("temperature")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        from ollama import AsyncClient
        return AsyncClient(host=self.host)

    def _build_options(self) -> Dict[str, Any]:
        """Build the nested "options" dict Ollama's native API expects for
        generation parameters -- num_predict (max tokens) and temperature
        are never top-level request keys on this API."""
        options: Dict[str, Any] = {}
        if self._max_tokens is not None:
            options["num_predict"] = self._max_tokens
        if self._temperature is not None:
            options["temperature"] = self._temperature
        return options

    def _build_messages(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Adapt the message list to Ollama's native tool-call format before
        sending to the Ollama SDK.

        EVA's agent_engine builds tool-related messages in OpenAI format
        (tool_call_id, type, null content, JSON-string arguments). Ollama
        expects a simpler schema — unknown fields are silently ignored by
        some models and cause empty / confused responses in others (notably
        Gemma 4 and several GPT-architecture open-weight models).

        Transformations applied:
          role=assistant with tool_calls:
            - content: null  →  content: ""
            - tool_calls[].id removed  (Ollama doesn't use roundtrip IDs)
            - tool_calls[].type removed
            - arguments JSON string  →  dict  (Ollama SDK expects Mapping)

          role=tool:
            - tool_call_id removed
            - name removed
            - content preserved as-is

          All other messages pass through unchanged.
        """
        import json as _json

        adapted: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")

            if role == "assistant" and msg.get("tool_calls"):
                tool_calls_adapted = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    raw_args = fn.get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            raw_args = _json.loads(raw_args)
                        except Exception as _parse_exc:
                            logger.debug(
                                "ollama_handler — tool call args JSON parse failed, using {}: %s",
                                _parse_exc,
                            )
                            raw_args = {}
                    tool_calls_adapted.append({
                        "function": {
                            "name": fn.get("name", ""),
                            "arguments": raw_args,
                        }
                    })
                adapted.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls_adapted,
                })

            elif role == "tool":
                adapted.append({
                    "role": "tool",
                    "content": msg.get("content", ""),
                })

            else:
                adapted.append(msg)

        if system_prompt:
            return [{"role": "system", "content": system_prompt}] + adapted
        return adapted

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

        # Prompt caching: not applicable in the cost-savings sense (there is
        # no request, so no per-token billing to discount). Local Ollama
        # does keep the model's KV-cache resident in VRAM/host memory
        # between calls while the model stays loaded (unloaded after ~5min
        # idle by default), so a byte-identical repeated prefix -- like this
        # execution's unchanged system prompt across ReAct-loop turns, see
        # core/agent_engine.py -- can already reuse that cache and skip
        # reprocessing. That reuse is internal to the Ollama server process,
        # not a request-configurable API feature, so there is no
        # cache_control-equivalent parameter for this handler to set.
        ollama_messages = self._build_messages(system_prompt, messages)
        tools = self.provider_tools if self.provider_tools else None
        think = ollama_think_value(reasoning)

        # log raw request context before hitting Ollama API
        logger.debug(
            "ollama_handler — sending request to Ollama API",
            extra={
                "model": self.model,
                "host": self.host,
                "stream": stream,
                "msg_count": len(ollama_messages),
                "has_tools": bool(tools),
                "tool_count": len(tools) if tools else 0,
                "last_msg_role": ollama_messages[-1].get("role") if ollama_messages else None,
                "last_msg_preview": repr(str(ollama_messages[-1].get("content") or "")[:120]) if ollama_messages else None,
            },
        )

        try:
            client = self._get_client()
            options = self._build_options()

            if stream:
                # ── STREAMING ────────────────────────────────────────────────
                async for chunk in await client.chat(
                    model=self.model,
                    messages=ollama_messages,
                    stream=True,
                    tools=tools,
                    think=think,  # V6.7: level string or None, see ollama_think_value()
                    options=options or None,
                ):
                    msg = chunk.message

                    # Thinking delta (only present when think=True and model supports it)
                    if getattr(msg, "thinking", None):
                        yield ReasoningChunk(content=msg.thinking)

                    # Text delta
                    if msg.content:
                        yield TextChunk(content=msg.content)

                    # Tool calls (Ollama emits these only on the final chunk)
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            yield ToolCallChunk(
                                call_id=str(uuid.uuid4()),
                                tool_name=tc.function.name,
                                # arguments is already a Mapping[str, Any]
                                arguments=dict(tc.function.arguments),
                            )

                    # Usage (only on the last chunk when done=True)
                    if chunk.done:
                        logger.debug(
                            "ollama_handler — stream done",
                            extra={
                                "model": self.model,
                                "done_reason": getattr(chunk, "done_reason", None),
                                "prompt_eval_count": getattr(chunk, "prompt_eval_count", None),
                                "eval_count": getattr(chunk, "eval_count", None),
                                "had_tool_calls": bool(msg.tool_calls),
                            },
                        )
                        prompt_tokens = chunk.prompt_eval_count or 0
                        completion_tokens = chunk.eval_count or 0
                        if prompt_tokens or completion_tokens:
                            yield UsageChunk(
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=prompt_tokens + completion_tokens,
                                provider="ollama",
                                model=self.model,
                            )
                        # Emit finish signal
                        if not (msg.tool_calls):
                            yield TextChunk(
                                content="",
                                finish_reason=chunk.done_reason or "stop",
                            )

            else:
                # ── NON-STREAMING ────────────────────────────────────────────
                response = await client.chat(
                    model=self.model,
                    messages=ollama_messages,
                    stream=False,
                    tools=tools,
                    think=think,  # V6.7: level string or None, see ollama_think_value()
                    options=options or None,
                )
                msg = response.message

                # Thinking content (only present when think=True and model supports it)
                if getattr(msg, "thinking", None):
                    yield ReasoningChunk(content=msg.thinking)

                # Tool calls take priority over text
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        yield ToolCallChunk(
                            call_id=str(uuid.uuid4()),
                            tool_name=tc.function.name,
                            arguments=dict(tc.function.arguments),
                        )
                else:
                    yield TextChunk(
                        content=msg.content or "",
                        finish_reason=getattr(response, "done_reason", None) or "stop",
                    )

                # Usage
                prompt_tokens = response.prompt_eval_count or 0
                completion_tokens = response.eval_count or 0
                if prompt_tokens or completion_tokens:
                    yield UsageChunk(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=prompt_tokens + completion_tokens,
                        provider="ollama",
                        model=self.model,
                    )

        except Exception as exc:
            # Build a human-readable message surfacing Ollama-specific status codes
            try:
                from ollama import ResponseError
                if isinstance(exc, ResponseError):
                    msg_text = f"Ollama ResponseError {exc.status_code}: {exc.error}"
                else:
                    msg_text = f"Ollama error: {exc}"
            except ImportError:
                msg_text = f"Ollama error: {exc}"

            _err_type = classify_error(exc)
            logger.error(
                "ollama_handler — unhandled LLM exception",
                exc_info=exc,
                extra={
                    "model":      self.model,
                    "host":       self.host,
                    "exc_class":  type(exc).__name__,
                    "error_type": _err_type.value,
                    "error_msg":  msg_text[:400],
                },
            )
            yield ErrorChunk(
                message=msg_text,
                provider="ollama",
                error_type=_err_type.value,
            )
