# tests/test_pev_final_answer_unit.py
"""
A-19/A-20: what the user actually reads as a plan/execute run's answer.

Before this fix, BufferedStream.aggregated() built the saved answer by
concatenating every step's TextChunk with no separator (core/stream_buffer.py)
-- run-together headings, leftover internal narration, the works. Now
core/execution/pev_runner.py's _pev_stream() computes the real final answer
itself (the last step's own closing synthesis -- see its is_last_step-aware
prompt in test_step_record_unit.py's TestRealStepLoop -- plus the plan
verifier's message when the verdict isn't "success") and sets it on
execution.final_answer_override; run_pev_execution() uses that instead of
aggregated()'s naive text whenever it's set, and falls back to aggregated()
unchanged for every path that returns before reaching it (cancelled,
awaiting_plan_approval rejected, AWAITING_WAKEUP).

This file drives the real _pev_stream() (same pattern as
test_step_record_unit.py's _drive()) with the plan verifier ALSO mocked, so
each test controls the verdict directly instead of hitting the real
"no session configured" fallback path.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.execution.plan_verifier import PlanVerificationResult
from core.execution.verifier import VerificationResult
from core.types import TextChunk


class _FakeAgent:
    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = []

    async def call(self, messages, stream=True):
        self.calls.append(messages)
        for chunk in self._scripts[len(self.calls) - 1]:
            yield chunk


def _plan(*titles, success_criteria=None):
    return {
        "goal": "fix the page",
        "success_criteria": success_criteria or ["fix the page"],
        "steps": [
            {"index": i, "title": t, "description": f"do: {t}", "status": "pending"}
            for i, t in enumerate(titles)
        ],
    }


async def _drive(agent, plan, verdicts, plan_verification, capture_plan_verify_kwargs=None):
    """Runs the real _pev_stream(); returns (chunks, execution mock).

    capture_plan_verify_kwargs: an optional dict that, if given, is updated
    in place with the kwargs plan_verifier_engine.verify_plan() was actually
    called with -- for tests asserting on what reaches that call, without a
    second, conflicting patch.object() on the same target (patches nest;
    only the innermost active one would ever fire).
    """
    from core.execution import pev_runner

    verdict_iter = iter(verdicts)

    async def fake_step_verify(**kwargs):
        passed, issues = next(verdict_iter)
        return VerificationResult(passed=passed, summary="s", issues=issues)

    async def fake_plan_verify(**kwargs):
        if capture_plan_verify_kwargs is not None:
            capture_plan_verify_kwargs.update(kwargs)
        return plan_verification

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    repo = MagicMock()
    for name in ("create", "update_steps", "update_status", "update_goal_verification"):
        setattr(repo, name, AsyncMock())

    execution = MagicMock(execution_id="e1", conversation_id="c1")
    execution.cancel_token = MagicMock(cancelled=False)
    execution.final_answer_override = None
    from core.execution.models import ExecutionStatus
    execution.status = ExecutionStatus.RUNNING

    chunks = []
    with patch.object(pev_runner.planner_engine, "generate", new=AsyncMock(return_value=plan)), \
         patch.object(pev_runner.verifier_engine, "verify", new=fake_step_verify), \
         patch.object(pev_runner.plan_verifier_engine, "verify_plan", new=fake_plan_verify), \
         patch.object(pev_runner, "PlanRepo", repo), \
         patch.object(pev_runner, "AsyncSessionLocal", return_value=session_cm):
        async for chunk in pev_runner._pev_stream(
            execution=execution, agent=agent,
            base_messages=[{"role": "user", "content": "fix my page"}],
            user_id="u", require_plan_approval=False,
        ):
            chunks.append(chunk)
    return chunks, execution, repo


def _plan_complete_chunk(chunks):
    from core.types import StepProgressChunk
    for c in chunks:
        if isinstance(c, StepProgressChunk) and c.step_status == "plan_complete":
            return c
    return None


class TestFinalAnswerOverride(unittest.IsolatedAsyncioTestCase):

    async def test_success_verdict_uses_only_the_last_steps_text_unchanged(self):
        agent = _FakeAgent([
            [TextChunk(content="Started the server.")],
            [TextChunk(content="Fixed the bug. All tests pass.")],
        ])
        _, execution, _repo = await _drive(
            agent, _plan("Start server", "Fix bug"), [(True, None), (True, None)],
            PlanVerificationResult(verdict="success", message=""),
        )
        self.assertEqual(execution.final_answer_override, "Fixed the bug. All tests pass.")
        # The concatenation bug this fix closes: step 1's text must NOT appear.
        self.assertNotIn("Started the server.", execution.final_answer_override)

    async def test_not_successful_verdict_appends_the_verifier_message(self):
        agent = _FakeAgent([
            [TextChunk(content="Attempted the fix.")],
        ])
        _, execution, _repo = await _drive(
            agent, _plan("Fix bug"), [(True, None)],
            PlanVerificationResult(
                verdict="not_successful",
                message="The multiply function was never implemented.",
            ),
        )
        self.assertIn("Attempted the fix.", execution.final_answer_override)
        self.assertIn("The multiply function was never implemented.", execution.final_answer_override)
        # The step's own claim comes first, the independent check after it.
        self.assertLess(
            execution.final_answer_override.index("Attempted the fix."),
            execution.final_answer_override.index("multiply function"),
        )

    async def test_pending_verdict_appends_the_verifier_message(self):
        agent = _FakeAgent([[TextChunk(content="Built the UI.")]])
        _, execution, _repo = await _drive(
            agent, _plan("Build UI"), [(True, None)],
            PlanVerificationResult(
                verdict="pending",
                message="Please confirm the layout looks right -- I can't check that myself.",
            ),
        )
        self.assertIn("Built the UI.", execution.final_answer_override)
        self.assertIn("confirm the layout", execution.final_answer_override)

    async def test_a_non_success_verdict_with_no_message_does_not_append_anything(self):
        # Defensive: PlanVerifierEngine itself guarantees a non-empty message
        # on non-success (tested in test_plan_verifier_unit.py), but the
        # pev_runner-side assembly must not crash or append a stray
        # separator if that guarantee were ever violated.
        agent = _FakeAgent([[TextChunk(content="Attempted the fix.")]])
        _, execution, _repo = await _drive(
            agent, _plan("Fix bug"), [(True, None)],
            PlanVerificationResult(verdict="not_successful", message=""),
        )
        self.assertEqual(execution.final_answer_override, "Attempted the fix.")

    async def test_the_plan_complete_chunk_carries_the_verdict(self):
        agent = _FakeAgent([[TextChunk(content="Done.")]])
        chunks, _, _repo = await _drive(
            agent, _plan("Do it"), [(True, None)],
            PlanVerificationResult(verdict="pending", message="Please double-check X."),
        )
        chunk = _plan_complete_chunk(chunks)
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.goal_verdict, "pending")
        self.assertEqual(chunk.goal_verification_message, "Please double-check X.")

    async def test_the_plan_complete_chunk_omits_the_message_on_success(self):
        agent = _FakeAgent([[TextChunk(content="Done.")]])
        chunks, _, _repo = await _drive(
            agent, _plan("Do it"), [(True, None)],
            PlanVerificationResult(verdict="success", message=""),
        )
        chunk = _plan_complete_chunk(chunks)
        self.assertEqual(chunk.goal_verdict, "success")
        self.assertIsNone(chunk.goal_verification_message)

    async def test_success_criteria_from_the_plan_reach_the_verifier_call(self):
        captured = {}
        agent = _FakeAgent([[TextChunk(content="Done.")]])
        await _drive(
            agent, _plan("Do it", success_criteria=["exit code 0", "no errors printed"]),
            [(True, None)],
            PlanVerificationResult(verdict="success", message=""),
            capture_plan_verify_kwargs=captured,
        )
        self.assertEqual(captured["success_criteria"], ["exit code 0", "no errors printed"])
        self.assertEqual(captured["goal"], "fix the page")
        self.assertIn("Done.", captured["draft_closing_summary"])

    async def test_the_verdict_is_persisted_via_plan_repo(self):
        agent = _FakeAgent([[TextChunk(content="Done.")]])
        _, _, repo = await _drive(
            agent, _plan("Do it"), [(True, None)],
            PlanVerificationResult(
                verdict="not_successful", message="multiply was never implemented.",
                criteria_results=[{"criterion": "multiply(4,5)==20", "met": False}],
            ),
        )
        repo.update_goal_verification.assert_awaited_once()
        _, kwargs = repo.update_goal_verification.call_args
        self.assertEqual(kwargs["execution_id"], "e1")
        self.assertEqual(kwargs["goal_verdict"], "not_successful")
        self.assertEqual(kwargs["goal_verification_message"], "multiply was never implemented.")
        self.assertEqual(kwargs["goal_criteria_results"], [{"criterion": "multiply(4,5)==20", "met": False}])

    async def test_a_success_verdicts_empty_message_persists_as_none_not_empty_string(self):
        agent = _FakeAgent([[TextChunk(content="Done.")]])
        _, _, repo = await _drive(
            agent, _plan("Do it"), [(True, None)],
            PlanVerificationResult(verdict="success", message=""),
        )
        _, kwargs = repo.update_goal_verification.call_args
        self.assertEqual(kwargs["goal_verdict"], "success")
        self.assertIsNone(kwargs["goal_verification_message"])


if __name__ == "__main__":
    unittest.main()
