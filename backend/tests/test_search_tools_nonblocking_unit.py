"""
V6.60 -- search_files / find_files must not block the server.

Observed live: both tools were `async def` whose bodies walked the tree and read
files synchronously on the event loop. One model-initiated search over a big tree
froze every request from every user for ~30 s (unrelated /set-session and /chat
calls timed out). These tests pin: the scan runs off the loop, the WORK is bounded
(time, files, file size, line length), noise folders are skipped, cancellation stops
the scan, and results are unchanged for ordinary searches.

"Slow disk" is simulated deterministically (a patched open() that sleeps) instead of
building a huge tree, so the blocking behaviour is visible in well under a second.
"""
import asyncio
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.default import search_tools as st


def _tool(cls, workspace, cancelled=False):
    tool = cls()
    tool.execution = MagicMock()
    tool.execution.user_id = "u"
    tool.execution.conversation_id = "c"
    tool.execution.project_workspace = workspace
    tool.execution.cancel_token = NS(cancelled=cancelled)
    return tool


def _write(root, rel, text="", size=None):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if size is None else "x" * size, encoding="utf-8")
    return p


class _SlowDisk:
    """Context manager: N fake files under the walk root, each open() costs `delay` s."""

    def __init__(self, files=150, delay=0.01, line="no match here\n"):
        self.files, self.delay, self.line = files, delay, line
        self.opened = 0
        self._patches = []

    def __enter__(self):
        def fake_walk(top, *a, **k):
            yield (top, [], [f"f{i}.txt" for i in range(self.files)])

        def slow_open(*a, **k):
            self.opened += 1
            time.sleep(self.delay)
            return io.StringIO(self.line)

        self._patches = [
            patch.object(st.os, "walk", fake_walk),
            patch.object(st, "open", slow_open, create=True),
            patch.object(st.os.path, "getsize", lambda p: 10),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


async def _max_loop_gap(coro, tick=0.02):
    """Run `coro` while a ticker measures the longest stretch the event loop was unresponsive."""
    gaps, done = [], False
    task = asyncio.ensure_future(coro)

    async def ticker():
        last = time.monotonic()
        while not done:
            await asyncio.sleep(tick)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    tk = asyncio.ensure_future(ticker())
    try:
        result = await task
    finally:
        done = True
        await tk
    return result, max(gaps) if gaps else 0.0, len(gaps)


class EventLoopStaysResponsiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_files_does_not_block_the_loop(self):
        with tempfile.TemporaryDirectory() as tmp, _SlowDisk(files=150, delay=0.01):
            result, worst_gap, ticks = await _max_loop_gap(
                _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "zzz"}))
        self.assertNotIn("error", result.result)
        self.assertLess(worst_gap, 0.3, f"event loop stalled for {worst_gap:.2f}s during the scan")
        self.assertGreater(ticks, 20)       # it really was running concurrently with the scan (~1.5 s)

    async def test_find_files_does_not_block_the_loop(self):
        def slow_walk(top, *a, **k):
            for i in range(60):
                time.sleep(0.02)                         # each directory costs 20 ms of disk
                yield (top, [], [f"f{i}.log"])

        with tempfile.TemporaryDirectory() as tmp, patch.object(st.os, "walk", slow_walk):
            result, worst_gap, ticks = await _max_loop_gap(
                _tool(st.FindFilesTool, tmp).run({"path": ".", "pattern": "*.nomatch"}))
        self.assertEqual(result.result["file_count"], 0)
        self.assertLess(worst_gap, 0.3, f"event loop stalled for {worst_gap:.2f}s during the scan")

    async def test_two_concurrent_searches_overlap_instead_of_serialising(self):
        with tempfile.TemporaryDirectory() as tmp, _SlowDisk(files=100, delay=0.01):
            t0 = time.monotonic()
            await asyncio.gather(*[_tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "zzz"}) for _ in range(3)])
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.2)       # serial would be 3 x ~1.0 s = 3 s+


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_time_budget_stops_the_scan_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp, _SlowDisk(files=500, delay=0.02) as disk, \
                patch.object(st, "SEARCH_TOOL_TIME_BUDGET_SECONDS", 0.15):
            t0 = time.monotonic()
            result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "zzz"})
            elapsed = time.monotonic() - t0
        self.assertTrue(result.result.get("truncated"))
        self.assertIn("search budget", result.result["message"])
        self.assertLess(disk.opened, 500)
        self.assertLess(elapsed, 1.5)       # it stopped; it did not grind through 500 x 20 ms = 10 s

    async def test_files_visited_cap_stops_the_scan_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(20):
                _write(tmp, f"f{i}.txt", "nothing\n")
            with patch.object(st, "SEARCH_TOOL_MAX_FILES_VISITED", 5):
                result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "zzz"})
        self.assertTrue(result.result.get("truncated"))
        self.assertIn("looking at 5 files", result.result["message"])

    async def test_find_files_budgets_apply_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(20):
                _write(tmp, f"f{i}.txt")
            with patch.object(st, "SEARCH_TOOL_MAX_FILES_VISITED", 4):
                result = await _tool(st.FindFilesTool, tmp).run({"path": ".", "pattern": "*.nomatch"})
        self.assertTrue(result.result.get("truncated"))
        self.assertIn("file list", result.result["message"])

    async def test_a_complete_search_is_not_marked_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "a.txt", "needle\n")
            result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "needle"})
        self.assertEqual(result.result["match_count"], 1)
        self.assertNotIn("truncated", result.result)
        self.assertNotIn("message", result.result)

    async def test_result_cap_message_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "big.txt", "match\n" * 20)
            with patch.object(st, "SEARCH_TOOL_MAX_RESULTS", 5):
                result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "match"})
        self.assertEqual(result.result["match_count"], 5)
        self.assertEqual(result.result["message"], "Results truncated at 5 matches -- narrow the pattern or path.")

    async def test_find_files_result_cap_message_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(10):
                _write(tmp, f"f{i}.py")
            with patch.object(st, "SEARCH_TOOL_MAX_RESULTS", 3):
                result = await _tool(st.FindFilesTool, tmp).run({"path": ".", "pattern": "*.py"})
        self.assertEqual(result.result["file_count"], 3)
        self.assertTrue(result.result["truncated"])
        self.assertEqual(result.result["message"], "Results truncated at 3 files -- narrow the pattern or path.")


class WhatIsSkippedTests(unittest.IsolatedAsyncioTestCase):
    async def test_noise_folders_are_not_entered(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "src/app.py", "needle\n")
            for noisy in (".git", "node_modules", ".venv", "__pycache__"):
                _write(tmp, f"{noisy}/hidden.py", "needle\n")
            s = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "needle"})
            f = await _tool(st.FindFilesTool, tmp).run({"path": ".", "pattern": "*.py"})
        self.assertEqual([Path(m["path"]).name for m in s.result["matches"]], ["app.py"])
        self.assertEqual([Path(p).name for p in f.result["files"]], ["app.py"])

    async def test_pointing_path_straight_at_a_skipped_folder_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "node_modules/pkg/index.js", "needle\n")
            result = await _tool(st.SearchFilesTool, tmp).run({"path": "node_modules", "pattern": "needle"})
        self.assertEqual(result.result["match_count"], 1)

    async def test_skip_list_is_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "vendor/x.py", "needle\n")
            _write(tmp, "src/y.py", "needle\n")
            with patch.object(st, "SEARCH_TOOL_SKIP_DIRS", frozenset({"vendor"})):
                result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "needle"})
        self.assertEqual([Path(m["path"]).name for m in result.result["matches"]], ["y.py"])

    async def test_oversized_files_are_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "small.txt", "needle\n")
            _write(tmp, "huge.log", "needle " * 1000)
            with patch.object(st, "SEARCH_TOOL_MAX_FILE_BYTES", 1000):
                result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "needle"})
        self.assertEqual([Path(m["path"]).name for m in result.result["matches"]], ["small.txt"])

    async def test_only_the_start_of_a_very_long_line_is_matched_and_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "min.js", "a" * 500 + "needle\n")
            with patch.object(st, "SEARCH_TOOL_MAX_LINE_CHARS", 100):
                result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "needle"})
            self.assertEqual(result.result["match_count"], 0)
            _write(tmp, "ok.js", "needle" + "b" * 500 + "\n")
            with patch.object(st, "SEARCH_TOOL_MAX_LINE_CHARS", 100):
                result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "needle"})
        self.assertEqual(result.result["match_count"], 1)
        self.assertLessEqual(len(result.result["matches"][0]["text"]), 100)


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_the_awaiting_task_stops_the_worker_thread(self):
        with tempfile.TemporaryDirectory() as tmp, _SlowDisk(files=1000, delay=0.02) as disk:
            task = asyncio.ensure_future(_tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "zzz"}))
            await asyncio.sleep(0.15)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            at_cancel = disk.opened
            await asyncio.sleep(0.4)
            after = disk.opened
        self.assertGreater(at_cancel, 0)
        self.assertLessEqual(after - at_cancel, 3, "the scan kept running after the task was cancelled")

    async def test_the_execution_cancel_token_stops_the_scan(self):
        with tempfile.TemporaryDirectory() as tmp, _SlowDisk(files=1000, delay=0.01) as disk:
            tool = _tool(st.SearchFilesTool, tmp)
            task = asyncio.ensure_future(tool.run({"path": ".", "pattern": "zzz"}))
            await asyncio.sleep(0.1)
            tool.execution.cancel_token.cancelled = True
            result = await asyncio.wait_for(task, timeout=2)
        self.assertLess(disk.opened, 1000)
        self.assertTrue(result.result.get("truncated"))
        self.assertIn("cancelled", result.result["message"])

    async def test_a_mock_cancel_token_is_not_mistaken_for_a_cancellation(self):
        """Test doubles (MagicMock executions) have a truthy .cancelled -- only a real True counts."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "a.txt", "needle\n")
            tool = _tool(st.SearchFilesTool, tmp)
            tool.execution.cancel_token = MagicMock()
            result = await tool.run({"path": ".", "pattern": "needle"})
        self.assertEqual(result.result["match_count"], 1)


class BehaviourUnchangedTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_results_extension_filter_and_case_insensitivity(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "a/b/deep.py", "Hello World\nbye\n")
            _write(tmp, "a/other.txt", "hello world\n")
            r1 = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "hello", "case_sensitive": False})
            r2 = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "hello", "case_sensitive": False, "extensions": [".PY"]})
            r3 = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "hello"})
        self.assertEqual(r1.result["match_count"], 2)
        self.assertEqual(r2.result["match_count"], 1)
        self.assertEqual((r2.result["matches"][0]["line"], r2.result["matches"][0]["text"]), (1, "Hello World"))
        self.assertEqual(r3.result["match_count"], 1)                  # case-sensitive by default

    async def test_errors_are_returned_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("error", (await _tool(st.SearchFilesTool, tmp).run({"path": "nope", "pattern": "x"})).result)
            self.assertIn("error", (await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "["})).result)
            self.assertIn("error", (await _tool(st.FindFilesTool, tmp).run({"path": "nope", "pattern": "*"})).result)
            with patch.object(st.os, "walk", side_effect=RuntimeError("disk gone")):
                r = await _tool(st.FindFilesTool, tmp).run({"path": ".", "pattern": "*"})
        self.assertEqual(r.result, {"error": "disk gone"})

    async def test_unreadable_and_binary_files_are_skipped_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "bin.dat").write_bytes(b"\xff\xfe\x00\x01needle")
            _write(tmp, "ok.txt", "needle\n")
            result = await _tool(st.SearchFilesTool, tmp).run({"path": ".", "pattern": "needle"})
        self.assertEqual([Path(m["path"]).name for m in result.result["matches"]], ["ok.txt"])


class GuardTests(unittest.TestCase):
    def test_no_blocking_walk_inside_an_async_run_method(self):
        import ast
        tree = ast.parse(Path(st.__file__).read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        name = ast.unparse(sub.func)
                        if name in ("os.walk", "open") or name.endswith(".walk"):
                            offenders.append(f"{node.name}: {name}")
        self.assertEqual(offenders, [])

    def test_defaults_are_sane(self):
        from config import settings
        self.assertGreater(settings.SEARCH_TOOL_TIME_BUDGET_SECONDS, 0)
        self.assertLess(settings.SEARCH_TOOL_TIME_BUDGET_SECONDS, 60)   # below the dispatcher's tool timeout
        self.assertTrue({".git", "node_modules", ".venv", "__pycache__"} <= set(settings.SEARCH_TOOL_SKIP_DIRS))

    def test_tool_descriptions_tell_the_model_about_the_limits(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        for name in ("search_files", "find_files"):
            self.assertIn("time budget", DefaultToolRegistry.all()[name].description)


if __name__ == "__main__":
    unittest.main()
