# tests/test_planner_success_criteria_unit.py
"""
A-20: the plan schema gained a "success_criteria" field (1-4 checkable
statements of what "done" means for this plan), so PlanVerifierEngine has
something concrete to judge the finished plan against instead of a vague
"did it work" free-for-all.

Covers:
  1. prompts/planner.py -- the new field/rule/examples actually landed in
     the prompt the LLM sees, in the right place (before "steps", so a
     streaming/partial JSON parse still sees it early).
  2. core/execution/planner.py::_normalise_success_criteria() -- the shared
     permissive-fallback helper both the LLM-output path and the
     user-edit path use.
  3. PlannerEngine.generate()/_normalise_plan() -- the permissive path (a
     flaky LLM never blocks the PEV loop).
  4. validate_plan_shape() -- the strict user-edit path, EXCEPT for this one
     field, which stays permissive on purpose (see its own docstring note).
"""

from __future__ import annotations

import unittest


class TestPlanningPromptHasSuccessCriteria(unittest.TestCase):

    def test_the_schema_block_includes_success_criteria_before_steps(self):
        from prompts.planner import PLANNING_SYSTEM_PROMPT
        self.assertIn('"success_criteria"', PLANNING_SYSTEM_PROMPT)
        self.assertLess(
            PLANNING_SYSTEM_PROMPT.index('"success_criteria"'),
            PLANNING_SYSTEM_PROMPT.index('"steps"'),
        )

    def test_the_rule_requires_checkable_not_subjective_criteria(self):
        from prompts.planner import PLANNING_SYSTEM_PROMPT
        text = " ".join(PLANNING_SYSTEM_PROMPT.split())
        self.assertIn("checkable from evidence", text)
        self.assertIn("never a subjective judgment", text)


class TestNormaliseSuccessCriteria(unittest.TestCase):

    def _norm(self, raw, goal="the goal"):
        from core.execution.planner import _normalise_success_criteria
        return _normalise_success_criteria(raw, goal)

    def test_a_clean_list_passes_through(self):
        self.assertEqual(
            self._norm(["exit code 0", "no console errors"]),
            ["exit code 0", "no console errors"],
        )

    def test_missing_falls_back_to_the_goal(self):
        self.assertEqual(self._norm(None, goal="build the app"), ["build the app"])

    def test_not_a_list_falls_back_to_the_goal(self):
        self.assertEqual(self._norm("not a list", goal="build the app"), ["build the app"])

    def test_empty_list_falls_back_to_the_goal(self):
        self.assertEqual(self._norm([], goal="build the app"), ["build the app"])

    def test_blank_and_non_string_entries_are_dropped(self):
        self.assertEqual(self._norm(["real one", "", "   ", 42, None]), ["real one"])

    def test_all_entries_blank_falls_back_to_the_goal(self):
        self.assertEqual(self._norm(["", "   "], goal="build the app"), ["build the app"])

    def test_entries_are_stripped(self):
        self.assertEqual(self._norm(["  spaced  "]), ["spaced"])

    def test_more_than_four_is_capped_at_four(self):
        result = self._norm([f"criterion {i}" for i in range(10)])
        self.assertEqual(len(result), 4)
        self.assertEqual(result, ["criterion 0", "criterion 1", "criterion 2", "criterion 3"])


class TestNormalisePlanCarriesSuccessCriteria(unittest.TestCase):

    def test_generate_returns_success_criteria_from_the_llm(self):
        from core.execution.planner import _normalise_plan
        raw = {
            "goal": "Build a calculator",
            "success_criteria": ["add(2,3) returns 5", "the script exits 0"],
            "steps": [{"title": "Write it", "description": "d"}],
        }
        plan = _normalise_plan(raw)
        self.assertEqual(plan["success_criteria"], ["add(2,3) returns 5", "the script exits 0"])

    def test_generate_falls_back_when_the_llm_omits_it(self):
        from core.execution.planner import _normalise_plan
        raw = {"goal": "Build a calculator", "steps": [{"title": "Write it", "description": "d"}]}
        plan = _normalise_plan(raw)
        self.assertEqual(plan["success_criteria"], ["Build a calculator"])

    def test_the_module_level_fallback_plan_has_success_criteria_too(self):
        from core.execution.planner import _FALLBACK_PLAN
        self.assertIn("success_criteria", _FALLBACK_PLAN)
        self.assertTrue(_FALLBACK_PLAN["success_criteria"])


class TestValidatePlanShapeSuccessCriteria(unittest.TestCase):

    def _plan(self, **overrides):
        base = {
            "goal": "Build a calculator",
            "steps": [{"title": "Write it", "description": "d"}],
        }
        base.update(overrides)
        return base

    def test_a_plan_with_no_success_criteria_is_still_accepted(self):
        # A-20: deliberately permissive here -- the approval UI never shows
        # this field, so a user editing a plan has no way to supply it.
        from core.execution.planner import validate_plan_shape
        plan = validate_plan_shape(self._plan())
        self.assertEqual(plan["success_criteria"], ["Build a calculator"])

    def test_a_plan_with_real_success_criteria_keeps_them(self):
        from core.execution.planner import validate_plan_shape
        plan = validate_plan_shape(self._plan(success_criteria=["tests pass"]))
        self.assertEqual(plan["success_criteria"], ["tests pass"])

    def test_a_malformed_success_criteria_field_does_not_raise(self):
        # Unlike every other field in this function, a bad success_criteria
        # value degrades to the goal instead of raising PlanValidationError --
        # confirms it really is exempt from the "intentionally strict" policy.
        from core.execution.planner import validate_plan_shape
        plan = validate_plan_shape(self._plan(success_criteria="not a list"))
        self.assertEqual(plan["success_criteria"], ["Build a calculator"])

    def test_other_fields_are_still_strictly_validated(self):
        from core.execution.planner import validate_plan_shape, PlanValidationError
        with self.assertRaises(PlanValidationError):
            validate_plan_shape(self._plan(goal=""))


if __name__ == "__main__":
    unittest.main()
