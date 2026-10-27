# tests/test_background_process_unit.py
"""
Unit tests for the background process supervision feature (2026-08-18).

Background: ShellExecutor.run() (core/shell/executor.py) always waits for a
subprocess to exit or kills it at SHELL_MAX_TIMEOUT_SECONDS -- there was no
way to start a dev server, leave it running, and check on it later. This
feature adds a DB-backed registry (BackgroundProcess / ProcessRepo) and
three new ShellExecutor methods (start_background/stop_background/
tail_background) that spawn a genuinely detached process instead.

Covers:
  - ProcessRepo CRUD, including purge_old_terminal's log-file cleanup
    (the retention gap identified after the first implementation pass --
    a background_processes row is operational state, not history, so
    terminal rows must not accumulate forever).
  - _pid_alive / _kill_process_tree helpers.
  - ShellExecutor.start_background/tail_background/stop_background against
    a REAL short-lived subprocess (not mocked create_subprocess_exec) --
    proves the detach-and-return-immediately behavior actually works, not
    just that the code compiles. Mirrors the existing precedent in
    test_midflight_cancellation_unit.py's TestShellExecutorRealSubprocessKill.
  - The background_process tool's start/list/tail/stop actions.
  - core/tasks/cleanup.py's _purge_background_process_data (force-stop past
    max lifetime, then purge old terminal rows).

Run with:
    cd <project_root>
    pytest tests/test_background_process_unit.py -v
"""

from __future__ import annotations

import asyncio
import os
import platform
import signal
import subprocess
import sys
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _make_engine_and_session():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from core.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, Session


class _DbHarnessMixin:
    """
    Shared in-memory SQLite setup. core.db.session.AsyncSessionLocal is the
    only patch target needed here (unlike worker.py's harness, which needed
    three separately-bound names) because every new call site added by this
    feature does `from core.db.session import AsyncSessionLocal` as a LOCAL
    import inside the function body, not a module-top-level one -- so
    patching core.db.session's own attribute is picked up at call time by
    all of them (core/shell/executor.py, core/tasks/cleanup.py,
    tools/default/process_tools.py, main.py's boot reconciliation).
    """

    async def asyncSetUp(self):
        self.engine, self.Session = _make_engine_and_session()
        async with self.engine.begin() as conn:
            from core.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        import core.db.session as _dbs
        self._orig_session_local = _dbs.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session

    async def asyncTearDown(self):
        import core.db.session as _dbs
        _dbs.AsyncSessionLocal = self._orig_session_local
        await self.engine.dispose()


# ===========================================================================
# ProcessRepo
# ===========================================================================

class TestProcessRepo(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    async def test_create_and_get_roundtrip(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(
                db, user_id="u1", command=["npm", "run", "dev"], cwd="/tmp/proj",
                log_path="/tmp/proj.log", pid=12345,
                conversation_id="conv-1", project_id="proj-1", execution_id="exec-1",
            )
        self.assertEqual(row.status, "running")
        self.assertEqual(row.pid, 12345)
        self.assertEqual(row.last_output_offset, 0)

        async with self.Session() as db:
            fetched = await ProcessRepo.get(db, row.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.command, ["npm", "run", "dev"])

    async def test_create_stores_pid_create_time(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(
                db, user_id="u1", command=["a"], cwd=".", log_path="a.log",
                pid=123, pid_create_time=1234567890.5,
            )
        self.assertEqual(row.pid_create_time, 1234567890.5)

    async def test_create_stores_keep_alive_on_app_shutdown(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(
                db, user_id="u1", command=["a"], cwd=".", log_path="a.log",
                pid=123, keep_alive_on_app_shutdown=True,
            )
        self.assertTrue(row.keep_alive_on_app_shutdown)

    async def test_create_defaults_keep_alive_on_app_shutdown_to_false(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(db, user_id="u1", command=["a"], cwd=".", log_path="a.log", pid=123)
        self.assertFalse(row.keep_alive_on_app_shutdown)

    async def test_set_keep_alive_on_app_shutdown_toggles_the_row(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(db, user_id="u1", command=["a"], cwd=".", log_path="a.log", pid=123)
            await ProcessRepo.set_keep_alive_on_app_shutdown(db, row.id, True)

        async with self.Session() as db:
            fetched = await ProcessRepo.get(db, row.id)
        self.assertTrue(fetched.keep_alive_on_app_shutdown)

    async def test_create_defaults_pid_create_time_to_none(self):
        """Matches the pre-migration/no-argument call shape exactly -- a
        row created without this argument must not error and must fall
        back to the identity-blind liveness check (see
        _pid_matches_process's own docstring)."""
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(db, user_id="u1", command=["a"], cwd=".", log_path="a.log", pid=123)
        self.assertIsNone(row.pid_create_time)

    async def test_list_running_for_user_excludes_other_users_and_terminal_rows(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            r1 = await ProcessRepo.create(db, user_id="u1", command=["a"], cwd=".", log_path="a.log", pid=1)
            await ProcessRepo.create(db, user_id="u2", command=["b"], cwd=".", log_path="b.log", pid=2)
            r3 = await ProcessRepo.create(db, user_id="u1", command=["c"], cwd=".", log_path="c.log", pid=3)
            await ProcessRepo.mark_status(db, r3.id, "stopped")

        async with self.Session() as db:
            running = await ProcessRepo.list_running_for_user(db, "u1")
        self.assertEqual([r.id for r in running], [r1.id])

    async def test_list_all_running_spans_users(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            await ProcessRepo.create(db, user_id="u1", command=["a"], cwd=".", log_path="a.log", pid=1)
            await ProcessRepo.create(db, user_id="u2", command=["b"], cwd=".", log_path="b.log", pid=2)

        async with self.Session() as db:
            all_running = await ProcessRepo.list_all_running(db)
        self.assertEqual(len(all_running), 2)

    async def test_update_offset_persists(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(db, user_id="u1", command=["a"], cwd=".", log_path="a.log", pid=1)
            await ProcessRepo.update_offset(db, row.id, 512)

        async with self.Session() as db:
            fetched = await ProcessRepo.get(db, row.id)
        self.assertEqual(fetched.last_output_offset, 512)

    async def test_mark_status_sets_exit_code_and_ended_at(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(db, user_id="u1", command=["a"], cwd=".", log_path="a.log", pid=1)
            await ProcessRepo.mark_status(db, row.id, "crashed", exit_code=1)

        async with self.Session() as db:
            fetched = await ProcessRepo.get(db, row.id)
        self.assertEqual(fetched.status, "crashed")
        self.assertEqual(fetched.exit_code, 1)
        self.assertIsNotNone(fetched.ended_at)

    async def test_purge_old_terminal_deletes_row_and_log_file(self):
        from core.db.repository import ProcessRepo

        tmp_dir = os.path.join(os.path.dirname(__file__), "_tmp_purge_test")
        os.makedirs(tmp_dir, exist_ok=True)
        log_path = os.path.join(tmp_dir, "old.log")
        with open(log_path, "w") as f:
            f.write("stale output")

        try:
            async with self.Session() as db:
                row = await ProcessRepo.create(
                    db, user_id="u1", command=["a"], cwd=".", log_path=log_path, pid=1,
                )
                await ProcessRepo.mark_status(db, row.id, "stopped")
                # Backdate ended_at past the retention window (mark_status
                # always stamps "now" -- overwrite directly for the test).
                from sqlalchemy import update
                from core.db.models import BackgroundProcess
                await db.execute(
                    update(BackgroundProcess)
                    .where(BackgroundProcess.id == row.id)
                    .values(ended_at=datetime.now(timezone.utc) - timedelta(hours=48))
                )
                await db.commit()

            async with self.Session() as db:
                purged = await ProcessRepo.purge_old_terminal(db, retention_hours=24)
            self.assertEqual(purged, 1)

            async with self.Session() as db:
                self.assertIsNone(await ProcessRepo.get(db, row.id))
            self.assertFalse(os.path.exists(log_path), "purge must also delete the log file")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def test_purge_old_terminal_never_touches_running_rows(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(db, user_id="u1", command=["a"], cwd=".", log_path="a.log", pid=1)
            purged = await ProcessRepo.purge_old_terminal(db, retention_hours=0)

        self.assertEqual(purged, 0, "a still-running row must never be purged regardless of retention_hours")
        async with self.Session() as db:
            self.assertIsNotNone(await ProcessRepo.get(db, row.id))

    async def test_purge_old_terminal_missing_log_file_is_non_fatal(self):
        from core.db.repository import ProcessRepo

        async with self.Session() as db:
            row = await ProcessRepo.create(
                db, user_id="u1", command=["a"], cwd=".",
                log_path="/nonexistent/path/does-not-exist.log", pid=1,
            )
            await ProcessRepo.mark_status(db, row.id, "exited")
            from sqlalchemy import update
            from core.db.models import BackgroundProcess
            await db.execute(
                update(BackgroundProcess).where(BackgroundProcess.id == row.id)
                .values(ended_at=datetime.now(timezone.utc) - timedelta(hours=48))
            )
            await db.commit()

        async with self.Session() as db:
            purged = await ProcessRepo.purge_old_terminal(db, retention_hours=24)
        self.assertEqual(purged, 1, "an already-missing log file must not block the DB row purge")


# ===========================================================================
# _pid_alive / _kill_process_tree helpers
# ===========================================================================

class TestPidAliveAndKillHelpers(unittest.IsolatedAsyncioTestCase):

    def test_pid_alive_true_for_own_process(self):
        from core.shell.executor import _pid_alive
        self.assertTrue(_pid_alive(os.getpid()))

    def test_pid_alive_false_for_implausible_pid(self):
        from core.shell.executor import _pid_alive
        # PID space is finite -- a very large PID is exceedingly unlikely to
        # be assigned to a real process on any platform this runs on.
        self.assertFalse(_pid_alive(2**31 - 2))

    async def test_kill_process_tree_actually_kills_a_real_process(self):
        from core.shell.executor import _pid_alive, _kill_process_tree

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        self.assertTrue(_pid_alive(proc.pid))

        await asyncio.wait_for(_kill_process_tree(proc.pid, grace_seconds=2.0), timeout=10)
        await proc.wait()  # reap so the test doesn't leave a zombie

        self.assertFalse(_pid_alive(proc.pid))

    async def test_kill_process_tree_kills_children_too(self):
        """A shell wrapper that spawns a child (e.g. `npm run dev` -> node)
        must have the whole tree killed, not just the wrapper -- the actual
        motivating scenario for psutil's children(recursive=True) over a
        single-PID kill."""
        from core.shell.executor import _pid_alive, _kill_process_tree

        # A parent that spawns a child sleeper and then also sleeps --
        # start_new_session so psutil sees it as the tree root, matching how
        # start_background() actually spawns things in production.
        script = (
            "import subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(child.pid); sys.stdout.flush(); time.sleep(30)"
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=(platform.system() != "Windows"),
        )
        child_pid_line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        child_pid = int(child_pid_line.decode().strip())
        self.assertTrue(_pid_alive(proc.pid))
        self.assertTrue(_pid_alive(child_pid))

        await asyncio.wait_for(_kill_process_tree(proc.pid, grace_seconds=2.0), timeout=10)
        await proc.wait()

        self.assertFalse(_pid_alive(proc.pid))
        self.assertFalse(_pid_alive(child_pid), "the child process must be killed too, not left orphaned")


# ===========================================================================
# _pid_matches_process -- identity check, not just liveness
# ===========================================================================

class TestPidIdentityCheck(unittest.IsolatedAsyncioTestCase):
    """
    2026-09-29: the actual PID-reuse safety fix. A bare liveness check
    (_pid_alive) can't tell "our process" apart from an unrelated one the
    OS has since reused the PID for -- realistic after a long uptime or a
    reboot, and every consumer (boot reconciliation, stop_background, the
    reaper, duplicate-detection) previously trusted PID equality alone.
    """

    async def test_matches_when_create_time_matches_a_real_process(self):
        import psutil
        from core.shell.executor import _pid_matches_process

        real_create_time = psutil.Process(os.getpid()).create_time()
        self.assertTrue(_pid_matches_process(os.getpid(), real_create_time))

    async def test_does_not_match_a_live_process_with_a_different_create_time(self):
        """The actual PID-reuse scenario: some process IS alive at this PID
        right now, but it is not the one this row was created for."""
        import psutil
        from core.shell.executor import _pid_matches_process

        real_create_time = psutil.Process(os.getpid()).create_time()
        wrong_create_time = real_create_time - 10_000  # nowhere near the real value
        self.assertFalse(_pid_matches_process(os.getpid(), wrong_create_time))

    async def test_falls_back_to_plain_liveness_when_expected_create_time_is_none(self):
        """Pre-migration rows (or a rare capture failure at spawn time) have
        no stored creation time -- must behave exactly like the old,
        identity-blind check for those, not start failing them."""
        from core.shell.executor import _pid_matches_process

        self.assertTrue(_pid_matches_process(os.getpid(), None))
        self.assertFalse(_pid_matches_process(2**31 - 2, None))

    async def test_does_not_match_a_dead_pid_regardless_of_expected_create_time(self):
        from core.shell.executor import _pid_matches_process

        self.assertFalse(_pid_matches_process(2**31 - 2, 1234567890.0))


# ===========================================================================
# ShellExecutor.start_background / tail_background / stop_background
# ===========================================================================

class TestShellExecutorBackgroundLifecycle(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):
    """
    One genuinely live pass (real subprocess, real log file, real DB row) --
    proves start_background() actually returns before the process exits,
    that the process really does keep running (the whole point of this
    feature), and that stop_background() really does kill it.
    """

    TEST_USER = "bgproc-test-user"

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        shutil.rmtree(get_user_root(self.TEST_USER), ignore_errors=True)

    async def test_start_returns_immediately_while_process_keeps_running(self):
        from core.shell.executor import ShellExecutor, _pid_alive

        cmd = [sys.executable, "-c", "import time,sys; print('hello-bg'); sys.stdout.flush(); time.sleep(3)"]

        start = asyncio.get_event_loop().time()
        result = await asyncio.wait_for(
            ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER),
            timeout=5,
        )
        elapsed = asyncio.get_event_loop().time() - start

        self.assertNotIn("error", result)
        self.assertIn("process_id", result)
        self.assertIn("pid", result)
        self.assertLess(elapsed, 2.0, "start_background must return almost immediately, not wait for the process")
        self.assertTrue(_pid_alive(result["pid"]), "the process must still be running after the call returns")

        # Clean up: the process would exit on its own after 3s, but stop it
        # explicitly so the test doesn't depend on timing.
        await ShellExecutor.stop_background(result["process_id"], self.TEST_USER)

    async def test_tail_returns_new_output_and_liveness(self):
        from core.shell.executor import ShellExecutor

        cmd = [sys.executable, "-c", "import time,sys; print('line-one'); sys.stdout.flush(); time.sleep(2)"]
        started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)
        process_id = started["process_id"]

        # Give the child a moment to actually write its output.
        await asyncio.sleep(0.5)

        tailed = await ShellExecutor.tail_background(process_id, self.TEST_USER)
        self.assertEqual(tailed["status"], "running")
        self.assertIn("line-one", tailed["new_output"])

        # A second tail call with nothing new written should return empty,
        # not repeat the same output (byte-offset tracking).
        tailed_again = await ShellExecutor.tail_background(process_id, self.TEST_USER)
        self.assertNotIn("line-one", tailed_again["new_output"])

        await ShellExecutor.stop_background(process_id, self.TEST_USER)

    async def test_tail_detects_process_that_exited_on_its_own(self):
        from core.shell.executor import ShellExecutor

        cmd = [sys.executable, "-c", "print('done')"]  # exits almost immediately
        started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)
        process_id = started["process_id"]

        await asyncio.sleep(1.0)  # let it finish

        tailed = await ShellExecutor.tail_background(process_id, self.TEST_USER)
        self.assertEqual(
            tailed["status"], "exited",
            "tail_background must reconcile a process that exited on its own, "
            "not keep reporting a stale 'running' status",
        )

    async def test_stop_kills_the_real_process(self):
        from core.shell.executor import ShellExecutor, _pid_alive

        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)
        process_id, pid = started["process_id"], started["pid"]
        self.assertTrue(_pid_alive(pid))

        result = await asyncio.wait_for(
            ShellExecutor.stop_background(process_id, self.TEST_USER), timeout=10,
        )
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(_pid_alive(pid))

    async def test_stop_does_not_kill_an_unrelated_process_at_a_reused_pid(self):
        """
        The actual safety property this whole fix exists for: if the row's
        PID is alive but belongs to a DIFFERENT process than the one this
        row was created for (simulating PID reuse after a long uptime/
        reboot), stop_background() must NOT kill it -- it should recognize
        its own tracked process is already gone and mark the row
        accordingly, leaving whatever is actually running at that PID
        completely untouched.
        """
        from core.shell.executor import ShellExecutor, _pid_alive, _kill_process_tree
        from sqlalchemy import update
        from core.db.models import BackgroundProcess

        # A real, genuinely-alive throwaway process to stand in for
        # "something unrelated the OS happens to be running at this PID
        # right now" -- started completely outside start_background() so
        # its own real creation time is never recorded anywhere.
        unrelated = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        self.assertTrue(_pid_alive(unrelated.pid))

        started = None
        try:
            cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
            started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)
            process_id = started["process_id"]

            # Overwrite the row so it *looks* like the "unrelated" PID above
            # is the one this row tracks, with a create_time that can't
            # possibly match it -- exactly what a genuinely reused PID would
            # look like from the DB's point of view.
            async with self.Session() as db:
                await db.execute(
                    update(BackgroundProcess)
                    .where(BackgroundProcess.id == process_id)
                    .values(pid=unrelated.pid, pid_create_time=1.0)
                )
                await db.commit()

            result = await asyncio.wait_for(
                ShellExecutor.stop_background(process_id, self.TEST_USER), timeout=10,
            )

            self.assertEqual(result["status"], "crashed",
                              "a PID-identity mismatch must be reported as the tracked "
                              "process already being gone, not as a successful stop")
            self.assertTrue(_pid_alive(unrelated.pid),
                             "the actually-running, unrelated process at this PID must "
                             "be left completely untouched")
        finally:
            unrelated.kill()
            await unrelated.wait()
            # The row now points at `unrelated`'s PID, not the process
            # start_background() actually spawned -- that real process is
            # orphaned from the DB's perspective after the overwrite above,
            # so it needs killing directly here rather than via stop_background().
            if started is not None:
                await _kill_process_tree(started["pid"], grace_seconds=2.0)

    async def test_stop_unknown_process_id_returns_error(self):
        from core.shell.executor import ShellExecutor
        result = await ShellExecutor.stop_background("not-a-real-id", self.TEST_USER)
        self.assertIn("error", result)

    async def test_stop_wrong_user_is_rejected(self):
        from core.shell.executor import ShellExecutor

        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)
        try:
            result = await ShellExecutor.stop_background(started["process_id"], "someone-else")
            self.assertIn("error", result, "a process must not be stoppable by a different user_id")
        finally:
            await ShellExecutor.stop_background(started["process_id"], self.TEST_USER)

    async def test_start_command_not_found_returns_error_not_exception(self):
        from core.shell.executor import ShellExecutor
        result = await ShellExecutor.start_background(cmd=["not-a-real-binary-xyz"], user_id=self.TEST_USER)
        self.assertIn("error", result)


class TestStartBackgroundJobBreakawayFallback(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):
    """
    V6.58 Phase 3 live-testing finding: CREATE_NEW_PROCESS_GROUP alone does
    not exempt a spawned child from a Windows Job Object the backend's own
    process happens to be inside -- confirmed live, a
    keep_alive_on_app_shutdown=True process still died when the backend's
    whole process tree was torn down at app-quit, even though
    /internal/shutdown correctly never touched its DB row.
    CREATE_BREAKAWAY_FROM_JOB fixes that, but fails outright (WinError 5,
    "Access is denied") when the ambient job doesn't set
    JOB_OBJECT_LIMIT_BREAKAWAY_OK -- also confirmed live, in this exact dev
    environment. These tests cover the fallback that keeps
    start_background() working either way, by mocking
    asyncio.create_subprocess_exec rather than relying on this environment's
    own (not portable/guaranteed) job restrictions.
    """

    TEST_USER = "bgproc-breakaway-fallback-user"

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        shutil.rmtree(get_user_root(self.TEST_USER), ignore_errors=True)

    @unittest.skipUnless(platform.system() == "Windows", "CREATE_BREAKAWAY_FROM_JOB is Windows-only")
    async def test_falls_back_and_still_starts_when_breakaway_is_denied(self):
        from core.shell.executor import ShellExecutor, _pid_alive

        real_create_subprocess_exec = asyncio.create_subprocess_exec
        calls = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append(kwargs.get("creationflags"))
            if len(calls) == 1:
                raise OSError(5, "Access is denied")
            return await real_create_subprocess_exec(*args, **kwargs)

        cmd = [sys.executable, "-c", "import time; time.sleep(3)"]
        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            result = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)

        self.assertNotIn("error", result, "must fall back and still start the process, not fail outright")
        self.assertEqual(len(calls), 2, "must retry exactly once after the first attempt is denied")
        self.assertTrue(
            calls[0] & subprocess.CREATE_BREAKAWAY_FROM_JOB,
            "first attempt must have requested breakaway",
        )
        self.assertFalse(
            calls[1] & subprocess.CREATE_BREAKAWAY_FROM_JOB,
            "fallback attempt must not request breakaway again",
        )
        self.assertTrue(_pid_alive(result["pid"]))

        await ShellExecutor.stop_background(result["process_id"], self.TEST_USER)

    @unittest.skipUnless(platform.system() == "Windows", "CREATE_BREAKAWAY_FROM_JOB is Windows-only")
    async def test_returns_error_when_both_attempts_are_denied(self):
        from core.shell.executor import ShellExecutor

        async def always_denied(*args, **kwargs):
            raise OSError(5, "Access is denied")

        cmd = [sys.executable, "-c", "import time; time.sleep(3)"]
        with patch("asyncio.create_subprocess_exec", side_effect=always_denied):
            result = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)

        self.assertIn("error", result)

    async def test_command_not_found_does_not_attempt_a_fallback(self):
        from core.shell.executor import ShellExecutor

        calls = []

        async def counting_not_found(*args, **kwargs):
            calls.append(1)
            raise FileNotFoundError()

        with patch("asyncio.create_subprocess_exec", side_effect=counting_not_found):
            result = await ShellExecutor.start_background(
                cmd=["not-a-real-binary-xyz"], user_id=self.TEST_USER,
            )
        self.assertIn("error", result)
        self.assertEqual(len(calls), 1, "a command-not-found error must not trigger a breakaway-fallback retry")


# ===========================================================================
# background_process tool
# ===========================================================================

class TestBackgroundProcessTool(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    TEST_USER = "bgproc-tool-test-user"

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        shutil.rmtree(get_user_root(self.TEST_USER), ignore_errors=True)

    def _make_tool(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        from core.shell.safety import ShellSafety
        load_all_tools()
        tool_cls = DefaultToolRegistry.all()["background_process"]
        tool = tool_cls()
        execution = MagicMock()
        execution.user_id = self.TEST_USER
        execution.conversation_id = "conv-1"
        execution.parent_project_id = None
        execution.execution_id = "exec-1"
        execution.project_workspace = None
        tool.execution = execution

        # Interpreter-location independence (V6.59). ShellSafety deliberately
        # blocks absolute paths under personal locations and only allow-lists
        # machine-wide ones (Program Files, ProgramData, Windows -- see
        # core/shell/safety.py), so a sys.executable that lives in a venv, a
        # conda env or a per-user install is rejected before the process ever
        # starts. These lifecycle tests are about dedup/list/tail/stop, not the
        # safety rules (test_start_blocked_by_shell_safety covers those), so
        # neutralise only the interpreter's LOCATION and keep the real check
        # for every other argument.
        real_check = ShellSafety.check

        def _check_ignoring_interpreter_location(*args, **kwargs):
            cmd = kwargs.get("cmd")
            if cmd and cmd[0] == sys.executable:
                kwargs["cmd"] = ["python", *cmd[1:]]
            return real_check(*args, **kwargs)

        patcher = patch.object(ShellSafety, "check", side_effect=_check_ignoring_interpreter_location)
        patcher.start()
        self.addCleanup(patcher.stop)
        return tool

    def test_registered_as_mutating(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        from core.tools.base import ToolAuthority
        load_all_tools()
        tool_cls = DefaultToolRegistry.all()["background_process"]
        self.assertEqual(tool_cls.authority, ToolAuthority.MUTATING)

    async def test_start_missing_command_returns_error(self):
        tool = self._make_tool()
        result = await tool.run({"action": "start"})
        self.assertIn("error", result.result)

    async def test_start_then_list_then_tail_then_stop_end_to_end(self):
        tool = self._make_tool()

        started = await tool.run({
            "action": "start",
            "command": [sys.executable, "-c", "import time,sys; print('tool-output'); sys.stdout.flush(); time.sleep(2)"],
        })
        self.assertNotIn("error", started.result)
        process_id = started.result["process_id"]

        listed = await tool.run({"action": "list"})
        self.assertEqual(len(listed.result["processes"]), 1)
        self.assertEqual(listed.result["processes"][0]["process_id"], process_id)

        await asyncio.sleep(0.5)
        tailed = await tool.run({"action": "tail", "process_id": process_id})
        self.assertIn("tool-output", tailed.result["new_output"])

        stopped = await tool.run({"action": "stop", "process_id": process_id})
        self.assertEqual(stopped.result["status"], "stopped")

        listed_after = await tool.run({"action": "list"})
        self.assertEqual(len(listed_after.result["processes"]), 0, "a stopped process must drop out of the running list")

    async def test_start_with_keep_alive_flag_persists_and_lists(self):
        tool = self._make_tool()

        started = await tool.run({
            "action": "start",
            "command": [sys.executable, "-c", "import time; time.sleep(5)"],
            "keep_alive_on_app_shutdown": True,
        })
        self.assertNotIn("error", started.result)
        process_id = started.result["process_id"]

        listed = await tool.run({"action": "list"})
        row = listed.result["processes"][0]
        self.assertEqual(row["process_id"], process_id)
        self.assertTrue(row["keep_alive_on_app_shutdown"])

        await tool.run({"action": "stop", "process_id": process_id})

    async def test_start_without_keep_alive_flag_defaults_false(self):
        tool = self._make_tool()

        started = await tool.run({
            "action": "start",
            "command": [sys.executable, "-c", "import time; time.sleep(5)"],
        })
        process_id = started.result["process_id"]

        listed = await tool.run({"action": "list"})
        self.assertFalse(listed.result["processes"][0]["keep_alive_on_app_shutdown"])

        await tool.run({"action": "stop", "process_id": process_id})

    async def test_start_blocked_by_shell_safety(self):
        tool = self._make_tool()
        # A destructive/catastrophic pattern ShellSafety should BLOCK
        # regardless of platform -- reuses the same safety layer the shell
        # tool itself goes through, not a separate bypassable path.
        result = await tool.run({"action": "start", "command": ["rm", "-rf", "/"]})
        self.assertTrue(result.result.get("blocked"))

    async def test_starting_the_same_command_in_the_same_folder_reuses_the_live_process(self):
        """Seen live: eight starts of one dev server, four rows still 'running' at the end."""
        tool = self._make_tool()
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        first = await tool.run({"action": "start", "command": cmd})
        try:
            self.assertNotIn("error", first.result)
            self.assertNotIn("already_running", first.result)

            second = await tool.run({"action": "start", "command": cmd})
            self.assertTrue(second.result.get("already_running"), second.result)
            self.assertEqual(second.result["process_id"], first.result["process_id"])
            self.assertEqual(second.result["pid"], first.result["pid"])
            self.assertEqual(second.result["status"], "running")
            self.assertIn("action='stop'", second.result["note"])

            listed = await tool.run({"action": "list"})
            self.assertEqual(len(listed.result["processes"]), 1, "no second process may have been spawned")
        finally:
            await tool.run({"action": "stop", "process_id": first.result["process_id"]})

    async def test_reuse_recognises_the_same_folder_however_it_is_spelled(self):
        from utils.workspace import get_conversation_dir
        tool = self._make_tool()
        site = os.path.join(str(get_conversation_dir(self.TEST_USER, "conv-1")), "site")
        os.makedirs(site, exist_ok=True)
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        # A RELATIVE cwd resolves into the conversation's folder (like the file tools), not the
        # server's own folder -- cwd="site" used to fail with WinError 267.
        first = await tool.run({"action": "start", "command": cmd, "cwd": "site"})
        try:
            self.assertNotIn("error", first.result, first.result)
            self.assertEqual(os.path.normcase(os.path.normpath(first.result["cwd"])),
                             os.path.normcase(os.path.normpath(site)))
            second = await tool.run({"action": "start", "command": cmd, "cwd": site})
            self.assertTrue(second.result.get("already_running"), second.result)
            third = await tool.run({"action": "start", "command": cmd, "cwd": "./site"})
            self.assertTrue(third.result.get("already_running"), third.result)
            self.assertEqual(second.result["process_id"], third.result["process_id"])
            self.assertEqual(second.result["process_id"], first.result["process_id"])
        finally:
            await tool.run({"action": "stop", "process_id": first.result["process_id"]})

    async def test_a_different_command_starts_a_new_process(self):
        tool = self._make_tool()
        a = await tool.run({"action": "start", "command": [sys.executable, "-c", "import time; time.sleep(30)"]})
        b = await tool.run({"action": "start", "command": [sys.executable, "-c", "import time; time.sleep(31)"]})
        try:
            self.assertNotIn("already_running", b.result)
            self.assertNotEqual(a.result["process_id"], b.result["process_id"])
        finally:
            for r in (a, b):
                await tool.run({"action": "stop", "process_id": r.result["process_id"]})

    async def test_after_a_stop_the_same_command_starts_a_fresh_process(self):
        tool = self._make_tool()
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        first = await tool.run({"action": "start", "command": cmd})
        await tool.run({"action": "stop", "process_id": first.result["process_id"]})
        again = await tool.run({"action": "start", "command": cmd})
        try:
            self.assertNotIn("already_running", again.result)
            self.assertNotEqual(again.result["process_id"], first.result["process_id"])
        finally:
            await tool.run({"action": "stop", "process_id": again.result["process_id"]})

    async def test_a_row_whose_process_died_on_its_own_is_not_reused(self):
        tool = self._make_tool()
        cmd = [sys.executable, "-c", "pass"]  # exits immediately
        first = await tool.run({"action": "start", "command": cmd})
        await asyncio.sleep(1.0)
        again = await tool.run({"action": "start", "command": cmd})
        try:
            self.assertNotIn("already_running", again.result, "a dead process must not be handed back as running")
            self.assertNotEqual(again.result["process_id"], first.result["process_id"])
        finally:
            for r in (first, again):
                await tool.run({"action": "stop", "process_id": r.result["process_id"]})

    async def test_tail_unknown_process_id_returns_error(self):
        tool = self._make_tool()
        result = await tool.run({"action": "tail", "process_id": "does-not-exist"})
        self.assertIn("error", result.result)

    def test_unknown_action_rejected_by_schema_before_reaching_run(self):
        # BackgroundProcessArgs.action is Literal["start","list","tail","stop"],
        # so an invalid action never reaches run()'s own "Unknown action"
        # fallback branch at all -- Pydantic validation is the actual guard.
        # Confirms that fallback is defensive dead code, not a reachable path.
        import pydantic
        from tools.default.process_tools import BackgroundProcessArgs
        with self.assertRaises(pydantic.ValidationError):
            BackgroundProcessArgs(action="bogus")


# ===========================================================================
# core/tasks/cleanup.py -- background process pass
# ===========================================================================

class TestCleanupBackgroundProcessPass(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    TEST_USER = "bgproc-cleanup-test-user"

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        shutil.rmtree(get_user_root(self.TEST_USER), ignore_errors=True)

    async def test_force_stops_process_past_max_lifetime(self):
        from core.db.repository import ProcessRepo
        from core.shell.executor import ShellExecutor, _pid_alive
        from core.tasks.cleanup import _purge_background_process_data
        from sqlalchemy import update
        from core.db.models import BackgroundProcess

        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)
        process_id, pid = started["process_id"], started["pid"]

        # Backdate started_at so it looks like it's been running far longer
        # than BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS, without needing the
        # test to actually wait hours.
        async with self.Session() as db:
            await db.execute(
                update(BackgroundProcess).where(BackgroundProcess.id == process_id)
                .values(started_at=datetime.now(timezone.utc) - timedelta(days=1))
            )
            await db.commit()

        with patch("core.tasks.cleanup.BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS", 60):
            summary = await _purge_background_process_data()

        self.assertEqual(summary["force_stopped"], 1)
        self.assertFalse(_pid_alive(pid), "the stale process must actually be killed, not just marked stopped")

        async with self.Session() as db:
            row = await ProcessRepo.get(db, process_id)
        self.assertEqual(row.status, "stopped")

    async def test_leaves_fresh_running_process_alone(self):
        from core.shell.executor import ShellExecutor, _pid_alive
        from core.tasks.cleanup import _purge_background_process_data

        cmd = [sys.executable, "-c", "import time; time.sleep(3)"]
        started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)

        summary = await _purge_background_process_data()
        self.assertEqual(summary["force_stopped"], 0)
        self.assertTrue(_pid_alive(started["pid"]))

        await ShellExecutor.stop_background(started["process_id"], self.TEST_USER)

    async def test_purges_old_terminal_rows(self):
        from core.db.repository import ProcessRepo
        from core.tasks.cleanup import _purge_background_process_data
        from sqlalchemy import update
        from core.db.models import BackgroundProcess

        tmp_dir = os.path.join(os.path.dirname(__file__), "_tmp_cleanup_purge_test")
        os.makedirs(tmp_dir, exist_ok=True)
        log_path = os.path.join(tmp_dir, "old.log")
        with open(log_path, "w") as f:
            f.write("x")

        try:
            async with self.Session() as db:
                row = await ProcessRepo.create(
                    db, user_id=self.TEST_USER, command=["a"], cwd=".", log_path=log_path, pid=999999,
                )
                await ProcessRepo.mark_status(db, row.id, "stopped")
                await db.execute(
                    update(BackgroundProcess).where(BackgroundProcess.id == row.id)
                    .values(ended_at=datetime.now(timezone.utc) - timedelta(hours=100))
                )
                await db.commit()

            with patch("core.tasks.cleanup.BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS", 24):
                summary = await _purge_background_process_data()

            self.assertEqual(summary["rows_purged"], 1)
            async with self.Session() as db:
                self.assertIsNone(await ProcessRepo.get(db, row.id))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ===========================================================================
# Phase 3 (background-process-supervision plan) -- GET /executions/running
# background-process rows, and the human-facing POST /processes/{id}/stop
# endpoint. Both are thin wiring on top of the already-tested ProcessRepo/
# ShellExecutor pieces above, so these tests call the FastAPI route
# functions directly (auth=... passed as a plain dict, bypassing Depends --
# same approach as calling any other plain async function) rather than
# spinning up a full TestClient.
# ===========================================================================

class TestRunningExecutionsIncludesBackgroundProcesses(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    TEST_USER = "bgproc-running-endpoint-user"

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        shutil.rmtree(get_user_root(self.TEST_USER), ignore_errors=True)

    async def test_includes_running_background_process_with_correct_fields(self):
        from core.shell.executor import ShellExecutor
        from api.executions import list_running_executions

        cmd = [sys.executable, "-c", "import time; time.sleep(3)"]
        started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)
        process_id = started["process_id"]

        try:
            summaries = await list_running_executions(auth={"user_id": self.TEST_USER})
            matches = [s for s in summaries if s.execution_id == process_id]
            self.assertEqual(len(matches), 1)
            row = matches[0]
            self.assertEqual(row.execution_mode, "background_process")
            self.assertEqual(row.status, "running")
            self.assertEqual(row.pid, started["pid"])
            self.assertEqual(row.log_path, started["log_path"])
            self.assertEqual(row.conversation_id, "", "no conversation_id was given at start -- must fall back to ''")
            self.assertIsNotNone(row.started_at)
            self.assertEqual(row.command, " ".join(cmd))
            self.assertFalse(row.keep_alive_on_app_shutdown, "default must be False (session-scoped)")
        finally:
            await ShellExecutor.stop_background(process_id, self.TEST_USER)

    async def test_running_background_process_reflects_keep_alive_true(self):
        from core.shell.executor import ShellExecutor
        from api.executions import list_running_executions

        cmd = [sys.executable, "-c", "import time; time.sleep(3)"]
        started = await ShellExecutor.start_background(
            cmd=cmd, user_id=self.TEST_USER, keep_alive_on_app_shutdown=True,
        )
        process_id = started["process_id"]

        try:
            summaries = await list_running_executions(auth={"user_id": self.TEST_USER})
            matches = [s for s in summaries if s.execution_id == process_id]
            self.assertTrue(matches[0].keep_alive_on_app_shutdown)
        finally:
            await ShellExecutor.stop_background(process_id, self.TEST_USER)

    async def test_command_field_is_none_for_non_background_process_rows(self):
        # A frontend-facing field added only for execution_mode="background_process"
        # rows must not silently appear (even as None-by-coincidence) for the
        # in-memory execution_registry rows -- covered implicitly by the Pydantic
        # default, but asserted directly since this is the one place a reviewer
        # would notice a regression if the two code paths ever got merged.
        from api.executions import RunningExecutionSummary
        row = RunningExecutionSummary(
            conversation_id="conv-1", execution_id="exec-1",
            execution_mode="conversational", status="running",
        )
        self.assertIsNone(row.command)

    async def test_stopped_background_process_is_not_listed(self):
        from core.shell.executor import ShellExecutor
        from api.executions import list_running_executions

        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        started = await ShellExecutor.start_background(cmd=cmd, user_id=self.TEST_USER)
        process_id = started["process_id"]
        await ShellExecutor.stop_background(process_id, self.TEST_USER)

        summaries = await list_running_executions(auth={"user_id": self.TEST_USER})
        self.assertNotIn(process_id, [s.execution_id for s in summaries])

    async def test_other_users_background_process_is_not_listed(self):
        from core.shell.executor import ShellExecutor
        from api.executions import list_running_executions

        cmd = [sys.executable, "-c", "import time; time.sleep(3)"]
        started = await ShellExecutor.start_background(cmd=cmd, user_id="someone-else-entirely")
        try:
            summaries = await list_running_executions(auth={"user_id": self.TEST_USER})
            self.assertNotIn(started["process_id"], [s.execution_id for s in summaries])
        finally:
            await ShellExecutor.stop_background(started["process_id"], "someone-else-entirely")
            from utils.workspace import get_user_root
            shutil.rmtree(get_user_root("someone-else-entirely"), ignore_errors=True)


class TestProcessesStopEndpoint(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    TEST_USER = "bgproc-stop-endpoint-user"

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        shutil.rmtree(get_user_root(self.TEST_USER), ignore_errors=True)

    async def _start(self, user_id=None, seconds=30):
        from core.shell.executor import ShellExecutor
        cmd = [sys.executable, "-c", f"import time; time.sleep({seconds})"]
        return await ShellExecutor.start_background(cmd=cmd, user_id=user_id or self.TEST_USER)

    async def test_stop_kills_process_and_returns_stopped_status(self):
        from core.shell.executor import _pid_alive
        from api.processes import stop_process

        started = await self._start()
        result = await stop_process(started["process_id"], auth={"user_id": self.TEST_USER})

        self.assertEqual(result, {"status": "stopped"})
        self.assertFalse(_pid_alive(started["pid"]))

    async def test_stop_nonexistent_process_id_raises_404(self):
        from fastapi import HTTPException
        from api.processes import stop_process

        with self.assertRaises(HTTPException) as ctx:
            await stop_process("not-a-real-process-id", auth={"user_id": self.TEST_USER})
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_stop_unowned_process_raises_404_not_409(self):
        # A process belonging to a different user must look exactly like a
        # nonexistent one -- 404, not a 409/403 that would leak its existence.
        from fastapi import HTTPException
        from core.shell.executor import ShellExecutor
        from api.processes import stop_process

        started = await self._start(user_id="a-different-user")
        try:
            with self.assertRaises(HTTPException) as ctx:
                await stop_process(started["process_id"], auth={"user_id": self.TEST_USER})
            self.assertEqual(ctx.exception.status_code, 404)
        finally:
            await ShellExecutor.stop_background(started["process_id"], "a-different-user")
            from utils.workspace import get_user_root
            shutil.rmtree(get_user_root("a-different-user"), ignore_errors=True)

    async def test_stop_already_stopped_process_raises_409(self):
        from fastapi import HTTPException
        from api.processes import stop_process

        started = await self._start()
        await stop_process(started["process_id"], auth={"user_id": self.TEST_USER})

        with self.assertRaises(HTTPException) as ctx:
            await stop_process(started["process_id"], auth={"user_id": self.TEST_USER})
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_stop_publishes_process_stopped_event(self):
        from api.processes import stop_process

        started = await self._start()
        with patch("core.events_publisher.publish_user_event") as mock_publish:
            mock_publish.return_value = None
            await stop_process(started["process_id"], auth={"user_id": self.TEST_USER})

        mock_publish.assert_called_once()
        _, kwargs = mock_publish.call_args
        self.assertEqual(kwargs["user_id"], self.TEST_USER)
        self.assertEqual(kwargs["event"], "process.stopped")
        self.assertEqual(kwargs["execution_id"], started["process_id"])


class TestProcessesKeepAliveEndpoint(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):
    """PATCH /processes/{id}/keep-alive -- the Activity panel's per-process
    'keep running after EVA closes' toggle. Pure DB state, no OS process
    touched -- mirrors TestProcessesStopEndpoint's ownership/not-found
    coverage without needing to re-verify process-killing here too."""

    TEST_USER = "bgproc-keepalive-endpoint-user"

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        shutil.rmtree(get_user_root(self.TEST_USER), ignore_errors=True)

    async def _start(self, user_id=None, seconds=30):
        from core.shell.executor import ShellExecutor
        cmd = [sys.executable, "-c", f"import time; time.sleep({seconds})"]
        return await ShellExecutor.start_background(cmd=cmd, user_id=user_id or self.TEST_USER)

    async def test_sets_keep_alive_true(self):
        from api.processes import set_process_keep_alive
        from api.schemas.requests import SetKeepAliveOnAppShutdownRequest
        from core.db.repository import ProcessRepo

        started = await self._start()
        try:
            result = await set_process_keep_alive(
                started["process_id"],
                SetKeepAliveOnAppShutdownRequest(keep_alive_on_app_shutdown=True),
                auth={"user_id": self.TEST_USER},
            )
            self.assertEqual(result, {"status": "ok"})

            async with self.Session() as db:
                row = await ProcessRepo.get(db, started["process_id"])
            self.assertTrue(row.keep_alive_on_app_shutdown)
        finally:
            from core.shell.executor import ShellExecutor
            await ShellExecutor.stop_background(started["process_id"], self.TEST_USER)

    async def test_toggles_back_to_false(self):
        from api.processes import set_process_keep_alive
        from api.schemas.requests import SetKeepAliveOnAppShutdownRequest
        from core.db.repository import ProcessRepo

        started = await self._start()
        try:
            await set_process_keep_alive(
                started["process_id"],
                SetKeepAliveOnAppShutdownRequest(keep_alive_on_app_shutdown=True),
                auth={"user_id": self.TEST_USER},
            )
            await set_process_keep_alive(
                started["process_id"],
                SetKeepAliveOnAppShutdownRequest(keep_alive_on_app_shutdown=False),
                auth={"user_id": self.TEST_USER},
            )
            async with self.Session() as db:
                row = await ProcessRepo.get(db, started["process_id"])
            self.assertFalse(row.keep_alive_on_app_shutdown)
        finally:
            from core.shell.executor import ShellExecutor
            await ShellExecutor.stop_background(started["process_id"], self.TEST_USER)

    async def test_nonexistent_process_id_raises_404(self):
        from fastapi import HTTPException
        from api.processes import set_process_keep_alive
        from api.schemas.requests import SetKeepAliveOnAppShutdownRequest

        with self.assertRaises(HTTPException) as ctx:
            await set_process_keep_alive(
                "not-a-real-process-id",
                SetKeepAliveOnAppShutdownRequest(keep_alive_on_app_shutdown=True),
                auth={"user_id": self.TEST_USER},
            )
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_unowned_process_raises_404_not_409(self):
        from fastapi import HTTPException
        from core.shell.executor import ShellExecutor
        from api.processes import set_process_keep_alive
        from api.schemas.requests import SetKeepAliveOnAppShutdownRequest

        started = await self._start(user_id="a-different-user-keepalive")
        try:
            with self.assertRaises(HTTPException) as ctx:
                await set_process_keep_alive(
                    started["process_id"],
                    SetKeepAliveOnAppShutdownRequest(keep_alive_on_app_shutdown=True),
                    auth={"user_id": self.TEST_USER},
                )
            self.assertEqual(ctx.exception.status_code, 404)
        finally:
            await ShellExecutor.stop_background(started["process_id"], "a-different-user-keepalive")
            from utils.workspace import get_user_root
            shutil.rmtree(get_user_root("a-different-user-keepalive"), ignore_errors=True)

    async def test_already_stopped_process_raises_409(self):
        from fastapi import HTTPException
        from core.shell.executor import ShellExecutor
        from api.processes import set_process_keep_alive
        from api.schemas.requests import SetKeepAliveOnAppShutdownRequest

        started = await self._start()
        await ShellExecutor.stop_background(started["process_id"], self.TEST_USER)

        with self.assertRaises(HTTPException) as ctx:
            await set_process_keep_alive(
                started["process_id"],
                SetKeepAliveOnAppShutdownRequest(keep_alive_on_app_shutdown=True),
                auth={"user_id": self.TEST_USER},
            )
        self.assertEqual(ctx.exception.status_code, 409)


# ===========================================================================
# shell tool description -- must steer long-running processes to background_process
# ===========================================================================

class TestShellToolSteersToBackgroundProcess(unittest.TestCase):
    """
    The shell tool's description used to tell the model to use trigger_daemon()
    for dev servers -- wrong (a daemon calls the same blocking shell tool) and
    predates background_process. Live test with Claude Haiku 4.5 (2026-09-21):
    the model followed it, ran `python -m http.server` through the blocking
    shell tool, and hung until the command timeout instead of using
    background_process.
    """

    def _description(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        return DefaultToolRegistry.all()["shell"].description

    def test_description_points_at_background_process(self):
        self.assertIn("background_process", self._description())

    def test_description_no_longer_recommends_trigger_daemon_for_servers(self):
        desc = self._description()
        self.assertNotIn("use trigger_daemon() instead", desc)
        self.assertNotIn("trigger_daemon", desc)

    def test_description_says_what_the_tool_is_for_and_its_limits(self):
        # The rewrite must not make shell look obsolete: it states the positive
        # use (finite commands), the real timeouts, and the exact exclusion.
        desc = self._description()
        self.assertIn("finite", desc)
        self.assertIn("tests, installs, builds", desc)
        self.assertIn("30 seconds", desc)
        self.assertIn("max 300", desc)
        self.assertIn("never exits", desc)
        self.assertIn("300 seconds", desc)


# ===========================================================================
# background_process tail: wait_seconds
# ===========================================================================

class TestBackgroundProcessTailWait(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):
    """
    tail used to return immediately, so waiting on a build or a server meant
    one agent step per poll and burned the step budget. wait_seconds blocks
    (bounded) until new output arrives or the process exits.
    """

    TEST_USER = "bgproc-tailwait-test-user"

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        shutil.rmtree(get_user_root(self.TEST_USER), ignore_errors=True)

    def _tool(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool = DefaultToolRegistry.all()["background_process"]()
        execution = MagicMock()
        execution.user_id = self.TEST_USER
        execution.conversation_id = "conv-1"
        execution.parent_project_id = None
        execution.execution_id = "exec-1"
        execution.project_workspace = None
        tool.execution = execution
        return tool

    async def _start(self, code):
        from core.shell.executor import ShellExecutor
        started = await ShellExecutor.start_background(
            cmd=[sys.executable, "-c", code], user_id=self.TEST_USER,
        )
        self.assertNotIn("error", started)
        return started["process_id"]

    async def _timed_tail(self, process_id, wait_seconds):
        t0 = asyncio.get_event_loop().time()
        result = await self._tool().run({"action": "tail", "process_id": process_id, "wait_seconds": wait_seconds})
        return result.result, asyncio.get_event_loop().time() - t0

    async def test_returns_as_soon_as_output_arrives(self):
        pid = await self._start("import time,sys; time.sleep(1.5); print('ready', flush=True); time.sleep(30)")
        try:
            result, elapsed = await self._timed_tail(pid, 20)
            self.assertIn("ready", result["new_output"])
            self.assertEqual(result["status"], "running")
            self.assertLess(elapsed, 10, "must return when output arrives, not wait out the full 20 s")
            self.assertGreaterEqual(elapsed, 1.0, "must actually have waited for the output")
            self.assertIn("waited_seconds", result)
        finally:
            from core.shell.executor import ShellExecutor
            await ShellExecutor.stop_background(pid, self.TEST_USER)

    async def test_returns_as_soon_as_the_process_exits(self):
        pid = await self._start("import time; time.sleep(1.5)")  # exits silently
        result, elapsed = await self._timed_tail(pid, 30)
        self.assertEqual(result["status"], "exited")
        self.assertEqual(result["new_output"], "")
        self.assertLess(elapsed, 15)

    async def test_waits_the_full_time_when_nothing_happens(self):
        pid = await self._start("import time; time.sleep(30)")
        try:
            result, elapsed = await self._timed_tail(pid, 2)
            self.assertEqual(result["status"], "running")
            self.assertEqual(result["new_output"], "")
            self.assertGreaterEqual(elapsed, 1.8)
            self.assertLess(elapsed, 8)
        finally:
            from core.shell.executor import ShellExecutor
            await ShellExecutor.stop_background(pid, self.TEST_USER)

    async def test_zero_wait_returns_immediately_as_before(self):
        pid = await self._start("import time; time.sleep(30)")
        try:
            result, elapsed = await self._timed_tail(pid, 0)
            self.assertLess(elapsed, 1.5)
            self.assertNotIn("waited_seconds", result)
        finally:
            from core.shell.executor import ShellExecutor
            await ShellExecutor.stop_background(pid, self.TEST_USER)

    async def test_unknown_process_id_errors_immediately_even_with_wait(self):
        result, elapsed = await self._timed_tail("not-a-real-process", 30)
        self.assertIn("error", result)
        self.assertLess(elapsed, 2)

    def test_wait_seconds_is_bounded(self):
        import pydantic
        from tools.default.process_tools import BackgroundProcessArgs, MAX_TAIL_WAIT_SECONDS
        self.assertEqual(BackgroundProcessArgs(action="tail", process_id="x", wait_seconds=MAX_TAIL_WAIT_SECONDS).wait_seconds,
                         MAX_TAIL_WAIT_SECONDS)
        with self.assertRaises(pydantic.ValidationError):
            BackgroundProcessArgs(action="tail", process_id="x", wait_seconds=MAX_TAIL_WAIT_SECONDS + 1)
        with self.assertRaises(pydantic.ValidationError):
            BackgroundProcessArgs(action="tail", process_id="x", wait_seconds=-1)

    def test_wait_seconds_accepts_null(self):
        """Models routinely send explicit nulls for optional fields (gpt-oss sent
        wait_seconds=null on action='list' in a live run and got a validation error)."""
        from tools.default.process_tools import BackgroundProcessArgs
        args = BackgroundProcessArgs(action="list", command=None, cwd=None,
                                     process_id=None, wait_seconds=None)
        self.assertIsNone(args.wait_seconds)

    def test_max_wait_stays_well_inside_the_tool_timeout(self):
        from config.settings import TOOL_EXECUTION_TIMEOUT_SECONDS
        from tools.default.process_tools import MAX_TAIL_WAIT_SECONDS
        self.assertLess(MAX_TAIL_WAIT_SECONDS, TOOL_EXECUTION_TIMEOUT_SECONDS)

    def test_description_tells_the_model_about_lifetime_and_waiting(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        desc = DefaultToolRegistry.all()["background_process"].description
        self.assertIn("OUTLIVES this run", desc)
        self.assertIn("wait_seconds", desc)
        self.assertIn("Activity panel", desc)


# ===========================================================================
# POST /internal/shutdown -- Phase 3, app-quit graceful shutdown
# ===========================================================================

class TestInternalShutdownEndpoint(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):
    """
    api/internal.py's POST /internal/shutdown -- the Electron-quit-time
    replacement for signal-based shutdown. Every test here mocks
    api.internal.signal.raise_signal so a real SIGINT is never actually
    raised in the test process itself;
    TestInternalShutdownEndpoint::test_schedules_self_directed_sigint_after_response
    is the one that actually asserts on that mock's call.
    """

    TEST_USER = "bgproc-shutdown-endpoint-user-a"
    OTHER_USER = "bgproc-shutdown-endpoint-user-b"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # graceful_shutdown() schedules a *delayed* self-directed SIGINT task.
        # A test's own `with patch(...)` around the call ends as soon as the
        # call returns, but the task fires _SELF_SIGNAL_DELAY_SECONDS later --
        # if the rest of the test (e.g. stopping real subprocesses in a
        # `finally`) outlasts that on a loaded machine, a REAL SIGINT hit the
        # pytest process itself and aborted the whole run. Keep the signal
        # mocked for the full lifetime of every test in this class; the one
        # test that asserts on the call installs its own, inner patch.
        _sig = patch("api.internal.signal.raise_signal")
        _sig.start()
        self.addCleanup(_sig.stop)

    async def asyncTearDown(self):
        # Drop any still-pending delayed-SIGINT task so it can never outlive
        # the test (and its mock) at all.
        for task in asyncio.all_tasks():
            coro = task.get_coro()
            if getattr(coro, "__name__", "") == "_delayed_self_sigint":
                task.cancel()
        await super().asyncTearDown()
        from utils.workspace import get_user_root
        for uid in (self.TEST_USER, self.OTHER_USER):
            shutil.rmtree(get_user_root(uid), ignore_errors=True)

    @staticmethod
    def _fake_request(host="127.0.0.1"):
        req = MagicMock()
        req.client.host = host
        return req

    async def _start(self, user_id=None, keep_alive=False, seconds=30):
        from core.shell.executor import ShellExecutor
        cmd = [sys.executable, "-c", f"import time; time.sleep({seconds})"]
        return await ShellExecutor.start_background(
            cmd=cmd, user_id=user_id or self.TEST_USER,
            keep_alive_on_app_shutdown=keep_alive,
        )

    async def test_localhost_guard_rejects_non_local_host(self):
        from fastapi import HTTPException
        from api.internal import graceful_shutdown

        with patch("api.internal.signal.raise_signal"):
            with self.assertRaises(HTTPException) as ctx:
                await graceful_shutdown(self._fake_request(host="203.0.113.7"))
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_no_running_processes_returns_zero_counts(self):
        from api.internal import graceful_shutdown

        with patch("api.internal.signal.raise_signal"):
            result = await graceful_shutdown(self._fake_request())

        self.assertEqual(
            result,
            {"status": "shutting_down", "processes_stopped": 0, "processes_kept_alive": 0},
        )

    async def test_stops_non_persistent_and_keeps_persistent_process_alive(self):
        from core.shell.executor import _pid_alive
        from api.internal import graceful_shutdown

        non_persistent = await self._start(keep_alive=False)
        persistent = await self._start(keep_alive=True)
        try:
            with patch("api.internal.signal.raise_signal"):
                result = await graceful_shutdown(self._fake_request())

            self.assertEqual(result["processes_stopped"], 1)
            self.assertEqual(result["processes_kept_alive"], 1)
            self.assertFalse(
                _pid_alive(non_persistent["pid"]),
                "a process without keep_alive_on_app_shutdown must be killed on shutdown",
            )
            self.assertTrue(
                _pid_alive(persistent["pid"]),
                "a process WITH keep_alive_on_app_shutdown=true must survive shutdown",
            )
        finally:
            from core.shell.executor import ShellExecutor
            await ShellExecutor.stop_background(persistent["process_id"], self.TEST_USER)

    async def test_stops_processes_across_all_users(self):
        """App-wide scope, confirmed by the user: shutdown is not scoped to
        one user_id -- it kills every eligible row across the whole DB."""
        from core.shell.executor import _pid_alive
        from api.internal import graceful_shutdown

        mine = await self._start(user_id=self.TEST_USER, keep_alive=False)
        theirs = await self._start(user_id=self.OTHER_USER, keep_alive=False)

        with patch("api.internal.signal.raise_signal"):
            result = await graceful_shutdown(self._fake_request())

        self.assertEqual(result["processes_stopped"], 2)
        self.assertFalse(_pid_alive(mine["pid"]))
        self.assertFalse(_pid_alive(theirs["pid"]))

    async def test_schedules_self_directed_sigint_after_response(self):
        from api.internal import graceful_shutdown, _SELF_SIGNAL_DELAY_SECONDS

        with patch("api.internal.signal.raise_signal") as mock_kill:
            result = await graceful_shutdown(self._fake_request())
            self.assertEqual(result["status"], "shutting_down")
            self.assertFalse(mock_kill.called, "the self-signal must not fire before the delay elapses")
            await asyncio.sleep(_SELF_SIGNAL_DELAY_SECONDS + 0.3)

        mock_kill.assert_called_once_with(signal.SIGINT)


if __name__ == "__main__":
    unittest.main()
