# tests/test_shell_timeout_tree_unit.py
"""
A `shell` command that times out or is cancelled must take its whole process tree with it.

Found live (2026-09-21, browser agent loop): an agent ran a dev server through `shell` wrapped in
``powershell -c "cd app; python -m http.server 8123"``. The 30 s timeout terminated the powershell
wrapper only; the python server it had launched was orphaned and kept the port open after the run.
`background_process` already kills the tree (taskkill /T); the foreground path did not.

These tests use REAL processes: a parent that launches a long-sleeping grandchild and records its pid.

Run with:
    pytest tests/test_shell_timeout_tree_unit.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


def _parent_command(pid_file: str):
    """A command that starts a grandchild sleeping 60 s, records its pid, then sleeps itself."""
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pid_file}', 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    return [sys.executable, "-c", code]


def _read_pid(pid_file: str, wait: float = 5.0) -> int:
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            text = open(pid_file).read().strip()
            if text:
                return int(text)
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError("the test command never recorded its grandchild's pid")


async def _wait_dead(pid: int, seconds: float = 5.0) -> bool:
    from core.shell.executor import _pid_alive
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        await asyncio.sleep(0.1)
    return not _pid_alive(pid)


def _force_kill(pid: int) -> None:
    """Test hygiene: never leave the sleeping grandchild behind if an assertion fails."""
    try:
        if os.name == "nt":
            import subprocess
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, 9)
    except Exception:
        pass


class TestShellTimeoutKillsTheTree(unittest.IsolatedAsyncioTestCase):

    async def test_grandchild_is_killed_when_the_command_times_out(self):
        from core.shell.executor import ShellExecutor

        pid_file = os.path.join(tempfile.mkdtemp(), "grandchild.pid")
        grandchild = None
        try:
            with patch("core.shell.executor._CANCEL_POLL_SECONDS", 0.1):
                result = await asyncio.wait_for(
                    ShellExecutor.run(
                        cmd=_parent_command(pid_file),
                        conversation_id="conv-tree-timeout", user_id="user-tree-timeout",
                        explicit_cwd=None, timeout=2,
                    ),
                    timeout=30,
                )
            grandchild = _read_pid(pid_file)
            self.assertIn("timeout", str(result.get("error", "")).lower(), result)
            self.assertTrue(await _wait_dead(grandchild),
                            "the grandchild survived the timeout -- an orphaned server would keep its port")
        finally:
            if grandchild:
                _force_kill(grandchild)

    async def test_grandchild_is_killed_when_the_command_is_cancelled(self):
        from core.shell.executor import ShellExecutor

        class _Token:
            cancelled = False

        token = _Token()
        pid_file = os.path.join(tempfile.mkdtemp(), "grandchild.pid")
        grandchild = None

        async def _cancel_when_started():
            # Poll without blocking the event loop -- the command has to be spawned by that same loop.
            deadline = time.time() + 10
            while time.time() < deadline and not (os.path.exists(pid_file) and open(pid_file).read().strip()):
                await asyncio.sleep(0.05)
            token.cancelled = True

        try:
            with patch("core.shell.executor._CANCEL_POLL_SECONDS", 0.1):
                canceller = asyncio.ensure_future(_cancel_when_started())
                result = await asyncio.wait_for(
                    ShellExecutor.run(
                        cmd=_parent_command(pid_file),
                        conversation_id="conv-tree-cancel", user_id="user-tree-cancel",
                        explicit_cwd=None, timeout=60, cancel_token=token,
                    ),
                    timeout=30,
                )
                await canceller
            grandchild = _read_pid(pid_file)
            self.assertTrue(result.get("cancelled"), result)
            self.assertTrue(await _wait_dead(grandchild),
                            "the grandchild survived the cancel -- an orphaned server would keep its port")
        finally:
            if grandchild:
                _force_kill(grandchild)

    async def test_a_fake_process_without_a_pid_is_left_alone(self):
        """Unit-test doubles (and anything else without an integer pid) must not break the timeout path."""
        from core.shell.executor import _kill_descendants_best_effort

        class _NoPid:
            returncode = None

        await _kill_descendants_best_effort(_NoPid())  # must not raise

    async def test_an_already_exited_process_is_skipped(self):
        from core.shell.executor import _kill_descendants_best_effort

        class _Done:
            pid = 999999
            returncode = 0

        await _kill_descendants_best_effort(_Done())  # must not raise or shell out


if __name__ == "__main__":
    unittest.main()
