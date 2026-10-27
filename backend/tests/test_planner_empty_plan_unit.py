# tests/test_planner_empty_plan_unit.py
"""
A-22: a plan with zero steps must never reach (or crash) the PEV runner.

Found live (2026-10-20, V6.59 Phase 0): the planner model answered
{"goal": "...", "steps": []}. core/execution/planner.py's _normalise_plan() is
deliberately permissive (a flaky LLM must not block the loop), so that empty plan
was persisted as-is; core/execution/pev_runner.py then failed in its closing-
synthesis block with `UnboundLocalError: cannot access local variable 'step_output'`
because `step_output` is only assigned once a step has run. The user saw a failed
run with no explanation.

Two layers are covered, independently:
  1. planner: generate()/revise() substitute the single catch-all step (the same
     recovery used for every other unusable-plan case) and KEEP the model's goal
     and success criteria.
  2. runner: even if some path still hands it an empty plan, _pev_stream() finishes
     cleanly instead of raising (the planner fix is bypassed in these tests).
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.execution.plan_verifier import PlanVerificationResult
from core.execution.planner import (
    PlannerEngine,
    _FALLBACK_PLAN,
    _ensure_executable_plan,
    _normalise_plan,
)


def _llm_returning(obj):
    return AsyncMock(return_value=json.dumps(obj))


_SESSION = {"provider": "ollama_cloud", "model": "gpt-oss:20b"}


class TestEnsureExecutablePlan(unittest.TestCase):

    def test_empty_steps_get_the_single_catch_all_step_and_keep_goal_and_criteria(self):
        plan = _normalise_plan({"goal": "Return the MCP echo", "success_criteria": ["it echoes"], "steps": []})
        self.assertEqual(plan["steps"], [])  # the permissive normaliser really does let this through

        fixed = _ensure_executable_plan(plan)

        self.assertEqual(len(fixed["steps"]), 1)
        self.assertEqual(fixed["steps"][0]["title"], _FALLBACK_PLAN["steps"][0]["title"])
        self.assertEqual(fixed["steps"][0]["status"], "pending")  # a fully-initialised runtime step
        self.assertEqual(fixed["goal"], "Return the MCP echo")
        self.assertEqual(fixed["success_criteria"], ["it echoes"])

    def test_a_plan_that_already_has_steps_is_left_untouched(self):
        plan = _normalise_plan({"goal": "g", "steps": [{"title": "A"}, {"title": "B"}]})
        before = json.dumps(plan, sort_keys=True)
        self.assertEqual(json.dumps(_ensure_executable_plan(plan), sort_keys=True), before)


class TestPlannerNeverReturnsAnEmptyPlan(unittest.IsolatedAsyncioTestCase):

    async def _generate(self, llm_plan):
        with patch("config.settings.get_user_session", return_value=dict(_SESSION)), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=_llm_returning(llm_plan)):
            return await PlannerEngine().generate(user_message="use the echo tool", user_id="u")

    async def test_generate_substitutes_a_step_for_an_empty_plan_and_keeps_the_goal(self):
        plan = await self._generate({"goal": "Echo ping via MCP", "success_criteria": ["returns echo:ping"], "steps": []})
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["goal"], "Echo ping via MCP")
        self.assertEqual(plan["success_criteria"], ["returns echo:ping"])

    async def test_generate_with_a_missing_steps_key_is_handled_too(self):
        plan = await self._generate({"goal": "g"})
        self.assertEqual(len(plan["steps"]), 1)

    async def test_generate_leaves_a_real_plan_alone(self):
        plan = await self._generate({"goal": "g", "steps": [{"title": "One"}, {"title": "Two"}]})
        self.assertEqual([s["title"] for s in plan["steps"]], ["One", "Two"])

    async def test_revise_substitutes_a_step_for_an_empty_revision(self):
        with patch("config.settings.get_user_session", return_value=dict(_SESSION)), \
             patch("core.providers.fallback_call.call_llm_with_fallback",
                   new=_llm_returning({"goal": "revised goal", "steps": []})):
            plan = await PlannerEngine().revise(
                goal="old goal", rejected_steps=[{"index": 0, "title": "x", "description": ""}],
                feedback="make it shorter", user_id="u",
            )
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["goal"], "revised goal")


class _NeverCalledAgent:
    """If the runner ever tries to execute a step of an empty plan, fail loudly."""
    calls = 0

    async def call(self, messages, stream=True):
        _NeverCalledAgent.calls += 1
        if False:
            yield None


class TestRunnerSurvivesAnEmptyPlan(unittest.IsolatedAsyncioTestCase):
    """Backstop: the planner fix is bypassed here (generate() is mocked to hand back zero steps)."""

    async def _drive_empty_plan(self, verdict):
        from core.execution import pev_runner
        from core.execution.models import ExecutionStatus

        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        session_cm.__aexit__ = AsyncMock(return_value=False)
        repo = MagicMock()
        for name in ("create", "update_steps", "update_status", "update_goal_verification"):
            setattr(repo, name, AsyncMock())

        execution = MagicMock(execution_id="e1", conversation_id="c1")
        execution.cancel_token = MagicMock(cancelled=False)
        execution.final_answer_override = None
        execution.status = ExecutionStatus.RUNNING

        empty = {"goal": "do something", "success_criteria": ["do something"], "steps": []}
        seen = {}

        async def fake_plan_verify(**kwargs):
            seen.update(kwargs)
            return verdict

        chunks = []
        with patch.object(pev_runner.planner_engine, "generate", new=AsyncMock(return_value=empty)), \
             patch.object(pev_runner.plan_verifier_engine, "verify_plan", new=fake_plan_verify), \
             patch.object(pev_runner, "PlanRepo", repo), \
             patch.object(pev_runner, "AsyncSessionLocal", return_value=session_cm):
            async for chunk in pev_runner._pev_stream(
                execution=execution, agent=_NeverCalledAgent(),
                base_messages=[{"role": "user", "content": "do something"}],
                user_id="u", require_plan_approval=False,
            ):
                chunks.append(chunk)
        return chunks, execution, seen

    async def test_an_empty_plan_finishes_without_raising_and_runs_no_step(self):
        _NeverCalledAgent.calls = 0
        _chunks, execution, seen = await self._drive_empty_plan(
            PlanVerificationResult(verdict="not_successful", message="Nothing was run."))
        self.assertEqual(_NeverCalledAgent.calls, 0)
        self.assertEqual(seen.get("draft_closing_summary"), "")  # the bound-to-"" step_output
        self.assertIn("Nothing was run.", execution.final_answer_override)
