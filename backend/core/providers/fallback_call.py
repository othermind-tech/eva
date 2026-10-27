# core/providers/fallback_call.py
#
# Shared fallback_chain support for EVA's non-streaming, one-shot LLM calls
# -- everything that isn't the main AgentEngine chat/daemon loop (which has
# its own streaming-shaped retry loop in core/agent_engine.py). Three tiers,
# all built on the same primitives in core/providers/fallback_state.py:
#
#   resolve_and_call_once()   -- "Resolve-Attempt-Record": resolve the one
#     best (cooldown-filtered) candidate, try it once, record a failure into
#     the shared cross-execution cooldown store if it fails, then raise.
#     Does NOT try a second candidate itself -- appropriate for call sites
#     where a human will notice the failure and naturally retry (a manual
#     button click, or the next scheduled background pass), and that retry
#     benefits automatically from whatever the failed attempt just recorded.
#     Used by: title generator, prompt enhancer, memory extractor,
#     conversation-ref attachment linking.
#
#   call_with_full_fallback() -- "Reactive Loop": walks the whole candidate
#     list, retrying the SAME candidate with backoff on Tier A errors
#     (rate_limit/timeout/provider_down) before advancing, honouring
#     AUTH's skip_same_provider, hard-stopping immediately on Tier C, and
#     recording/clearing cooldown exactly like AgentEngine._gen_with_fallback
#     -- just for a single non-streaming await instead of a stream. Used by
#     call sites where a silent failure would derail a larger operation with
#     no human watching an intermediate step: tools/default/coordinate_tool.py's
#     planning/synthesis calls, core/memory/summarizer.py (a failure here
#     drops the user's entire turn -- see build_context's hard dependency),
#     core/project/artifact_updater.py (fire-and-forget, so the extra retry
#     latency costs nothing).
#
#   call_llm_with_fallback()  -- V6.5 Phase 3's original helper (Verifier /
#     Planner / IntentRouter). Deliberately kept simpler than the two above
#     -- no retry-with-backoff, no error-tier gating -- these are one-shot
#     utility calls in the middle of the PEV loop (verification runs after
#     every step) where the latency cost of a sleep-and-retry isn't worth
#     it. V6.8 adds cooldown read/write (skip a known-bad candidate, record
#     a new failure, clear on success) since that's a pure latency WIN (skip
#     attempting something already known to be down) rather than a cost --
#     everything else about this function's behaviour is unchanged, and it
#     remains a no-op on cooldown when user_id isn't passed (existing
#     callers/tests that don't pass it see zero behaviour change).

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


async def _resolve_candidates_or_raise(
    session: Dict[str, Any],
    user_id: Optional[str],
    caller_tag: str,
) -> List[Dict[str, str]]:
    """
    Shared setup for all three tiers below: validate a provider/model is
    configured, build the ordered candidate list (primary + fallback_chain,
    API keys resolved), and drop anything currently in cross-execution
    cooldown. Raises RuntimeError (never returns an empty list) so callers
    don't each re-implement the same two guard clauses.
    """
    from core.providers.fallback_state import build_candidates, filter_cooldown

    provider = session.get("provider")
    model = session.get("model")
    if not provider or not model:
        raise RuntimeError(f"{caller_tag}: no provider/model configured for this session.")

    candidates = build_candidates(provider, model, session.get("api_key"), session.get("fallback_chain"))
    if user_id:
        candidates = await filter_cooldown(user_id, candidates)
    if not candidates:
        raise RuntimeError(f"{caller_tag}: all candidates are in cooldown from recent failures.")
    return candidates


async def _attempt_candidate(
    candidate: Dict[str, str],
    system_prompt: str,
    messages: List[Dict[str, str]],
    max_tokens: Optional[int],
    temperature: Optional[float],
    reasoning: Optional[str],
    tools: Any,
    dispatcher: Any,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Try exactly one candidate, exactly once — the single-attempt mechanics
    shared by all three tiers below (build the handler, make the
    non-streaming call, collect TextChunks, notice an ErrorChunk).

    Returns (text, error_message, error_type):
      - success: (non-empty stripped text, None, None)
      - handled failure (ErrorChunk, or an empty/whitespace-only response):
        (None, error message, error_type or None)
    A raised exception (connection error, etc.) is NOT caught here — it
    propagates so each tier's own retry/advance/hard-stop decision logic
    can classify it consistently with how it classifies an ErrorChunk.
    """
    from core.types import TextChunk, ErrorChunk
    from utils.handler_utils import create_provider_handler

    handler = create_provider_handler(
        provider=candidate["provider"], api_key=candidate["api_key"], model=candidate["model"],
        max_tokens=max_tokens, temperature=temperature,
        tools=tools, dispatcher=dispatcher, enable_reasoning=reasoning,
    )
    chunks: List[str] = []
    error_message: Optional[str] = None
    error_type_value: Optional[str] = None
    async for chunk in handler.create_message(
        system_prompt=system_prompt, messages=messages, stream=False, reasoning=reasoning,
    ):
        if isinstance(chunk, TextChunk):
            chunks.append(chunk.content)
        elif isinstance(chunk, ErrorChunk):
            error_message = chunk.message
            error_type_value = chunk.error_type

    raw_text = "".join(chunks).strip()
    if raw_text and error_message is None:
        return raw_text, None, None
    return None, (error_message or "empty response"), error_type_value


async def resolve_and_call_once(
    *,
    session: Dict[str, Any],
    system_prompt: str,
    messages: List[Dict[str, str]],
    user_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning: Optional[str] = None,
    tools: Any = None,
    dispatcher: Any = None,
    caller_tag: str,
) -> str:
    """
    Resolve-Attempt-Record tier — see module docstring.

    Tries exactly the first candidate not currently in cooldown (session's
    provider/model, or the first fallback_chain entry if the primary is
    cooling down). On failure, records a Tier A (retryable) failure into the
    shared cooldown store so the NEXT invocation of this function (a manual
    retry, or the next background trigger) automatically resolves to a
    different candidate -- but this call itself does not try a second one.

    Raises RuntimeError on failure (never returns ""). Callers that treat
    this as best-effort should wrap the call in their own try/except, same
    as they already do today.
    """
    from core.error_handling import classify_error, get_error_policy_by_value
    from core.providers.fallback_state import record_cooldown_failure, clear_cooldown

    candidates = await _resolve_candidates_or_raise(session, user_id, caller_tag)
    c = candidates[0]
    error_type_value: Optional[str] = None
    last_error = "empty response"
    try:
        text, error_message, error_type_value = await _attempt_candidate(
            c, system_prompt, messages, max_tokens, temperature, reasoning, tools, dispatcher,
        )
        if text is not None:
            if user_id:
                await clear_cooldown(user_id, c["provider"], c["model"])
            return text
        last_error = error_message
    except Exception as exc:
        last_error = str(exc)
        error_type_value = classify_error(exc).value

    if user_id and error_type_value:
        policy = get_error_policy_by_value(error_type_value)
        if policy.retryable:
            await record_cooldown_failure(user_id, c["provider"], c["model"])

    raise RuntimeError(f"{caller_tag}: {c['provider']}/{c['model']} failed — {last_error}")


async def call_with_full_fallback(
    *,
    session: Dict[str, Any],
    system_prompt: str,
    messages: List[Dict[str, str]],
    user_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning: Optional[str] = None,
    tools: Any = None,
    dispatcher: Any = None,
    caller_tag: str,
) -> str:
    """
    Reactive Loop tier — see module docstring. Full parity with
    AgentEngine._gen_with_fallback's decision logic (core/error_handling.py's
    ERROR_POLICIES: Tier A retries the same candidate with backoff before
    advancing and records cooldown once exhausted; Tier B/AUTH advance
    immediately, AUTH additionally skipping the rest of that provider; Tier C
    is a hard stop with no further candidates tried), adapted for a single
    non-streaming await instead of a stream.

    Raises RuntimeError (Tier A/B exhaustion) or re-raises the original
    exception (Tier C) if no candidate succeeds.
    """
    from core.error_handling import classify_error, get_error_policy_by_value
    from core.providers.fallback_state import record_cooldown_failure, clear_cooldown, backoff_sleep

    candidates = await _resolve_candidates_or_raise(session, user_id, caller_tag)
    skip_providers: set = set()
    last_error = "no candidates available"

    for idx, c in enumerate(candidates):
        if c["provider"] in skip_providers:
            continue

        retries_used = 0
        while True:
            error_type_value: Optional[str] = None
            try:
                text, error_message, error_type_value = await _attempt_candidate(
                    c, system_prompt, messages, max_tokens, temperature, reasoning, tools, dispatcher,
                )
                if text is not None:
                    if user_id:
                        await clear_cooldown(user_id, c["provider"], c["model"])
                    if idx > 0:
                        logger.info(
                            "%s: recovered via fallback_chain entry", caller_tag,
                            extra={"provider": c["provider"], "model": c["model"], "chain_index": idx},
                        )
                    return text
                last_error = error_message
            except Exception as exc:
                last_error = str(exc)
                error_type_value = classify_error(exc).value

            policy = get_error_policy_by_value(error_type_value) if error_type_value else None

            if policy is not None and policy.retryable and retries_used < policy.max_retries:
                retries_used += 1
                logger.warning(
                    "%s: candidate failed — retrying same candidate", caller_tag,
                    extra={
                        "provider": c["provider"], "model": c["model"],
                        "error_type": error_type_value, "retry": retries_used,
                    },
                )
                await backoff_sleep(retries_used - 1)
                continue  # retry same candidate

            if policy is not None and policy.retryable and user_id:
                await record_cooldown_failure(user_id, c["provider"], c["model"])

            if policy is not None and not policy.fallback_eligible:
                # Tier C — hard stop, nothing downstream fixes this.
                raise RuntimeError(
                    f"{caller_tag}: {c['provider']}/{c['model']} failed with a "
                    f"non-recoverable error — {last_error}"
                )

            if policy is not None and policy.fallback_eligible and policy.skip_same_provider:
                skip_providers.add(c["provider"])

            break  # give up on this candidate, outer for-loop advances

        if idx < len(candidates) - 1:
            logger.warning(
                "%s: candidate failed — trying next in fallback_chain", caller_tag,
                extra={"provider": c["provider"], "model": c["model"], "error": last_error},
            )

    raise RuntimeError(f"{caller_tag}: all candidates failed — {last_error}")


async def call_llm_with_fallback(
    *,
    session: Dict[str, Any],
    system_prompt: str,
    messages: List[Dict[str, str]],
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    user_id: Optional[str] = None,
    caller_tag: str,
) -> str:
    """
    Non-streaming, one-shot LLM call with fallback_chain support.

    Tries session["provider"]/["model"], then each entry in
    session["fallback_chain"] in order, until one returns a non-empty
    response with no ErrorChunk. Deliberately simpler than
    call_with_full_fallback: no retry-with-backoff, no error-tier gating --
    every failure advances to the next candidate unconditionally (see module
    docstring for why that's still the right call for this tier).

    V6.8: when user_id is passed, candidates currently in cross-execution
    cooldown are skipped, and a failure/success is recorded/cleared into the
    same shared store AgentEngine's main chat path uses -- so this tier and
    the main chat path now inform each other. user_id is optional and
    defaults to None (fully inert, zero behaviour change) so this remains
    backward compatible with every existing caller/test.

    Raises RuntimeError (never returns "") if no provider/model is
    configured, or every candidate fails -- callers already wrap this in
    a broad except Exception and fall back to their own safe default.
    """
    from core.error_handling import classify_error, get_error_policy_by_value
    from core.providers.fallback_state import record_cooldown_failure, clear_cooldown

    candidates = await _resolve_candidates_or_raise(session, user_id, caller_tag)
    last_error = "no candidates configured"
    for idx, c in enumerate(candidates):
        c_provider, c_model = c["provider"], c["model"]
        error_type_value: Optional[str] = None
        try:
            text, error_message, error_type_value = await _attempt_candidate(
                c, system_prompt, messages, max_tokens, temperature,
                reasoning=None, tools=None, dispatcher=None,
            )
            if text is not None:
                if user_id:
                    await clear_cooldown(user_id, c_provider, c_model)
                if idx > 0:
                    logger.info(
                        "%s: recovered via fallback_chain entry",
                        caller_tag,
                        extra={"provider": c_provider, "model": c_model, "chain_index": idx},
                    )
                return text
            last_error = error_message
        except Exception as exc:
            last_error = str(exc)
            error_type_value = classify_error(exc).value

        # Cooldown is only recorded for errors classified as Tier A
        # (retryable — rate_limit/timeout/provider_down), never for a
        # content-policy-style rejection or empty response, which say
        # nothing about whether the provider itself is healthy and would
        # otherwise falsely poison the shared cooldown store for every
        # OTHER caller (including the main chat path). This tier still
        # advances to the next candidate unconditionally on ANY failure —
        # only the cooldown *signal* is now classified, not the control flow.
        if user_id and error_type_value:
            policy = get_error_policy_by_value(error_type_value)
            if policy.retryable:
                await record_cooldown_failure(user_id, c_provider, c_model)

        if idx < len(candidates) - 1:
            logger.warning(
                "%s: candidate failed — trying next in fallback_chain",
                caller_tag,
                extra={"provider": c_provider, "model": c_model, "error": last_error},
            )

    raise RuntimeError(f"{caller_tag}: all candidates failed — {last_error}")
