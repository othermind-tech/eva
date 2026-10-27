# tests/test_pev_provider_failure_unit.py
"""
A plan/execute run whose provider fails for good must end FAILED, not COMPLETED.

Found live (2026-09-21): the Anthropic account ran out of credit. The API answered
400 "Your credit balance is too low"; the planner fell back to a one-step plan, the
step and the verifier failed the same way, and the run finished as
`completed` / `termination_reason: success` with the assistant text "I wasn't able to
generate a response for this request". The user saw no hint that credit was the
cause. core/execution/runner.py already turned "the stream ended with an error and
nothing else" into FAILED; run_pev_execution's finalize path did not.

Drives the real run_pev_execution with a stubbed _pev_stream, so the FSM transition,
the broker call and the published event are all the real ones.

Run with:
    pytest tests/test_pev_provider_failure_unit.py -v
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.execution.models import ExecutionStatus
from core.types import ErrorChunk, TextChunk

CREDIT_MESSAGE = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
                  "'message': 'Your credit balance is too low to access the Anthropic API.'}}")


def _execution():
    return SimpleNamespace(
        execution_id="exec-pev-fail", conversation_id="conv-pev-fail", user_id="pev-fail-user",
        status=ExecutionStatus.PENDING, cancel_token=SimpleNamespace(cancelled=False),
        parent_project_id=None, started_at=None, completed_at=None, error=None, buffer=None,
    )


def _stream_of(*chunks):
    async def _fake(**_kwargs):
        for c in chunks:
            yield c
    return _fake


async def _run(*chunks):
    from core.execution import pev_runner
    execution = _execution()
    registry = MagicMock()
    for name in ("mark_execution_planning", "update_execution", "mark_execution_running",
                 "mark_execution_failed", "mark_execution_finished", "heartbeat"):
        setattr(registry, name, AsyncMock())
    bus = MagicMock()
    finalizer = AsyncMock()
    with patch.object(pev_runner, "_pev_stream", _stream_of(*chunks)), \
         patch.object(pev_runner, "redis_execution_registry", registry), \
         patch.object(pev_runner, "event_bus", bus), \
         patch("core.events_publisher.publish_user_event", new=AsyncMock()) as user_event:
        await pev_runner.run_pev_execution(
            execution=execution, agent=MagicMock(), messages=[{"role": "user", "content": "hi"}],
            finalizer=finalizer,
        )
    return execution, registry, bus, finalizer, user_event


class TestProviderFailureEndsTheRunAsFailed(unittest.IsolatedAsyncioTestCase):

    async def test_error_and_no_output_is_failed_with_the_providers_message(self):
        execution, registry, bus, finalizer, user_event = await _run(
            ErrorChunk(message=CREDIT_MESSAGE, provider="anthropic", error_type="invalid_request"),
        )
        self.assertEqual(execution.status, ExecutionStatus.FAILED)
        self.assertEqual(execution.error, {"message": CREDIT_MESSAGE})
        registry.mark_execution_failed.assert_awaited_once_with("exec-pev-fail", error=CREDIT_MESSAGE)
        registry.mark_execution_finished.assert_not_awaited()
        finalizer.assert_awaited_once()

    async def test_published_event_is_execution_failed_with_the_error_type(self):
        from core.events import ExecutionFailed
        *_, bus, _fin, user_event = await _run(
            ErrorChunk(message=CREDIT_MESSAGE, provider="anthropic", error_type="invalid_request"),
        )
        published = [c.args[0] for c in bus.publish.call_args_list]
        failed = [e for e in published if isinstance(e, ExecutionFailed)]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].error_type, "invalid_request")
        self.assertEqual(failed[0].error_message, CREDIT_MESSAGE)
        user_event.assert_awaited_once()
        self.assertEqual(user_event.await_args.kwargs["event"], "execution.failed")

    async def test_a_message_less_error_still_fails_with_a_generic_reason(self):
        execution, *_ = await _run(ErrorChunk(message="", error_type=None))
        self.assertEqual(execution.status, ExecutionStatus.FAILED)
        self.assertTrue(execution.error["message"])


class TestRunsThatProducedOutputAreUnchanged(unittest.IsolatedAsyncioTestCase):

    async def test_an_earlier_error_followed_by_a_real_answer_still_completes(self):
        # e.g. one step hit a transient error, a replanned step then succeeded.
        execution, registry, *_ = await _run(
            ErrorChunk(message="rate limited", provider="anthropic", error_type="rate_limit"),
            TextChunk(content="Here is the answer."),
        )
        self.assertEqual(execution.status, ExecutionStatus.COMPLETED)
        registry.mark_execution_failed.assert_not_awaited()
        registry.mark_execution_finished.assert_awaited_once()

    async def test_a_clean_run_completes(self):
        execution, registry, bus, *_ = await _run(TextChunk(content="done"))
        self.assertEqual(execution.status, ExecutionStatus.COMPLETED)
        registry.mark_execution_failed.assert_not_awaited()

    async def test_an_empty_run_with_no_error_is_not_turned_into_a_failure(self):
        # No ErrorChunk means nothing went wrong that we can name; leave that case alone.
        execution, registry, *_ = await _run()
        self.assertEqual(execution.status, ExecutionStatus.COMPLETED)
        registry.mark_execution_failed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
