# tests/test_mcp_sync_error_handling_unit.py
"""
B-100: POST /mcp/{id}/sync returned a raw, unhandled 500 on a connection failure.

Found live via frontend testing of Section 91's MCP panel: clicking "Sync" on a
broken/unreachable MCP server row hit an exception with no try/except around it in
`sync_mcp_tools()` (api/mcp.py), which FastAPI turns into a bodyless 500 -- nothing the
frontend's existing error handling could render. The sibling auto-sync-on-enable path
(`update_mcp_config`, PUT /{config_id}) already catches the identical failure and reports
it as `sync_error` in an ordinary 200 response; `sync_mcp_tools` now does the same.

Calls the real endpoint function directly (not over HTTP) with AsyncSessionLocal/MCPRepo/
MCPManager mocked -- the same pattern already used for update_mcp_config's own wiring
tests (TestMcpConfigEndpointCredentialWiring in test_features.py).

Run with:
    pytest tests/test_mcp_sync_error_handling_unit.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


def _config(enabled=True):
    cfg = MagicMock()
    cfg.id = "cfg1"
    cfg.enabled = enabled
    return cfg


class TestSyncMcpToolsErrorHandling(unittest.IsolatedAsyncioTestCase):

    async def _call(self, cfg, tools_side_effect=None, tools_return=None):
        with patch("api.mcp.AsyncSessionLocal") as MockSession, \
             patch("api.mcp.MCPRepo") as MockRepo, \
             patch("api.mcp.MCPManager") as MockManager:
            MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)
            MockRepo.get_config = AsyncMock(return_value=cfg)

            mgr = MockManager.return_value
            if tools_side_effect is not None:
                mgr.sync_tools_for_config = AsyncMock(side_effect=tools_side_effect)
            else:
                mgr.sync_tools_for_config = AsyncMock(return_value=tools_return or [])
            mgr.sync_resources_for_config = AsyncMock(return_value=[])
            mgr.sync_prompts_for_config = AsyncMock(return_value=[])

            from api.mcp import sync_mcp_tools
            result = await sync_mcp_tools("cfg1", auth={"user_id": "u1"})
            return result, mgr

    async def test_a_connection_failure_is_reported_not_raised(self):
        """The exact live scenario: the MCP server is unreachable. Must NOT propagate out of
        the endpoint (which is what turns into an unhandled 500 at the HTTP layer)."""
        result, _mgr = await self._call(
            _config(), tools_side_effect=ConnectionError("[Errno 61] Connection refused"))
        self.assertIn("Connection refused", result["sync_error"])
        self.assertEqual(result["tools"], [])

    async def test_resources_and_prompts_are_empty_too_on_failure(self):
        result, _mgr = await self._call(_config(), tools_side_effect=RuntimeError("down"))
        self.assertEqual(result["resources"], [])
        self.assertEqual(result["prompts"], [])

    async def test_resources_and_prompts_sync_is_skipped_after_a_tools_failure(self):
        """Same connection that just failed for tools would almost certainly fail for these
        too -- don't spend two more (slow) connection attempts to a server just found dead."""
        _result, mgr = await self._call(_config(), tools_side_effect=OSError("host down"))
        mgr.sync_resources_for_config.assert_not_awaited()
        mgr.sync_prompts_for_config.assert_not_awaited()

    async def test_a_successful_sync_has_no_sync_error(self):
        tool = MagicMock(name="t", description="d", input_schema={}, output_schema=None, last_synced_at=None)
        tool.name = "web_search"
        result, mgr = await self._call(_config(), tools_return=[tool])
        self.assertIsNone(result["sync_error"])
        self.assertEqual(len(result["tools"]), 1)
        self.assertEqual(result["tools"][0]["name"], "web_search")
        mgr.sync_resources_for_config.assert_awaited_once()
        mgr.sync_prompts_for_config.assert_awaited_once()

    async def test_disabled_config_still_400s_unaffected_by_this_change(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            await self._call(_config(enabled=False))
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_missing_config_still_404s_unaffected_by_this_change(self):
        from fastapi import HTTPException
        with patch("api.mcp.AsyncSessionLocal") as MockSession, \
             patch("api.mcp.MCPRepo") as MockRepo:
            MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)
            MockRepo.get_config = AsyncMock(return_value=None)

            from api.mcp import sync_mcp_tools
            with self.assertRaises(HTTPException) as ctx:
                await sync_mcp_tools("nonexistent", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
