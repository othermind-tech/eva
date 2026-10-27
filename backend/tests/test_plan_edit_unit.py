# tests/test_plan_edit_unit.py
"""
Unit tests for UX-01: Plan Edit-Approve-Execute.

Covers:
  - core.execution.planner.validate_plan_shape() — the strict validator for
    user-submitted (edited) plans, distinct from _normalise_plan()'s
    lenient handling of raw LLM output.
  - core.execution.pev_runner.resolve_plan_approval()'s decision-value
    validation (approve/edit/reject).

Mocked throughout — no live server, no real Redis, no real API calls.

Run with:
    cd <project_root>
    python tests/test_plan_edit_unit.py
    (or: pytest tests/test_plan_edit_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestValidatePlanShape(unittest.TestCase):
    def test_valid_plan_passes(self):
        from core.execution.planner import validate_plan_shape
        plan = {
            "goal": "Determine the current stable Python version",
            "steps": [
                {"index": 0, "title": "Search for it", "description": "Use web_search", "tool_hint": "web_search"},
                {"index": 1, "title": "Report it", "description": "Summarise the finding", "tool_hint": None},
            ],
        }
        result = validate_plan_shape(plan)
        self.assertEqual(result["goal"], plan["goal"])
        self.assertEqual(len(result["steps"]), 2)

    def test_not_a_dict_rejected(self):
        from core.execution.planner import validate_plan_shape, PlanValidationError
        for bad in [None, "a plan", 42, ["steps"]]:
            with self.subTest(bad=bad):
                with self.assertRaises(PlanValidationError):
                    validate_plan_shape(bad)

    def test_missing_or_empty_goal_rejected(self):
        from core.execution.planner import validate_plan_shape, PlanValidationError
        for bad_goal in [None, "", "   ", 42]:
            with self.subTest(bad_goal=bad_goal):
                with self.assertRaises(PlanValidationError):
                    validate_plan_shape({"goal": bad_goal, "steps": [{"title": "x", "description": "y"}]})

    def test_missing_or_empty_steps_rejected(self):
        from core.execution.planner import validate_plan_shape, PlanValidationError
        for bad_steps in [None, [], "not a list", {}]:
            with self.subTest(bad_steps=bad_steps):
                with self.assertRaises(PlanValidationError):
                    validate_plan_shape({"goal": "a goal", "steps": bad_steps})

    def test_step_not_a_dict_rejected(self):
        from core.execution.planner import validate_plan_shape, PlanValidationError
        with self.assertRaises(PlanValidationError):
            validate_plan_shape({"goal": "a goal", "steps": ["not a dict"]})

    def test_step_missing_title_rejected(self):
        from core.execution.planner import validate_plan_shape, PlanValidationError
        with self.assertRaises(PlanValidationError):
            validate_plan_shape({"goal": "a goal", "steps": [{"description": "no title here"}]})

    def test_step_bad_index_type_rejected(self):
        from core.execution.planner import validate_plan_shape, PlanValidationError
        with self.assertRaises(PlanValidationError):
            validate_plan_shape({"goal": "a goal", "steps": [{"title": "t", "index": "not an int"}]})
        # bool is technically an int subclass in Python -- must be explicitly rejected too.
        with self.assertRaises(PlanValidationError):
            validate_plan_shape({"goal": "a goal", "steps": [{"title": "t", "index": True}]})

    def test_step_bad_tool_hint_type_rejected(self):
        from core.execution.planner import validate_plan_shape, PlanValidationError
        with self.assertRaises(PlanValidationError):
            validate_plan_shape({"goal": "a goal", "steps": [{"title": "t", "tool_hint": 123}]})

    def test_step_tool_hint_null_is_valid(self):
        from core.execution.planner import validate_plan_shape
        result = validate_plan_shape({"goal": "a goal", "steps": [{"title": "t", "tool_hint": None}]})
        self.assertEqual(len(result["steps"]), 1)

    def test_step_index_defaults_to_position_when_omitted(self):
        from core.execution.planner import validate_plan_shape
        result = validate_plan_shape({
            "goal": "a goal",
            "steps": [{"title": "first"}, {"title": "second"}],
        })
        self.assertEqual(result["steps"][0]["index"], 0)
        self.assertEqual(result["steps"][1]["index"], 1)


class TestResolvePlanApprovalDecisionValidation(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_decision_value_rejected_without_touching_broker(self):
        """An invalid decision string must fail fast, before ever touching
        the broker/Redis layer -- confirms the validation happens first."""
        from core.execution.pev_runner import resolve_plan_approval

        with patch("core.execution.pev_runner.redis_execution_registry") as mock_registry:
            result = await resolve_plan_approval("some-token", decision="not_a_real_decision")

        self.assertFalse(result)
        mock_registry.connect.assert_not_called()

    async def test_edit_decision_requires_no_special_handling_at_this_layer(self):
        """resolve_plan_approval() itself doesn't validate modified_plan's
        shape (that's the API layer's job, via validate_plan_shape()) --
        confirm it accepts a plan dict through for a valid 'edit' decision
        without raising, given a mocked broker that reports the token as
        known."""
        from core.execution.pev_runner import resolve_plan_approval

        mock_client = AsyncMock()
        mock_client.exists = AsyncMock(return_value=True)
        mock_client.rpush = AsyncMock()
        mock_client.expire = AsyncMock()
        mock_client.delete = AsyncMock()

        with patch("core.execution.pev_runner.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=mock_client)
            result = await resolve_plan_approval(
                "some-token", decision="edit", modified_plan={"goal": "g", "steps": [{"title": "t"}]}
            )

        self.assertTrue(result)
        mock_client.rpush.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
