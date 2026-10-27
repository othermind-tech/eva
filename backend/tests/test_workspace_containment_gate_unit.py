# tests/test_workspace_containment_gate_unit.py
"""
Unit tests for B2-1.1 / B2-1.1a (2026-08-18): the out-of-workspace approval
gate for MUTATING/DESTRUCTIVE file-tool calls.

This file verifies:
  - utils/workspace.py::is_path_in_workspace() -- new containment check,
    resolve_path_for_user() itself stays intentionally unrestricted.
  - core/tools/dispatcher.py::_should_require_approval() -- new forced-gate
    branch, checked before (and independent of) the existing
    tool_approval_mode matrix.
  - tools/default/file_tools.py -- WriteFileTool/EditFileTool/DeleteFileTool
    opt in via ToolBase.path_arg_name = "path".

Three layers tested, each in its own TestCase:
  1. is_path_in_workspace() in isolation (pure path-containment logic).
  2. _should_require_approval() in isolation (mocked session, no real tool).
  3. ToolDispatcher.execute_tool() end-to-end with a real WriteFileTool,
     proving the gate actually blocks the filesystem write when rejected,
     and that in-workspace behavior is unchanged.

Mocked throughout except layer 3's real file I/O (into a temp directory).

Run with:
    cd <project_root>
    python tests/test_workspace_containment_gate_unit.py
    (or: pytest tests/test_workspace_containment_gate_unit.py -v)
"""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Layer 1 — utils.workspace.is_path_in_workspace()
# ---------------------------------------------------------------------------

class TestIsPathInWorkspace(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="eva-workspace-gate-test-")
        self._patcher = patch("utils.workspace.WORKSPACE_ROOT", self._tmp)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_relative_path_inside_workspace_is_in_workspace(self):
        from utils.workspace import is_path_in_workspace
        self.assertTrue(is_path_in_workspace("user1", "notes.txt"))

    def test_relative_traversal_escaping_workspace_is_outside(self):
        from utils.workspace import is_path_in_workspace, get_user_workspace
        ws = pathlib.Path(get_user_workspace("user1")).resolve()
        # Go up more levels than the resolved workspace path actually has,
        # guaranteeing escape regardless of how deep the temp dir is.
        traversal = "/".join([".."] * (len(ws.parts) + 3)) + "/outside_target.txt"
        self.assertFalse(is_path_in_workspace("user1", traversal))

    def test_absolute_path_inside_workspace_is_in_workspace(self):
        from utils.workspace import is_path_in_workspace, get_user_workspace
        ws = get_user_workspace("user1")
        inside_abs = os.path.join(ws, "sub", "file.txt")
        self.assertTrue(is_path_in_workspace("user1", inside_abs))

    def test_absolute_path_outside_workspace_is_outside(self):
        from utils.workspace import is_path_in_workspace
        outside = os.path.join(
            tempfile.gettempdir(), f"eva-gate-test-outside-{os.getpid()}"
        )
        self.assertFalse(is_path_in_workspace("user1", outside))

    def test_project_workspace_root_used_when_provided(self):
        from utils.workspace import is_path_in_workspace
        proj_dir = tempfile.mkdtemp(prefix="eva-gate-test-project-")
        try:
            self.assertTrue(
                is_path_in_workspace("user1", "src/main.py", project_workspace=proj_dir)
            )
            proj_resolved = pathlib.Path(proj_dir).resolve()
            traversal = "/".join([".."] * (len(proj_resolved.parts) + 3)) + "/outside.txt"
            self.assertFalse(
                is_path_in_workspace("user1", traversal, project_workspace=proj_dir)
            )
        finally:
            shutil.rmtree(proj_dir, ignore_errors=True)

    def test_malformed_path_fails_closed(self):
        """An unresolvable path (embedded NUL byte -- invalid on both POSIX
        and Windows) must return False, not raise -- the gate must never be
        silently skipped just because a path was weird."""
        from utils.workspace import is_path_in_workspace
        self.assertFalse(is_path_in_workspace("user1", "bad\x00path"))


# ---------------------------------------------------------------------------
# Layer 2 — core.tools.dispatcher._should_require_approval()
# ---------------------------------------------------------------------------

class TestShouldRequireApprovalOutOfWorkspaceGate(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="eva-workspace-gate-test-")
        self._patcher = patch("utils.workspace.WORKSPACE_ROOT", self._tmp)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    async def test_mutating_in_workspace_auto_mode_not_gated_unchanged(self):
        from core.tools.dispatcher import _should_require_approval
        from core.tools.base import ToolAuthority
        tool_cls = SimpleNamespace(authority=ToolAuthority.MUTATING, path_arg_name="path")
        execution_obj = SimpleNamespace(project_workspace=None)
        with patch("core.tools.dispatcher.get_user_session", return_value={"tool_approval_mode": "auto"}):
            gated = await _should_require_approval(
                tool_cls, execution_obj, "user1", "conv1", {"path": "notes.txt"}
            )
        self.assertFalse(gated)

    async def test_mutating_outside_workspace_auto_mode_forced_gate(self):
        from core.tools.dispatcher import _should_require_approval
        from core.tools.base import ToolAuthority
        tool_cls = SimpleNamespace(authority=ToolAuthority.MUTATING, path_arg_name="path")
        execution_obj = SimpleNamespace(project_workspace=None)
        outside = os.path.join(tempfile.gettempdir(), f"eva-gate-test-mut-{os.getpid()}")
        with patch("core.tools.dispatcher.get_user_session", return_value={"tool_approval_mode": "auto"}):
            gated = await _should_require_approval(
                tool_cls, execution_obj, "user1", "conv1", {"path": outside}
            )
        self.assertTrue(gated, "MUTATING call outside workspace must gate even under auto mode")

    async def test_destructive_outside_workspace_auto_mode_forced_gate(self):
        from core.tools.dispatcher import _should_require_approval
        from core.tools.base import ToolAuthority
        tool_cls = SimpleNamespace(authority=ToolAuthority.DESTRUCTIVE, path_arg_name="path")
        execution_obj = SimpleNamespace(project_workspace=None)
        outside = os.path.join(tempfile.gettempdir(), f"eva-gate-test-del-{os.getpid()}")
        with patch("core.tools.dispatcher.get_user_session", return_value={"tool_approval_mode": "auto"}):
            gated = await _should_require_approval(
                tool_cls, execution_obj, "user1", "conv1", {"path": outside}
            )
        self.assertTrue(gated, "DESTRUCTIVE call outside workspace must gate even under auto mode")

    async def test_read_only_outside_workspace_not_gated_by_design(self):
        """READ_ONLY tools are explicitly out of scope for this fix -- data
        disclosure outside the workspace is a different risk, deliberately
        not folded into this destructive/mutating-only gate."""
        from core.tools.dispatcher import _should_require_approval
        from core.tools.base import ToolAuthority
        tool_cls = SimpleNamespace(authority=ToolAuthority.READ_ONLY, path_arg_name="path")
        execution_obj = SimpleNamespace(project_workspace=None)
        outside = os.path.join(tempfile.gettempdir(), f"eva-gate-test-ro-{os.getpid()}")
        gated = await _should_require_approval(
            tool_cls, execution_obj, "user1", "conv1", {"path": outside}
        )
        self.assertFalse(gated)

    async def test_always_ask_mode_in_workspace_still_gates_unchanged_behavior(self):
        from core.tools.dispatcher import _should_require_approval
        from core.tools.base import ToolAuthority
        tool_cls = SimpleNamespace(authority=ToolAuthority.MUTATING, path_arg_name="path")
        execution_obj = SimpleNamespace(project_workspace=None)
        with patch("core.tools.dispatcher.get_user_session", return_value={"tool_approval_mode": "always_ask"}):
            gated = await _should_require_approval(
                tool_cls, execution_obj, "user1", "conv1", {"path": "notes.txt"}
            )
        self.assertTrue(gated, "always_ask mode's existing behavior must be unaffected by the new branch")

    async def test_tool_without_path_arg_name_unaffected_by_new_branch(self):
        """A MUTATING tool with no path concept (e.g. git_commit) has
        path_arg_name=None -- the new branch must be a complete no-op,
        leaving the existing mode-based matrix as the sole decision."""
        from core.tools.dispatcher import _should_require_approval
        from core.tools.base import ToolAuthority
        tool_cls = SimpleNamespace(authority=ToolAuthority.MUTATING, path_arg_name=None)
        execution_obj = SimpleNamespace(project_workspace=None)
        with patch("core.tools.dispatcher.get_user_session", return_value={"tool_approval_mode": "auto"}):
            gated = await _should_require_approval(
                tool_cls, execution_obj, "user1", "conv1", {"some_other_arg": "value"}
            )
        self.assertFalse(gated)


# ---------------------------------------------------------------------------
# Layer 3 — ToolDispatcher.execute_tool() end-to-end, real WriteFileTool
# ---------------------------------------------------------------------------

class TestDispatcherEnforcesOutOfWorkspaceGate(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="eva-workspace-gate-test-")
        self._patcher = patch("utils.workspace.WORKSPACE_ROOT", self._tmp)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_execution(self, user_id: str, conversation_id: str, reg):
        execution = MagicMock()
        execution.execution_id = f"test-exec-{user_id}"
        execution.user_id = user_id
        execution.conversation_id = conversation_id
        execution.project_workspace = None
        execution.user_registry = reg
        return execution

    async def test_write_file_outside_workspace_auto_mode_gates_and_blocks_on_rejection(self):
        from core.tools.dispatcher import ToolDispatcher
        from core.tools.user_registry import UserToolRegistry
        from core.types import ToolExecutionStatus, ToolErrorType

        reg = UserToolRegistry(default_tools=True, execution_mode="conversational", progressive=False)
        tool_classes = reg.all_tools()
        execution = self._make_execution("gate-test-user-outside", "gate-test-conv-1", reg)

        outside_target = os.path.join(
            tempfile.gettempdir(), f"eva-gate-test-blocked-{os.getpid()}.txt"
        )
        if os.path.exists(outside_target):
            os.remove(outside_target)

        try:
            with patch("core.tools.dispatcher.get_user_session", return_value={"tool_approval_mode": "auto"}), \
                 patch("core.tools.dispatcher.gate_approval", new=AsyncMock(return_value=False)) as mock_gate:
                result = await ToolDispatcher.execute_tool(
                    call_id="gate-test-call-blocked",
                    tool_name="write_file",
                    arguments={"path": outside_target, "content": "should never be written"},
                    tool_classes=tool_classes,
                    execution_metadata={"execution": execution},
                )

            mock_gate.assert_awaited_once()
            self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
            self.assertEqual(result.error_type, ToolErrorType.PERMISSION)
            self.assertFalse(
                os.path.exists(outside_target),
                "Approval gate must block the write before it reaches the filesystem",
            )
        finally:
            if os.path.exists(outside_target):
                os.remove(outside_target)

    async def test_write_file_inside_workspace_auto_mode_not_gated_unchanged_behavior(self):
        from core.tools.dispatcher import ToolDispatcher
        from core.tools.user_registry import UserToolRegistry
        from core.types import ToolExecutionStatus
        from utils.workspace import get_conversation_dir

        reg = UserToolRegistry(default_tools=True, execution_mode="conversational", progressive=False)
        tool_classes = reg.all_tools()
        execution = self._make_execution("gate-test-user-inside", "gate-test-conv-2", reg)

        with patch("core.tools.dispatcher.get_user_session", return_value={"tool_approval_mode": "auto"}), \
             patch("core.tools.dispatcher.gate_approval", new=AsyncMock(return_value=False)) as mock_gate:
            result = await ToolDispatcher.execute_tool(
                call_id="gate-test-call-allowed",
                tool_name="write_file",
                arguments={"path": "inside-workspace-notes.txt", "content": "fine, unchanged behavior"},
                tool_classes=tool_classes,
                execution_metadata={"execution": execution},
            )

        mock_gate.assert_not_awaited()
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        # resolve_path_for_user() resolves into the conversation-scoped dir
        # here (execution.conversation_id is set, project_workspace is not)
        # -- same priority order is_path_in_workspace() itself replicates,
        # which is exactly why the gate correctly stayed silent.
        written_path = os.path.join(
            str(get_conversation_dir("gate-test-user-inside", "gate-test-conv-2")),
            "inside-workspace-notes.txt",
        )
        self.assertTrue(os.path.exists(written_path))

    async def test_delete_file_outside_workspace_auto_mode_gates(self):
        from core.tools.dispatcher import ToolDispatcher
        from core.tools.user_registry import UserToolRegistry
        from core.types import ToolExecutionStatus, ToolErrorType

        reg = UserToolRegistry(default_tools=True, execution_mode="conversational", progressive=False)
        tool_classes = reg.all_tools()
        execution = self._make_execution("gate-test-user-del", "gate-test-conv-3", reg)

        outside_target = os.path.join(
            tempfile.gettempdir(), f"eva-gate-test-delete-target-{os.getpid()}.txt"
        )
        with open(outside_target, "w", encoding="utf-8") as f:
            f.write("must survive this test")

        try:
            with patch("core.tools.dispatcher.get_user_session", return_value={"tool_approval_mode": "auto"}), \
                 patch("core.tools.dispatcher.gate_approval", new=AsyncMock(return_value=False)) as mock_gate:
                result = await ToolDispatcher.execute_tool(
                    call_id="gate-test-call-delete",
                    tool_name="delete_file",
                    arguments={"path": outside_target},
                    tool_classes=tool_classes,
                    execution_metadata={"execution": execution},
                )

            mock_gate.assert_awaited_once()
            self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
            self.assertEqual(result.error_type, ToolErrorType.PERMISSION)
            self.assertTrue(
                os.path.exists(outside_target),
                "Approval gate must block the delete before it reaches the filesystem",
            )
        finally:
            if os.path.exists(outside_target):
                os.remove(outside_target)


if __name__ == "__main__":
    unittest.main()
