# tests/test_rate_limit_cleanup_unit.py
"""
Unit tests for B2-1.4 (2026-08-18): rate limiter stale-key eviction.

The design this verifies: core/rate_limit.py's SlidingWindowRateLimiter has an
unbounded key space by its own documented design (a failed login/signup
attempt from a username or IP that never recurs occupies an in-process
dict entry for the life of the server) -- purge_stale_keys() evicts keys
whose deque is now empty, and every limiter instance self-registers into
a module-level list so core/tasks/cleanup.py's daily sweep
(purge_all_stale_rate_limit_keys()) can sweep all of them without core/
importing from api/.

Run with:
    cd <project_root>
    python tests/test_rate_limit_cleanup_unit.py
    (or: pytest tests/test_rate_limit_cleanup_unit.py -v)
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPurgeStaleKeys(unittest.IsolatedAsyncioTestCase):

    async def test_key_with_only_stale_entries_is_purged(self):
        from core.rate_limit import SlidingWindowRateLimiter

        limiter = SlidingWindowRateLimiter(window_seconds=0.05, max_calls=5)
        await limiter.record_failure("stale-user")
        import asyncio
        await asyncio.sleep(0.1)  # let the window fully elapse

        purged = await limiter.purge_stale_keys()

        self.assertEqual(purged, 1)
        self.assertNotIn("stale-user", limiter._call_log)

    async def test_key_with_recent_entries_is_not_purged(self):
        from core.rate_limit import SlidingWindowRateLimiter

        limiter = SlidingWindowRateLimiter(window_seconds=60, max_calls=5)
        await limiter.record_failure("active-user")

        purged = await limiter.purge_stale_keys()

        self.assertEqual(purged, 0)
        self.assertIn("active-user", limiter._call_log)

    async def test_mixed_stale_and_active_keys_only_stale_purged(self):
        from core.rate_limit import SlidingWindowRateLimiter
        import asyncio

        limiter = SlidingWindowRateLimiter(window_seconds=0.05, max_calls=5)
        await limiter.record_failure("will-go-stale")
        await asyncio.sleep(0.1)
        await limiter.record_failure("still-active")  # recorded after the sleep

        purged = await limiter.purge_stale_keys()

        self.assertEqual(purged, 1)
        self.assertNotIn("will-go-stale", limiter._call_log)
        self.assertIn("still-active", limiter._call_log)

    async def test_never_purges_a_key_that_is_currently_over_limit(self):
        """A key at its limit has a full, non-empty deque of recent calls --
        purge_stale_keys() must never evict live rate-limit state."""
        from core.rate_limit import SlidingWindowRateLimiter

        limiter = SlidingWindowRateLimiter(window_seconds=60, max_calls=2)
        await limiter.record_failure("attacker")
        await limiter.record_failure("attacker")

        purged = await limiter.purge_stale_keys()

        self.assertEqual(purged, 0)
        self.assertIn("attacker", limiter._call_log)
        self.assertEqual(len(limiter._call_log["attacker"]), 2)


class TestLimiterSelfRegistration(unittest.IsolatedAsyncioTestCase):

    async def test_new_limiter_instance_is_registered_and_swept(self):
        from core.rate_limit import SlidingWindowRateLimiter, _ALL_LIMITERS, purge_all_stale_rate_limit_keys
        import asyncio

        limiter = SlidingWindowRateLimiter(window_seconds=0.05, max_calls=5)
        self.assertIn(limiter, _ALL_LIMITERS)

        await limiter.record_failure("temp-key")
        await asyncio.sleep(0.1)

        summary = await purge_all_stale_rate_limit_keys()

        self.assertGreaterEqual(summary["limiters_swept"], 1)
        self.assertGreaterEqual(summary["keys_purged"], 1)
        self.assertNotIn("temp-key", limiter._call_log)

    async def test_one_limiter_failing_does_not_stop_the_sweep_of_others(self):
        from core.rate_limit import SlidingWindowRateLimiter, purge_all_stale_rate_limit_keys
        from unittest.mock import AsyncMock, patch

        broken = SlidingWindowRateLimiter(window_seconds=60, max_calls=5)
        with patch.object(broken, "purge_stale_keys", new=AsyncMock(side_effect=RuntimeError("boom"))):
            # Should not raise -- purge_all_stale_rate_limit_keys catches
            # per-limiter failures so one broken limiter can't block the
            # rest of the daily cleanup sweep.
            summary = await purge_all_stale_rate_limit_keys()

        self.assertIn("limiters_swept", summary)


class TestCleanupWrapper(unittest.IsolatedAsyncioTestCase):

    async def test_purge_rate_limiter_keys_returns_the_sweep_summary(self):
        from unittest.mock import AsyncMock, patch
        from core.tasks.cleanup import _purge_rate_limiter_keys

        with patch(
            "core.rate_limit.purge_all_stale_rate_limit_keys",
            new=AsyncMock(return_value={"limiters_swept": 4, "keys_purged": 7}),
        ):
            summary = await _purge_rate_limiter_keys()

        self.assertEqual(summary, {"limiters_swept": 4, "keys_purged": 7})


if __name__ == "__main__":
    unittest.main()
