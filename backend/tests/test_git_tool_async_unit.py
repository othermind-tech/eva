# tests/test_git_tool_async_unit.py
"""
Unit tests for B2-1.3 / B2-3.1 (2026-08-18): git_tool.py's async migration.

This file verifies: `_run_git()` used to call blocking `subprocess.run()` directly
inside `async def run()` (no `await`, no thread-wrap) -- every git
operation, including `git_push`'s 60s timeout, froze the entire FastAPI
process (every other user's requests, SSE streams, the worker heartbeat)
for the command's full duration. Now uses
`asyncio.create_subprocess_exec()` + `asyncio.wait_for()`, matching
`core/shell/executor.py`'s `ShellExecutor` and for the same documented
reason: a real subprocess handle can be `.kill()`ed on timeout, a
thread-wrapped blocking call cannot be interrupted once started.

No dedicated git-tool test file existed before this one -- prior coverage
was scattered inside test_features.py / exercised live via test_apis.py.
This is a clean new home, not a fork of existing coverage.

Mocked throughout (no real git binary invoked) except a final live smoke
test against this repo's own real git.

Run with:
    cd <project_root>
    python tests/test_git_tool_async_unit.py
    (or: pytest tests/test_git_tool_async_unit.py -v)
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRunGitResultShapeUnchanged(unittest.IsolatedAsyncioTestCase):
    """Confirms the return-value contract every one of _run_git's 8
    callers depends on is byte-for-byte unchanged from the pre-fix
    subprocess.run()-based implementation."""

    async def test_successful_command_returns_same_shape_as_before(self):
        from tools.default.git_tool import _run_git

        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(return_value=(b"On branch main\n", b""))
        fake_proc.returncode = 0

        with patch(
            "tools.default.git_tool.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ):
            result = await _run_git(["git", "status", "--short"], cwd="/tmp/repo")

        self.assertEqual(result["cmd"], ["git", "status", "--short"])
        self.assertEqual(result["cwd"], "/tmp/repo")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "On branch main\n")
        self.assertEqual(result["stderr"], "")
        self.assertNotIn("error", result)

    async def test_nonzero_exit_code_still_returned_not_raised(self):
        from tools.default.git_tool import _run_git

        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(return_value=(b"", b"fatal: not a git repository\n"))
        fake_proc.returncode = 128

        with patch(
            "tools.default.git_tool.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ):
            result = await _run_git(["git", "status"], cwd="/tmp/not-a-repo")

        self.assertEqual(result["exit_code"], 128)
        self.assertIn("fatal: not a git repository", result["stderr"])
        self.assertNotIn("error", result)

    async def test_command_not_found_returns_error_shape(self):
        from tools.default.git_tool import _run_git

        with patch(
            "tools.default.git_tool.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("no such file or directory: 'git'")),
        ):
            result = await _run_git(["git", "status"], cwd="/tmp/repo")

        self.assertEqual(result["cmd"], ["git", "status"])
        self.assertEqual(result["cwd"], "/tmp/repo")
        self.assertIn("error", result)

    async def test_timeout_kills_process_and_returns_error_shape(self):
        from tools.default.git_tool import _run_git

        fake_proc = MagicMock()

        async def _never_returns(*_args, **_kwargs):
            await asyncio.sleep(1000)

        fake_proc.communicate = _never_returns
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock()

        with patch(
            "tools.default.git_tool.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ):
            result = await _run_git(["git", "push"], cwd="/tmp/repo", timeout=0.05)

        fake_proc.kill.assert_called_once()
        self.assertEqual(result["cmd"], ["git", "push"])
        self.assertEqual(result["cwd"], "/tmp/repo")
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(result["timeout_seconds"], 0.05)


class TestRunGitDoesNotBlockEventLoop(unittest.IsolatedAsyncioTestCase):
    """The actual regression test for the bug being fixed -- proves a slow
    git call doesn't stall the event loop, not just that it returns the
    right shape (that alone wouldn't have caught the original bug)."""

    async def test_slow_git_command_does_not_block_concurrent_work(self):
        from tools.default.git_tool import _run_git

        counter = {"n": 0}

        async def ticker():
            for _ in range(15):
                await asyncio.sleep(0.01)
                counter["n"] += 1

        fake_proc = MagicMock()

        async def _slow_communicate(*_args, **_kwargs):
            await asyncio.sleep(0.2)
            return (b"ok", b"")

        fake_proc.communicate = _slow_communicate
        fake_proc.returncode = 0

        with patch(
            "tools.default.git_tool.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ):
            ticker_task = asyncio.create_task(ticker())
            result = await _run_git(["git", "push", "origin", "main"], cwd="/tmp/repo", timeout=5)
            await ticker_task

        self.assertGreater(
            counter["n"], 5,
            "an independent concurrent task should have made real progress "
            "while the git call was in flight -- if this is 0, the event "
            "loop was blocked, exactly the bug this fix closes",
        )
        self.assertEqual(result["stdout"], "ok")


class TestGitToolLiveSmoke(unittest.IsolatedAsyncioTestCase):
    """Real git binary, real (throwaway) repo -- confirms the async
    rewrite actually works end-to-end, not just against mocks."""

    async def test_git_status_against_a_real_repo(self):
        import tempfile
        import shutil
        from tools.default.git_tool import _run_git

        repo_dir = tempfile.mkdtemp(prefix="eva-git-tool-live-test-")
        try:
            init_result = await _run_git(["git", "init"], cwd=repo_dir)
            self.assertEqual(init_result.get("exit_code"), 0, init_result)

            status_result = await _run_git(["git", "status", "--short"], cwd=repo_dir)
            self.assertEqual(status_result["exit_code"], 0)
            self.assertEqual(status_result["stdout"].strip(), "")  # clean empty repo
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
