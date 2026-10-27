# tests/test_step_record_unit.py
"""
A-14 / A-13: later plan steps see what earlier steps did, and the verifier sees which tools a step ran.

Found live (2026-09-21, browser debug loop): in plan/execute mode each step got only the original
request plus its own step text. The closing "summarise" step had nothing to summarise and invented
details (a `consoel.log` typo that did not exist; an `app.js` -> `todo.js` rename that never
happened; once the answer was literally "READYREADYREADYREADY"). The verifier judged a step from its
narration alone, so "file edited" without any edit could pass.

Three layers:
  1. core/execution/step_record.py -- the small record (pure functions).
  2. prompts/verifier.py + VerifierEngine.verify() -- the verifier is handed the tool lines.
  3. the real _pev_stream loop, driven with a fake agent/planner/verifier -- step 2 really receives
     step 1's record, and the verifier really receives the ledger.

Run with:
    pytest tests/test_step_record_unit.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.execution.step_record import (
    StepRecord,
    StepToolLedger,
    build_prior_steps_block,
    make_record,
    outcome_of,
)
from core.types import TextChunk, ToolCallChunk, ToolResultChunk


# ─────────────────────────────────────────────────────────────────────────────
# 1. The record
# ─────────────────────────────────────────────────────────────────────────────

class TestOutcomeOf(unittest.TestCase):

    def test_plain_success_is_ok(self):
        self.assertEqual(outcome_of({"ok": True, "result": {"path": "a.txt", "bytes_written": 3}}), "ok")

    def test_wrapper_failure_carries_the_start_of_the_message(self):
        out = outcome_of({"ok": False, "error": {"type": "validation", "message": "2 validation errors for X"}})
        self.assertTrue(out.startswith("ERROR: 2 validation errors"), out)

    def test_error_reported_inside_a_successful_wrapper_is_an_error(self):
        # Most EVA tools answer {"ok": true, "result": {"error": "..."}} for a failed operation.
        out = outcome_of({"ok": True, "result": {"error": "File not found: x.log"}})
        self.assertEqual(out, "ERROR: File not found: x.log")

    def test_nonzero_shell_exit_code_is_reported(self):
        self.assertEqual(outcome_of({"ok": True, "result": {"exit_code": 2, "stdout": "boom"}}), "exit 2")

    def test_zero_exit_code_is_ok(self):
        self.assertEqual(outcome_of({"ok": True, "result": {"exit_code": 0}}), "ok")

    def test_blocked_shell_command_is_an_error(self):
        self.assertEqual(outcome_of({"ok": True, "result": {"blocked": True, "error": ""}}), "ERROR: blocked")

    def test_mcp_error_block_is_an_error(self):
        out = outcome_of({"ok": True, "result": ["### Error\nInvalid arguments for tool"]})
        self.assertTrue(out.startswith("ERROR: Invalid arguments"), out)

    def test_mcp_success_blocks_are_ok(self):
        self.assertEqual(outcome_of({"ok": True, "result": ["### Result\n- done"]}), "ok")

    def test_long_errors_are_clipped(self):
        out = outcome_of({"ok": False, "error": "x" * 5000})
        self.assertLess(len(out), 200)

    def test_non_dict_payload_is_ok(self):
        self.assertEqual(outcome_of("whatever"), "ok")


class TestLedger(unittest.TestCase):

    def _ledger(self):
        return StepToolLedger()

    def test_one_line_names_the_tool_what_it_acted_on_and_the_outcome(self):
        led = self._ledger()
        led.add_call("background_process", {"action": "start", "command": ["python", "-m", "http.server", "8123"]}, "c1")
        led.add_result("background_process", {"ok": True, "result": {"process_id": "p1"}}, "c1")
        led.add_call("edit_file", {"path": "todo_probe/index.html", "old_string": "OLD", "new_string": "NEW" * 500}, "c2")
        led.add_result("edit_file", {"ok": True, "result": {"path": "todo_probe/index.html"}}, "c2")
        self.assertEqual(led.lines(), [
            "background_process start python -m http.server 8123 -> ok",
            "edit_file todo_probe/index.html -> ok",
        ])

    def test_arguments_bodies_and_results_are_not_recorded(self):
        led = self._ledger()
        led.add_call("edit_file", {"path": "a.html", "old_string": "SECRET-OLD", "new_string": "SECRET-NEW"}, "c1")
        led.add_result("edit_file", {"ok": True, "result": {"content": "SECRET-RESULT " * 100}}, "c1")
        rendered = led.render()
        for leaked in ("SECRET-OLD", "SECRET-NEW", "SECRET-RESULT"):
            self.assertNotIn(leaked, rendered)

    def test_a_failed_call_shows_its_error(self):
        led = self._ledger()
        led.add_call("edit_file", {"path": "a.html"}, "c1")
        led.add_result("edit_file", {"ok": True, "result": {"error": "old_string not found"}}, "c1")
        self.assertEqual(led.lines(), ["edit_file a.html -> ERROR: old_string not found"])

    def test_describe_tool_is_left_out(self):
        led = self._ledger()
        led.add_call("describe_tool", {"name": "shell"}, "c1")
        led.add_result("describe_tool", {"ok": True, "result": {}}, "c1")
        self.assertEqual(led.lines(), [])
        self.assertEqual(led.call_count, 0)

    def test_no_calls_says_so_explicitly(self):
        self.assertEqual(self._ledger().render(), "(no tools were called in this step)")

    def test_a_call_without_a_result_is_marked_interrupted(self):
        led = self._ledger()
        led.add_call("shell", {"cmd": ["npm", "test"]}, "c1")
        self.assertEqual(led.lines(), ["shell npm test -> no result (interrupted)"])

    def test_a_result_whose_call_was_never_seen_is_still_listed(self):
        led = self._ledger()
        led.add_result("read_file", {"ok": True, "result": {}}, "zzz")
        self.assertEqual(led.lines(), ["read_file -> ok"])

    def test_consecutive_identical_calls_collapse(self):
        led = self._ledger()
        for i in range(3):
            led.add_call("browser_navigate", {"url": "http://x/"}, f"c{i}")
            led.add_result("browser_navigate", {"ok": True, "result": []}, f"c{i}")
        self.assertEqual(led.lines(), ["browser_navigate http://x/ -> ok (x3)"])

    def test_only_the_most_recent_lines_are_kept_and_the_cut_is_stated(self):
        led = self._ledger()
        for i in range(40):
            led.add_call("read_file", {"path": f"f{i}.txt"}, f"c{i}")
            led.add_result("read_file", {"ok": True, "result": {}}, f"c{i}")
        lines = led.lines()
        self.assertEqual(len(lines), 26)  # 25 kept + the marker
        self.assertTrue(lines[0].startswith("(15 earlier tool calls not shown)"), lines[0])
        self.assertEqual(lines[-1], "read_file f39.txt -> ok")

    def test_target_falls_back_to_the_first_short_argument(self):
        led = self._ledger()
        led.add_call("weird_tool", {"flag": True, "thing": "widget-7"}, "c1")
        led.add_result("weird_tool", {"ok": True, "result": {}}, "c1")
        self.assertEqual(led.lines(), ["weird_tool widget-7 -> ok"])

    def test_target_is_clipped(self):
        led = self._ledger()
        led.add_call("shell", {"cmd": ["echo"] + ["x" * 40] * 10}, "c1")
        led.add_result("shell", {"ok": True, "result": {}}, "c1")
        self.assertLess(len(led.lines()[0]), 130)


def _rec(n, title="t", verdict="passed", text="did it", tools=None, issues=None):
    return StepRecord(number=n, title=title, verdict=verdict, text=text, tool_lines=tools or [], issues=issues)


class TestPriorStepsBlock(unittest.TestCase):

    def test_first_step_gets_nothing(self):
        self.assertEqual(build_prior_steps_block([]), "")

    def test_block_carries_text_tools_and_verdict(self):
        block = build_prior_steps_block([
            _rec(1, "Start the server", text="Server started, process id p1",
                 tools=["background_process start python -m http.server 8123 -> ok"]),
        ])
        self.assertIn("Step 1 — Start the server [verified]", block)
        self.assertIn("process id p1", block)
        self.assertIn("background_process start python -m http.server 8123 -> ok", block)

    def test_a_failed_step_is_marked_and_shows_what_the_verifier_found(self):
        block = build_prior_steps_block([_rec(2, "Edit the file", verdict="failed", issues="no edit call was made")])
        self.assertIn("FAILED verification", block)
        self.assertIn("no edit call was made", block)

    def test_unverified_is_marked(self):
        self.assertIn("not verified", build_prior_steps_block([_rec(1, verdict="unverified")]))

    def test_a_step_with_no_tools_says_none(self):
        self.assertIn("Tools it ran: none", build_prior_steps_block([_rec(1)]))

    def test_over_budget_cuts_the_oldest_steps_down_first_and_keeps_the_newest_whole(self):
        recs = [_rec(i, f"Step title {i}", text=("finding-%d " % i) * 60, tools=[f"tool{i} x -> ok"]) for i in range(1, 9)]
        block = build_prior_steps_block(recs, max_chars=2500)
        self.assertLessEqual(len(block), 2600)
        self.assertIn("Tools it ran:", block)           # some step is still shown in full
        newest = block.split("Step 8")[1]
        self.assertIn("tool8 x -> ok", newest)           # the newest one is complete
        self.assertNotIn("tool1 x -> ok", block)         # the oldest lost its tool lines

    def test_extremely_small_budget_drops_the_oldest_with_a_note(self):
        recs = [_rec(i, text="x " * 300) for i in range(1, 6)]
        block = build_prior_steps_block(recs, max_chars=700)
        self.assertIn("earlier step(s) omitted", block)
        self.assertIn("Step 5", block)


class TestMakeRecord(unittest.TestCase):

    def test_text_is_trimmed_and_tools_come_from_the_ledger(self):
        led = StepToolLedger()
        led.add_call("read_file", {"path": "a"}, "c1")
        led.add_result("read_file", {"ok": True, "result": {}}, "c1")
        rec = make_record({"index": 2, "title": "Read it"}, output_text="word " * 2000, ledger=led, verdict="passed")
        self.assertEqual(rec.number, 3)
        self.assertLessEqual(len(rec.text), 1200)
        self.assertEqual(rec.tool_lines, ["read_file a -> ok"])
        self.assertIsNone(rec.issues)


# ─────────────────────────────────────────────────────────────────────────────
# 2. The verifier is handed the ledger
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifierPromptAndCall(unittest.IsolatedAsyncioTestCase):

    def test_template_has_a_tool_activity_section_before_the_output(self):
        from prompts.verifier import VERIFIER_USER_TEMPLATE
        self.assertIn("{tool_activity}", VERIFIER_USER_TEMPLATE)
        self.assertLess(VERIFIER_USER_TEMPLATE.index("{tool_activity}"),
                        VERIFIER_USER_TEMPLATE.index("{execution_output}"))

    def test_template_has_a_prior_steps_section_before_the_tool_activity(self):
        # A-16: the earlier-steps block must land before this step's own ledger,
        # same ordering the step's own execution prompt uses.
        from prompts.verifier import VERIFIER_USER_TEMPLATE
        self.assertIn("{prior_steps_section}", VERIFIER_USER_TEMPLATE)
        self.assertLess(VERIFIER_USER_TEMPLATE.index("{prior_steps_section}"),
                        VERIFIER_USER_TEMPLATE.index("{tool_activity}"))

    def test_system_prompt_requires_a_matching_successful_call_for_changes(self):
        from prompts.verifier import VERIFIER_SYSTEM_PROMPT
        text = " ".join(VERIFIER_SYSTEM_PROMPT.split())
        self.assertIn("matching call that succeeded", text)
        self.assertIn("needs no tool call", text)          # read-only/answer steps are not punished
        self.assertIn("(not recorded)", text)              # graceful when the list is unavailable

    def test_system_prompt_lets_an_earlier_step_satisfy_this_ones_goal(self):
        # A-16: a step with an empty ledger of its own must not be auto-failed
        # when an earlier step's record already shows the real work succeeded --
        # but rule 8's evidence requirement must still be referenced, not dropped.
        from prompts.verifier import VERIFIER_SYSTEM_PROMPT
        text = " ".join(VERIFIER_SYSTEM_PROMPT.split())
        self.assertIn("Work already done in earlier steps", text)
        self.assertIn("does not relax rule 8", text)

    async def _run_verify(self, **extra):
        from core.execution.verifier import VerifierEngine
        captured = {}

        async def fake_call(**kwargs):
            captured["messages"] = kwargs["messages"]
            return '{"passed": true}'

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=fake_call):
            result = await VerifierEngine().verify(
                step={"title": "Edit the file", "description": "fix it", "index": 0},
                execution_output="Edited it.", user_id="u", **extra)
        return result, captured["messages"][0]["content"]

    async def test_the_ledger_reaches_the_verifier_prompt(self):
        result, content = await self._run_verify(tool_activity="- edit_file a.html -> ok")
        self.assertTrue(result.passed)
        self.assertIn("- edit_file a.html -> ok", content)
        self.assertIn("Edited it.", content)

    async def test_callers_that_pass_no_ledger_still_work(self):
        result, content = await self._run_verify()
        self.assertTrue(result.passed)
        self.assertIn("(not recorded)", content)

    async def test_the_prior_steps_block_reaches_the_verifier_prompt(self):
        # A-16
        block = "## Work already done in earlier steps of this plan\n\nStep 1 — Fix the file [verified]"
        result, content = await self._run_verify(
            tool_activity="(no tools were called in this step)",
            prior_steps_block=block,
        )
        self.assertTrue(result.passed)
        self.assertIn("Step 1 — Fix the file [verified]", content)
        # It lands before the current step's own (empty) ledger, same order as the template.
        self.assertLess(content.index("Step 1 — Fix the file"), content.index("(no tools were called"))

    async def test_callers_that_pass_no_prior_steps_block_still_work(self):
        # "" (first step, or a caller that predates A-16) must not leave a stray empty section.
        result, content = await self._run_verify()
        self.assertTrue(result.passed)
        self.assertNotIn("Work already done", content)


# ─────────────────────────────────────────────────────────────────────────────
# 2b. A-21: the look-ahead skip check bundled into the same verifier call
# ─────────────────────────────────────────────────────────────────────────────

class TestLookaheadSkip(unittest.IsolatedAsyncioTestCase):

    def test_template_has_a_remaining_steps_section_before_the_tool_activity(self):
        from prompts.verifier import VERIFIER_USER_TEMPLATE
        self.assertIn("{remaining_steps_section}", VERIFIER_USER_TEMPLATE)
        self.assertLess(VERIFIER_USER_TEMPLATE.index("{remaining_steps_section}"),
                        VERIFIER_USER_TEMPLATE.index("{tool_activity}"))

    def test_system_prompt_requires_a_contiguous_run_and_real_evidence(self):
        from prompts.verifier import VERIFIER_SYSTEM_PROMPT
        text = " ".join(VERIFIER_SYSTEM_PROMPT.split())
        self.assertIn("Steps planned after this one", text)
        self.assertIn("gap never counts", text)
        self.assertIn("Only relevant when \"passed\" is true", text)

    async def _run_verify(self, llm_reply, remaining_steps=None, **extra):
        from core.execution.verifier import VerifierEngine
        captured = {}

        async def fake_call(**kwargs):
            captured["messages"] = kwargs["messages"]
            return llm_reply

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=fake_call):
            result = await VerifierEngine().verify(
                step={"title": "Fix the bug", "description": "fix it", "index": 0},
                execution_output="Fixed it and confirmed step 2 also works.", user_id="u",
                remaining_steps=remaining_steps, **extra)
        return result, captured["messages"][0]["content"]

    async def test_a_real_skip_claim_is_parsed_and_clamped_to_the_given_steps(self):
        remaining = [{"title": "Confirm the fix", "description": "d"}]
        result, content = await self._run_verify(
            '{"passed": true, "also_satisfies_count": 1, "also_satisfies_reason": "same shell run also covers it"}',
            remaining_steps=remaining,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.also_satisfies_count, 1)
        self.assertEqual(result.also_satisfies_reason, "same shell run also covers it")
        self.assertIn("Confirm the fix", content)

    async def test_a_count_larger_than_the_given_steps_is_clamped_not_trusted(self):
        remaining = [{"title": "Step A", "description": "d"}]
        result, _ = await self._run_verify(
            '{"passed": true, "also_satisfies_count": 99, "also_satisfies_reason": "r"}',
            remaining_steps=remaining,
        )
        self.assertEqual(result.also_satisfies_count, 1)  # clamped to len(remaining_steps), not 99

    async def test_a_failed_steps_claimed_skip_is_never_honoured(self):
        # Defence in depth: even if a malformed/adversarial response claims a
        # skip on a step that itself failed, the code-level guard zeroes it --
        # not left to the prompt's own discipline alone.
        remaining = [{"title": "Step A", "description": "d"}]
        result, _ = await self._run_verify(
            '{"passed": false, "issues": "broke", "also_satisfies_count": 3, "also_satisfies_reason": "r"}',
            remaining_steps=remaining,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.also_satisfies_count, 0)
        self.assertIsNone(result.also_satisfies_reason)

    async def test_no_remaining_steps_given_means_no_skip_regardless_of_the_reply(self):
        result, content = await self._run_verify(
            '{"passed": true, "also_satisfies_count": 2, "also_satisfies_reason": "r"}',
            remaining_steps=None,
        )
        self.assertEqual(result.also_satisfies_count, 0)
        self.assertNotIn("Steps planned after this one", content)

    async def test_an_empty_remaining_steps_list_means_no_skip(self):
        result, _ = await self._run_verify(
            '{"passed": true, "also_satisfies_count": 2, "also_satisfies_reason": "r"}',
            remaining_steps=[],
        )
        self.assertEqual(result.also_satisfies_count, 0)

    async def test_a_plain_pass_with_no_skip_fields_defaults_to_zero(self):
        remaining = [{"title": "Step A", "description": "d"}]
        result, _ = await self._run_verify('{"passed": true}', remaining_steps=remaining)
        self.assertEqual(result.also_satisfies_count, 0)
        self.assertIsNone(result.also_satisfies_reason)

    async def test_a_zero_or_negative_count_is_treated_as_no_skip(self):
        remaining = [{"title": "Step A", "description": "d"}]
        result, _ = await self._run_verify(
            '{"passed": true, "also_satisfies_count": 0, "also_satisfies_reason": "r"}',
            remaining_steps=remaining,
        )
        self.assertEqual(result.also_satisfies_count, 0)
        self.assertIsNone(result.also_satisfies_reason)

    async def test_multiple_remaining_steps_are_all_listed_in_order(self):
        remaining = [
            {"title": "Step A", "description": "desc A"},
            {"title": "Step B", "description": "desc B"},
        ]
        _, content = await self._run_verify('{"passed": true}', remaining_steps=remaining)
        self.assertLess(content.index("Step A"), content.index("Step B"))


# ─────────────────────────────────────────────────────────────────────────────
# 3. The real step loop
# ─────────────────────────────────────────────────────────────────────────────

class _FakeAgent:
    """Yields a scripted list of chunks per call and remembers the messages it was given."""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = []

    async def call(self, messages, stream=True):
        self.calls.append(messages)
        for chunk in self._scripts[len(self.calls) - 1]:
            yield chunk


def _plan(*titles):
    return {"goal": "fix the page", "steps": [
        {"index": i, "title": t, "description": f"do: {t}", "status": "pending"} for i, t in enumerate(titles)]}


async def _drive(agent, plan, verdicts):
    """Run the real _pev_stream; returns (chunks, verifier_calls)."""
    from core.execution import pev_runner
    from core.execution.verifier import VerificationResult

    verifier_calls = []
    verdict_iter = iter(verdicts)

    async def fake_verify(**kwargs):
        verifier_calls.append(kwargs)
        passed, issues = next(verdict_iter)
        return VerificationResult(passed=passed, summary="s", issues=issues)

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    repo = MagicMock()
    for name in ("create", "update_steps", "update_status", "update_goal_verification"):
        setattr(repo, name, AsyncMock())

    execution = MagicMock(execution_id="e1", conversation_id="c1")
    execution.cancel_token = MagicMock(cancelled=False)
    from core.execution.models import ExecutionStatus
    execution.status = ExecutionStatus.RUNNING

    chunks = []
    with patch.object(pev_runner.planner_engine, "generate", new=AsyncMock(return_value=plan)), \
         patch.object(pev_runner.verifier_engine, "verify", new=fake_verify), \
         patch.object(pev_runner, "PlanRepo", repo), \
         patch.object(pev_runner, "AsyncSessionLocal", return_value=session_cm):
        async for chunk in pev_runner._pev_stream(
            execution=execution, agent=agent,
            base_messages=[{"role": "user", "content": "debug my page"}],
            user_id="u", require_plan_approval=False,
        ):
            chunks.append(chunk)
    return chunks, verifier_calls


def _last_user_text(messages):
    return messages[-1]["content"]


class TestRealStepLoop(unittest.IsolatedAsyncioTestCase):

    def _two_step_agent(self):
        return _FakeAgent([
            [ToolCallChunk(tool_name="background_process", arguments={"action": "start", "command": ["python", "-m", "http.server", "8123"]}, call_id="c1"),
             ToolResultChunk(call_id="c1", tool_name="background_process", result={"ok": True, "result": {"process_id": "p-42"}}),
             TextChunk(content="Server started, process id p-42.")],
            [TextChunk(content="Summary written.")],
        ])

    async def test_step_two_is_told_what_step_one_did(self):
        agent = self._two_step_agent()
        await _drive(agent, _plan("Start server", "Summarise"), [(True, None), (True, None)])
        step1_prompt, step2_prompt = _last_user_text(agent.calls[0]), _last_user_text(agent.calls[1])
        self.assertNotIn("Work already done", step1_prompt)          # nothing before step 1
        self.assertIn("Work already done in earlier steps", step2_prompt)
        self.assertIn("Server started, process id p-42.", step2_prompt)
        self.assertIn("background_process start python -m http.server 8123 -> ok", step2_prompt)
        self.assertIn("[verified]", step2_prompt)

    async def test_the_summary_step_is_told_not_to_invent_details(self):
        # A-19: "Summarise" is the LAST step of this 2-step plan, so it now
        # gets the closing-synthesis instruction (_FINAL_STEP_CLOSING_INSTRUCTION)
        # instead of the plain per-step one -- its own anti-hallucination
        # rule, worded for the whole plan rather than just this one step.
        agent = self._two_step_agent()
        await _drive(agent, _plan("Start server", "Summarise"), [(True, None), (True, None)])
        self.assertIn("Do not state a result, number, or file content you did not see",
                      _last_user_text(agent.calls[1]))

    async def test_the_original_conversation_is_still_first(self):
        agent = self._two_step_agent()
        await _drive(agent, _plan("Start server", "Summarise"), [(True, None), (True, None)])
        self.assertEqual(agent.calls[1][0], {"role": "user", "content": "debug my page"})

    async def test_the_verifier_is_handed_the_tool_lines_of_the_step(self):
        agent = self._two_step_agent()
        _, verifier_calls = await _drive(agent, _plan("Start server", "Summarise"), [(True, None), (True, None)])
        self.assertIn("background_process start python -m http.server 8123 -> ok", verifier_calls[0]["tool_activity"])
        self.assertEqual(verifier_calls[1]["tool_activity"], "(no tools were called in this step)")

    async def test_the_verifier_gets_the_same_prior_steps_block_the_step_itself_got(self):
        # A-16: step 2's verifier call must see step 1's record, exactly like step 2's own
        # execution prompt does (test_step_two_is_told_what_step_one_did) -- so a step that made
        # no tool calls because step 1 already did the work isn't judged as if nothing happened.
        agent = self._two_step_agent()
        _, verifier_calls = await _drive(agent, _plan("Start server", "Summarise"), [(True, None), (True, None)])
        self.assertEqual(verifier_calls[0]["prior_steps_block"], "")  # nothing before step 1
        self.assertIn("Work already done in earlier steps", verifier_calls[1]["prior_steps_block"])
        self.assertIn("Server started, process id p-42.", verifier_calls[1]["prior_steps_block"])
        self.assertIn("background_process start python -m http.server 8123 -> ok",
                      verifier_calls[1]["prior_steps_block"])

    async def test_a_step_that_only_narrates_is_shown_to_the_verifier_as_having_run_no_tools(self):
        # The A-13 case: the text claims an edit, no edit call exists.
        agent = _FakeAgent([[TextChunk(content="I edited index.html and fixed the bug.")]])
        _, verifier_calls = await _drive(agent, _plan("Edit the file"), [(False, "no edit call was made")])
        self.assertEqual(verifier_calls[0]["tool_activity"], "(no tools were called in this step)")
        self.assertIn("I edited index.html", verifier_calls[0]["execution_output"])

    async def test_a_failed_step_is_carried_forward_with_what_the_verifier_found(self):
        agent = _FakeAgent([
            [TextChunk(content="Fixed it.")],
            [TextChunk(content="Retrying the edit.")],
            [TextChunk(content="Done.")],
        ])
        planner_replan = AsyncMock(return_value=None)
        from core.execution import pev_runner
        with patch.object(pev_runner, "MAX_REPLAN_ATTEMPTS", 0), patch.object(pev_runner, "_MAX_REPLAN_ATTEMPTS", 0):
            await _drive(agent, _plan("Edit the file", "Check it"), [(False, "no edit call was made"), (True, None)])
        step2_prompt = _last_user_text(agent.calls[1])
        self.assertIn("FAILED verification", step2_prompt)
        self.assertIn("no edit call was made", step2_prompt)

    async def test_tool_errors_are_carried_forward_so_the_next_step_does_not_assume_success(self):
        agent = _FakeAgent([
            [ToolCallChunk(tool_name="edit_file", arguments={"path": "a.html"}, call_id="c1"),
             ToolResultChunk(call_id="c1", tool_name="edit_file", result={"ok": True, "result": {"error": "old_string not found"}}),
             TextChunk(content="Tried to edit.")],
            [TextChunk(content="ok")],
        ])
        await _drive(agent, _plan("Edit", "Check"), [(True, None), (True, None)])
        self.assertIn("edit_file a.html -> ERROR: old_string not found", _last_user_text(agent.calls[1]))


if __name__ == "__main__":
    unittest.main()
