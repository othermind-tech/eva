# core/providers/fallback_state.py
#
# V6.8: shared fallback-resolution primitives, extracted from
# core/agent_engine.py so every LLM call site in EVA -- not just the main
# streaming chat/daemon execution path -- can resolve "which provider/model
# is actually healthy right now" and record/consult failures against the
# SAME cross-execution cooldown store, instead of each independently
# re-reading session.provider/session.model and never learning from a
# sibling call's failure.
#
# This module is intentionally shape-agnostic (no StreamChunk/ErrorChunk
# handling, no event_bus/SSE notification) -- it only answers "what should
# I try, and what happened last time." The streaming retry loop
# (AgentEngine._gen_with_fallback) and the two non-streaming helpers
# (core/providers/fallback_call.py) each build their own attempt/react loop
# on top of these primitives.
#
# Storage: core/broker/local_broker.py's LocalRedisAdapter -- in-memory,
# single-process, NOT real Redis (this predates V6.8). State resets on server
# restart -- a fresh start assumes every provider is healthy, which is the
# correct default, not a bug.

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any, Dict, List, Optional

from providers import credentials as _credentials

logger = logging.getLogger(__name__)

# eva.env-overridable (pydantic Settings class) -- same pattern as every
# other runtime-tunable constant in EVA. Defensive fallback matches the
# Field() defaults on Settings exactly, in case settings is unavailable
# this early in import order.
try:
    from config.settings import settings as _settings
    _FALLBACK_RETRY_BASE_DELAY_S = _settings.FALLBACK_RETRY_BASE_DELAY_S
    _FALLBACK_RETRY_MAX_DELAY_S = _settings.FALLBACK_RETRY_MAX_DELAY_S
    _FALLBACK_COOLDOWN_BASE_SECONDS = _settings.FALLBACK_COOLDOWN_BASE_SECONDS
    _FALLBACK_COOLDOWN_MAX_SECONDS = _settings.FALLBACK_COOLDOWN_MAX_SECONDS
    _FALLBACK_COOLDOWN_STREAK_TTL_SECONDS = _settings.FALLBACK_COOLDOWN_STREAK_TTL_SECONDS
except Exception:
    _FALLBACK_RETRY_BASE_DELAY_S = 0.5
    _FALLBACK_RETRY_MAX_DELAY_S = 8.0
    _FALLBACK_COOLDOWN_BASE_SECONDS = 30.0
    _FALLBACK_COOLDOWN_MAX_SECONDS = 900.0
    _FALLBACK_COOLDOWN_STREAK_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Cross-execution cooldown (moved verbatim from core/agent_engine.py, V6.5
# Phase 4 -- behavior unchanged, only the import location moves). A
# (user, provider, model) that exhausts Tier A retries is skipped by every
# caller that reaches it -- not just future AgentEngine executions -- until
# the cooldown expires.
# ---------------------------------------------------------------------------

def _cooldown_key(user_id: str, provider: str, model: str) -> str:
    return f"fallback_cooldown:{user_id}:{provider}:{model}"


def _cooldown_streak_key(user_id: str, provider: str, model: str) -> str:
    return f"fallback_cooldown_streak:{user_id}:{provider}:{model}"


async def is_in_cooldown(user_id: str, provider: str, model: str) -> bool:
    from core.broker import local_broker
    value = await local_broker.client.get(_cooldown_key(user_id, provider, model))
    return value is not None


async def record_cooldown_failure(user_id: str, provider: str, model: str) -> None:
    """
    Put (user_id, provider, model) into cooldown. Duration escalates on
    consecutive cooldown-triggering failures: min(cap, base * 2**(streak-1)).
    The streak counter lives in a separate, longer-TTL key so a failure
    right after a cooldown expires still escalates instead of resetting to
    the base duration every time.
    """
    from core.broker import local_broker
    client = local_broker.client

    streak_key = _cooldown_streak_key(user_id, provider, model)
    streak_raw = await client.get(streak_key)
    streak = int(streak_raw) + 1 if streak_raw else 1

    cooldown_seconds = min(
        _FALLBACK_COOLDOWN_MAX_SECONDS,
        _FALLBACK_COOLDOWN_BASE_SECONDS * (2 ** (streak - 1)),
    )

    await client.set(_cooldown_key(user_id, provider, model), "1", ex=int(cooldown_seconds))
    await client.set(streak_key, str(streak), ex=_FALLBACK_COOLDOWN_STREAK_TTL_SECONDS)

    logger.warning(
        "Provider entering cooldown after exhausting retries",
        extra={
            "user_id": user_id, "provider": provider, "model": model,
            "cooldown_seconds": cooldown_seconds, "consecutive_failures": streak,
        },
    )


async def clear_cooldown(user_id: str, provider: str, model: str) -> None:
    """Called on a successful call -- a fresh success closes the circuit."""
    from core.broker import local_broker
    client = local_broker.client
    await client.delete(_cooldown_key(user_id, provider, model))
    await client.delete(_cooldown_streak_key(user_id, provider, model))


# ---------------------------------------------------------------------------
# Backoff (moved verbatim from core/agent_engine.py's _backoff_sleep)
# ---------------------------------------------------------------------------

async def backoff_sleep(
    retry_number: int,
    base: float = _FALLBACK_RETRY_BASE_DELAY_S,
    cap: float = _FALLBACK_RETRY_MAX_DELAY_S,
) -> None:
    """
    Exponential backoff with jitter before retrying the same candidate.
    retry_number is 0-indexed (0 = the delay before the *first* retry).
    """
    delay = min(cap, base * (2 ** retry_number))
    delay += delay * random.uniform(0, 0.25)
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# API key resolution (moved from core/agent_engine.py's _fallback_env_key +
# the inline `entry.get("api_key") or os.getenv(...)` pattern in
# _build_fallback_handlers -- this was the correct, complete version; two
# other hand-rolled copies of "resolve an API key" existed elsewhere in the
# codebase (api/prompts.py, core/execution/title_generator.py) with real
# behavioral drift -- one missing the together->together_ai alias, others
# missing the environment-variable fallback entirely (core/memory/
# extractor.py, core/project/artifact_updater.py, tools/default/
# coordinate_tool.py all read session["api_key"] only). This is now the one
# canonical implementation.
# ---------------------------------------------------------------------------

def resolve_api_key(explicit_key: Optional[str], provider: str) -> str:
    """
    Resolve an API key for `provider`: explicit value first (a fallback_chain
    entry's own "api_key" field), then the key in the OS credential store via
    providers.credentials -- the one resolver every call site shares. Returns
    "" (never None) if neither is available -- callers already treat an empty
    key as "try anyway" for key-less providers like ollama, or as a
    fallback-eligible AUTH failure for everyone else.
    """
    return _credentials.resolve_api_key(provider, explicit_key)


# ---------------------------------------------------------------------------
# Candidate list construction + cooldown filtering
# ---------------------------------------------------------------------------

def build_candidates(
    provider: str,
    model: str,
    api_key: Optional[str],
    fallback_chain: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    """
    Normalise the primary provider/model + a user's fallback_chain config
    into one ordered list of {"provider", "model", "api_key"} dicts, each
    with its API key already resolved (explicit -> env var). Malformed
    fallback_chain entries (missing provider or model) are skipped.
    """
    candidates: List[Dict[str, str]] = []
    if provider and model:
        candidates.append({
            "provider": provider,
            "model": model,
            "api_key": resolve_api_key(api_key, provider),
        })
    for entry in (fallback_chain or []):
        fb_provider = entry.get("provider", "")
        fb_model = entry.get("model", "")
        if not fb_provider or not fb_model:
            continue
        candidates.append({
            "provider": fb_provider,
            "model": fb_model,
            "api_key": resolve_api_key(entry.get("api_key"), fb_provider),
        })
    return candidates


async def filter_cooldown(
    user_id: Optional[str],
    candidates: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """
    Drop candidates currently in cooldown (order preserved). A no-op when
    user_id is falsy -- matches AgentEngine's own graceful degradation for
    bare/test instances with no execution context.
    """
    if not user_id:
        return candidates
    result: List[Dict[str, str]] = []
    for c in candidates:
        if await is_in_cooldown(user_id, c["provider"], c["model"]):
            logger.info(
                "Skipping candidate — in cooldown from a recent failure",
                extra={"provider": c["provider"], "model": c["model"], "user_id": user_id},
            )
            continue
        result.append(c)
    return result
