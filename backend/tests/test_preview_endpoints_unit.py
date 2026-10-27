# tests/test_preview_endpoints_unit.py
#
# CDP-embedded preview pane (browser automation frontend spec, 2026-09-23):
# unit tests for the in-memory registry (core/mcp/preview_registry.py), the
# args-resolution it feeds into the two real MCP connection-construction
# sites, and api/session.py's three preview-endpoints handlers plus the
# hardcoded playwright resync they trigger. Pure unit tests, no real
# subprocess/CDP server -- mirrors tests/test_mcp_unit.py's own style for
# the session-pool pieces (fake conn_factory, MagicMock config).

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from core.mcp import preview_registry
from core.mcp.session_pool import MCPSessionPool


def _make_fake_config(name="playwright", config_id="cfg-1", cwd="/ws/u1", args=None):
    cfg = MagicMock()
    cfg.id = config_id
    cfg.name = name
    cfg.cwd = cwd
    cfg.args = args if args is not None else [
        "-y", "@playwright/mcp@0.0.82",
        "--browser", "msedge",
        "--isolated",
        "--image-responses", "omit",
        "--file-paths", "absolute",
        "--output-dir", f"{cwd}/_browser_output",
    ]
    return cfg


def _make_fake_conn(call_tool_return=None):
    conn = MagicMock()
    conn.call_tool = AsyncMock(return_value=call_tool_return)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.session = MagicMock()
    conn.session.send_ping = AsyncMock(return_value=None)
    return conn


class TestPreviewRegistry(unittest.TestCase):
    def setUp(self):
        preview_registry._ENDPOINTS.clear()

    def tearDown(self):
        preview_registry._ENDPOINTS.clear()

    def test_register_then_get(self):
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        entry = preview_registry.get("preview")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.cdp_endpoint, "ws://127.0.0.1:9001/relay")
        self.assertIsInstance(entry.registered_at, datetime)
        self.assertEqual(entry.registered_at.tzinfo, timezone.utc)

    def test_register_upserts_existing_name(self):
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        preview_registry.register("preview", "ws://127.0.0.1:9002/relay")
        entry = preview_registry.get("preview")
        self.assertEqual(entry.cdp_endpoint, "ws://127.0.0.1:9002/relay")
        self.assertEqual(len(preview_registry.list_all()), 1)

    def test_get_missing_returns_none(self):
        self.assertIsNone(preview_registry.get("nope"))

    def test_unregister_removes_and_returns_true(self):
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        self.assertTrue(preview_registry.unregister("preview"))
        self.assertIsNone(preview_registry.get("preview"))

    def test_unregister_absent_name_is_idempotent(self):
        # Not an error -- returns False, doesn't raise.
        self.assertFalse(preview_registry.unregister("never-registered"))

    def test_list_all_multiple_names(self):
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        preview_registry.register("testing-agent-1", "ws://127.0.0.1:9002/relay")
        names = {e.name for e in preview_registry.list_all()}
        self.assertEqual(names, {"preview", "testing-agent-1"})


class TestResolveConnectionArgs(unittest.TestCase):
    def setUp(self):
        preview_registry._ENDPOINTS.clear()

    def tearDown(self):
        preview_registry._ENDPOINTS.clear()

    def test_non_playwright_config_always_unchanged(self):
        cfg = _make_fake_config(name="some_other_server", args=["--foo"])
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        self.assertEqual(preview_registry.resolve_connection_args(cfg), ["--foo"])

    def test_playwright_no_registration_falls_back_to_stored_args(self):
        cfg = _make_fake_config()
        result = preview_registry.resolve_connection_args(cfg)
        self.assertEqual(result, cfg.args)

    def test_playwright_with_registration_returns_cdp_attach_args(self):
        cfg = _make_fake_config(cwd="/ws/u1")
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")

        result = preview_registry.resolve_connection_args(cfg)

        self.assertEqual(result, [
            "-y", "@playwright/mcp@0.0.82",
            "--cdp-endpoint", "ws://127.0.0.1:9001/relay",
            "--image-responses", "omit",
            "--file-paths", "absolute",
            "--output-dir", "/ws/u1/_browser_output",
        ])
        # The two flags that are meaningless once attaching to an existing
        # browser must be gone entirely, not just left alongside the new ones.
        self.assertNotIn("--browser", result)
        self.assertNotIn("--isolated", result)

    def test_playwright_with_registration_no_cwd_still_produces_output_dir(self):
        cfg = _make_fake_config(cwd=None)
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")

        result = preview_registry.resolve_connection_args(cfg)

        self.assertIn("--output-dir", result)
        self.assertEqual(result[result.index("--output-dir") + 1], "_browser_output")

    def test_unregistering_reverts_to_fallback_args(self):
        cfg = _make_fake_config()
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        self.assertIn("--cdp-endpoint", preview_registry.resolve_connection_args(cfg))

        preview_registry.unregister("preview")

        self.assertEqual(preview_registry.resolve_connection_args(cfg), cfg.args)


class TestConnectionSitesUseResolver(unittest.IsolatedAsyncioTestCase):
    """
    Confirms the two real connection-construction sites actually call the
    resolver instead of using config.args directly -- the wiring itself,
    not the resolver's own logic (covered above).
    """

    async def test_session_pool_default_conn_factory_uses_resolver(self):
        from core.mcp.session_pool import _default_conn_factory

        cfg = _make_fake_config(cwd="/ws/u1")
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        try:
            with patch("core.mcp.session_pool.resolve_secrets", return_value=(None, None)), \
                 patch("core.mcp.session_pool.MCPConnection") as MockConn:
                instance = MockConn.return_value
                instance.__aenter__ = AsyncMock(return_value=instance)
                await _default_conn_factory(cfg)

                _, kwargs = MockConn.call_args
                self.assertIn("--cdp-endpoint", kwargs["args"])
        finally:
            preview_registry.unregister("preview")

    async def test_manager_connection_uses_resolver(self):
        from core.mcp.manager import MCPManager

        cfg = _make_fake_config(cwd="/ws/u1")
        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        try:
            with patch("core.mcp.manager.resolve_secrets", return_value=(None, None)), \
                 patch("core.mcp.manager.MCPConnection") as MockConn:
                MCPManager(registry=None)._connection(cfg)

                _, kwargs = MockConn.call_args
                self.assertIn("--cdp-endpoint", kwargs["args"])
        finally:
            preview_registry.unregister("preview")


class TestResyncPlaywrightIfEnabled(unittest.IsolatedAsyncioTestCase):
    """
    api/session.py's _resync_playwright_if_enabled: the hardcoded v1
    register/unregister -> playwright resync relationship, and specifically
    that it ACTIVELY kills an already-open connection (e.g. a standalone
    browser window a fallback connection spawned before the pane opened)
    rather than leaving it to the lazy next-tool-call path.
    """

    def _db_ctx(self):
        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)
        return db_ctx

    async def test_no_playwright_config_does_nothing(self):
        import api.session as mod

        with patch.object(mod, "AsyncSessionLocal", return_value=self._db_ctx()), \
             patch.object(mod.MCPRepo, "get_config_by_name", new=AsyncMock(return_value=None)), \
             patch.object(mod, "mcp_session_pool") as mock_pool:
            await mod._resync_playwright_if_enabled("user-1")
            mock_pool.bump_generation.assert_not_called()
            mock_pool.discard.assert_not_called()

    async def test_disabled_playwright_config_does_nothing(self):
        import api.session as mod

        cfg = _make_fake_config()
        cfg.enabled = False
        with patch.object(mod, "AsyncSessionLocal", return_value=self._db_ctx()), \
             patch.object(mod.MCPRepo, "get_config_by_name", new=AsyncMock(return_value=cfg)), \
             patch.object(mod, "mcp_session_pool") as mock_pool:
            await mod._resync_playwright_if_enabled("user-1")
            mock_pool.bump_generation.assert_not_called()
            mock_pool.discard.assert_not_called()

    async def test_enabled_playwright_config_triggers_bump_and_discard(self):
        import api.session as mod

        cfg = _make_fake_config()
        cfg.enabled = True
        with patch.object(mod, "AsyncSessionLocal", return_value=self._db_ctx()), \
             patch.object(mod.MCPRepo, "get_config_by_name", new=AsyncMock(return_value=cfg)), \
             patch.object(mod, "mcp_session_pool") as mock_pool, \
             patch.object(mod, "MCPManager") as MockManager:
            mock_pool.discard = AsyncMock()
            mgr_instance = MockManager.return_value
            mgr_instance.sync_tools_for_config = AsyncMock(return_value=[])

            await mod._resync_playwright_if_enabled("user-1")

            mock_pool.bump_generation.assert_called_once_with(cfg.id)
            mock_pool.discard.assert_awaited_once_with(cfg.id)
            mgr_instance.sync_tools_for_config.assert_awaited_once()

    async def test_sync_failure_is_non_fatal(self):
        import api.session as mod

        cfg = _make_fake_config()
        cfg.enabled = True
        with patch.object(mod, "AsyncSessionLocal", return_value=self._db_ctx()), \
             patch.object(mod.MCPRepo, "get_config_by_name", new=AsyncMock(return_value=cfg)), \
             patch.object(mod, "mcp_session_pool") as mock_pool, \
             patch.object(mod, "MCPManager") as MockManager:
            mock_pool.discard = AsyncMock()
            mgr_instance = MockManager.return_value
            mgr_instance.sync_tools_for_config = AsyncMock(side_effect=RuntimeError("cdp unreachable"))

            # Must not raise -- registration/unregistration itself should
            # still succeed even if the freshly-resolved endpoint is bad.
            await mod._resync_playwright_if_enabled("user-1")
            mock_pool.discard.assert_awaited_once_with(cfg.id)

    async def test_resync_actively_closes_an_orphaned_standalone_connection(self):
        """
        End-to-end through the REAL pool (not mocked) for the one behaviour
        the spec explicitly called out as worth testing: a standalone
        browser window a fallback connection already spawned must be
        cleanly killed by the resync, not left orphaned.
        """
        import api.session as mod

        real_pool = MCPSessionPool()
        cfg = _make_fake_config()
        cfg.enabled = True
        standalone_conn = _make_fake_conn(call_tool_return="ok")
        factory = AsyncMock(return_value=standalone_conn)

        # Simulate: before the pane ever opened, a real tool call already
        # created a pooled (standalone-browser) connection for this config.
        await real_pool.call_tool(cfg, "browser_navigate", {}, conn_factory=factory)
        self.assertIn(cfg.id, real_pool._entries)

        with patch.object(mod, "AsyncSessionLocal", return_value=self._db_ctx()), \
             patch.object(mod.MCPRepo, "get_config_by_name", new=AsyncMock(return_value=cfg)), \
             patch.object(mod, "mcp_session_pool", real_pool), \
             patch.object(mod, "MCPManager") as MockManager:
            mgr_instance = MockManager.return_value
            mgr_instance.sync_tools_for_config = AsyncMock(return_value=[])

            await mod._resync_playwright_if_enabled("user-1")

        # The orphaned standalone connection was actively closed, right now --
        # not left for a next-tool-call that might never come.
        standalone_conn.__aexit__.assert_awaited_once()
        # And the pool entry itself is gone, not just stale.
        self.assertNotIn(cfg.id, real_pool._entries)


class TestPreviewEndpointHandlers(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        preview_registry._ENDPOINTS.clear()

    def tearDown(self):
        preview_registry._ENDPOINTS.clear()

    async def test_post_registers_and_triggers_resync(self):
        import api.session as mod
        from api.schemas.requests import PreviewEndpointRequest

        with patch.object(mod, "_resync_playwright_if_enabled", new=AsyncMock()) as mock_resync:
            payload = PreviewEndpointRequest(name="preview", cdp_endpoint="ws://127.0.0.1:9001/relay")
            result = await mod.register_preview_endpoint(payload, auth={"user_id": "user-1"})

        self.assertEqual(result, {"status": "ok"})
        entry = preview_registry.get("preview")
        self.assertEqual(entry.cdp_endpoint, "ws://127.0.0.1:9001/relay")
        mock_resync.assert_awaited_once_with("user-1")

    async def test_delete_unregisters_and_triggers_resync(self):
        import api.session as mod

        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        with patch.object(mod, "_resync_playwright_if_enabled", new=AsyncMock()) as mock_resync:
            result = await mod.unregister_preview_endpoint("preview", auth={"user_id": "user-1"})

        self.assertEqual(result, {"status": "ok"})
        self.assertIsNone(preview_registry.get("preview"))
        mock_resync.assert_awaited_once_with("user-1")

    async def test_delete_absent_name_is_idempotent_not_an_error(self):
        import api.session as mod

        with patch.object(mod, "_resync_playwright_if_enabled", new=AsyncMock()):
            result = await mod.unregister_preview_endpoint("never-registered", auth={"user_id": "user-1"})

        self.assertEqual(result, {"status": "ok"})

    async def test_get_lists_current_registrations(self):
        import api.session as mod

        preview_registry.register("preview", "ws://127.0.0.1:9001/relay")
        result = await mod.list_preview_endpoints(auth={"user_id": "user-1"})

        self.assertIn("preview", result["endpoints"])
        self.assertEqual(result["endpoints"]["preview"]["cdp_endpoint"], "ws://127.0.0.1:9001/relay")

    async def test_get_empty_registry_returns_empty_dict(self):
        import api.session as mod

        result = await mod.list_preview_endpoints(auth={"user_id": "user-1"})
        self.assertEqual(result["endpoints"], {})


if __name__ == "__main__":
    unittest.main()
