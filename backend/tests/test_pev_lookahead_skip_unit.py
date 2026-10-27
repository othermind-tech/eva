# tests/test_pev_lookahead_skip_unit.py
"""
A-21: pev_runner.py's own loop-control application of the look-ahead skip --
distinct from tests/test_step_record_unit.py's TestLookaheadSkip, which
covers VerifierEngine's fail-closed judgment/parsing in isolation.

This file drives the real _pev_stream() (same pattern as
tests/test_pev_final_answer_unit.py's _drive()), with verifier.verify()
mocked to return a controlled VerificationResult (including
also_satisfies_count/also_satisfies_reason) so each test can assert on:
  - the correct number of steps actually get marked "skipped" and a
    "skipped" StepProgressChunk is yielded for each
  - PEV_LOOKAHEAD_MAX_SKIP clamps an over-claiming verifier
  - the plan's own true final step is never skipped, regardless of a high
    claimed count
  - no skip is ever applied outside the verification.passed branch (a
    failed step, or one that hit infra_error, must not skip anything even
    if also_satisfies_count is contrived to be non-zero on the mock)
  - lookahead_skip_enabled=False disables the whole mechanism (mirrors the
    per-user session preference wired in api/session.py)
  - step_idx/next_step_idx jump past skipped steps correctly, and
    steps_skipped is tallied (never folded into steps_completed) on the
    final plan_complete chunk
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.execution.plan_verifier import PlanVerificationResult
from core.execution.verifier import VerificationResult
from core.types import StepProgressChunk, TextChunk


class _FakeAgent:
    def __init__(self, n_steps):
        self._n_steps = n_steps

    async def call(self, messages, stream=True):
        yield TextChunk(content="ok")


def _plan(*titles):
    return {
        "goal": "ship the feature",
        "success_criteria": ["ship the feature"],
        "steps": [
            {"index": i, "title": t, "description": f"do: {t}", "status": "pending"}
            for i, t in enumerate(titles)
        ],
    }


_PASS_PLAN_VERIFICATION = PlanVerificationResult(verdict="success")


async def _drive(plan, verifications, lookahead_skip_enabled=True):
    """Runs the real _pev_stream(); returns (chunks, steps_seen_by_verify).

    verifications: list of VerificationResult, one per step actually
    verified (steps skipped by A-21 never reach verify(), so this list is
    shorter than the plan's own step count whenever a skip occurs).
    """
    from core.execution import pev_runner

    verify_iter = iter(verifications)
    remaining_steps_seen = []

    async def fake_step_verify(**kwargs):
        remaining_steps_seen.append(kwargs.get("remaining_steps"))
        return next(verify_iter)

    async def fake_plan_verify(**kwargs):
        return _PASS_PLAN_VERIFICATION

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

    agent = _FakeAgent(len(plan["steps"]))

    async def fake_replan(**kwargs):
        # Keeps a failing step's re-plan a no-op passthrough (returns the
        # original remaining steps unchanged) so tests can control the
        # exact sequence of verify() calls without the real planner's
        # no-provider-configured fallback collapsing the plan to one step.
        return kwargs["remaining_steps"]

    chunks = []
    with patch.object(pev_runner.planner_engine, "generate", new=AsyncMock(return_value=plan)), \
         patch.object(pev_runner.planner_engine, "replan", new=fake_replan), \
         patch.object(pev_runner.verifier_engine, "verify", new=fake_step_verify), \
         patch.object(pev_runner.plan_verifier_engine, "verify_plan", new=fake_plan_verify), \
         patch.object(pev_runner, "PlanRepo", repo), \
         patch.object(pev_runner, "AsyncSessionLocal", return_value=session_cm):
        async for chunk in pev_runner._pev_stream(
            execution=execution, agent=agent,
            base_messages=[{"role": "user", "content": "ship it"}],
            user_id="u", require_plan_approval=False,
            lookahead_skip_enabled=lookahead_skip_enabled,
        ):
            chunks.append(chunk)
    return chunks, remaining_steps_seen


def _progress_chunks(chunks, status=None):
    out = [c for c in chunks if isinstance(c, StepProgressChunk)]
    if status is not None:
        out = [c for c in out if c.step_status == status]
    return out


def _plan_complete_chunk(chunks):
    for c in _progress_chunks(chunks, "plan_complete"):
        return c
    return None


class TestLookaheadSkipApplication(unittest.IsolatedAsyncioTestCase):
    async def test_single_skip_advances_past_satisfied_step(self):
        plan = _plan("write file", "verify file", "report done")
        verifications = [
            VerificationResult(passed=True, summary="s", also_satisfies_count=1, also_satisfies_reason="already verified"),
            VerificationResult(passed=True, summary="s"),  # step index 2 (the true last step) always runs for real
        ]
        chunks, remaining_seen = await _drive(plan, verifications)

        skipped = _progress_chunks(chunks, "skipped")
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].step_index, 1)
        self.assertIn("step 1", skipped[0].verification_summary)
        self.assertIn("satisfied", skipped[0].verification_summary)

        # Step 0's verify() call should have been offered exactly the
        # last-step-excluding candidate slice: steps[1:2].
        self.assertEqual(len(remaining_seen[0]), 1)
        self.assertEqual(remaining_seen[0][0]["title"], "verify file")

        complete = _plan_complete_chunk(chunks)
        self.assertEqual(complete.steps_skipped, 1)
        self.assertEqual(complete.steps_completed, 2)  # step 0 + true last step, skip not folded in

    async def test_max_skip_cap_clamps_overclaiming_verifier(self):
        from core.execution import pev_runner

        titles = ["s0", "s1", "s2", "s3", "s4", "s5"]
        plan = _plan(*titles)
        # 6 steps total; candidates for step 0 are steps[1:5] (4 steps,
        # excluding the true last step index 5) -- more than the cap, so
        # the cap (not the candidate count) is what binds here. Pin the
        # cap to a known value so the test doesn't depend on the deployer
        # env default.
        verifications = [
            VerificationResult(passed=True, summary="s", also_satisfies_count=999, also_satisfies_reason="way too many"),
            VerificationResult(passed=True, summary="s"),  # step index 3, plain pass, no further skip
            VerificationResult(passed=True, summary="s"),  # step index 4, plain pass, no further skip
            VerificationResult(passed=True, summary="s"),  # step index 5, the true last step
        ]
        with patch.object(pev_runner, "PEV_LOOKAHEAD_MAX_SKIP", 2):
            chunks, remaining_seen = await _drive(plan, verifications)

        expected_skip = 2  # min(999, 4 candidates, cap=2)
        skipped = _progress_chunks(chunks, "skipped")
        self.assertEqual(len(skipped), expected_skip)
        self.assertEqual({c.step_index for c in skipped}, {1, 2})

        complete = _plan_complete_chunk(chunks)
        self.assertEqual(complete.steps_skipped, expected_skip)

    async def test_true_last_step_never_skipped(self):
        plan = _plan("s0", "s1", "s2")
        # Step 0 claims to satisfy everything remaining, including the
        # plan's own final step -- candidates must still exclude it.
        verifications = [
            VerificationResult(passed=True, summary="s", also_satisfies_count=5, also_satisfies_reason="claims everything"),
            VerificationResult(passed=True, summary="s"),  # index 2, the true last step, must still be verified for real
        ]
        chunks, remaining_seen = await _drive(plan, verifications)

        # Only one candidate was ever offered: step index 1. Index 2 (the
        # true last step) must never appear in remaining_steps.
        self.assertEqual(len(remaining_seen[0]), 1)
        self.assertEqual(remaining_seen[0][0]["title"], "s1")

        skipped = _progress_chunks(chunks, "skipped")
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].step_index, 1)

        # The true last step was actually verified (two verify() calls
        # consumed, not one) and completed normally, not marked skipped.
        self.assertEqual(len(remaining_seen), 2)
        completed = _progress_chunks(chunks, "completed")
        self.assertTrue(any(c.step_index == 2 for c in completed))

    async def test_no_skip_when_verification_failed(self):
        # Contrived: a failing VerificationResult with a non-zero
        # also_satisfies_count should never happen from the real verifier
        # (fail-closed there), but pev_runner's own skip application must
        # independently guard on verification.passed too -- it lives
        # entirely inside the `if verification.passed:` branch.
        plan = _plan("s0", "s1", "s2")
        verifications = [
            VerificationResult(passed=False, summary="s", issues="broken", also_satisfies_count=2, also_satisfies_reason="should be ignored"),
            VerificationResult(passed=True, summary="s"),
            VerificationResult(passed=True, summary="s"),
        ]
        chunks, remaining_seen = await _drive(plan, verifications)

        skipped = _progress_chunks(chunks, "skipped")
        self.assertEqual(len(skipped), 0)
        complete = _plan_complete_chunk(chunks)
        self.assertEqual(complete.steps_skipped, 0)

    async def test_no_skip_on_infra_error(self):
        plan = _plan("s0", "s1", "s2")
        verifications = [
            VerificationResult(passed=False, summary="s", infra_error=True, also_satisfies_count=2, also_satisfies_reason="should be ignored"),
            VerificationResult(passed=True, summary="s"),
            VerificationResult(passed=True, summary="s"),
        ]
        chunks, remaining_seen = await _drive(plan, verifications)

        skipped = _progress_chunks(chunks, "skipped")
        self.assertEqual(len(skipped), 0)
        # infra_error path treats the step as completed-but-unverified and
        # continues; all 3 steps should have been individually verified.
        self.assertEqual(len(remaining_seen), 3)

    async def test_lookahead_disabled_session_preference(self):
        plan = _plan("s0", "s1", "s2")
        verifications = [
            VerificationResult(passed=True, summary="s"),
            VerificationResult(passed=True, summary="s"),
            VerificationResult(passed=True, summary="s"),
        ]
        chunks, remaining_seen = await _drive(plan, verifications, lookahead_skip_enabled=False)

        # No candidates ever offered to the verifier when disabled.
        self.assertTrue(all(r is None for r in remaining_seen))
        skipped = _progress_chunks(chunks, "skipped")
        self.assertEqual(len(skipped), 0)
        complete = _plan_complete_chunk(chunks)
        self.assertEqual(complete.steps_skipped, 0)
        self.assertEqual(complete.steps_completed, 3)


if __name__ == "__main__":
    unittest.main()
