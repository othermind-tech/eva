# tests/test_mcp_naming_unit.py
"""
Unit tests for the MCP tool-naming fix (2026-08-19).

Background: EVA namespaces MCP tools as "<config_name><delimiter><tool_name>".
This was previously "." (a raw f-string duplicated across 4 call sites:
core/mcp/manager.py, api/tools.py, core/mcp/runtime.py x2), which broke
every major provider's tool-name validation (Anthropic:
^[a-zA-Z0-9_-]{1,128}$) the moment an MCP tool got promoted into the
model's active toolkit -- confirmed live: a real chat turn calling a real
MCP tool failed with a 400 from Anthropic, reproduced identically on a
clean baseline with no provider-handler changes (root cause was the
delimiter itself, not anything Anthropic-specific).

Fix: core/mcp/naming.py is now the single source of truth for the
convention ("__" delimiter), used by all 4 call sites, plus MCP config
names are rejected at creation/update time if they contain the delimiter
(so the split is always unambiguous by construction).

Run with:
    cd <project_root>
    pytest tests/test_mcp_naming_unit.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestBuildAndParseRoundTrip(unittest.TestCase):

    def test_build_then_parse_round_trips(self):
        from core.mcp.naming import build_mcp_tool_name, parse_mcp_tool_name

        compound = build_mcp_tool_name("brave_search", "web_search")
        self.assertEqual(compound, "brave_search__web_search")
        config_name, tool_name = parse_mcp_tool_name(compound)
        self.assertEqual(config_name, "brave_search")
        self.assertEqual(tool_name, "web_search")

    def test_delimiter_is_double_underscore_valid_under_anthropic_pattern(self):
        import re
        from core.mcp.naming import MCP_TOOL_NAME_DELIMITER, build_mcp_tool_name

        compound = build_mcp_tool_name("my-server", "my_tool")
        # Anthropic's actual documented tool-name pattern.
        self.assertRegex(compound, r"^[a-zA-Z0-9_-]{1,128}$")
        self.assertIn(MCP_TOOL_NAME_DELIMITER, compound)

    def test_parse_raises_on_missing_delimiter(self):
        from core.mcp.naming import parse_mcp_tool_name

        with self.assertRaises(ValueError):
            parse_mcp_tool_name("not_a_compound_name")

    def test_parse_splits_on_first_occurrence_only(self):
        """A tool name (set by the MCP server, outside EVA's control) that
        happens to itself contain the delimiter must not break parsing --
        only the config-name boundary matters, guaranteed delimiter-free by
        validation at config creation time."""
        from core.mcp.naming import build_mcp_tool_name, parse_mcp_tool_name

        compound = build_mcp_tool_name("myserver", "get__the__code")
        config_name, tool_name = parse_mcp_tool_name(compound)
        self.assertEqual(config_name, "myserver")
        self.assertEqual(tool_name, "get__the__code")

    def test_build_prefix_matches_build_tool_name_prefix(self):
        from core.mcp.naming import build_mcp_tool_name, build_mcp_tool_name_prefix

        prefix = build_mcp_tool_name_prefix("myserver")
        full = build_mcp_tool_name("myserver", "anything")
        self.assertTrue(full.startswith(prefix))


class TestConfigNameValidity(unittest.TestCase):

    def test_normal_names_are_valid(self):
        from core.mcp.naming import is_valid_mcp_config_name

        self.assertTrue(is_valid_mcp_config_name("brave_search"))
        self.assertTrue(is_valid_mcp_config_name("my-server"))
        self.assertTrue(is_valid_mcp_config_name("server1"))

    def test_name_containing_delimiter_is_invalid(self):
        from core.mcp.naming import is_valid_mcp_config_name

        self.assertFalse(is_valid_mcp_config_name("my__server"))


class TestAPIRejectsDelimiterInConfigName(unittest.TestCase):

    def test_create_rejects_name_with_delimiter(self):
        import api.mcp as mcp_api
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            mcp_api.MCPConfigCreate(
                name="my__server", transport="stdio", command="echo", enabled=True,
            )

    def test_create_accepts_normal_name(self):
        import api.mcp as mcp_api

        payload = mcp_api.MCPConfigCreate(
            name="my-server", transport="stdio", command="echo", enabled=True,
        )
        self.assertEqual(payload.name, "my-server")

    def test_update_rejects_name_with_delimiter(self):
        import api.mcp as mcp_api
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            mcp_api.MCPConfigUpdate(name="renamed__server")

    def test_update_with_no_name_change_is_unaffected(self):
        import api.mcp as mcp_api

        payload = mcp_api.MCPConfigUpdate(enabled=False)
        self.assertIsNone(payload.name)


class TestMCPRuntimeInvalidNameErrorPath(unittest.IsolatedAsyncioTestCase):
    """Regression test for the "if '.' not in tool_name" check this
    replaced -- must still return the same error shape for a name with no
    delimiter at all, now via parse_mcp_tool_name()'s ValueError."""

    async def test_execute_returns_error_for_name_without_delimiter(self):
        from core.mcp.runtime import MCPRuntime

        result = await MCPRuntime.execute(
            db=AsyncMock(), user_id="u1", tool_name="not_a_compound_name", arguments={},
        )
        self.assertIn("error", result.result)
        self.assertIn("Invalid MCP tool name", result.result["error"])


class TestManagerAndToolsAPIUseSharedHelper(unittest.IsolatedAsyncioTestCase):
    """core/mcp/manager.py and api/tools.py both build the compound name --
    confirm both now go through build_mcp_tool_name() (new delimiter),
    not a leftover local f-string using "."."""

    async def test_manager_register_tools_uses_new_delimiter(self):
        from core.tools.dynamic_registry import DynamicToolRegistry
        from core.mcp.manager import MCPManager

        registry = DynamicToolRegistry()
        manager = MCPManager(registry=registry)

        cfg = MagicMock()
        cfg.name = "myserver"
        cfg.id = "cfg-1"
        cfg.enabled = True

        tool = MagicMock()
        tool.name = "get_thing"
        tool.description = "d"
        tool.input_schema = {}
        tool.output_schema = None

        manager.load_user_configs = AsyncMock(return_value=[cfg])

        with patch("core.mcp.manager.MCPRepo.list_tools_for_config",
                   new=AsyncMock(return_value=[tool])):
            await manager.register_tools_for_user(db=AsyncMock(), user_id="u1")

        names = [m.name for m in registry.list_tools()]
        self.assertIn("myserver__get_thing", names)
        self.assertNotIn("myserver.get_thing", names)

    async def test_tools_api_mcp_items_use_new_delimiter(self):
        import api.tools as tools_api

        cfg = MagicMock()
        cfg.name = "myserver"
        cfg.id = "cfg-1"
        cfg.enabled = True

        tool = MagicMock()
        tool.name = "get_thing"
        tool.description = "d"

        with patch.object(tools_api.MCPRepo, "list_configs", new=AsyncMock(return_value=[cfg])), \
             patch.object(tools_api.MCPRepo, "list_tools_for_config", new=AsyncMock(return_value=[tool])), \
             patch.object(tools_api, "AsyncSessionLocal") as mock_session_local:
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_db
            mock_session_local.return_value.__aexit__.return_value = None

            items = await tools_api._mcp_tool_items("u1")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "myserver__get_thing")


if __name__ == "__main__":
    unittest.main()
