# core/rate_limit.py
#
# Shared in-process sliding-window rate limiter.
#
# No external Redis needed -- state lives in-process, matching the rest of
# EVA's broker architecture (core/broker/local_broker.py). Originally
# built inline for webhook triggers (api/webhooks.py, S-3); extracted here
# so the auth endpoints (S-02) can reuse the identical, already-proven
# mechanism instead of a second hand-rolled implementation.
#
# Two usage shapes:
#   - check_and_record(key) -- single call, every call counts towards the
#     limit regardless of what the caller does with it afterwards. This is
#     the original webhook shape: any trigger call, successful or not,
#     consumes one slot.
#   - check(key) / record_failure(key) / reset(key) -- split shape for
#     "only failures should count, and a success clears the slate" cases
#     (e.g. login attempts, per OWASP's Authentication Cheat Sheet: don't
#     penalize a user who eventually logs in correctly).

from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Dict, List, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# B2-1.4 (2026-08-18): every SlidingWindowRateLimiter instance registers
# itself here on construction so core/tasks/cleanup.py's daily sweep can
# purge stale keys across all of them without importing from the api/
# layer (limiters are instantiated in api/auth.py, api/webhooks.py, etc.;
# core/ modules importing from api/ would invert this codebase's usual
# dependency direction). See purge_all_stale_rate_limit_keys() below.
_ALL_LIMITERS: List["SlidingWindowRateLimiter"] = []


class SlidingWindowRateLimiter:
    """
    Per-key sliding-window rate limiter. Each key gets its own bounded
    deque of call timestamps (monotonic clock); a call is rejected with
    HTTP 429 (+ Retry-After) once the key has recorded `max_calls` within
    the last `window_seconds`.

    All state is in-process. The key space is unbounded by default,
    matching the original webhook limiter's behaviour -- callers with a
    large or unbounded key space that can go permanently stale (e.g. a
    deleted webhook's token) should call reset() when a key can never
    recur, to avoid holding an empty deque forever.
    """

    def __init__(self, window_seconds: int, max_calls: int, message: Optional[str] = None):
        self._window_seconds = window_seconds
        self._max_calls = max_calls
        self._message = message or (
            f"Rate limit exceeded. Max {max_calls} calls per {window_seconds}s window."
        )
        self._call_log: Dict[str, collections.deque] = {}
        self._lock = asyncio.Lock()
        _ALL_LIMITERS.append(self)

    def _evict_stale(self, dq: collections.deque, cutoff: float) -> None:
        while dq and dq[0] < cutoff:
            dq.popleft()

    def _raise_429(self, dq: collections.deque, now: float) -> None:
        oldest = dq[0]
        retry_after = int(self._window_seconds - (now - oldest)) + 1
        raise HTTPException(
            status_code=429,
            detail=self._message,
            headers={"Retry-After": str(retry_after)},
        )

    async def check_and_record(self, key: str) -> None:
        """
        Raise HTTP 429 if `key` is already at its limit; otherwise record
        this call. Every call counts -- use for endpoints where all
        traffic (not just failures) should be throttled.
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds
        async with self._lock:
            dq = self._call_log.setdefault(key, collections.deque(maxlen=self._max_calls))
            self._evict_stale(dq, cutoff)
            if len(dq) >= self._max_calls:
                self._raise_429(dq, now)
            dq.append(now)

    async def check(self, key: str) -> None:
        """
        Raise HTTP 429 if `key` is currently over its limit, without
        recording this call. Pair with record_failure()/reset() for
        failure-counted limiting.
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds
        async with self._lock:
            dq = self._call_log.get(key)
            if not dq:
                return
            self._evict_stale(dq, cutoff)
            if len(dq) >= self._max_calls:
                self._raise_429(dq, now)

    async def record_failure(self, key: str) -> None:
        """Record a failed attempt against `key`."""
        now = time.monotonic()
        async with self._lock:
            dq = self._call_log.setdefault(key, collections.deque(maxlen=self._max_calls))
            dq.append(now)

    async def reset(self, key: str) -> None:
        """Clear recorded calls for `key` (e.g. after a successful login,
        or when the key can never recur, such as a deleted webhook token)."""
        async with self._lock:
            self._call_log.pop(key, None)

    async def purge_stale_keys(self) -> int:
        """
        B2-1.4 (2026-08-18): evict every key whose deque is now empty once
        stale entries are dropped -- i.e. keys with no calls inside the
        current window, which will hold an empty (but not-yet-garbage-
        collected) deque forever otherwise. This is the class's own
        documented unbounded-key-space gap (see the class docstring):
        `reset()` handles the single-key case a caller already knows about
        (a successful login, a deleted webhook token); this handles the
        general case -- e.g. a failed login/signup attempt from a
        username or IP that never recurs, which without this sweep
        occupies memory for the life of the process.

        Called periodically by core/tasks/cleanup.py's daily sweep via
        purge_all_stale_rate_limit_keys() below, not on every request.

        Returns the number of keys purged.
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds
        async with self._lock:
            stale_keys = []
            for key, dq in self._call_log.items():
                self._evict_stale(dq, cutoff)
                if not dq:
                    stale_keys.append(key)
            for key in stale_keys:
                del self._call_log[key]
            return len(stale_keys)


async def purge_all_stale_rate_limit_keys() -> dict:
    """
    Sweep every registered SlidingWindowRateLimiter instance (currently:
    login/signup/refresh in api/auth.py, webhooks in api/webhooks.py --
    new limiters are picked up automatically via _ALL_LIMITERS, no call
    site here needs updating when one is added). Called by
    core/tasks/cleanup.py's daily loop.

    Returns {"limiters_swept": N, "keys_purged": total}.
    """
    total_purged = 0
    for limiter in _ALL_LIMITERS:
        try:
            total_purged += await limiter.purge_stale_keys()
        except Exception:
            logger.warning("Rate limiter stale-key purge failed for one limiter", exc_info=True)
    return {"limiters_swept": len(_ALL_LIMITERS), "keys_purged": total_purged}
