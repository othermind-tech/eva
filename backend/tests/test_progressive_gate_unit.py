# tests/test_progressive_gate_unit.py
"""
ToolDispatcher's progressive-disclosure gates (core/tools/dispatcher.py).

Progressive disclosure shows a model a tool's schema on demand (describe_tool)
to save tokens. Until V6.50, a model that called a tool it had not described got
a "not yet active" stub instead of a result and had to repeat the call: one
wasted step per first-used tool, and in a live run the stub swallowed the final
edit_file just before the step limit, so the fix never landed.

Now the dispatcher promotes the tool AND runs the call. These tests pin that,
and pin that it is not a way around anything else: authority policy still
applies, an unknown tool still fails, bad arguments still come back as a
validation failure, and only the first call is flagged.

Run with:
    pytest tests/test_progressive_gate_unit.py -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace


def _registry(progressive=True):
    from core.tools.loader import load_all_tools
    from core.tools.user_registry import UserToolRegistry
    load_all_tools()  # populates DefaultToolRegistry; without it the registry is empty
    return UserToolRegistry(default_tools=True, execution_mode="agent", progressive=progressive)


def _execution(reg, user_id="gate-user"):
    return SimpleNamespace(
        execution_id="exec-gate", user_id=user_id, conversation_id="conv-gate",
        user_registry=reg, project_workspace=None,
    )


async def _call(tool_name, arguments, reg, tool_classes=None, user_id="gate-user"):
    from core.tools.dispatcher import ToolDispatcher
    if tool_classes is None:
        tool_classes = reg.get_active_tool_classes()
    return await ToolDispatcher.execute_tool(
        call_id="call-1", tool_name=tool_name, arguments=arguments,
        tool_classes=tool_classes, execution_metadata={"execution": _execution(reg, user_id)},
    )


class TestFirstCallToAnUndescribedToolExecutes(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        self.tmp.write("hello from the gate test")
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    async def test_call_runs_and_returns_the_real_result_not_a_stub(self):
        from core.types import ToolExecutionStatus
        reg = _registry()
        self.assertNotIn("read_file", reg.get_active_tool_classes(), "precondition: read_file starts undescribed")

        result = await _call("read_file", {"path": self.tmp.name}, reg)

        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertIn("hello from the gate test", str(result.output))
        self.assertNotIn("not_yet_active", str(result.output))
        self.assertNotIn("auto_promoted", str(result.output))

    async def test_tool_is_promoted_and_only_the_first_call_is_flagged(self):
        reg = _registry()
        first = await _call("read_file", {"path": self.tmp.name}, reg)
        self.assertTrue(first.metadata.get("auto_activated"))
        self.assertIn("read_file", reg._promoted_tools)
        self.assertIn("read_file", reg.get_active_tool_classes())

        second = await _call("read_file", {"path": self.tmp.name}, reg)
        self.assertNotIn("auto_activated", second.metadata)

    async def test_flag_does_not_reuse_the_old_stub_marker(self):
        # core/agent_engine.py used metadata.auto_promoted to mean "this was only a stub".
        reg = _registry()
        result = await _call("read_file", {"path": self.tmp.name}, reg)
        self.assertNotIn("auto_promoted", result.metadata)

    async def test_tool_resolved_but_never_promoted_also_executes(self):
        # e.g. an MCP tool found through the dynamic registry: it is in tool_classes
        # already but was never promoted -- the second gate.
        from core.types import ToolExecutionStatus
        reg = _registry()
        result = await _call("read_file", {"path": self.tmp.name}, reg, tool_classes=reg.all_tools())
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertIn("hello from the gate test", str(result.output))
        self.assertTrue(result.metadata.get("auto_activated"))
        self.assertIn("read_file", reg._promoted_tools)

    async def test_already_promoted_tool_is_unchanged(self):
        reg = _registry()
        reg.promote("read_file")
        result = await _call("read_file", {"path": self.tmp.name}, reg)
        self.assertNotIn("auto_activated", result.metadata)
        self.assertIn("hello from the gate test", str(result.output))


class TestTheGateIsNotABypass(unittest.IsolatedAsyncioTestCase):

    async def test_bad_arguments_return_a_validation_failure_with_the_schema_errors(self):
        from core.types import ToolErrorType, ToolExecutionStatus
        reg = _registry()
        result = await _call("read_file", {"wrong_name": "x"}, reg)
        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
        self.assertEqual(result.error_type, ToolErrorType.VALIDATION)
        self.assertIn("path", result.error_message)

    async def test_tool_that_does_not_exist_still_fails(self):
        reg = _registry()
        with self.assertRaises(RuntimeError) as ctx:
            await _call("definitely_not_a_tool", {}, reg)
        self.assertIn("not available", str(ctx.exception))

    async def test_authority_policy_still_applies_to_an_auto_promoted_tool(self):
        # A read-only session must still refuse a mutating tool, however it is reached.
        from config.settings import _USER_SESSIONS
        from core.types import ToolErrorType, ToolExecutionStatus
        user = "gate-readonly-user"
        _USER_SESSIONS[user] = {"allowed_authorities": "read_only"}
        try:
            reg = _registry()
            self.assertNotIn("shell", reg.get_active_tool_classes())
            result = await _call("shell", {"cmd": ["echo", "hi"]}, reg, user_id=user)
        finally:
            _USER_SESSIONS.pop(user, None)
        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
        self.assertEqual(result.error_type, ToolErrorType.PERMISSION)

    async def test_non_progressive_registry_is_untouched(self):
        # Subagents and daemons get every tool up front; nothing about them changes.
        from core.types import ToolExecutionStatus
        reg = _registry(progressive=False)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("plain")
        tmp.close()
        try:
            result = await _call("read_file", {"path": tmp.name}, reg)
        finally:
            os.unlink(tmp.name)
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertNotIn("auto_activated", result.metadata)


class TestEngineNoLongerHasStubHandling(unittest.TestCase):

    def test_agent_engine_has_no_leftover_stub_bookkeeping(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "agent_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("_fp_by_call_id", src)
        self.assertNotIn('get("auto_promoted")', src)


if __name__ == "__main__":
    unittest.main()
