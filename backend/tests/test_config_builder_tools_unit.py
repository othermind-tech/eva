# tests/test_config_builder_tools_unit.py
#
# Unit tests for the agent/daemon config builder tools (V6.22):
#   tools/default/subagent_config_tools.py  -- create/get/edit/delete_subagent_config
#   tools/default/daemon_config_tools.py    -- create/get/edit/delete_daemon_config
#
# Mocking convention matches the established pattern (test_tools_unit.py's
# TestTriggerDaemonConsolidation): pull the tool class from
# DefaultToolRegistry.all() rather than importing it directly, patch the
# registry singleton at its *usage* import path (core.agents.registry /
# core.daemons.registry), and assert both on the returned ToolResult dict
# and on the mock's call arguments.
#
# Run with:
#     cd <project_root>
#     pytest tests/test_config_builder_tools_unit.py -v

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.tools.registry import DefaultToolRegistry
from core.tools.loader import load_all_tools

load_all_tools()


def _make_execution(user_id="u1", dynamic_registry=None):
    execution = MagicMock()
    execution.user_id = user_id
    execution.dynamic_registry = dynamic_registry
    return execution


def _get_tool(name: str):
    tool_cls = DefaultToolRegistry.all()[name]
    tool = tool_cls()
    tool.execution = _make_execution()
    return tool


def _row(name: str, **kwargs) -> MagicMock:
    """
    Build a MagicMock standing in for a SubagentConfig/DaemonConfig row.

    MagicMock(name="x") is a trap: `name` is a reserved constructor kwarg
    that sets the mock's own debug repr, NOT an attribute -- `mock.name`
    would come back as an auto-generated child mock, not "x". Every row
    mock needs `.name` set as a real attribute afterward instead.
    """
    m = MagicMock(**kwargs)
    m.name = name
    return m


# ===========================================================================
# Registration sanity -- authority tiers and conversational-mode visibility
# ===========================================================================

class TestRegistrationAndAuthority(unittest.TestCase):

    def test_all_eight_tools_registered(self):
        names = [
            "create_subagent_config", "get_subagent_config",
            "edit_subagent_config", "delete_subagent_config",
            "create_daemon_config", "get_daemon_config",
            "edit_daemon_config", "delete_daemon_config",
        ]
        reg = DefaultToolRegistry.all()
        missing = [n for n in names if n not in reg]
        self.assertEqual(missing, [], f"not registered: {missing}")

    def test_authority_tiers(self):
        from core.tools.base import ToolAuthority
        reg = DefaultToolRegistry.all()
        expected = {
            "create_subagent_config": ToolAuthority.MUTATING,
            "edit_subagent_config": ToolAuthority.MUTATING,
            "get_subagent_config": ToolAuthority.READ_ONLY,
            "delete_subagent_config": ToolAuthority.DESTRUCTIVE,
            "create_daemon_config": ToolAuthority.MUTATING,
            "edit_daemon_config": ToolAuthority.MUTATING,
            "get_daemon_config": ToolAuthority.READ_ONLY,
            "delete_daemon_config": ToolAuthority.DESTRUCTIVE,
        }
        for name, tier in expected.items():
            self.assertEqual(reg[name].authority, tier, f"{name} wrong tier")

    def test_all_visible_in_conversational_mode(self):
        """Regression guard: category must stay in the conversational
        allowlist (core/execution/worker.py's TOOL_CATEGORIES_BY_MODE) --
        these tools exist specifically to work in plain chat."""
        from core.execution.worker import TOOL_CATEGORIES_BY_MODE
        allowlist = TOOL_CATEGORIES_BY_MODE["conversational"]
        reg = DefaultToolRegistry.all()
        for name in (
            "create_subagent_config", "get_subagent_config", "edit_subagent_config",
            "delete_subagent_config", "create_daemon_config", "get_daemon_config",
            "edit_daemon_config", "delete_daemon_config",
        ):
            self.assertIn(reg[name].category, allowlist, f"{name} invisible in conversational mode")
            self.assertFalse(reg[name].chat_excluded, f"{name} should not be chat_excluded")


# ===========================================================================
# create_subagent_config
# ===========================================================================

class TestCreateSubagentConfig(unittest.IsolatedAsyncioTestCase):

    async def test_success(self):
        tool = _get_tool("create_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_row = _row("researcher", id="id-1", description="d", system_prompt="sp",
                         model=None, provider=None, tools=["web_search"], max_steps=5)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({
                "name": "researcher", "description": "d", "system_prompt": "sp",
                "tools": ["web_search"], "max_steps": 5,
            })

        self.assertTrue(result.result["created"])
        mock_registry.upsert.assert_awaited_once()
        self.assertEqual(mock_registry.upsert.call_args.args[1]["name"], "researcher")

    async def test_duplicate_name_refused_not_overwritten(self):
        tool = _get_tool("create_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=MagicMock())  # already exists
        mock_registry.upsert = AsyncMock()

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher", "tools": []})

        self.assertIn("already exists", result.result["error"])
        mock_registry.upsert.assert_not_awaited()

    async def test_invalid_tool_name_rejected(self):
        tool = _get_tool("create_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_registry.upsert = AsyncMock()

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher", "tools": ["not_a_real_tool_xyz"]})

        self.assertIn("not_a_real_tool_xyz", result.result["error"])
        mock_registry.upsert.assert_not_awaited()

    async def test_no_execution_context_returns_error(self):
        tool = _get_tool("create_subagent_config")
        tool.execution = _make_execution(user_id=None)
        result = await tool.run({"name": "x", "tools": []})
        self.assertIn("error", result.result)

    async def test_empty_name_returns_error(self):
        tool = _get_tool("create_subagent_config")
        result = await tool.run({"name": "   ", "tools": []})
        self.assertIn("error", result.result)

    async def test_unrecognised_model_gives_warning_not_hard_error(self):
        tool = _get_tool("create_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_row = _row("x", id="id-1", description=None, system_prompt=None,
                         model="fake-model-xyz", provider="fake-provider-xyz",
                         tools=[], max_steps=5)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("core.providers.capability_registry.get_model", return_value=None):
            result = await tool.run({
                "name": "x", "provider": "fake-provider-xyz", "model": "fake-model-xyz", "tools": [],
            })

        self.assertTrue(result.result["created"], "unrecognised model must not block creation")
        self.assertIn("warnings", result.result)

    async def test_recognised_model_no_warning(self):
        tool = _get_tool("create_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_row = _row("x", id="id-1", description=None, system_prompt=None,
                         model="claude-sonnet-5", provider="anthropic", tools=[], max_steps=5)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("core.providers.capability_registry.get_model", return_value=MagicMock()):
            result = await tool.run({
                "name": "x", "provider": "anthropic", "model": "claude-sonnet-5", "tools": [],
            })

        self.assertTrue(result.result["created"])
        self.assertNotIn("warnings", result.result)

    async def test_dynamic_registry_tools_accepted(self):
        """A tool name from the execution's MCP/dynamic registry, not just
        DefaultToolRegistry, must be accepted as valid."""
        mock_manifest = MagicMock()
        mock_manifest.name = "my_mcp_tool"
        mock_dynamic = MagicMock()
        mock_dynamic.list_tools = MagicMock(return_value=[mock_manifest])

        tool_cls = DefaultToolRegistry.all()["create_subagent_config"]
        tool = tool_cls()
        tool.execution = _make_execution(dynamic_registry=mock_dynamic)

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_row = _row("x", id="id-1", description=None, system_prompt=None,
                         model=None, provider=None, tools=["my_mcp_tool"], max_steps=5)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "x", "tools": ["my_mcp_tool"]})

        self.assertTrue(result.result["created"])


# ===========================================================================
# get_subagent_config
# ===========================================================================

class TestGetSubagentConfig(unittest.IsolatedAsyncioTestCase):

    async def test_found(self):
        tool = _get_tool("get_subagent_config")
        mock_row = _row("researcher", description="d", system_prompt="sp",
                         model=None, provider=None, tools=[], max_steps=5, is_default=False)
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=mock_row)

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher"})

        self.assertTrue(result.result["found"])
        self.assertEqual(result.result["name"], "researcher")

    async def test_not_found(self):
        tool = _get_tool("get_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "nonexistent"})

        self.assertFalse(result.result["found"])
        self.assertIn("error", result.result)


# ===========================================================================
# edit_subagent_config
# ===========================================================================

class TestEditSubagentConfig(unittest.IsolatedAsyncioTestCase):

    def _existing(self, **overrides):
        base = dict(name="researcher", description="old", system_prompt="old sp",
                    model=None, provider=None, tools=["web_search"], max_steps=5, is_default=False)
        base.update(overrides)
        m = MagicMock(**base)
        m.name = base["name"]
        return m

    async def test_partial_update_only_changes_passed_fields(self):
        tool = _get_tool("edit_subagent_config")
        existing = self._existing()
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=existing)
        mock_row = self._existing(description="new description")
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher", "description": "new description"})

        self.assertTrue(result.result["updated"])
        called_data = mock_registry.upsert.call_args.args[1]
        # Only name + description should be present -- system_prompt/model/etc
        # must be OMITTED entirely (not sent as None), matching upsert()'s
        # own "absent key = unchanged" contract.
        self.assertEqual(set(called_data.keys()), {"name", "description"})
        self.assertEqual(result.result["changed_fields"], ["description"])

    async def test_not_found_returns_error(self):
        tool = _get_tool("edit_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_registry.upsert = AsyncMock()

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "nonexistent", "description": "x"})

        self.assertIn("error", result.result)
        mock_registry.upsert.assert_not_awaited()

    async def test_no_fields_to_change_returns_error(self):
        tool = _get_tool("edit_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=self._existing())
        mock_registry.upsert = AsyncMock()

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher"})

        self.assertIn("error", result.result)
        mock_registry.upsert.assert_not_awaited()

    async def test_default_agent_tools_change_refused(self):
        """Mirrors PUT /agents/{name}'s own restriction -- must not be a
        silent bypass of that safety rail."""
        tool = _get_tool("edit_subagent_config")
        existing = self._existing(is_default=True, tools=["web_search"])
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=existing)
        mock_registry.upsert = AsyncMock()

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher", "tools": ["shell"]})

        self.assertIn("error", result.result)
        self.assertIn("default agent", result.result["error"])
        mock_registry.upsert.assert_not_awaited()

    async def test_default_agent_tools_unchanged_is_allowed(self):
        """Passing the SAME tools list as a default agent already has must
        not be blocked -- only an actual change is."""
        tool = _get_tool("edit_subagent_config")
        existing = self._existing(is_default=True, tools=["web_search"])
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=existing)
        mock_row = self._existing(is_default=True, tools=["web_search"], max_steps=10)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher", "tools": ["web_search"], "max_steps": 10})

        self.assertTrue(result.result["updated"])

    async def test_invalid_tool_name_rejected(self):
        tool = _get_tool("edit_subagent_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=self._existing())
        mock_registry.upsert = AsyncMock()

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher", "tools": ["not_a_real_tool_xyz"]})

        self.assertIn("not_a_real_tool_xyz", result.result["error"])
        mock_registry.upsert.assert_not_awaited()


# ===========================================================================
# delete_subagent_config
# ===========================================================================

class TestDeleteSubagentConfig(unittest.IsolatedAsyncioTestCase):

    async def test_success(self):
        tool = _get_tool("delete_subagent_config")
        mock_registry = MagicMock()
        mock_registry.delete = AsyncMock(return_value=True)

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "researcher"})

        self.assertTrue(result.result["deleted"])

    async def test_not_found(self):
        tool = _get_tool("delete_subagent_config")
        mock_registry = MagicMock()
        mock_registry.delete = AsyncMock(return_value=False)

        with patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"name": "nonexistent"})

        self.assertIn("error", result.result)

    async def test_empty_name_returns_error(self):
        tool = _get_tool("delete_subagent_config")
        result = await tool.run({"name": ""})
        self.assertIn("error", result.result)


# ===========================================================================
# create_daemon_config
# ===========================================================================

class TestCreateDaemonConfig(unittest.IsolatedAsyncioTestCase):

    async def test_success_with_schedule(self):
        tool = _get_tool("create_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_row = _row("daily-digest", id="id-1", description="d", system_prompt=None,
                         trigger_message="go", model=None, provider=None, tools=[],
                         max_steps=5, run_interval_seconds=3600, enabled=True)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({
                "name": "daily-digest", "description": "d", "trigger_message": "go",
                "tools": [], "run_interval_seconds": 3600,
            })

        self.assertTrue(result.result["created"])
        self.assertIn("every 3600s", result.result["message"])

    async def test_success_manual_trigger_only(self):
        tool = _get_tool("create_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_row = _row("x", id="id-1", description=None, system_prompt=None,
                         trigger_message=None, model=None, provider=None, tools=[],
                         max_steps=5, run_interval_seconds=None, enabled=True)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "x", "tools": []})

        self.assertTrue(result.result["created"])
        self.assertIn("manual trigger only", result.result["message"])

    async def test_duplicate_name_refused(self):
        tool = _get_tool("create_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=MagicMock())
        mock_registry.upsert = AsyncMock()

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "daily-digest", "tools": []})

        self.assertIn("already exists", result.result["error"])
        mock_registry.upsert.assert_not_awaited()

    async def test_invalid_tool_name_rejected(self):
        tool = _get_tool("create_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_registry.upsert = AsyncMock()

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "x", "tools": ["not_a_real_tool_xyz"]})

        self.assertIn("not_a_real_tool_xyz", result.result["error"])
        mock_registry.upsert.assert_not_awaited()

    async def test_no_execution_context_returns_error(self):
        tool = _get_tool("create_daemon_config")
        tool.execution = _make_execution(user_id=None)
        result = await tool.run({"name": "x", "tools": []})
        self.assertIn("error", result.result)

    async def test_empty_name_returns_error(self):
        tool = _get_tool("create_daemon_config")
        result = await tool.run({"name": "  ", "tools": []})
        self.assertIn("error", result.result)

    async def test_interval_below_minimum_rejected_by_schema(self):
        """run_interval_seconds has ge=60 on the Pydantic schema itself --
        confirm the schema actually enforces it (dispatcher.py validates
        via schema.parse_obj() before run() is ever called)."""
        from tools.default.daemon_config_tools import CreateDaemonConfigArgs
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            CreateDaemonConfigArgs(name="x", run_interval_seconds=30)


# ===========================================================================
# get_daemon_config
# ===========================================================================

class TestGetDaemonConfig(unittest.IsolatedAsyncioTestCase):

    async def test_found_includes_lifecycle_status(self):
        tool = _get_tool("get_daemon_config")
        mock_row = MagicMock(description="d", system_prompt=None, trigger_message="go",
                              model=None, provider=None, tools=[], max_steps=5,
                              run_interval_seconds=3600, enabled=True,
                              lifecycle_status="idle", last_run_at=None, next_run_at=None)
        mock_row.name = "daily-digest"
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=mock_row)

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "daily-digest"})

        self.assertTrue(result.result["found"])
        self.assertEqual(result.result["lifecycle_status"], "idle")

    async def test_not_found(self):
        tool = _get_tool("get_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "nonexistent"})

        self.assertFalse(result.result["found"])


# ===========================================================================
# edit_daemon_config
# ===========================================================================

class TestEditDaemonConfig(unittest.IsolatedAsyncioTestCase):

    def _existing(self, **overrides):
        base = dict(name="daily-digest", description="old", system_prompt=None,
                    trigger_message="old trigger", model=None, provider=None, tools=[],
                    max_steps=5, run_interval_seconds=3600, enabled=True)
        base.update(overrides)
        m = MagicMock(**base)
        m.name = base["name"]
        return m

    async def test_partial_update(self):
        tool = _get_tool("edit_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=self._existing())
        mock_row = self._existing(run_interval_seconds=7200)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "daily-digest", "run_interval_seconds": 7200})

        self.assertTrue(result.result["updated"])
        called_data = mock_registry.upsert.call_args.args[1]
        self.assertEqual(set(called_data.keys()), {"name", "run_interval_seconds"})

    async def test_not_found_returns_error(self):
        tool = _get_tool("edit_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_registry.upsert = AsyncMock()

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "nonexistent", "enabled": False})

        self.assertIn("error", result.result)
        mock_registry.upsert.assert_not_awaited()

    async def test_no_fields_to_change_returns_error(self):
        tool = _get_tool("edit_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=self._existing())
        mock_registry.upsert = AsyncMock()

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "daily-digest"})

        self.assertIn("error", result.result)
        mock_registry.upsert.assert_not_awaited()

    async def test_enabled_false_is_a_real_change_not_treated_as_omitted(self):
        """enabled=False must be distinguishable from 'omitted' -- False is
        falsy but not None, so it must still be included in update_data."""
        tool = _get_tool("edit_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=self._existing(enabled=True))
        mock_row = self._existing(enabled=False)
        mock_registry.upsert = AsyncMock(return_value=mock_row)

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "daily-digest", "enabled": False})

        called_data = mock_registry.upsert.call_args.args[1]
        self.assertIn("enabled", called_data)
        self.assertFalse(called_data["enabled"])


# ===========================================================================
# delete_daemon_config
# ===========================================================================

class TestDeleteDaemonConfig(unittest.IsolatedAsyncioTestCase):

    async def test_success_not_running(self):
        tool = _get_tool("delete_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=MagicMock(lifecycle_status="idle"))
        mock_registry.delete = AsyncMock(return_value=True)

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "daily-digest"})

        self.assertTrue(result.result["deleted"])
        self.assertNotIn("was_running", result.result)

    async def test_reports_was_running_without_blocking(self):
        """The core design decision from this feature's discussion: deleting
        a running daemon must NOT be blocked, but must be reported."""
        tool = _get_tool("delete_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=MagicMock(lifecycle_status="running"))
        mock_registry.delete = AsyncMock(return_value=True)

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "daily-digest"})

        self.assertTrue(result.result["deleted"], "must not be blocked just because it's running")
        self.assertTrue(result.result["was_running"])
        self.assertIn("note", result.result)

    async def test_not_found(self):
        tool = _get_tool("delete_daemon_config")
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_registry.delete = AsyncMock()

        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"name": "nonexistent"})

        self.assertIn("error", result.result)
        mock_registry.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
