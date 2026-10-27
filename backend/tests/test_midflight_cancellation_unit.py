# tests/test_midflight_cancellation_unit.py
"""
Unit tests for V6.15 (S-09): mid-flight tool-call cancellation.

Background: EVA's cancellation checks were only ever evaluated *between*
tool calls (before the next one starts) or *between* LLM tokens -- never
during one, for any tool. Shell was the most dangerous instance of this
(a destructive command like `rm -rf` on a mis-scoped path would run to
completion regardless of the user hitting stop, because the underlying
subprocess.run() was wrapped in asyncio.to_thread(), which is immune to
asyncio task cancellation).

Two complementary fixes, both covered here:
  1. General layer (core/agent_engine.py) -- AgentEngine._execute_tool_cancellable()
     races execute_tool_safely() against cancel_token so any tool
     implemented as genuine async I/O actually gets interrupted via
     task.cancel() -> CancelledError at its next await point.
  2. Shell-specific layer (core/shell/executor.py) -- ShellExecutor._run()
     now uses asyncio.create_subprocess_exec() (a real, killable process
     handle) instead of asyncio.to_thread(subprocess.run, ...), because
     task.cancel() alone does not reach into a thread pool.

Also covers:
  - tools/default/shell_tool.py's ShellArgs.timeout upper bound
    (SHELL_MAX_TIMEOUT_SECONDS) -- previously unbounded.
  - core/execution/pev_runner.py's execution_plans status backfill on
    cancellation (the compounding gap noted in S-09: a cancelled
    execution's plan record previously stayed stuck at "running"/
    "executing" forever).

Run with:
    cd <project_root>
    pytest tests/test_midflight_cancellation_unit.py -v
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from core.broker import local_broker as _local_broker  # the singleton; patch.object avoids the module-vs-instance ambiguity of the string path (resolves differently on py3.10 vs 3.11)


# ===========================================================================
# Layer 1 -- AgentEngine._execute_tool_cancellable()
# ===========================================================================

class _FakeCancelToken:
    def __init__(self, cancelled: bool = False):
        self.cancelled = cancelled


def _build_agent(cancel_token=None):
    """Minimal AgentEngine instance -- only what _execute_tool_cancellable() needs."""
    from core.agent_engine import AgentEngine

    with patch("core.agent_engine.create_provider_handler", return_value=MagicMock()):
        agent = AgentEngine(
            provider="p", api_key="k", model="m",
            reasoning=None, cancel_token=cancel_token,
        )
    return agent


class TestExecuteToolCancellable(unittest.IsolatedAsyncioTestCase):

    async def test_normal_completion_returns_result_unchanged(self):
        agent = _build_agent(cancel_token=_FakeCancelToken(cancelled=False))

        async def _fast_tool(**kwargs):
            return {"ok": True, "result": "done"}

        with patch("core.agent_engine.execute_tool_safely", side_effect=_fast_tool):
            result = await agent._execute_tool_cancellable(tool_name="x")

        self.assertEqual(result, {"ok": True, "result": "done"})

    async def test_cancellation_mid_flight_returns_cancelled_marker(self):
        # Cancelled from the start -- the very first poll iteration (one
        # _TOOL_CANCEL_POLL_SECONDS interval) must see it and cancel the task,
        # rather than waiting for the (very long) mock tool to finish.
        agent = _build_agent(cancel_token=_FakeCancelToken(cancelled=True))
        tool_was_cancelled = False

        async def _slow_tool(**kwargs):
            nonlocal tool_was_cancelled
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                tool_was_cancelled = True
                raise
            return {"ok": True, "result": "should never get here"}

        with patch("core.agent_engine.execute_tool_safely", side_effect=_slow_tool):
            result = await asyncio.wait_for(
                agent._execute_tool_cancellable(tool_name="x"), timeout=2,
            )

        self.assertTrue(result.get("cancelled"))
        self.assertFalse(result.get("ok"))
        self.assertTrue(tool_was_cancelled,
                         "CancelledError must actually propagate into the tool coroutine")

    async def test_cancellation_latency_is_bounded_by_poll_interval(self):
        """Must not wait anywhere near the mock's full duration."""
        import time
        agent = _build_agent(cancel_token=_FakeCancelToken(cancelled=True))

        async def _slow_tool(**kwargs):
            await asyncio.sleep(30)

        with patch("core.agent_engine.execute_tool_safely", side_effect=_slow_tool):
            start = time.monotonic()
            await asyncio.wait_for(
                agent._execute_tool_cancellable(tool_name="x"), timeout=2,
            )
            elapsed = time.monotonic() - start

        self.assertLess(elapsed, 1.0,
                         "cancellation should resolve within ~1 poll interval, not wait on the mock")

    async def test_no_cancel_token_behaves_as_uncancellable(self):
        agent = _build_agent(cancel_token=None)

        async def _fast_tool(**kwargs):
            return {"ok": True, "result": "fine"}

        with patch("core.agent_engine.execute_tool_safely", side_effect=_fast_tool):
            result = await agent._execute_tool_cancellable(tool_name="x")

        self.assertEqual(result, {"ok": True, "result": "fine"})


# ===========================================================================
# Layer 2 -- ShellExecutor real-process cancellation
# ===========================================================================

class _FakeProcess:
    """
    Minimal stand-in for asyncio.subprocess.Process, controllable per-test.

    communicate_delay: seconds before communicate() resolves (simulates a
                        long-running command).
    terminate_is_graceful: if True, .wait() after .terminate() resolves
                            promptly (simulating a well-behaved child); if
                            False, .wait() after .terminate() hangs until
                            .kill() is also called (forces the escalation path).
    """
    def __init__(self, communicate_delay=0.0, terminate_is_graceful=True,
                 stdout=b"", stderr=b"", returncode=0):
        self._communicate_delay = communicate_delay
        self._terminate_is_graceful = terminate_is_graceful
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = None
        self._final_returncode = returncode
        self.terminate_called = False
        self.kill_called = False
        self._killed_event = asyncio.Event()

    async def communicate(self):
        await asyncio.sleep(self._communicate_delay)
        self.returncode = self._final_returncode
        return self._stdout, self._stderr

    def terminate(self):
        self.terminate_called = True
        if self._terminate_is_graceful:
            self.returncode = -15
            self._killed_event.set()

    def kill(self):
        self.kill_called = True
        self.returncode = -9
        self._killed_event.set()

    async def wait(self):
        await self._killed_event.wait()
        return self.returncode


def _shell_test_kwargs(**overrides):
    kwargs = dict(
        cmd=["echo", "hi"], conversation_id="conv-1", user_id="user-1",
        explicit_cwd=None, timeout=30, project_workspace=None, cancel_token=None,
    )
    kwargs.update(overrides)
    return kwargs


class TestShellExecutorCancellation(unittest.IsolatedAsyncioTestCase):

    async def _run_with_fake_process(self, fake_proc, **kwargs):
        from core.shell.executor import ShellExecutor
        from core.shell.session import ShellSession

        fake_session = MagicMock(spec=ShellSession)
        fake_session.cwd = "/tmp"
        fake_session.env_vars = {}
        fake_session.add_history = MagicMock()
        fake_session.effective_env = MagicMock(return_value={})
        fake_session.to_dict = MagicMock(return_value={})
        fake_session.save = AsyncMock()

        with patch("core.shell.executor.ShellSession.load", new=AsyncMock(return_value=fake_session)), \
             patch("core.shell.executor.asyncio.create_subprocess_exec",
                   new=AsyncMock(return_value=fake_proc)):
            from core.shell.executor import ShellExecutor as SE
            return await SE.run(**_shell_test_kwargs(**kwargs))

    async def test_normal_completion_returns_stdout_stderr_exit_code(self):
        fake_proc = _FakeProcess(stdout=b"hello\n", stderr=b"", returncode=0)
        result = await self._run_with_fake_process(fake_proc)

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "hello\n")
        self.assertNotIn("cancelled", result)

    async def test_cancellation_terminates_process_gracefully(self):
        fake_proc = _FakeProcess(communicate_delay=30, terminate_is_graceful=True)
        cancel_token = _FakeCancelToken(cancelled=True)

        result = await asyncio.wait_for(
            self._run_with_fake_process(fake_proc, cancel_token=cancel_token, timeout=60),
            timeout=5,
        )

        self.assertTrue(fake_proc.terminate_called,
                         "terminate() must actually be called on the process")
        self.assertFalse(fake_proc.kill_called,
                          "kill() should not be needed when terminate() succeeds promptly")
        self.assertTrue(result.get("cancelled"))
        self.assertEqual(result["exit_code"], -1)

    async def test_cancellation_escalates_to_kill_after_grace_period(self):
        # terminate() alone does NOT resolve .wait() here -- forces the
        # kill() escalation path after the grace period.
        fake_proc = _FakeProcess(communicate_delay=30, terminate_is_graceful=False)
        cancel_token = _FakeCancelToken(cancelled=True)

        with patch("core.shell.executor._KILL_GRACE_SECONDS", 0.2):
            result = await asyncio.wait_for(
                self._run_with_fake_process(fake_proc, cancel_token=cancel_token, timeout=60),
                timeout=5,
            )

        self.assertTrue(fake_proc.terminate_called)
        self.assertTrue(fake_proc.kill_called,
                         "kill() must be called when terminate() alone doesn't stop the process in time")
        self.assertTrue(result.get("cancelled"))

    async def test_own_timeout_kills_process_without_cancel_token(self):
        fake_proc = _FakeProcess(communicate_delay=30, terminate_is_graceful=True)

        with patch("core.shell.executor._CANCEL_POLL_SECONDS", 0.05):
            result = await asyncio.wait_for(
                self._run_with_fake_process(fake_proc, cancel_token=None, timeout=0.2),
                timeout=5,
            )

        self.assertTrue(fake_proc.terminate_called)
        self.assertFalse(result.get("cancelled"))
        self.assertIn("timeout", result.get("error", ""))

    async def test_file_not_found_returns_error_result_not_exception(self):
        from core.shell.session import ShellSession
        fake_session = MagicMock(spec=ShellSession)
        fake_session.cwd = "/tmp"
        fake_session.add_history = MagicMock()
        fake_session.effective_env = MagicMock(return_value={})
        fake_session.to_dict = MagicMock(return_value={})
        fake_session.save = AsyncMock()

        with patch("core.shell.executor.ShellSession.load", new=AsyncMock(return_value=fake_session)), \
             patch("core.shell.executor.asyncio.create_subprocess_exec",
                   new=AsyncMock(side_effect=FileNotFoundError())):
            from core.shell.executor import ShellExecutor
            result = await ShellExecutor.run(**_shell_test_kwargs(cmd=["not-a-real-binary"]))

        self.assertIn("error", result)
        self.assertIn("command not found", result["error"])


class TestShellExecutorRealSubprocessKill(unittest.IsolatedAsyncioTestCase):
    """
    One genuinely live (non-mocked) test proving an actual OS process gets
    killed -- unit tests with a fake process prove the *logic* is right,
    but only a real subprocess proves the fix actually works end to end.
    Uses a short-lived command so the test itself stays fast.
    """

    async def test_real_process_is_actually_killed_on_cancel(self):
        import sys
        from core.shell.executor import ShellExecutor
        from core.shell.session import ShellSession

        cancel_token = _FakeCancelToken(cancelled=False)

        async def _cancel_shortly():
            await asyncio.sleep(0.3)
            cancel_token.cancelled = True

        # A python busy-sleep that would run for 10s if never interrupted.
        cmd = [sys.executable, "-c", "import time; time.sleep(10)"]

        with patch("core.shell.executor._CANCEL_POLL_SECONDS", 0.1):
            canceller = asyncio.ensure_future(_cancel_shortly())
            start = asyncio.get_event_loop().time()
            result = await asyncio.wait_for(
                ShellExecutor.run(
                    cmd=cmd, conversation_id="conv-real-kill", user_id="user-real-kill",
                    explicit_cwd=None, timeout=10, cancel_token=cancel_token,
                ),
                timeout=8,
            )
            elapsed = asyncio.get_event_loop().time() - start
            await canceller

        self.assertTrue(result.get("cancelled"))
        # Must have been killed well before the 10s sleep would have finished.
        self.assertLess(elapsed, 3.0,
                         "the real subprocess must be killed promptly, not left to run 10s")


# ===========================================================================
# End-to-end: both cancellation layers active together (the actual
# production wiring). This is the case that was NEVER exercised by the
# tests above -- each layer was proven correct in isolation, but the two
# layers racing over the SAME cancel_token was untested, and that race is
# exactly what live user testing (Windows, polling for the real python.exe
# process via Get-CimInstance while clicking Stop) exposed: the UI reported
# "cancelled" while the actual OS process kept running to completion,
# because agent_engine.py's task.cancel() won the race and interrupted
# ShellExecutor's poll loop *before* its cleanup code could run (see the
# V6.15.1 fix in core/shell/executor.py's except asyncio.CancelledError
# block).
# ===========================================================================

class TestEndToEndCancellationDoesNotOrphanProcess(unittest.IsolatedAsyncioTestCase):

    async def test_real_subprocess_is_killed_even_when_outer_wrapper_wins_the_race(self):
        import sys
        from core.execution.models import CancellationToken
        from core.shell.executor import ShellExecutor, _pid_alive

        cancel_token = CancellationToken(cancelled=False)
        agent = _build_agent(cancel_token=cancel_token)

        # A python busy-sleep that would run 10s if never interrupted.
        cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
        captured_pid = {}

        real_create_subprocess_exec = asyncio.create_subprocess_exec

        async def _spying_create_subprocess_exec(*a, **kw):
            proc = await real_create_subprocess_exec(*a, **kw)
            captured_pid["pid"] = proc.pid
            return proc

        async def _shell_call(**kwargs):
            # Mirrors production wiring exactly: shell_tool.py passes the
            # SAME cancel_token into ShellExecutor.run() that agent_engine.py
            # is also polling -- both layers watch one shared flag and race.
            with patch("core.shell.executor.asyncio.create_subprocess_exec",
                       new=_spying_create_subprocess_exec):
                return await ShellExecutor.run(
                    cmd=cmd, conversation_id="conv-e2e", user_id="user-e2e",
                    explicit_cwd=None, timeout=10, cancel_token=cancel_token,
                )

        async def _flip_cancel_shortly():
            await asyncio.sleep(0.3)
            cancel_token.cancelled = True

        with patch("core.agent_engine.execute_tool_safely", side_effect=_shell_call):
            canceller = asyncio.ensure_future(_flip_cancel_shortly())
            start = asyncio.get_event_loop().time()
            await asyncio.wait_for(
                agent._execute_tool_cancellable(tool_name="shell"), timeout=8,
            )
            elapsed = asyncio.get_event_loop().time() - start
            await canceller

        self.assertIn("pid", captured_pid, "the real subprocess must actually have been spawned")
        pid = captured_pid["pid"]

        # Poll for OS process teardown rather than a single fixed-delay
        # check -- the same technique used in live testing (Get-CimInstance
        # Win32_Process), via the project's own cross-platform liveness
        # check (see core/shell/executor.py::_pid_alive). A one-shot
        # sleep(0.5)-then-check was flaky under heavy concurrent load (e.g.
        # running as part of the full ~1900-test suite): the kill itself is
        # correct, but OS teardown time isn't a hard 0.5s guarantee under
        # contention. Polling up to 3s keeps the same real assertion --
        # catch a genuinely orphaned process -- without being sensitive to
        # how fast the OS happens to reap it on a busy machine.
        deadline = asyncio.get_event_loop().time() + 3.0
        still_alive = _pid_alive(pid)
        while still_alive and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.1)
            still_alive = _pid_alive(pid)
        self.assertFalse(
            still_alive,
            f"pid {pid} is still running -- the real OS process was orphaned "
            "despite the UI-facing cancellation succeeding (this is the exact "
            "bug found via live testing: task.cancel() from the outer wrapper "
            "won the race and skipped ShellExecutor's cleanup).",
        )
        self.assertLess(elapsed, 3.0,
                         "cancellation must resolve promptly, not wait anywhere near the 10s sleep")


# ===========================================================================
# core/execution/worker.py -- background cancel watcher (V6.15.2)
#
# The ACTUAL root cause behind every live-tested failure above: the worker
# reconstructs its own ConversationExecution from broker data (see
# core/execution/worker.py:_process_execution(), around the
# `execution = ConversationExecution(...)` call) rather than sharing the
# object the /cancel API endpoint mutates. The only thing that ever bridged
# the two was a broker-status poll living *inside* runner.py's / pev_runner.
# py's own chunk/step consumption loop -- which never gets a turn to run
# while a single tool call is in flight (a tool call doesn't yield a chunk
# until it resolves). So cancel_token.cancelled was never actually True
# during a tool call, no matter how correctly the mid-flight cancellation
# logic above reacts to it. This is what the live PowerShell process-polling
# test caught: a 60s shell sleep, cancelled at ~t=2s, still ran the full 60s.
# ===========================================================================

class TestBackgroundCancelWatcher(unittest.IsolatedAsyncioTestCase):

    async def test_watcher_sets_cancel_token_promptly_while_something_else_is_blocked(self):
        import core.execution.worker as worker_mod
        from core.execution.models import ConversationExecution, ExecutionStatus

        execution = ConversationExecution(
            execution_id="exec-watcher-1", conversation_id="c1", user_id="u1",
            session_id="s1", status=ExecutionStatus.RUNNING, buffer=None,
        )

        mock_client = AsyncMock()
        mock_client.hget = AsyncMock(return_value="cancelled")
        mock_registry = MagicMock()
        mock_registry.connect = AsyncMock(return_value=mock_client)

        # Simulates a tool call that blocks the main execution coroutine for
        # a while -- the watcher must still detect cancellation because it
        # runs as an independent task, not gated behind this coroutine.
        async def _blocked_like_a_tool_call():
            await asyncio.sleep(5)

        with patch.object(worker_mod, "redis_execution_registry", mock_registry), \
             patch.object(worker_mod, "_CANCEL_WATCH_POLL_SECONDS", 0.05):
            blocked_task = asyncio.ensure_future(_blocked_like_a_tool_call())
            watcher_task = asyncio.ensure_future(
                worker_mod._watch_for_external_cancellation(execution)
            )

            await asyncio.sleep(0.3)

            self.assertTrue(
                execution.cancel_token.cancelled,
                "the watcher must set cancel_token.cancelled on its own schedule, "
                "even while the main execution coroutine (blocked_task, standing "
                "in for a tool call) is still running",
            )
            self.assertFalse(blocked_task.done(),
                              "sanity check -- the 'tool call' must still be in flight "
                              "when cancellation was detected, proving this isn't just "
                              "boundary-check timing")

            blocked_task.cancel()
            watcher_task.cancel()
            for t in (blocked_task, watcher_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def test_watcher_exits_once_cancel_token_already_true(self):
        import core.execution.worker as worker_mod
        from core.execution.models import ConversationExecution, ExecutionStatus

        execution = ConversationExecution(
            execution_id="exec-watcher-2", conversation_id="c1", user_id="u1",
            session_id="s1", status=ExecutionStatus.RUNNING, buffer=None,
        )
        execution.cancel_token.cancelled = True

        mock_registry = MagicMock()
        mock_registry.connect = AsyncMock(side_effect=AssertionError(
            "should not even poll the broker once already cancelled"))

        with patch.object(worker_mod, "redis_execution_registry", mock_registry), \
             patch.object(worker_mod, "_CANCEL_WATCH_POLL_SECONDS", 0.05):
            await asyncio.wait_for(
                worker_mod._watch_for_external_cancellation(execution), timeout=2,
            )
        # Reaching here without the AssertionError firing confirms the early return.


# ===========================================================================
# ShellArgs.timeout upper bound
# ===========================================================================

class TestShellArgsTimeoutUpperBound(unittest.TestCase):

    def test_default_timeout_accepted(self):
        from tools.default.shell_tool import ShellArgs
        args = ShellArgs(cmd=["echo", "hi"])
        self.assertEqual(args.timeout, 30)

    def test_timeout_within_bound_accepted(self):
        from tools.default.shell_tool import ShellArgs
        from config.settings import SHELL_MAX_TIMEOUT_SECONDS
        args = ShellArgs(cmd=["echo", "hi"], timeout=SHELL_MAX_TIMEOUT_SECONDS)
        self.assertEqual(args.timeout, SHELL_MAX_TIMEOUT_SECONDS)

    def test_timeout_exceeding_bound_rejected(self):
        from pydantic import ValidationError
        from tools.default.shell_tool import ShellArgs
        from config.settings import SHELL_MAX_TIMEOUT_SECONDS
        with self.assertRaises(ValidationError):
            ShellArgs(cmd=["echo", "hi"], timeout=SHELL_MAX_TIMEOUT_SECONDS + 1)


# ===========================================================================
# execution_plans status backfill on cancellation (pev_runner.py)
# ===========================================================================

class TestPlanStatusBackfillOnCancellation(unittest.IsolatedAsyncioTestCase):
    """
    S-09's compounding gap: PlanRepo.update_status()/update_steps() with
    status="cancelled" must actually be called on every cancellation path
    in _pev_stream(), not just the normal-completion path.
    """

    def _make_kwargs(self):
        mock_execution = MagicMock()
        mock_execution.execution_id = "exec-cancel-backfill"
        mock_execution.conversation_id = "conv-cancel-backfill"
        mock_execution.status = MagicMock()
        mock_cancel_token = MagicMock()
        mock_cancel_token.cancelled = False
        mock_execution.cancel_token = mock_cancel_token

        base_messages = [{"role": "user", "content": "Do the thing"}]
        original_plan = {
            "goal": "Original goal",
            "steps": [{
                "index": 0, "title": "Original Step", "description": "orig",
                "tool_hint": None, "status": "pending", "result_summary": None,
                "verified": False, "verification_notes": None,
            }],
        }
        return mock_execution, base_messages, original_plan

    async def test_reject_path_backfills_plan_status_cancelled(self):
        import core.execution.pev_runner as mod

        mock_execution, base_messages, original_plan = self._make_kwargs()
        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod, "AsyncSessionLocal", return_value=db_ctx), \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_status", new=AsyncMock()) as mock_update_status, \
             patch.object(mod, "_gate_plan_approval",
                           new=AsyncMock(return_value=mod.PlanApprovalResult(
                               decision="reject", plan=None))):

            chunks = []
            async for chunk in mod._pev_stream(
                execution=mock_execution, agent=AsyncMock(), base_messages=base_messages,
                user_id="user-1", require_plan_approval=True,
            ):
                chunks.append(chunk)

        cancelled_calls = [
            c for c in mock_update_status.call_args_list
            if c.kwargs.get("status") == "cancelled"
            and c.kwargs.get("execution_id") == "exec-cancel-backfill"
        ]
        self.assertTrue(cancelled_calls,
                         "PlanRepo.update_status(status='cancelled') must be called on reject")

    async def test_between_step_cancel_backfills_plan_status(self):
        import core.execution.pev_runner as mod

        mock_execution, base_messages, original_plan = self._make_kwargs()
        # Cancelled BEFORE the step loop even starts its first iteration.
        mock_execution.cancel_token.cancelled = True

        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod, "AsyncSessionLocal", return_value=db_ctx), \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_status", new=AsyncMock()) as mock_update_status:

            chunks = []
            async for chunk in mod._pev_stream(
                execution=mock_execution, agent=AsyncMock(), base_messages=base_messages,
                user_id="user-1", require_plan_approval=False,
            ):
                chunks.append(chunk)

        cancelled_calls = [
            c for c in mock_update_status.call_args_list
            if c.kwargs.get("status") == "cancelled"
        ]
        self.assertTrue(cancelled_calls,
                         "between-step cancellation must backfill plan status to 'cancelled'")

    async def test_mid_step_cancel_marks_the_running_step_cancelled(self):
        import core.execution.pev_runner as mod

        mock_execution, base_messages, original_plan = self._make_kwargs()

        from core.types import TextChunk as _TC

        class _FakeAgent:
            async def call(self, messages, stream=True):
                # Yield one real chunk so the mid-step cancel check (which
                # lives inside the `async for chunk in agent.call(...)` loop
                # body) actually gets a turn to run, then flip cancellation
                # on -- simulating the flag being set while this step is
                # genuinely in flight.
                yield _TC(content="partial output before cancellation")
                mock_execution.cancel_token.cancelled = True
                yield _TC(content="this should never be reached")

        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod, "AsyncSessionLocal", return_value=db_ctx), \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_steps", new=AsyncMock()) as mock_update_steps:

            chunks = []
            async for chunk in mod._pev_stream(
                execution=mock_execution, agent=_FakeAgent(), base_messages=base_messages,
                user_id="user-1", require_plan_approval=False,
            ):
                chunks.append(chunk)

        cancelled_step_calls = [
            c for c in mock_update_steps.call_args_list
            if c.kwargs.get("status") == "cancelled"
            and c.kwargs.get("steps", [{}])[0].get("status") == "cancelled"
        ]
        self.assertTrue(cancelled_step_calls,
                         "the in-flight step's own status must be backfilled to 'cancelled', "
                         "not left stuck at 'running'")


# ===========================================================================
# V6.15.3: run_execution() (the conversational/ReAct path, as opposed to
# pev_runner.py's run_pev_execution() -- both get the same fix) must also
# recover partial usage on cancellation. See the matching PEV-path test in
# tests/test_pev_narration_structured_unit.py::
# TestRunPevExecutionRecoversPartialUsageOnCancel for the fuller rationale.
# ===========================================================================

class TestRunExecutionRecoversPartialUsageOnCancel(unittest.IsolatedAsyncioTestCase):

    async def test_mid_stream_cancel_recovers_streamed_usage(self):
        import json as _json
        import core.execution.runner as mod
        from core.execution.models import ConversationExecution, ExecutionStatus
        from core.broker.shadow_buffer import LocalShadowBuffer
        from core.types import TextChunk, UsageChunk

        execution = ConversationExecution(
            execution_id="exec-conv-usage-recover",
            conversation_id="conv-conv-usage-recover",
            user_id="user-1", session_id="sess-1",
            status=ExecutionStatus.PENDING, buffer=None,
        )

        class _FakeAgent:
            async def call(self, messages, stream=True):
                yield TextChunk(content="partial answer before cancel")
                yield UsageChunk(
                    prompt_tokens=85, completion_tokens=22, total_tokens=107,
                    provider="openai", model="gpt-x",
                )
                execution.cancel_token.cancelled = True
                yield TextChunk(content="never reached")

        finalizer_calls = []

        async def fake_finalizer(final):
            finalizer_calls.append(final)

        real_shadow_buffer = LocalShadowBuffer()
        update_execution_calls = []

        async def fake_update_execution(execution_id, **fields):
            update_execution_calls.append((execution_id, fields))

        fake_blackboard = MagicMock()
        fake_blackboard.delete = AsyncMock()

        with patch.object(mod.redis_execution_registry, "mark_execution_running", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "update_execution", new=fake_update_execution), \
             patch.object(mod.redis_execution_registry, "heartbeat", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "connect", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "mark_execution_cancelled", new=AsyncMock()), \
             patch.object(_local_broker, "get_shadow_buffer", return_value=real_shadow_buffer), \
             patch.object(mod, "ExecutionBlackboard", return_value=fake_blackboard), \
             patch.object(mod.ExecutionStateMachine, "assert_transition_allowed"), \
             patch.object(mod.event_bus, "publish"):

            await mod.run_execution(
                execution=execution,
                agent=_FakeAgent(),
                messages=[{"role": "user", "content": "do things"}],
                finalizer=fake_finalizer,
            )

        self.assertEqual(execution.status, ExecutionStatus.CANCELLED)
        self.assertEqual(len(finalizer_calls), 1,
                          "finalizer must run exactly once on cancellation")

        usage = finalizer_calls[0].get("usage")
        self.assertIsNotNone(
            usage,
            "the UsageChunk streamed before cancellation must be recovered, "
            "not discarded by buffered.clear()",
        )
        self.assertEqual(usage.get("prompt_tokens"), 85)
        self.assertEqual(usage.get("completion_tokens"), 22)

        usage_persist_calls = [
            c for c in update_execution_calls if "usage_json" in c[1]
        ]
        self.assertTrue(
            usage_persist_calls,
            "partial usage must be persisted to the broker as usage_json",
        )
        persisted = _json.loads(usage_persist_calls[0][1]["usage_json"])
        self.assertEqual(persisted.get("prompt_tokens"), 85)
        self.assertEqual(persisted.get("completion_tokens"), 22)


# ===========================================================================
# api/eva_response.py: the SSE relay must emit the persisted partial usage
# as a real `event: usage` frame on the CANCELLED terminal path.
# ===========================================================================

class TestSSERelayEmitsPersistedUsageOnCancel(unittest.IsolatedAsyncioTestCase):

    async def test_cancelled_terminal_state_emits_usage_frame(self):
        """
        Drives the real eva_response_endpoint() (not a re-mock of its logic)
        with a fake broker client reporting status=cancelled and a stored
        usage_json field, and asserts the actual SSE byte stream it produces
        contains a well-formed `event: usage` frame with the recovered
        token counts, ahead of `event: cancelled` / `event: finish` /
        `event: done`.
        """
        import json as _json
        import api.eva_response as mod

        stored_usage = {
            "type": "usage", "prompt_tokens": 85, "completion_tokens": 22,
            "reasoning_tokens": None, "total_tokens": 107,
            "provider": "openai", "model": "gpt-x",
        }

        class _FakeClient:
            async def get(self, key):
                return "exec-usage-relay-1"

            async def hgetall(self, key):
                return {
                    "user_id": "user-1", "status": "running",
                    "created_at": "0", "execution_mode": "conversational",
                }

            async def hget(self, key, field):
                if field == "status":
                    return "cancelled"
                if field == "usage_json":
                    return _json.dumps(stored_usage)
                return None

        class _FakeShadowBuffer:
            def chunks_from(self, start):
                return []

            def is_closed(self):
                return False

            async def wait_for_more(self, min_count, timeout=5.0):
                return None

        class _FakeRequest:
            async def is_disconnected(self):
                return False

        with patch.object(mod, "_resolve_sse_auth", new=AsyncMock(return_value="user-1")), \
             patch.object(mod.redis_execution_registry, "connect",
                           new=AsyncMock(return_value=_FakeClient())), \
             patch.object(_local_broker, "get_shadow_buffer",
                   return_value=_FakeShadowBuffer()):

            response = await mod.eva_response_endpoint(
                request=_FakeRequest(), conversation_id="conv-1", sse_token=None,
            )

            collected = ""
            async for part in response.body_iterator:
                collected += part
                if "event: done" in collected:
                    break

        self.assertIn("event: usage", collected,
                       "a real event: usage frame must be emitted on the "
                       "cancelled terminal path when usage was recovered")

        usage_block = collected.split("event: usage\n", 1)[1].split("\n\n", 1)[0]
        self.assertTrue(usage_block.startswith("data: "))
        payload = _json.loads(usage_block[len("data: "):])
        self.assertEqual(payload["prompt_tokens"], 85)
        self.assertEqual(payload["completion_tokens"], 22)

        # Ordering: usage must arrive before cancelled/finish/done so the
        # frontend has it attached by the time it finalises the message.
        self.assertLess(
            collected.index("event: usage"), collected.index("event: cancelled"),
        )


if __name__ == "__main__":
    unittest.main()
