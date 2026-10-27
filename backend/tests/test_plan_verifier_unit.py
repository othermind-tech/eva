# tests/test_plan_verifier_unit.py
"""
A-20: PlanVerifierEngine judges a FINISHED plan as a whole against its own
goal/success criteria -- the check A-16's per-step verifier structurally
cannot do (it never sees more than one step at a time). One call per plan
run, tri-state verdict (success/pending/not_successful), fail-closed to
"pending" on any infra error -- mirrors VerifierEngine's V4.41/A-08 policy
(never silently claim success), extended to a third state that means
"couldn't be determined" rather than forcing a binary guess either way.

Covers:
  1. Fallback policy: no session configured, LLM call raises, unparseable
     JSON, an invalid verdict string -- all "pending", infra_error=True.
  2. The real prompt-building + call path (mirrors
     test_step_record_unit.py's TestVerifierPromptAndCall pattern).
  3. Message-required enforcement for non-"success" verdicts.
  4. "success" never carries a message (redundant with the plan's own
     closing summary).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestPlanVerifierFallbackPolicy(unittest.IsolatedAsyncioTestCase):

    async def test_no_provider_configured_falls_back_to_pending(self):
        from core.execution.plan_verifier import PlanVerifierEngine
        with patch("config.settings.get_user_session", return_value={"provider": "", "model": ""}):
            result = await PlanVerifierEngine().verify_plan(
                goal="g", success_criteria=["c"], step_record_trail="t",
                draft_closing_summary="s", user_id="u",
            )
        self.assertEqual(result.verdict, "pending")
        self.assertTrue(result.infra_error)
        self.assertTrue(result.message)  # never an empty string on a fallback

    async def test_llm_call_raising_falls_back_to_pending(self):
        from core.execution.plan_verifier import PlanVerifierEngine

        async def boom(**kwargs):
            raise RuntimeError("provider down")

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=boom):
            result = await PlanVerifierEngine().verify_plan(
                goal="g", success_criteria=["c"], step_record_trail="t",
                draft_closing_summary="s", user_id="u",
            )
        self.assertEqual(result.verdict, "pending")
        self.assertTrue(result.infra_error)

    async def test_unparseable_json_falls_back_to_pending(self):
        from core.execution.plan_verifier import PlanVerifierEngine

        async def bad_json(**kwargs):
            return "this is not json"

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=bad_json):
            result = await PlanVerifierEngine().verify_plan(
                goal="g", success_criteria=["c"], step_record_trail="t",
                draft_closing_summary="s", user_id="u",
            )
        self.assertEqual(result.verdict, "pending")
        self.assertTrue(result.infra_error)

    async def test_an_invalid_verdict_string_falls_back_to_pending(self):
        from core.execution.plan_verifier import PlanVerifierEngine

        async def bogus_verdict(**kwargs):
            return '{"verdict": "maybe", "criteria_results": [], "message": ""}'

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=bogus_verdict):
            result = await PlanVerifierEngine().verify_plan(
                goal="g", success_criteria=["c"], step_record_trail="t",
                draft_closing_summary="s", user_id="u",
            )
        # Not a crash, not silently "success" -- falls back the same as any
        # other malformed-response case.
        self.assertEqual(result.verdict, "pending")
        self.assertTrue(result.infra_error)


class TestPlanVerifierRealCall(unittest.IsolatedAsyncioTestCase):

    async def _verify(self, llm_reply, **extra):
        from core.execution.plan_verifier import PlanVerifierEngine
        captured = {}

        async def fake_call(**kwargs):
            captured["messages"] = kwargs["messages"]
            captured["max_tokens"] = kwargs["max_tokens"]
            return llm_reply

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=fake_call):
            result = await PlanVerifierEngine().verify_plan(
                goal="Build a calculator", success_criteria=["add(2,3)==5", "exits 0"],
                step_record_trail="Step 1 -- wrote calc_app.py [verified]",
                draft_closing_summary="Built it and it works.",
                user_id="u", **extra,
            )
        return result, captured["messages"][0]["content"]

    async def test_the_goal_and_criteria_and_trail_all_reach_the_prompt(self):
        _, content = await self._verify('{"verdict": "success", "criteria_results": [], "message": ""}')
        self.assertIn("Build a calculator", content)
        self.assertIn("add(2,3)==5", content)
        self.assertIn("Step 1 -- wrote calc_app.py", content)
        self.assertIn("Built it and it works.", content)

    async def test_success_verdict_carries_no_message(self):
        result, _ = await self._verify(
            '{"verdict": "success", "criteria_results": [{"criterion": "add(2,3)==5", "met": true}], "message": ""}'
        )
        self.assertEqual(result.verdict, "success")
        self.assertEqual(result.message, "")
        self.assertFalse(result.infra_error)

    async def test_not_successful_verdict_keeps_its_message(self):
        result, _ = await self._verify(
            '{"verdict": "not_successful", '
            '"criteria_results": [{"criterion": "add(2,3)==5", "met": false}], '
            '"message": "The multiply function was never implemented."}'
        )
        self.assertEqual(result.verdict, "not_successful")
        self.assertEqual(result.message, "The multiply function was never implemented.")

    async def test_pending_verdict_keeps_its_message(self):
        result, _ = await self._verify(
            '{"verdict": "pending", '
            '"criteria_results": [{"criterion": "looks right", "met": null}], '
            '"message": "Please confirm the UI looks correct -- I can\'t check that myself."}'
        )
        self.assertEqual(result.verdict, "pending")
        self.assertIn("confirm", result.message)
        self.assertFalse(result.infra_error)  # a genuine model-returned pending, not a fallback

    async def test_a_non_success_verdict_with_no_message_gets_a_safe_default(self):
        # The model followed the letter (valid verdict) but not rule 6
        # (message required on non-success) -- must not reach the user as "".
        result, _ = await self._verify(
            '{"verdict": "not_successful", "criteria_results": [], "message": ""}'
        )
        self.assertEqual(result.verdict, "not_successful")
        self.assertTrue(result.message.strip())

    async def test_criteria_results_are_preserved_in_order(self):
        result, _ = await self._verify(
            '{"verdict": "not_successful", "criteria_results": ['
            '{"criterion": "add(2,3)==5", "met": true}, '
            '{"criterion": "exits 0", "met": false}], '
            '"message": "The exit code was 1, not 0."}'
        )
        self.assertEqual(result.criteria_results[0]["met"], True)
        self.assertEqual(result.criteria_results[1]["met"], False)

    async def test_max_tokens_defaults_to_800_and_is_overridable(self):
        from core.execution.plan_verifier import PlanVerifierEngine
        captured = {}

        async def fake_call(**kwargs):
            captured["max_tokens"] = kwargs["max_tokens"]
            return '{"verdict": "success", "criteria_results": [], "message": ""}'

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=fake_call):
            await PlanVerifierEngine().verify_plan(
                goal="g", success_criteria=["c"], step_record_trail="t",
                draft_closing_summary="s", user_id="u",
            )
        self.assertEqual(captured["max_tokens"], 800)

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=fake_call):
            await PlanVerifierEngine().verify_plan(
                goal="g", success_criteria=["c"], step_record_trail="t",
                draft_closing_summary="s", user_id="u", max_tokens=1500,
            )
        self.assertEqual(captured["max_tokens"], 1500)


if __name__ == "__main__":
    unittest.main()
