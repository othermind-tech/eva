# tests/test_replan_and_cwd_unit.py
"""
Two defects found by the same live run (2026-09-21, `gpt-oss:120b`, browser debug loop) -- the
second one caused the first.

1. A relative `cwd` handed to background_process / shell was passed to the OS as-is, so it resolved
   against the EVA server's own folder instead of the conversation's or project's folder. `cwd="todo_probe"`
   failed with "[WinError 267] The directory name is invalid" (the folder exists -- in the conversation
   folder), while `cwd="."` "worked" only because the server's folder exists. The tool description
   promised file-tool semantics; the code did not deliver them.

2. That failure made plan step 1 fail verification, and the RE-PLAN returned a single step (a retry of
   step 1) instead of the rest of the goal. The runner replaces the failed step and everything after it
   with what replan() returns, so once that one step passed the plan was over: the run finished
   "completed" with the browser, fix and clean-up steps never attempted.

Run with:
    pytest tests/test_replan_and_cwd_unit.py -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# 1. relative cwd
# ─────────────────────────────────────────────────────────────────────────────

class TestRelativeCwdResolution(unittest.TestCase):

    def _resolve(self, explicit, **kw):
        from core.shell.executor import ShellExecutor
        return ShellExecutor._resolve_cwd(explicit=explicit, session_cwd="", user_id="cwd-user", **kw)

    def test_absolute_cwd_is_untouched(self):
        p = os.path.abspath(tempfile.gettempdir())
        self.assertEqual(self._resolve(p, conversation_id="c1"), p)

    def test_relative_cwd_resolves_into_the_project_workspace_first(self):
        project = os.path.abspath(tempfile.mkdtemp())
        self.assertEqual(self._resolve("todo_probe", conversation_id="c1", project_workspace=project),
                         os.path.normpath(os.path.join(project, "todo_probe")))

    def test_relative_cwd_resolves_into_the_conversation_folder_without_a_project(self):
        from utils.workspace import get_conversation_dir
        base = str(get_conversation_dir("cwd-user", "c-rel"))
        self.assertEqual(self._resolve("todo_probe", conversation_id="c-rel"),
                         os.path.normpath(os.path.join(base, "todo_probe")))

    def test_dot_means_the_base_folder_itself_not_the_servers_folder(self):
        project = os.path.abspath(tempfile.mkdtemp())
        self.assertEqual(self._resolve(".", project_workspace=project), os.path.normpath(project))
        self.assertNotEqual(os.path.normcase(self._resolve(".", project_workspace=project)),
                            os.path.normcase(os.getcwd()))

    def test_no_explicit_cwd_still_falls_back_as_before(self):
        project = os.path.abspath(tempfile.mkdtemp())
        self.assertEqual(self._resolve(None, project_workspace=project), project)


class TestRelativeCwdReachesTheProcess(unittest.IsolatedAsyncioTestCase):
    """Real process: a relative cwd must start the command inside the conversation's subfolder."""

    USER = "cwd-live-user"

    async def test_shell_runs_in_a_relative_cwd_under_the_conversation_folder(self):
        from core.shell.executor import ShellExecutor
        from utils.workspace import get_conversation_dir, get_user_root

        conv = f"conv-{uuid.uuid4().hex[:8]}"
        site = os.path.join(str(get_conversation_dir(self.USER, conv)), "site")
        os.makedirs(site, exist_ok=True)
        try:
            result = await ShellExecutor.run(
                cmd=[sys.executable, "-c", "import os; print(os.getcwd())"],
                conversation_id=conv, user_id=self.USER, explicit_cwd="site", timeout=20,
            )
            self.assertEqual(result["exit_code"], 0, result)
            self.assertEqual(os.path.normcase(os.path.normpath(result["stdout"].strip())),
                             os.path.normcase(os.path.normpath(site)))
        finally:
            shutil.rmtree(get_user_root(self.USER), ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 2. replan keeps the rest of the goal
# ─────────────────────────────────────────────────────────────────────────────

def _steps(*titles, start=0):
    return [{"index": start + i, "title": t, "description": f"do {t}", "status": "pending", "tool_hint": None}
            for i, t in enumerate(titles)]


class TestKeepRemainingSteps(unittest.TestCase):

    def _keep(self, new_titles, remaining_titles, first_index=0):
        from core.execution.planner import _keep_remaining_steps
        return _keep_remaining_steps(_steps(*new_titles, start=first_index), _steps(*remaining_titles, start=1), first_index)

    def test_a_retry_only_replan_gets_the_original_remaining_steps_back(self):
        out = self._keep(["Retry start server"], ["Open page", "Fix file", "Verify", "Stop server"])
        self.assertEqual([s["title"] for s in out],
                         ["Retry start server", "Open page", "Fix file", "Verify", "Stop server"])

    def test_restored_steps_are_indexed_sequentially_after_the_retry(self):
        out = self._keep(["Retry"], ["A", "B"], first_index=3)
        self.assertEqual([s["index"] for s in out], [3, 4, 5])

    def test_restored_steps_keep_their_description_and_start_pending(self):
        out = self._keep(["Retry"], ["Open page"])
        self.assertEqual(out[1]["description"], "do Open page")
        self.assertEqual(out[1]["status"], "pending")

    def test_a_replan_with_several_steps_is_left_alone(self):
        out = self._keep(["Retry", "Then the rest"], ["A", "B", "C"])
        self.assertEqual([s["title"] for s in out], ["Retry", "Then the rest"])

    def test_nothing_remaining_means_nothing_to_restore(self):
        out = self._keep(["Retry"], [])
        self.assertEqual([s["title"] for s in out], ["Retry"])


class TestPlannerReplanEndToEnd(unittest.IsolatedAsyncioTestCase):
    """The real replan(), with only the LLM call stubbed."""

    async def _replan(self, llm_reply, remaining, provider=True):
        from core.execution.planner import planner_engine

        async def fake_llm(**kwargs):
            if isinstance(llm_reply, Exception):
                raise llm_reply
            return llm_reply

        session = {"provider": "p", "model": "m"} if provider else {}
        with patch("config.settings.get_user_session", return_value=session), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=fake_llm):
            return await planner_engine.replan(
                goal="fix the page", completed_steps=[],
                failed_step={"index": 0, "title": "Start server", "description": "d"},
                remaining_steps=remaining, user_id=f"u-{uuid.uuid4().hex}")

    async def test_llm_returns_a_single_retry_step(self):
        out = await self._replan('[{"title": "Retry start", "description": "again"}]', _steps("Open", "Fix", "Stop", start=1))
        self.assertEqual([s["title"] for s in out], ["Retry start", "Open", "Fix", "Stop"])

    async def test_no_provider_fallback_keeps_the_remaining_steps(self):
        out = await self._replan("", _steps("Open", "Fix", start=1), provider=False)
        self.assertEqual([s["title"] for s in out], ["Retry failed step", "Open", "Fix"])

    async def test_unparseable_reply_fallback_keeps_the_remaining_steps(self):
        out = await self._replan("this is not json", _steps("Open", "Fix", start=1))
        self.assertEqual([s["title"] for s in out], ["Retry failed step", "Open", "Fix"])

    async def test_a_full_replan_is_used_as_the_model_wrote_it(self):
        reply = '[{"title": "Retry", "description": "a"}, {"title": "Open", "description": "b"}, {"title": "Fix", "description": "c"}]'
        out = await self._replan(reply, _steps("Open", "Fix", "Stop", start=1))
        self.assertEqual([s["title"] for s in out], ["Retry", "Open", "Fix"])

    async def test_the_default_max_tokens_budget_reaching_the_llm_call_has_real_headroom(self):
        """A-18: 900 was live-confirmed to still truncate a reasoning-mandatory
        model's response before any JSON was emitted (Unterminated string /
        JSONDecodeError, matching production logs); 1200/1500/1800 each
        cleared it 4/4 in live trials. This only pins the actual number that
        reaches the call -- the live behaviour itself was confirmed manually,
        not re-run here (no network in unit tests)."""
        from core.execution.planner import planner_engine

        captured = {}

        async def fake_llm(**kwargs):
            captured["max_tokens"] = kwargs["max_tokens"]
            return '[{"title": "Retry", "description": "a"}]'

        with patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=fake_llm):
            await planner_engine.replan(
                goal="fix the page", completed_steps=[],
                failed_step={"index": 0, "title": "Start server", "description": "d"},
                remaining_steps=[], user_id=f"u-{uuid.uuid4().hex}")

        self.assertEqual(captured["max_tokens"], 2000)
        self.assertGreater(captured["max_tokens"], 1800)  # above every value confirmed reliable live


class TestReplanPrompt(unittest.TestCase):

    def test_prompt_demands_the_whole_remainder_and_no_longer_asks_for_minimal(self):
        from prompts.planner import REPLAN_SYSTEM_PROMPT, REPLAN_USER_TEMPLATE
        text = " ".join(REPLAN_SYSTEM_PROMPT.split())
        self.assertIn("EVERYTHING still needed", text)
        self.assertIn("keep every one of the original remaining steps", text)
        self.assertIn("merely retries the failed step", text)
        self.assertNotIn("Keep the number of new steps minimal", text)
        self.assertIn("still part of the goal", REPLAN_USER_TEMPLATE)


# ─────────────────────────────────────────────────────────────────────────────
# The live failure, reproduced through the real step loop
# ─────────────────────────────────────────────────────────────────────────────

class TestTheLiveFailureIsNotReproduced(unittest.IsolatedAsyncioTestCase):

    async def test_after_a_failed_first_step_and_a_retry_only_replan_every_original_step_still_runs(self):
        from core.execution import pev_runner
        from core.execution.models import ExecutionStatus
        from core.execution.verifier import VerificationResult
        from core.types import TextChunk

        class Agent:
            def __init__(self):
                self.prompts = []

            async def call(self, messages, stream=True):
                self.prompts.append(messages[-1]["content"])
                yield TextChunk(content="did it")

        plan = {"goal": "fix the page", "steps": _steps("Start server", "Open page", "Fix file", "Stop server")}
        verdicts = iter([False, True, True, True, True, True])

        async def fake_verify(**kwargs):
            ok = next(verdicts)
            return VerificationResult(passed=ok, summary="s", issues=None if ok else "WinError 267")

        async def fake_llm(**kwargs):  # the replanner: retry only, drops the rest (the live behaviour)
            return '[{"title": "Retry start server", "description": "try again"}]'

        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        session_cm.__aexit__ = AsyncMock(return_value=False)
        repo = MagicMock()
        for name in ("create", "update_steps", "update_status", "update_goal_verification"):
            setattr(repo, name, AsyncMock())
        execution = MagicMock(execution_id="e1", conversation_id="c1")
        execution.cancel_token = MagicMock(cancelled=False)
        execution.status = ExecutionStatus.RUNNING

        agent = Agent()
        with patch.object(pev_runner.planner_engine, "generate", new=AsyncMock(return_value=plan)), \
             patch.object(pev_runner.verifier_engine, "verify", new=fake_verify), \
             patch.object(pev_runner, "PlanRepo", repo), \
             patch.object(pev_runner, "AsyncSessionLocal", return_value=session_cm), \
             patch("config.settings.get_user_session", return_value={"provider": "p", "model": "m"}), \
             patch("core.providers.fallback_call.call_llm_with_fallback", new=fake_llm):
            async for _ in pev_runner._pev_stream(
                execution=execution, agent=agent,
                base_messages=[{"role": "user", "content": "fix my page"}],
                user_id="u", require_plan_approval=False,
            ):
                pass

        ran = " || ".join(agent.prompts)
        for title in ("Retry start server", "Open page", "Fix file", "Stop server"):
            self.assertIn(title, ran, f"'{title}' never ran -- the plan ended early")
        self.assertEqual(len(agent.prompts), 5)  # the failed try + retry + the three that were still owed

    async def test_step_numbers_follow_the_list_position_after_a_replan(self):
        from core.execution.pev_runner import _build_step_messages
        prompt = _build_step_messages([], {"goal": "g"}, {"index": 0, "title": "Retry", "description": "d"},
                                      "", step_number=2)[-1]["content"]
        self.assertIn("step 2 of a multi-step plan", prompt)


if __name__ == "__main__":
    unittest.main()
