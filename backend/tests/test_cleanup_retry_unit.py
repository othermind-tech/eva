# tests/test_cleanup_retry_unit.py
"""
Unit tests for B2-4.1 (2026-08-18): retry-with-backoff for the daily
cleanup task's five passes.

Previously a
single failed pass just logged a WARNING and waited for the next
scheduled 24h cycle -- core/tasks/cleanup.py's new _run_pass_with_retry()
gives each pass CLEANUP_RETRY_MAX_ATTEMPTS chances within the same cycle
first, with exponential backoff (reusing the same delay*2^(attempt-1)
shape core/daemons/registry.py's daemon retry policy already uses).

asyncio.sleep is mocked throughout -- these tests assert retry/backoff
*behavior* (call counts, timing values passed to sleep, cancellation
propagation), not real wall-clock delay.

Run with:
    cd <project_root>
    python tests/test_cleanup_retry_unit.py
    (or: pytest tests/test_cleanup_retry_unit.py -v)
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRunPassWithRetry(unittest.IsolatedAsyncioTestCase):

    async def test_success_on_first_attempt_no_retry_no_sleep(self):
        from core.tasks.cleanup import _run_pass_with_retry

        pass_fn = AsyncMock(return_value=None)
        with patch("core.tasks.cleanup.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await _run_pass_with_retry("test-pass", pass_fn)

        pass_fn.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    async def test_fails_twice_then_succeeds_retries_with_exponential_backoff(self):
        from core.tasks.cleanup import _run_pass_with_retry
        from config.settings import CLEANUP_RETRY_BASE_DELAY_SECONDS

        pass_fn = AsyncMock(side_effect=[RuntimeError("transient"), RuntimeError("transient"), None])
        with patch("core.tasks.cleanup.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await _run_pass_with_retry("test-pass", pass_fn)

        self.assertEqual(pass_fn.await_count, 3)
        self.assertEqual(mock_sleep.await_count, 2)
        # Exponential: base * 2^0, base * 2^1
        first_delay = mock_sleep.await_args_list[0].args[0]
        second_delay = mock_sleep.await_args_list[1].args[0]
        self.assertEqual(first_delay, CLEANUP_RETRY_BASE_DELAY_SECONDS)
        self.assertEqual(second_delay, CLEANUP_RETRY_BASE_DELAY_SECONDS * 2)

    async def test_backoff_is_capped_at_max_delay(self):
        from core.tasks.cleanup import _run_pass_with_retry
        from config.settings import CLEANUP_RETRY_MAX_DELAY_SECONDS

        with patch("core.tasks.cleanup.CLEANUP_RETRY_MAX_ATTEMPTS", 6), \
             patch("core.tasks.cleanup.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            pass_fn = AsyncMock(side_effect=[RuntimeError("x")] * 5 + [None])
            await _run_pass_with_retry("test-pass", pass_fn)

        delays = [call.args[0] for call in mock_sleep.await_args_list]
        self.assertTrue(all(d <= CLEANUP_RETRY_MAX_DELAY_SECONDS for d in delays))
        # Later attempts should actually hit the cap, not just happen to be under it.
        self.assertEqual(delays[-1], CLEANUP_RETRY_MAX_DELAY_SECONDS)

    async def test_exhausts_all_retries_then_gives_up_without_raising(self):
        from core.tasks.cleanup import _run_pass_with_retry
        from config.settings import CLEANUP_RETRY_MAX_ATTEMPTS

        pass_fn = AsyncMock(side_effect=RuntimeError("permanently broken"))
        with patch("core.tasks.cleanup.asyncio.sleep", new=AsyncMock()):
            await _run_pass_with_retry("test-pass", pass_fn)  # must not raise

        self.assertEqual(pass_fn.await_count, CLEANUP_RETRY_MAX_ATTEMPTS)

    async def test_cancelled_error_from_pass_propagates_immediately_no_retry(self):
        from core.tasks.cleanup import _run_pass_with_retry

        pass_fn = AsyncMock(side_effect=asyncio.CancelledError())
        with patch("core.tasks.cleanup.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(asyncio.CancelledError):
                await _run_pass_with_retry("test-pass", pass_fn)

        pass_fn.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    async def test_cancelled_error_during_backoff_sleep_propagates(self):
        from core.tasks.cleanup import _run_pass_with_retry

        pass_fn = AsyncMock(side_effect=RuntimeError("transient"))
        with patch("core.tasks.cleanup.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await _run_pass_with_retry("test-pass", pass_fn)

        pass_fn.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
