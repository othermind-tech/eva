# core/agent_engine.py
import json, time, asyncio
from typing import List, Dict, Optional, AsyncGenerator, Any, Tuple
import logging
from utils.handler_utils import create_provider_handler
from utils.message_builder import build_tool_message
from core.api import ApiHandler, ApiStream
from core.factory import HandlerFactory
from core.react.context import ReActContext
from core.types import (
    StreamChunk,
    TextChunk,
    ReasoningChunk,
    ErrorChunk,
    UsageChunk,
    ToolCallChunk,
    ToolResultChunk,
    ReActPhase,
    ReActStep,
    ProviderFallbackChunk,
)
from core.error_handling import classify_error, get_error_policy_by_value
from core.event_bus import event_bus
from core.events import ProviderFallbackTriggered
from core.tools.executor import execute_tool_safely
from core.tools.dynamic_registry import DynamicToolRegistry
from core.tools.base import ToolAuthority
from core.providers.capability_registry import get_model as get_model_capability
from core.memory.token_counter import TokenCounter
from observability.null import NullObservabilityHook

from config.settings import (
    MAX_REACT_STEPS,
    CONTEXT_WATCHDOG_TRIGGER_RATIO,
    CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS,
    EVA_FIRST_CHUNK_TIMEOUT_S as _FIRST_CHUNK_TIMEOUT_S,
    EVA_INTER_CHUNK_TIMEOUT_S as _INTER_CHUNK_TIMEOUT_S,
    DUPLICATE_TOOL_CALL_THRESHOLD_READ_ONLY,
    DUPLICATE_TOOL_CALL_THRESHOLD_MUTATING,
    DUPLICATE_TOOL_CALL_THRESHOLD_DESTRUCTIVE,
    get_user_session,
)
from core.hooks import HookRunner
from utils.workspace import get_user_workspace

# V6.8: cooldown primitives, backoff, and API-key resolution used to be
# defined here and only here. They now live in core/providers/fallback_state.py
# so every non-streaming LLM call site (title generator, prompt enhancer,
# memory extractor/summarizer, artifact updater, coordinate_tool, Verifier/
# Planner/IntentRouter) can share the same cross-execution cooldown store
# and API-key resolution logic instead of each re-deriving it (or, in
# several cases, silently missing the environment-variable fallback
# entirely). Behavior here is unchanged -- this is an import move, not a
# logic change.
from core.providers.fallback_state import (
    backoff_sleep as _backoff_sleep,
    is_in_cooldown as _is_in_cooldown,
    record_cooldown_failure as _record_cooldown_failure,
    clear_cooldown as _clear_cooldown,
    resolve_api_key as _resolve_fallback_api_key,
    _cooldown_key,
    _cooldown_streak_key,
    _FALLBACK_COOLDOWN_MAX_SECONDS,
    _FALLBACK_RETRY_BASE_DELAY_S,
    _FALLBACK_RETRY_MAX_DELAY_S,
)

logger = logging.getLogger(__name__)


async def _timed_stream(
    gen: AsyncGenerator,
    execution_id: str,
    provider: str,
    model: str,
) -> AsyncGenerator:
    """
    Wraps a provider streaming generator with per-chunk idle timeouts.

    - First chunk: _FIRST_CHUNK_TIMEOUT_S (long — reasoning models start slow)
    - Subsequent chunks: _INTER_CHUNK_TIMEOUT_S (short — TCP stall detection)

    On timeout, yields a single ErrorChunk(error_type="timeout") and closes
    the underlying generator so the caller can route the error to the fallback
    chain or surface it to the user.

    This wrapper is transparent to all other logic: ErrorChunks, ToolCallChunks,
    ReasoningChunks, etc. all pass through unchanged.
    """
    first_received = False
    try:
        while True:
            timeout = (
                _FIRST_CHUNK_TIMEOUT_S if not first_received
                else _INTER_CHUNK_TIMEOUT_S
            )
            try:
                chunk = await asyncio.wait_for(
                    gen.__anext__(),
                    timeout=timeout,
                )
            except StopAsyncIteration:
                return  # generator exhausted normally
            except asyncio.TimeoutError:
                idle_label = "first-chunk" if not first_received else "inter-chunk"
                logger.warning(
                    "Provider stream idle timeout — closing connection",
                    extra={
                        "execution_id": execution_id,
                        "provider": provider,
                        "model": model,
                        "timeout_type": idle_label,
                        "timeout_seconds": timeout,
                    },
                )
                yield ErrorChunk(
                    message=(
                        f"Provider '{provider}' did not respond within {timeout}s "
                        f"({idle_label} timeout). The connection may be stalled. "
                        "Trying next provider in fallback chain if configured."
                    ),
                    provider=provider,
                    error_type="timeout",
                )
                return
            first_received = True
            yield chunk
    finally:
        # Always close the underlying generator to free the HTTP connection.
        try:
            await gen.aclose()
        except Exception:
            pass


def _next_eligible_candidate(
    candidates: List[Tuple[Any, str, str]],
    from_idx: int,
    skip_providers: set,
) -> Optional[Tuple[int, str, str]]:
    """
    Scan forward from from_idx+1 for the next candidate whose provider is
    not in skip_providers (populated for AUTH failures — see
    core/error_handling.ERROR_POLICIES' skip_same_provider).  For every
    other error type skip_providers stays empty, so this is equivalent to
    "does candidates[from_idx + 1] exist" — the original behaviour.

    Returns (index, provider, model), or None if no eligible candidate
    remains — including the case where every remaining candidate shares
    the same provider as the one that just failed.
    """
    for idx in range(from_idx + 1, len(candidates)):
        _, provider, model = candidates[idx]
        if provider not in skip_providers:
            return idx, provider, model
    return None


class AgentEngine:
    """
    Provider-agnostic agent engine supporting:
    - Reasoning
    - Tool calling
    - Streaming + non-streaming
    - Provider fallback chain (transparent to the caller)
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        system_prompt: Optional[str] = None,
        reasoning: Optional[str] = None,  # V6.7: level string, or None for off
        tools=None,                 # UserToolRegistry instance
        dispatcher=None,             # ToolDispatcher instance
        session_id: Optional[str] = None,
        memory_provider: Optional[Any] = None,
        cancel_token=None,
        dynamic_registry: Optional[DynamicToolRegistry] = None,
        execution: Optional[Any] = None,  # ConversationExecution — passed to tool instances
        fallback_chain: Optional[List[Dict[str, str]]] = None,
        on_step_complete=None,
        max_steps: Optional[int] = None,   # G-005: per-instance cap; overrides module MAX_REACT_STEPS
        progressive: bool = False,  # V5.0 Sprint 2: progressive tool disclosure
    ):
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt or ""
        self.reasoning = reasoning
        self.tools = tools
        self.dispatcher = dispatcher
        self.session_id = session_id
        self.memory_provider = memory_provider
        self.cancel_token = cancel_token
        self.observability = getattr(dispatcher, "observability", None) or NullObservabilityHook()
        self.dynamic_registry = dynamic_registry
        self.execution = execution  # injected into tool instances at execution time
        self.on_step_complete = on_step_complete
        self._max_steps: Optional[int] = max_steps  # None → use module-level MAX_REACT_STEPS
        self._progressive: bool = progressive  # V5.0 Sprint 2: progressive tool disclosure
        self._step_extensions_used: int = 0  # V6.12: ask-to-continue gate at the step limit

        # Primary handler
        self.handler = create_provider_handler(
            provider=provider,
            api_key=api_key,
            model=model,
            tools=tools,
            dispatcher=dispatcher,
            enable_reasoning=reasoning,
        )

        # Fallback chain config: list of {provider, model, api_key?} dicts.
        # Handlers are built lazily on first use to avoid unnecessary
        # API-client initialisation when no fallback is needed.
        self._fallback_chain_config: List[Dict[str, str]] = fallback_chain or []
        self._fallback_handlers: Optional[List[Tuple[Any, str, str]]] = None  # lazy

    # ------------------------------------------------------------------
    # Fallback handler construction
    # ------------------------------------------------------------------

    def _build_fallback_handlers(self) -> List[Tuple[Any, str, str]]:
        """
        Build (handler, provider, model) tuples for every entry in the
        user's fallback_chain config.  Entries that fail to initialise
        (unknown provider, etc.) are skipped with a warning so a single
        misconfigured entry does not kill the whole chain.

        Capability check
        ----------------
        Each fallback model is checked against the capability registry.
        If the primary session has a reasoning level set but the fallback
        model does not support reasoning at all, the level is cleared to
        None for that handler.  This prevents the same silent-empty-
        response bug that affects the primary model when reasoning is
        enabled on a model that doesn't support it. If the model DOES
        support reasoning but only a subset of the requested level's
        vocabulary (or a different shape entirely -- e.g. falling over
        from an OpenAI-style level to Anthropic's own range), the level
        is snapped to the nearest one that candidate actually accepts
        (core/providers/reasoning_support.py), same as the save-time
        check in api/session.py.
        """
        result: List[Tuple[Any, str, str]] = []
        for entry in self._fallback_chain_config:
            fb_provider = entry.get("provider", "")
            fb_model    = entry.get("model", "")
            if not fb_provider or not fb_model:
                logger.warning(
                    "Skipping fallback chain entry with missing provider/model",
                    extra={"entry": entry},
                )
                continue

            # Resolve API key: explicit → env var → empty string (V6.8:
            # now the shared resolver every LLM call site uses, not a
            # copy local to this method).
            fb_key = _resolve_fallback_api_key(entry.get("api_key"), fb_provider)

            # Check whether this fallback model supports the session's
            # reasoning level.  If not supported at all, disable it for
            # this handler only; if supported but with a smaller level
            # set, snap to the nearest one this candidate accepts.
            fb_reasoning = self.reasoning
            if fb_reasoning is not None:
                cap = get_model_capability(fb_provider, fb_model)
                if cap is not None and not cap.supports_reasoning:
                    fb_reasoning = None
                    logger.warning(
                        "Fallback model does not support reasoning — disabling for this handler",
                        extra={"provider": fb_provider, "model": fb_model},
                    )
                elif cap is not None and cap.supports_reasoning:
                    from core.providers.reasoning_support import get_reasoning_support, snap_to_nearest_level  # noqa: PLC0415
                    support = get_reasoning_support(fb_provider, fb_model)
                    snapped = snap_to_nearest_level(fb_reasoning, support.levels)
                    if snapped != fb_reasoning:
                        logger.info(
                            "Fallback model reasoning level snapped to nearest supported value",
                            extra={"provider": fb_provider, "model": fb_model,
                                   "requested": fb_reasoning, "snapped_to": snapped},
                        )
                    fb_reasoning = snapped
                else:
                    # Unknown model — leave reasoning as-is and let the
                    # provider API return an error if unsupported.
                    logger.debug(
                        "No capability record for fallback model — passing reasoning level through",
                        extra={"provider": fb_provider, "model": fb_model},
                    )

            try:
                handler = create_provider_handler(
                    provider=fb_provider,
                    api_key=fb_key,
                    model=fb_model,
                    tools=self.tools,
                    dispatcher=self.dispatcher,
                    enable_reasoning=fb_reasoning,
                )
                result.append((handler, fb_provider, fb_model))
            except Exception as exc:
                logger.warning(
                    "Failed to build fallback handler — entry skipped",
                    exc_info=exc,
                    extra={"provider": fb_provider, "model": fb_model},
                )

        return result

    # ------------------------------------------------------------------
    # Provider invocation with transparent fallback
    # ------------------------------------------------------------------

    async def _gen_with_fallback(
        self,
        messages: List[Dict],
        stream: bool,
        execution_id: str,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Wraps handler.create_message() with a transparent fallback loop.

        Strategy
        --------
        1. Try the primary handler (self.handler).
        2. Peek at the *first* chunk.
           - If it is an ErrorChunk whose error_type policy has
             fallback_eligible=True (see core/error_handling.ERROR_POLICIES),
             *and* an eligible candidate remains, emit ProviderFallbackTriggered
             and retry with the next eligible handler in the chain.
           - If the first chunk is healthy, commit to this handler (updating
             self.handler / self.provider / self.model if it's a fallback)
             and forward all chunks.
        3. Exceptions raised *before* the first chunk are also caught and
           classified the same way.
        4. If all candidates fail with fallback-eligible errors, yield the
           final ErrorChunk so the caller can handle it normally.

        AUTH failures are fallback-eligible but scoped (skip_same_provider —
        see ERROR_POLICIES): every remaining candidate sharing the failed
        provider is skipped without being attempted, since they likely share
        the same credential/account; a candidate on a different provider is
        still tried. A fully-missing credential never reaches this loop at
        all — POST /set-session rejects/drops that at config-save time (see
        api/session.py) — so this only ever handles a key that was valid
        when configured but got rejected since (revoked, expired, etc).

        This approach buffers exactly one chunk per attempt — preserving
        streaming semantics while detecting provider-level failures early.
        """
        if self._fallback_handlers is None:
            self._fallback_handlers = self._build_fallback_handlers()

        # Full ordered candidate list: primary first, then fallbacks.
        candidates: List[Tuple[Any, str, str]] = (
            [(self.handler, self.provider, self.model)]
            + self._fallback_handlers
        )

        # Providers to skip entirely for the rest of this loop — populated
        # when a failure's policy says skip_same_provider (AUTH today).
        skip_providers: set = set()

        # V6.5 Phase 4: cross-execution cooldown scope. self.execution is
        # None for bare/test AgentEngine instances -- cooldown is silently
        # a no-op in that case rather than erroring (matches how the rest
        # of this method already degrades gracefully when optional context
        # is missing).
        user_id = getattr(self.execution, "user_id", "") if self.execution else ""

        for attempt_idx, (handler, fb_provider, fb_model) in enumerate(candidates):
            if fb_provider in skip_providers:
                continue

            if user_id and await _is_in_cooldown(user_id, fb_provider, fb_model):
                logger.info(
                    "Skipping candidate — in cooldown from a recent failure",
                    extra={"provider": fb_provider, "model": fb_model, "user_id": user_id},
                )
                continue

            # Retries (Tier A — rate_limit/timeout/provider_down) stay on
            # THIS SAME candidate before ever considering the next one in
            # the chain — a transient blip often clears by itself, and
            # burning through the whole fallback_chain for a one-off 429 is
            # wasteful. give_up_on_candidate distinguishes "this candidate's
            # own result is final" (success, or an unrecoverable error with
            # nothing left to retry/fall back into) from "abandon this
            # candidate, the outer for-loop should advance" — needed because
            # a bare `break` here would otherwise fall into the commit-and-
            # stream block below with a stale/error first_chunk.
            retries_used = 0
            give_up_on_candidate = False

            while True:
                # --- Attempt to create the generator (connection errors surface here) ---
                try:
                    _raw_gen = handler.create_message(
                        system_prompt="",
                        messages=messages,
                        stream=stream,
                        reasoning=self.reasoning,
                    )
                    # Wrap with per-chunk idle timeout so a stalled TCP connection
                    # is detected promptly.  _timed_stream is transparent — all
                    # chunk types pass through; a TimeoutError becomes an ErrorChunk.
                    gen = _timed_stream(
                        _raw_gen,
                        execution_id=execution_id,
                        provider=fb_provider,
                        model=fb_model,
                    )
                except Exception as exc:
                    err_type = classify_error(exc)
                    policy = get_error_policy_by_value(err_type.value)
                    if policy.retryable and retries_used < policy.max_retries:
                        retries_used += 1
                        logger.warning(
                            "Provider raised on connect — retrying same provider",
                            extra={
                                "provider": fb_provider, "model": fb_model,
                                "error_type": err_type.value,
                                "retry": retries_used, "max_retries": policy.max_retries,
                            },
                        )
                        await _backoff_sleep(retries_used - 1)
                        continue  # retry same candidate
                    if policy.retryable and user_id:
                        # Exhausted retries on a Tier A error -- cool this
                        # candidate down for future executions too.
                        await _record_cooldown_failure(user_id, fb_provider, fb_model)
                    next_candidate = None
                    if policy.fallback_eligible:
                        if policy.skip_same_provider:
                            skip_providers.add(fb_provider)
                        next_candidate = _next_eligible_candidate(candidates, attempt_idx, skip_providers)
                    if next_candidate is not None:
                        _, next_provider, next_model = next_candidate
                        logger.warning(
                            "Provider raised on connect — trying fallback",
                            extra={
                                "failed_provider": fb_provider,
                                "failed_model":    fb_model,
                                "error_type":      err_type.value,
                                "fallback_to":     f"{next_provider}/{next_model}",
                            },
                        )
                        event_bus.publish(ProviderFallbackTriggered(
                            execution_id=execution_id,
                            session_id=self.session_id or "",
                            failed_provider=fb_provider,
                            failed_model=fb_model,
                            failure_reason=err_type.value,
                            fallback_provider=next_provider,
                            fallback_model=next_model,
                            fallback_index=next_candidate[0],
                        ))
                        give_up_on_candidate = True
                        break
                    raise  # not a fallback-eligible error, or no eligible candidate remains

                # --- Peek at first chunk ---
                first_chunk: Optional[StreamChunk] = None
                try:
                    first_chunk = await gen.__anext__()
                except StopAsyncIteration:
                    # Empty response — treat as non-error, nothing to yield.
                    return
                except Exception as exc:
                    err_type = classify_error(exc)
                    policy = get_error_policy_by_value(err_type.value)
                    if policy.retryable and retries_used < policy.max_retries:
                        retries_used += 1
                        logger.warning(
                            "Provider raised on first chunk — retrying same provider",
                            extra={
                                "provider": fb_provider, "model": fb_model,
                                "error_type": err_type.value,
                                "retry": retries_used, "max_retries": policy.max_retries,
                            },
                        )
                        await gen.aclose()
                        await _backoff_sleep(retries_used - 1)
                        continue  # retry same candidate
                    if policy.retryable and user_id:
                        # Exhausted retries on a Tier A error -- cool this
                        # candidate down for future executions too.
                        await _record_cooldown_failure(user_id, fb_provider, fb_model)
                    next_candidate = None
                    if policy.fallback_eligible:
                        if policy.skip_same_provider:
                            skip_providers.add(fb_provider)
                        next_candidate = _next_eligible_candidate(candidates, attempt_idx, skip_providers)
                    if next_candidate is not None:
                        _, next_provider, next_model = next_candidate
                        logger.warning(
                            "Provider raised on first chunk — trying fallback",
                            extra={
                                "failed_provider": fb_provider,
                                "failed_model":    fb_model,
                                "error_type":      err_type.value,
                                "fallback_to":     f"{next_provider}/{next_model}",
                            },
                        )
                        event_bus.publish(ProviderFallbackTriggered(
                            execution_id=execution_id,
                            session_id=self.session_id or "",
                            failed_provider=fb_provider,
                            failed_model=fb_model,
                            failure_reason=err_type.value,
                            fallback_provider=next_provider,
                            fallback_model=next_model,
                            fallback_index=next_candidate[0],
                        ))
                        await gen.aclose()
                        give_up_on_candidate = True
                        break
                    raise

                # --- Check if first chunk is a fallback-eligible ErrorChunk ---
                if isinstance(first_chunk, ErrorChunk):
                    policy = get_error_policy_by_value(first_chunk.error_type)
                    if policy.retryable and retries_used < policy.max_retries:
                        retries_used += 1
                        logger.warning(
                            "Provider returned retryable error — retrying same provider",
                            extra={
                                "provider": fb_provider, "model": fb_model,
                                "error_type": first_chunk.error_type,
                                "retry": retries_used, "max_retries": policy.max_retries,
                            },
                        )
                        await gen.aclose()
                        await _backoff_sleep(retries_used - 1)
                        continue  # retry same candidate
                    if policy.retryable and user_id:
                        # Exhausted retries on a Tier A error -- cool this
                        # candidate down for future executions too.
                        await _record_cooldown_failure(user_id, fb_provider, fb_model)
                    next_candidate = None
                    if policy.fallback_eligible:
                        if policy.skip_same_provider:
                            skip_providers.add(fb_provider)
                        next_candidate = _next_eligible_candidate(candidates, attempt_idx, skip_providers)
                    if next_candidate is not None:
                        _, next_provider, next_model = next_candidate
                        logger.warning(
                            "Provider returned fallback-eligible error — switching provider",
                            extra={
                                "failed_provider": fb_provider,
                                "failed_model":    fb_model,
                                "error_type":      first_chunk.error_type,
                                "fallback_to":     f"{next_provider}/{next_model}",
                            },
                        )
                        event_bus.publish(ProviderFallbackTriggered(
                            execution_id=execution_id,
                            session_id=self.session_id or "",
                            failed_provider=fb_provider,
                            failed_model=fb_model,
                            failure_reason=first_chunk.error_type,
                            fallback_provider=next_provider,
                            fallback_model=next_model,
                            fallback_index=next_candidate[0],
                        ))
                        await gen.aclose()
                        give_up_on_candidate = True
                        break  # try next candidate

                # Success, or an unrecoverable ErrorChunk with nothing left
                # to retry/fall back into (yielded as the final result below).
                break

            if give_up_on_candidate:
                continue  # outer for-loop advances to the next candidate

            # --- This candidate succeeded — commit it and stream all chunks ---
            #
            # NOTE: this block is also reached when first_chunk is an
            # unrecoverable ErrorChunk (Tier C, or Tier A/B exhausted with
            # nothing left to fall back into) -- that's the pre-existing
            # "yield the failure as the final result" path. Cooldown must
            # only be cleared on a genuine success; a Tier A failure's
            # cooldown was already recorded above, and clearing it here
            # would immediately undo that.
            if user_id and not isinstance(first_chunk, ErrorChunk):
                await _clear_cooldown(user_id, fb_provider, fb_model)

            if attempt_idx > 0:
                # A fallback is now the active provider; update instance state
                # so that subsequent ReAct loop iterations use it too.
                self.handler  = handler
                self.provider = fb_provider
                self.model    = fb_model
                logger.info(
                    "Fallback provider committed for remainder of execution",
                    extra={"provider": fb_provider, "model": fb_model},
                )
                if not isinstance(first_chunk, ErrorChunk):
                    # V6.5 Phase 5: live SSE notice -- deliberately emitted
                    # HERE, once, only on genuine success, rather than at
                    # each individual failure/retry decision above. Two
                    # reasons: (1) accuracy -- attempt_idx > 0 is true for
                    # whichever candidate actually ends up serving the
                    # response, so this can never announce a candidate
                    # that itself later failed (a real risk with the
                    # original design, which fired at "about to try X"
                    # rather than "X confirmed working"); (2) it uniformly
                    # covers BOTH an active failure that switched
                    # candidates AND a candidate silently bypassed via the
                    # cross-execution cooldown check (Phase 4) at the top
                    # of this loop -- both cases leave attempt_idx > 0 with
                    # no other distinction needed.
                    yield ProviderFallbackChunk(provider=fb_provider, model=fb_model)

            yield first_chunk
            async for chunk in gen:
                yield chunk
            return  # done — do not continue the candidates loop

        # All candidates exhausted and all yielded fallback-eligible errors.
        # The last error chunk was consumed but never forwarded; yield a
        # synthetic one so the call() loop can surface it to the caller.
        # (In practice this path is only reached if n > 1 and all fail.)
        yield ErrorChunk(
            message="All providers in the fallback chain failed.",
            provider=self.provider,
            error_type="provider_down",
        )

    # ------------------------------------------------------------------
    # Cancellation helper
    # ------------------------------------------------------------------
    def _check_cancelled(self, *, emit_error: bool = False):
        """
        Cooperative cancellation check.

        Returns:
            ErrorChunk | None

        Contract:
        - Caller must immediately `return` if this returns non-None
        - Does NOT raise
        - Safe to call frequently
        """
        if self.cancel_token and self.cancel_token.cancelled:
            logger.info(
                "AgentEngine cancelled",
                extra={
                    "provider": self.provider,
                    "model": self.model,
                    "session_id": self.session_id,
                },
            )

            if emit_error:
                return ErrorChunk(
                    message="Execution cancelled by user",
                    provider=self.provider,
                    error_type="cancelled",
                )

            return ErrorChunk(
                message="",  # empty sentinel; caller should just return
                provider=self.provider,
                error_type="cancelled",
            )

        return None

    # ------------------------------------------------------------------
    # V6.15: cancellable tool execution
    # ------------------------------------------------------------------
    # S-09 root cause: _check_cancelled() above is only ever polled *between*
    # tool calls (before the next one starts) or *between* LLM tokens -- the
    # `await execute_tool_safely(...)` call itself was a bare, unraced await,
    # so nothing could act on a cancel signal while a tool call was actually
    # in flight, for any tool. _execute_tool_cancellable() below fixes that
    # for every tool implemented as genuine async I/O (web_search, http_get,
    # memory_recall, MCP calls, etc.) by racing the call against the cancel
    # token and calling task.cancel() if it fires -- CancelledError then
    # raises inside the tool's coroutine at its next await point, and
    # well-behaved async I/O libraries actually abort the underlying
    # operation when that happens (confirmed neither execute_tool_safely()
    # nor ToolDispatcher.execute_tool() catches bare Exception in a way that
    # would swallow CancelledError -- it subclasses BaseException, not
    # Exception, since Python 3.8).
    #
    # This does NOT, on its own, fix the shell tool: asyncio.to_thread()-
    # wrapped subprocess.run() is immune to task.cancel() (cancelling the
    # wrapping task only stops us from waiting on it, not the thread's
    # actual work). Shell's own fix -- a real asyncio.subprocess.Process
    # handle it can .terminate()/.kill() -- lives in core/shell/executor.py
    # and is required in addition to this, not instead of it.
    _TOOL_CANCEL_POLL_SECONDS = 0.15

    async def _execute_tool_cancellable(self, **kwargs) -> Dict[str, Any]:
        """
        Run execute_tool_safely(**kwargs) as a task, racing it against
        self.cancel_token so a mid-flight cancel signal can actually reach
        it instead of only being checked before/after the call.
        """
        task = asyncio.ensure_future(execute_tool_safely(**kwargs))
        while True:
            done, _ = await asyncio.wait({task}, timeout=self._TOOL_CANCEL_POLL_SECONDS)
            if task in done:
                return task.result()
            if self.cancel_token and self.cancel_token.cancelled:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return {
                    "ok": False,
                    "error": "Tool call cancelled by user before it completed.",
                    "cancelled": True,
                }

    # ------------------------------------------------------------------
    # Context-budget watchdog (V6.11)
    # ------------------------------------------------------------------
    def _remaining_context_budget(self, messages: List[Dict]) -> Optional[int]:
        """
        Estimate how many tokens remain before `messages` would fill this
        model's context window.

        Falls back to CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS when the
        model's max_context_tokens isn't known via capability_registry --
        deliberately NOT the same "never guess when data doesn't exist"
        convention used elsewhere in this codebase (e.g.
        reasoning_support.py's optimistic level offering), because that
        convention is safe when guessing wrong just means offering an
        option the provider might reject. Here, guessing "no budget known"
        and silently disabling the watchdog is the actively unsafe
        direction -- confirmed empirically that ollama_cloud (the provider
        of the reference incident, execution 9e029fc2) has no cached
        capability data on disk at all, so this is the common case for
        that provider, not a rare edge case.

        Used to size Layer 1's per-tool-call output cap (dispatcher.py)
        before each step's tool calls execute.
        """
        try:
            cap = get_model_capability(self.provider, self.model)
        except Exception:
            cap = None
        max_tokens = (cap.max_context_tokens if cap else None) or CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS

        text = "\n".join(str(m.get("content", "") or "") for m in messages)
        used = TokenCounter.estimate(text, self.model)
        return max(0, max_tokens - used)

    _COMPRESS_KEEP_RECENT = 4

    def _compress_history_if_needed(self, messages: List[Dict]) -> None:
        """
        Context-budget watchdog Layer 2 (V6.11): proactive, deterministic,
        non-LLM compaction of accumulated ReAct-loop history, in place,
        once cumulative tokens cross CONTEXT_WATCHDOG_TRIGGER_RATIO of the
        model's max_context_tokens. Called after each step's tool
        calls/results are appended, before the next LLM call is made.

        Unlike Layer 1 (_remaining_context_budget / dispatcher.py's
        _cap_tool_output, which bounds a single tool result), this bounds
        the CUMULATIVE growth of many individually-reasonable results
        piling up over a long loop -- confirmed as the dominant driver of
        the reference incident (execution 9e029fc2: describe_tool x3,
        web_search x4, http_get x1, none individually huge, 60,702 prompt
        tokens by the end).

        Deliberately NOT an LLM call: an extra LLM round-trip to summarize
        would itself need input budget from the same constrained context.
        A deterministic mechanism was already found more reliable than an
        algorithmic/LLM-based one under this exact kind of budget pressure
        (a ratio-learning design was considered and rejected).

        Falls back to CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS when the
        model's budget is unknown rather than no-op'ing -- see
        _remaining_context_budget()'s docstring for why this diverges from
        this codebase's usual "don't guess" convention; ollama_cloud (the
        reference incident's provider) has no cached capability data on
        disk at all, so treating "unknown" as "skip the watchdog" would
        disable it for exactly the provider it was built to protect.
        No-ops only when usage is comfortably under the trigger ratio, or
        when there isn't enough history to usefully compress.
        """
        try:
            cap = get_model_capability(self.provider, self.model)
        except Exception:
            cap = None
        max_tokens = (cap.max_context_tokens if cap else None) or CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS

        text = "\n".join(str(m.get("content", "") or "") for m in messages)
        used = TokenCounter.estimate(text, self.model)
        if used < max_tokens * CONTEXT_WATCHDOG_TRIGGER_RATIO:
            return

        keep_recent = self._COMPRESS_KEEP_RECENT
        if len(messages) <= keep_recent + 2:
            return  # not enough history to usefully compress

        # Preserve the leading system message (if any) and the first user
        # message (the original task) verbatim -- everything else between
        # that head and the most recent exchange is collapsible.
        head: List[Dict] = []
        idx = 0
        if messages and messages[0].get("role") == "system":
            head.append(messages[0])
            idx = 1
        if idx < len(messages) and messages[idx].get("role") == "user":
            head.append(messages[idx])
            idx += 1

        recent = messages[-keep_recent:]
        middle = messages[idx: len(messages) - keep_recent]
        if not middle:
            return

        tool_names: List[str] = []
        for m in middle:
            for tc in (m.get("tool_calls") or []):
                fn = (tc.get("function") or {}).get("name")
                if fn:
                    tool_names.append(fn)

        note = {
            "role": "user",
            "content": (
                f"[EVA context-budget watchdog: {len(middle)} earlier messages "
                f"({len(tool_names)} tool call(s): {', '.join(tool_names) or 'n/a'}) "
                "condensed to stay within the context budget. Continue the task "
                "using the most recent results below — do not repeat work already "
                "done above unless it genuinely needs redoing.]"
            ),
        }

        collapsed_count = len(middle)
        messages[:] = head + [note] + recent

        logger.warning(
            "Context-budget watchdog: compressed ReAct history in place",
            extra={
                "provider": self.provider,
                "model": self.model,
                "used_tokens_before": used,
                "max_context_tokens": max_tokens,
                "max_context_tokens_source": "capability_registry" if (cap and cap.max_context_tokens) else "fallback_default",
                "trigger_ratio": CONTEXT_WATCHDOG_TRIGGER_RATIO,
                "collapsed_message_count": collapsed_count,
            },
        )

    # ----------------------------------------------------------------------
    # MAIN CALL METHOD (STREAMING + NON-STREAMING)
    # ----------------------------------------------------------------------
    async def call(
        self,
        messages: List[Dict[str, str]],
        stream: bool = True
    ) -> AsyncGenerator[StreamChunk, None]:

    # ------------------------------------------------------
    # Inject memory FIRST (if available)
    # ------------------------------------------------------
        if self.memory_provider and not messages:
            memory_messages = await self.memory_provider.build_context()
            messages = memory_messages

        # prepend system prompt
        if self.system_prompt:
            messages = [{"role": "system", "content": self.system_prompt}] + messages

        react = ReActContext()
        step_index = 0

        # Execution ID for event publishing (may be None in unit tests)
        execution_id: str = getattr(self.execution, "execution_id", "") or ""

        # Empty-response guard state (Fix: Ollama + open-weight models sometimes
        # return an empty string after receiving tool results instead of synthesising
        # a response.  We detect this and inject one recovery nudge before giving up.)
        _prev_step_had_tool_calls: bool = False
        _recovery_attempted: bool = False

        # Duplicate tool-call guard (V6.20, threshold raised V6.25, tiered
        # consecutive-streak redesign V6.26): tracks how many times IN A ROW
        # each (tool_name, args_fingerprint) pair has just been seen. Any
        # call with a DIFFERENT fingerprint resets every other fingerprint's
        # count back to zero -- so a genuinely stuck model calling the same
        # thing back-to-back still trips the guard, but a legitimate
        # interleaved pattern (e.g. run a test, edit a file, run the same
        # test again) never accumulates, since the edit in between resets
        # the test's count. The trigger threshold itself is tiered by the
        # tool's ToolAuthority (core/tools/base.py) -- a repeated read-only
        # check is cheap and tolerated more than a repeated mutating or
        # destructive call, where a stuck loop does real damage. See
        # _duplicate_call_threshold_for() below and
        # config.settings.DUPLICATE_TOOL_CALL_THRESHOLD_*.
        # Uses a frozenset of sorted items so argument ordering doesn't matter.
        _seen_tool_fingerprints: Dict[tuple, int] = {}

        def _duplicate_call_threshold_for(_tool_name: str) -> int:
            _tool_cls = None
            try:
                _tool_cls = (self.handler.tool_classes or {}).get(_tool_name)
            except AttributeError:
                pass  # tool_classes isn't dict-like -- treat as unresolvable
            if _tool_cls is None and self.dynamic_registry:
                _tool_cls = self.dynamic_registry.get(_tool_name)
            _authority = getattr(_tool_cls, "authority", None)
            if _authority == ToolAuthority.READ_ONLY:
                return DUPLICATE_TOOL_CALL_THRESHOLD_READ_ONLY
            if _authority == ToolAuthority.DESTRUCTIVE:
                return DUPLICATE_TOOL_CALL_THRESHOLD_DESTRUCTIVE
            # Unresolvable tool class (e.g. a dynamic/MCP manifest with no
            # authority tag) defaults to the middle tier -- unknown risk is
            # treated as MUTATING, not assumed safe.
            return DUPLICATE_TOOL_CALL_THRESHOLD_MUTATING

        try:
            while True:
                # -------- ReAct loop boundary cancellation --------
                cancel = self._check_cancelled()
                if cancel:
                    self.observability.emit(
                        "agent.terminated_early",
                        {
                            "session_id": self.session_id,
                            "ts": time.time(),
                            "step_index": step_index,
                            "reason": "cancelled",
                        },
                    )
                    return
        # Emit: agent.step_started
                self.observability.emit(
                    "agent.step_started",
                    {
                        "session_id": self.session_id,
                        "ts": time.time(),
                        "step_index": step_index,
                    },
                )

                # ── B-076 ModelCall hook (EVA extension) ───────────────────
                # Fire before each LLM API call — non-blocking, never delays
                # the response.  Hooks observe the call context only; they
                # cannot block or modify the LLM request.
                _execution_for_hooks = getattr(self, "execution", None)
                _user_id_for_hooks = getattr(_execution_for_hooks, "user_id", "") or ""
                if _user_id_for_hooks:
                    try:
                        _mc_session = get_user_session(_user_id_for_hooks)
                        _mc_hooks = _mc_session.get("tool_hooks") or []
                        if _mc_hooks:
                            _mc_conv_id = getattr(_execution_for_hooks, "conversation_id", "")
                            asyncio.create_task(
                                HookRunner.fire_model_call(
                                    hook_definitions=_mc_hooks,
                                    user_id=_user_id_for_hooks,
                                    session_id=_mc_conv_id or _user_id_for_hooks,
                                    workspace_root=get_user_workspace(_user_id_for_hooks),
                                    model=self.model,
                                    provider=self.provider,
                                    turn_number=step_index,
                                    execution_id=execution_id,
                                ),
                            )
                    except Exception:
                        pass  # ModelCall hook failure must never affect execution
                # ───────────────────────────────────────────────────────────

                # ── V5.0 Sprint 2: Progressive Tool Disclosure ─────────────
                # Before every API call rebuild the handler's provider_tools
                # to reflect the current promoted set.  This is a no-op in
                # non-progressive mode (get_active_tool_classes returns the
                # full set, which is already what the handler was built with).
                if getattr(self, "_progressive", False) and self.tools is not None:
                    try:
                        from core.tools.builder import build_provider_tools  # noqa
                        _active = self.tools.get_active_tool_classes()
                        _ptools = build_provider_tools(self.provider, _active)
                        self.handler.provider_tools = _ptools
                        self.handler.tool_classes = _active
                        # Propagate to any already-built fallback handlers.
                        if self._fallback_handlers:
                            for _fb_handler, _fb_provider, _ in self._fallback_handlers:
                                try:
                                    _fb_handler.provider_tools = build_provider_tools(
                                        _fb_provider, _active
                                    )
                                    _fb_handler.tool_classes = _active
                                except Exception:
                                    pass
                    except Exception as _ptd_exc:
                        logger.warning(
                            "Progressive disclosure: failed to update handler tools (non-fatal)",
                            extra={
                                "execution_id": execution_id,
                                "error": str(_ptd_exc),
                            },
                        )
                # ───────────────────────────────────────────────────────────

                # ---- Provider call with transparent fallback ----
                gen: AsyncGenerator[StreamChunk, None] = self._gen_with_fallback(
                    messages=messages,
                    stream=stream,
                    execution_id=execution_id,
                )

                tool_calls: List[ToolCallChunk] = []
                _accumulated_text_len: int = 0  # tracks whether any text was streamed

                async for chunk in gen:
                    # -------- Token-stream cancellation --------
                    cancel = self._check_cancelled()
                    if cancel:
                        return

                    # ---------------- THINK ----------------
                    if isinstance(chunk, ReasoningChunk):
                        react.record(ReActStep(phase=ReActPhase.THINK))
                        # self.reasoning is a level string or None (V6.7) --
                        # "off" is also treated as disabled defensively,
                        # even though upstream (SessionRequest's field
                        # validator, api/session.py) already normalises it
                        # to None before it gets here.
                        if self.reasoning not in (None, "off"):
                            yield chunk
                        continue

                    # ---------------- ACT (collect ALL tool calls) ----
                    if isinstance(chunk, ToolCallChunk):
                        react.record(
                            ReActStep(
                                phase=ReActPhase.ACT,
                                tool_name=chunk.tool_name,
                                tool_call_id=chunk.call_id,
                                tool_arguments=chunk.arguments,
                            )
                        )

                        _step_limit = self._max_steps or MAX_REACT_STEPS
                        if react.tool_steps >= _step_limit:
                            react.record(
                                ReActStep(
                                    phase=ReActPhase.ERROR,
                                    error="Maximum tool steps exceeded",
                                )
                            )
                            self.observability.emit(
                                "agent.terminated_early",
                                {
                                    "session_id": self.session_id,
                                    "ts": time.time(),
                                    "step_index": step_index,
                                    "reason": "max_steps",
                                },
                            )

                            # Context-budget watchdog (V6.11): instead of
                            # erroring immediately, attempt one forced
                            # best-effort answer from whatever's been
                            # gathered so far. Compress history first so
                            # this attempt isn't doomed by the same
                            # overflow, then explicitly instruct no more
                            # tool calls. Any tool call the model tries
                            # anyway during this attempt is simply never
                            # executed (no budget left for it) -- only
                            # text output is used. Falls back to the
                            # original error below if this attempt
                            # produces no usable text.
                            self._compress_history_if_needed(messages)
                            messages.append({
                                "role": "user",
                                "content": (
                                    "You have reached the maximum number of tool "
                                    "calls for this task. Do not call any more "
                                    "tools. Summarise what you have found so far "
                                    "and give the best answer you can with the "
                                    "information already gathered."
                                ),
                            })
                            _final_text_parts: List[str] = []
                            try:
                                _final_gen = self._gen_with_fallback(
                                    messages=messages,
                                    stream=stream,
                                    execution_id=execution_id,
                                )
                                async for _fchunk in _final_gen:
                                    if isinstance(_fchunk, TextChunk):
                                        _final_text_parts.append(_fchunk.content or "")
                                        yield _fchunk
                                    elif isinstance(_fchunk, UsageChunk):
                                        yield _fchunk
                                    elif isinstance(_fchunk, ErrorChunk):
                                        break  # forced attempt failed too
                                    # ToolCallChunk / ReasoningChunk / anything
                                    # else: consumed but never acted on -- no
                                    # more tool budget for this execution.
                            except Exception:
                                pass  # fall through to the original error below

                            _forced_answer_text = "".join(_final_text_parts).strip()

                            # V6.12: ask-to-continue gate. Fires regardless of
                            # whether the forced-best-effort attempt above
                            # produced text -- either way the user gets a
                            # chance to extend rather than the execution
                            # ending unconditionally at this point. Only
                            # offered while extensions remain; once exhausted,
                            # falls through to the same terminal behavior as
                            # before this feature existed (return on text,
                            # ErrorChunk on none).
                            _user_id_for_ext = getattr(self.execution, "user_id", "") or ""
                            _max_extensions = 0
                            if _user_id_for_ext:
                                try:
                                    _max_extensions = int(
                                        get_user_session(_user_id_for_ext).get("max_step_extensions", 0)
                                    )
                                except Exception:
                                    _max_extensions = 0

                            if self.execution is not None and self._step_extensions_used < _max_extensions:
                                _wants_continue = False

                                # Daemon runs are unattended -- there is
                                # deterministically nobody to answer
                                # gate_step_extension()'s prompt, so it would
                                # otherwise block on blpop for up to
                                # STEP_EXTENSION_TIMEOUT_SECONDS (3600s
                                # default) before auto-resolving to "stop"
                                # anyway. Skip straight to that same outcome
                                # instead of waiting out the timeout. A
                                # daemon's own max_steps (DaemonConfig,
                                # e.g. PROJECT_SYNC_DAEMON_MAX_STEPS) is the
                                # explicit, intentional step budget for
                                # unattended runs -- if that's not enough,
                                # the fix is raising it directly, not letting
                                # the owning user's interactive-session
                                # max_step_extensions preference (a distinct
                                # setting, never designed with daemons in
                                # mind) implicitly extend it further.
                                if getattr(self.execution, "execution_mode", "conversational") == "daemon":
                                    logger.info(
                                        "Step-extension gate skipped for unattended daemon run — "
                                        "stopping cleanly at step limit",
                                        extra={"execution_id": execution_id},
                                    )
                                else:
                                    from core.execution.step_extension import gate_step_extension  # noqa: PLC0415

                                    try:
                                        _wants_continue = await gate_step_extension(
                                            self.execution,
                                            steps_used=react.tool_steps,
                                            max_steps=_step_limit,
                                            extensions_used=self._step_extensions_used,
                                            extensions_max=_max_extensions,
                                        )
                                    except Exception as _ext_exc:
                                        logger.warning(
                                            "Step-extension gate failed — falling back to terminal behavior",
                                            exc_info=_ext_exc,
                                            extra={"execution_id": execution_id},
                                        )

                                if _wants_continue:
                                    from core.execution.models import ExecutionStatus as _ES  # noqa: PLC0415
                                    from core.execution.state_machine import ExecutionStateMachine as _ESM  # noqa: PLC0415

                                    _ESM.assert_transition_allowed(
                                        execution_id, self.execution.status, _ES.RUNNING
                                    )
                                    self.execution.status = _ES.RUNNING
                                    self._step_extensions_used += 1
                                    react.tool_steps = 0
                                    logger.info(
                                        "Step-extension granted — resuming ReAct loop",
                                        extra={
                                            "execution_id": execution_id,
                                            "extensions_used": self._step_extensions_used,
                                            "extensions_max": _max_extensions,
                                        },
                                    )
                                    continue  # resume the outer while True loop

                            if _forced_answer_text:
                                self.observability.emit(
                                    "agent.step_completed",
                                    {
                                        "session_id": self.session_id,
                                        "ts": time.time(),
                                        "step_index": step_index,
                                    },
                                )
                                return

                            yield ErrorChunk(
                                message="Maximum tool call steps exceeded.",
                                provider=self.provider,
                                error_type="tool-step_limit",
                                details={"max_steps": _step_limit},
                            )
                            return

                        react.tool_steps += 1

                        # ---- Duplicate tool-call detection ----
                        # Compute a stable fingerprint from tool name + arguments.
                        # frozenset of sorted items ignores argument key ordering.
                        # Each value's type name travels alongside its string
                        # form so e.g. int 1 and str "1" don't collide just
                        # because they stringify the same.
                        try:
                            _args = chunk.arguments or {}
                            _fp = (
                                chunk.tool_name,
                                frozenset(
                                    (k, type(v).__name__, str(v))
                                    for k, v in (
                                        _args.items()
                                        if isinstance(_args, dict)
                                        else {}
                                    )
                                ),
                            )
                        except Exception:
                            # Fail OPEN: a fingerprint unique to this one call
                            # (its own call_id can never repeat) rather than
                            # the old (tool_name, frozenset()) fallback, which
                            # merged every call to that tool -- regardless of
                            # actual arguments -- into one shared bucket and
                            # could falsely block calls that were never
                            # actually duplicates of each other.
                            _fp = (chunk.tool_name, frozenset({("__unparseable__", "id", chunk.call_id)}))

                        _threshold = _duplicate_call_threshold_for(chunk.tool_name)

                        if _seen_tool_fingerprints.get(_fp, 0) >= _threshold - 1:
                            react.record(
                                ReActStep(
                                    phase=ReActPhase.ERROR,
                                    error=f"Duplicate tool call: {chunk.tool_name}",
                                )
                            )
                            self.observability.emit(
                                "agent.terminated_early",
                                {
                                    "session_id": self.session_id,
                                    "ts": time.time(),
                                    "step_index": step_index,
                                    "reason": "duplicate_tool_call",
                                    "tool_name": chunk.tool_name,
                                },
                            )
                            logger.warning(
                                "Duplicate tool call detected — breaking ReAct loop",
                                extra={
                                    "execution_id": execution_id,
                                    "tool_name": chunk.tool_name,
                                    "step_index": step_index,
                                },
                            )
                            yield ErrorChunk(
                                message=(
                                    f"Duplicate tool call detected: '{chunk.tool_name}' "
                                    f"was called with identical arguments "
                                    f"{_threshold} times in a row. Stopping to "
                                    "prevent an infinite loop."
                                ),
                                provider=self.provider,
                                error_type="duplicate_tool_call",
                                details={"tool_name": chunk.tool_name},
                            )
                            return

                        # Consecutive-streak update: this fingerprint's count
                        # goes up by one; every OTHER fingerprint's count is
                        # dropped, since a different call just proved the
                        # model isn't stuck repeating this one.
                        _new_count = _seen_tool_fingerprints.get(_fp, 0) + 1
                        _seen_tool_fingerprints.clear()
                        _seen_tool_fingerprints[_fp] = _new_count

                        tool_calls.append(chunk)
                        yield chunk
                        continue  # keep consuming — model may emit multiple tool calls

                    # ---------------- FINAL TEXT ----------------
                    if isinstance(chunk, TextChunk):
                        _accumulated_text_len += len(chunk.content or "")
                        react.record(ReActStep(phase=ReActPhase.FINAL))
                        yield chunk

                    elif isinstance(chunk, UsageChunk):
                        yield chunk

                    elif isinstance(chunk, ProviderFallbackChunk):
                        # V6.5 Phase 5: mid-stream, non-terminal notice --
                        # forward and keep consuming (unlike ErrorChunk,
                        # this never ends the ReAct loop).
                        yield chunk

                    elif isinstance(chunk, ErrorChunk):
                        react.record(
                            ReActStep(
                                phase=ReActPhase.ERROR,
                                error=chunk.message,
                            )
                        )
                        yield chunk
                        return

                # --------------------------------------------------
                # No tool call → check for empty response, then DONE
                # --------------------------------------------------
                if not tool_calls:
                    # Empty-response guard: some open-weight / Ollama models return
                    # an empty string after receiving tool results instead of
                    # synthesising a response.  Inject one recovery nudge and retry.
                    if (
                        _prev_step_had_tool_calls
                        and _accumulated_text_len == 0
                        and not _recovery_attempted
                    ):
                        _recovery_attempted = True
                        messages.append({
                            "role": "user",
                            "content": (
                                "The tool has returned its results. "
                                "Please summarise the findings and answer the user's question."
                            ),
                        })
                        logger.warning(
                            "Empty response after tool execution — injecting recovery nudge",
                            extra={"execution_id": execution_id},
                        )
                        continue  # retry with nudge in history

                    self.observability.emit(
                        "agent.step_completed",
                        {
                            "session_id": self.session_id,
                            "ts": time.time(),
                            "step_index": step_index,
                        },
                    )
                    return

                # --------------------------------------------------
                # OBSERVE: execute ALL tool calls from this response
                # --------------------------------------------------
                _prev_step_had_tool_calls = True
                _recovery_attempted = False  # reset — fresh tool cycle
                try:
                    cancel = self._check_cancelled()
                    if cancel:
                        return

                    # Context-budget watchdog Layer 1 (V6.11): computed once
                    # per step, from the message state before this step's
                    # tool calls execute -- caps any single tool result to
                    # whatever headroom actually remains, so one oversized
                    # result (a large web_search page, a big shell dump)
                    # can't blow the whole context window in one shot. See
                    # core/tools/dispatcher.py's _cap_tool_output().
                    _remaining_budget = self._remaining_context_budget(messages)

                    tool_results: List[tuple] = []
                    for tc in tool_calls:
                        payload = await self._execute_tool_cancellable(
                            dispatcher=self.dispatcher,
                            call_id=tc.call_id,
                            tool_name=tc.tool_name,
                            arguments=tc.arguments,
                            tool_classes=self.handler.tool_classes,
                            execution=self.execution,
                            dynamic_registry=self.dynamic_registry,
                            max_result_tokens=_remaining_budget,
                            model_name=self.model,
                        )
                        tool_results.append((tc, payload))

                        yield ToolResultChunk(
                            call_id=tc.call_id,
                            tool_name=tc.tool_name,
                            result=payload if isinstance(payload, dict) else {"output": str(payload)},
                        )

                        cancel = self._check_cancelled()
                        if cancel:
                            return

                        react.record(
                            ReActStep(
                                phase=ReActPhase.OBSERVE,
                                tool_name=tc.tool_name,
                                tool_call_id=tc.call_id,
                                tool_result=payload,
                            )
                        )

                    self.observability.emit(
                        "agent.step_completed",
                        {
                            "session_id": self.session_id,
                            "ts": time.time(),
                            "step_index": step_index,
                        },
                    )
                    step_index += 1

                    # Single assistant message with ALL tool calls
                    # (OpenAI/Groq require this before the tool results)
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc.call_id,
                                "type": "function",
                                "function": {
                                    "name": tc.tool_name,
                                    "arguments": json.dumps(tc.arguments)
                                    if isinstance(tc.arguments, dict)
                                    else (tc.arguments or "{}"),
                                },
                            }
                            for tc in tool_calls
                        ],
                    })

                    # Individual tool result messages. M-07: build_tool_message() now returns a
                    # list (1 element normally, 2 when the result carries a real image for a
                    # vision-capable model -- the tool message itself, then a follow-up
                    # role="user" message carrying the image) -- extend, not append.
                    for tc, payload in tool_results:
                        messages.extend(
                            build_tool_message(
                                tool_call_id=tc.call_id,
                                tool_name=tc.tool_name,
                                result=payload,
                            )
                        )

                    # Persist tool-call step to DB for history reconstruction
                    if self.on_step_complete:
                        await self.on_step_complete(tool_calls, tool_results)

                    # P-19 Phase 1: a successful schedule_wakeup call ends this
                    # execution cleanly right here -- unlike the other
                    # AWAITING_* gates above, nothing blocks waiting for a
                    # decision. The tool call and its result are already
                    # persisted by on_step_complete just above, so the new
                    # execution WakeupScheduler starts later has this step in
                    # its history. See core/execution/wakeup_scheduler.py.
                    _wakeup_scheduled = any(
                        tc.tool_name == "schedule_wakeup"
                        and isinstance(payload, dict)
                        and payload.get("ok")
                        for tc, payload in tool_results
                    )
                    if _wakeup_scheduled:
                        from core.execution.models import ExecutionStatus as _ES  # noqa: PLC0415
                        from core.execution.state_machine import ExecutionStateMachine as _ESM  # noqa: PLC0415
                        from core.execution.redis_registry import redis_execution_registry as _rer  # noqa: PLC0415

                        try:
                            _ESM.assert_transition_allowed(
                                execution_id, self.execution.status, _ES.AWAITING_WAKEUP
                            )
                            self.execution.status = _ES.AWAITING_WAKEUP
                            await _rer.mark_execution_awaiting_wakeup(execution_id)
                        except Exception as _wakeup_exc:
                            logger.warning(
                                "Failed to transition execution to AWAITING_WAKEUP "
                                "(non-fatal — the scheduled_wakeups row still exists "
                                "and will still resolve, this execution's own status "
                                "just won't reflect it accurately in the meantime)",
                                exc_info=_wakeup_exc,
                                extra={"execution_id": execution_id},
                            )
                        self.observability.emit(
                            "agent.step_completed",
                            {
                                "session_id": self.session_id,
                                "ts": time.time(),
                                "step_index": step_index,
                            },
                        )
                        return

                    # Context-budget watchdog Layer 2 (V6.11): proactive,
                    # in-place compaction once cumulative history crosses
                    # the trigger ratio -- runs after this step's results
                    # are appended, before the next LLM call is made.
                    self._compress_history_if_needed(messages)

                except Exception as exc:
                    err_type = classify_error(exc)
                    react.record(
                        ReActStep(
                            phase=ReActPhase.ERROR,
                            error=str(exc),
                        )
                    )
                    self.observability.emit(
                        "agent.terminated_early",
                        {
                            "session_id": self.session_id,
                            "ts": time.time(),
                            "step_index": step_index,
                            "reason": "error",
                            "error_type": type(exc).__name__,
                        },
                    )
                    yield ErrorChunk(
                        message=str(exc),
                        provider=self.provider,
                        error_type=err_type.value,
                    )
                    return

                # Continue loop → model sees all tool results
                continue

        except Exception as exc:
            err_type = classify_error(exc)
            logger.exception(
                "AgentEngine fatal error",
                extra={"provider": self.provider, "model": self.model},
            )

            self.observability.emit(
                "agent.terminated_early",
                {
                    "session_id": self.session_id,
                    "ts": time.time(),
                    "step_index": step_index,
                    "reason": "fatal",
                    "error_type": type(exc).__name__,
                },
            )
            yield ErrorChunk(
                message="AgentEngine failed.",
                provider=self.provider,
                error_type=err_type.value,
                details={"raw": repr(exc)},
            )
