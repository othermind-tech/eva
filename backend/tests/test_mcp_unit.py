# tests/test_mcp_unit.py
#
# Unit tests for the MCP reliability work (M-04 -> superseded by P-06b,
# P-06a: registration decoupled from live sync): the persistent connection
# pool and its reconnect/eviction/liveness behaviour. Pure unit tests
# against mocked MCPConnection -- no real MCP server needed (that
# live-in-the-loop verification lives in tests/test_behaviour.py B18 /
# tests/fixtures/mock_mcp_server.py instead).

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.mcp.session_pool import MCPSessionPool, _default_conn_factory


def _make_fake_conn(call_tool_side_effect=None, call_tool_return=None, ping_side_effect=None):
    """A mock MCPConnection: async call_tool()/__aexit__()/session.send_ping()."""
    conn = MagicMock()
    conn.call_tool = AsyncMock(
        side_effect=call_tool_side_effect,
        return_value=call_tool_return,
    )
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.session = MagicMock()
    conn.session.send_ping = AsyncMock(side_effect=ping_side_effect, return_value=None)
    return conn


def _make_fake_config(config_id="cfg-1"):
    cfg = MagicMock()
    cfg.id = config_id
    cfg.name = "server_a"
    cfg.transport = "stdio"
    cfg.command = "echo"
    cfg.args = []
    cfg.env = None
    cfg.cwd = None
    cfg.env_file = None
    cfg.url = None
    cfg.headers = None
    return cfg


class TestMCPSessionPoolLazyCreationAndReuse(unittest.IsolatedAsyncioTestCase):
    """P-06b: connections are created lazily and reused across calls."""

    async def test_first_call_creates_via_factory(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        conn = _make_fake_conn(call_tool_return="ok")
        factory = AsyncMock(return_value=conn)

        result = await pool.call_tool(config, "some_tool", {}, conn_factory=factory)

        self.assertEqual(result, "ok")
        self.assertEqual(factory.await_count, 1)
        conn.__aexit__.assert_not_awaited()  # kept open, not closed after success

    async def test_second_call_reuses_same_connection(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        conn = _make_fake_conn(call_tool_return="ok")
        factory = AsyncMock(return_value=conn)

        await pool.call_tool(config, "tool_a", {}, conn_factory=factory)
        await pool.call_tool(config, "tool_b", {}, conn_factory=factory)

        self.assertEqual(factory.await_count, 1)  # only opened once
        self.assertEqual(conn.call_tool.await_count, 2)
        conn.session.send_ping.assert_awaited_once()  # checked before the 2nd reuse only

    async def test_different_configs_get_independent_connections(self):
        pool = MCPSessionPool()
        config_a = _make_fake_config("cfg-a")
        config_b = _make_fake_config("cfg-b")
        conn_a = _make_fake_conn(call_tool_return="a")
        conn_b = _make_fake_conn(call_tool_return="b")
        factory = AsyncMock(side_effect=[conn_a, conn_b])

        result_a = await pool.call_tool(config_a, "t", {}, conn_factory=factory)
        result_b = await pool.call_tool(config_b, "t", {}, conn_factory=factory)

        self.assertEqual((result_a, result_b), ("a", "b"))
        self.assertEqual(factory.await_count, 2)


class TestMCPSessionPoolEviction(unittest.IsolatedAsyncioTestCase):
    """P-06b: mark-and-sweep (config change) and pre-call liveness (ping) eviction."""

    async def test_generation_bump_forces_new_connection(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        old_conn = _make_fake_conn(call_tool_return="old")
        new_conn = _make_fake_conn(call_tool_return="new")
        factory = AsyncMock(side_effect=[old_conn, new_conn])

        await pool.call_tool(config, "t", {}, conn_factory=factory)
        pool.bump_generation(config.id)
        result = await pool.call_tool(config, "t", {}, conn_factory=factory)

        self.assertEqual(result, "new")
        self.assertEqual(factory.await_count, 2)
        old_conn.__aexit__.assert_awaited_once()  # stale one released, not leaked

    async def test_dead_ping_forces_new_connection(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        dying_conn = _make_fake_conn(call_tool_return="first")
        fresh_conn = _make_fake_conn(call_tool_return="second")
        factory = AsyncMock(side_effect=[dying_conn, fresh_conn])

        await pool.call_tool(config, "t", {}, conn_factory=factory)
        # Simulate the pooled connection dying silently while idle.
        dying_conn.session.send_ping = AsyncMock(side_effect=ConnectionError("dead"))

        result = await pool.call_tool(config, "t", {}, conn_factory=factory)

        self.assertEqual(result, "second")
        self.assertEqual(factory.await_count, 2)
        dying_conn.__aexit__.assert_awaited_once()

    async def test_discard_removes_pooled_entry(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        conn = _make_fake_conn(call_tool_return="ok")
        factory = AsyncMock(return_value=conn)

        await pool.call_tool(config, "t", {}, conn_factory=factory)
        await pool.discard(config.id)

        self.assertNotIn(config.id, pool._entries)
        conn.__aexit__.assert_awaited_once()

    async def test_discard_during_in_flight_connect_does_not_orphan_the_call(self):
        """
        Found live (2026-09-24): a preview-pane registration fired discard()
        on the "playwright" config while a browser_navigate call's connection
        was still mid-handshake. The original discard() popped+closed the
        entry immediately, with no regard for entry.lock -- the same lock
        call_tool()'s _acquire_entry() holds for a call's full duration.
        The owner task's "close" request (enqueued by discard(), which never
        waits on _OwnedConnection._ready) beat the in-flight call's "call"
        request (only enqueued once the connection finishes opening) into
        the internal queue, so the owner task processed "close" and returned
        WITHOUT ever looking at "call" -- that call's awaited future then
        had no one left to resolve it. It hung for the full 300s
        TOOL_EXECUTION_TIMEOUT_SECONDS before an automatic retry recovered
        it. This reproduces that exact interleaving against a fake slow
        conn_factory and confirms discard() now waits for entry.lock
        instead of racing ahead of it.
        """
        pool = MCPSessionPool()
        config = _make_fake_config()

        handshake_release = asyncio.Event()

        async def slow_conn_factory(cfg):
            await handshake_release.wait()  # simulate a slow npx cold-start handshake
            return _make_fake_conn(call_tool_return="ok")

        call_task = asyncio.create_task(
            pool.call_tool(config, "t", {}, conn_factory=slow_conn_factory)
        )
        # Let call_tool() run far enough to create the entry and start
        # (the still-blocked) connect -- entry.lock is now held.
        await asyncio.sleep(0.01)

        discard_task = asyncio.create_task(pool.discard(config.id))
        # Give discard() a chance to run. Pre-fix, this is exactly where it
        # would pop the entry and enqueue "close" without ever checking the
        # lock. Post-fix, it should simply block on entry.lock here.
        await asyncio.sleep(0.01)
        self.assertFalse(discard_task.done())  # waiting on the lock, not racing ahead

        handshake_release.set()  # let the handshake (and then the call) complete

        result = await asyncio.wait_for(call_task, timeout=5)
        await asyncio.wait_for(discard_task, timeout=5)

        self.assertEqual(result, "ok")
        self.assertNotIn(config.id, pool._entries)


class TestMCPSessionPoolRetryOnCallFailure(unittest.IsolatedAsyncioTestCase):
    """
    P-06b: a call_tool() failure evicts and retries once -- same shape as
    M-04's original call_with_reconnect, but the connection stays pooled
    (not closed) on success.
    """

    async def test_call_failure_then_success_retries_once(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        dead_conn = _make_fake_conn(call_tool_side_effect=ConnectionError("dropped"))
        live_conn = _make_fake_conn(call_tool_return="recovered")
        factory = AsyncMock(side_effect=[dead_conn, live_conn])

        result = await pool.call_tool(config, "t", {}, conn_factory=factory)

        self.assertEqual(result, "recovered")
        self.assertEqual(factory.await_count, 2)
        dead_conn.__aexit__.assert_awaited_once()
        live_conn.__aexit__.assert_not_awaited()  # kept pooled after success

    async def test_call_failure_twice_propagates(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        dead_conn = _make_fake_conn(call_tool_side_effect=ConnectionError("dropped"))
        also_dead_conn = _make_fake_conn(call_tool_side_effect=TimeoutError("still down"))
        factory = AsyncMock(side_effect=[dead_conn, also_dead_conn])

        with self.assertRaises(TimeoutError):
            await pool.call_tool(config, "t", {}, conn_factory=factory)

        self.assertEqual(factory.await_count, 2)


class TestMCPSessionPoolConcurrency(unittest.IsolatedAsyncioTestCase):
    """P-06b: concurrent calls to the same config's connection serialize via the entry lock."""

    async def test_concurrent_calls_do_not_overlap(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        overlap_detected = False
        in_flight = 0

        async def _slow_call_tool(name, args):
            nonlocal overlap_detected, in_flight
            in_flight += 1
            if in_flight > 1:
                overlap_detected = True
            await asyncio.sleep(0.05)
            in_flight -= 1
            return "ok"

        conn = MagicMock()
        conn.call_tool = _slow_call_tool
        conn.__aexit__ = AsyncMock(return_value=None)
        conn.session = MagicMock()
        conn.session.send_ping = AsyncMock(return_value=None)
        factory = AsyncMock(return_value=conn)

        await asyncio.gather(
            pool.call_tool(config, "t", {}, conn_factory=factory),
            pool.call_tool(config, "t", {}, conn_factory=factory),
            pool.call_tool(config, "t", {}, conn_factory=factory),
        )

        self.assertFalse(overlap_detected)
        self.assertEqual(factory.await_count, 1)  # one call opened it, others reused


class TestMCPSessionPoolIdleReaper(unittest.IsolatedAsyncioTestCase):
    """P-06b: background idle-TTL reaper closes connections nothing has used recently."""

    async def test_reap_idle_closes_stale_connection(self):
        import time
        pool = MCPSessionPool()
        config = _make_fake_config()
        conn = _make_fake_conn(call_tool_return="ok")
        factory = AsyncMock(return_value=conn)

        await pool.call_tool(config, "t", {}, conn_factory=factory)
        entry = pool._entries[config.id]
        entry.last_used = time.monotonic() - 999999  # force it far past the idle TTL

        await pool._reap_idle()

        conn.__aexit__.assert_awaited_once()
        self.assertIsNone(entry.owned)

    async def test_reap_idle_skips_locked_entry(self):
        import time
        pool = MCPSessionPool()
        config = _make_fake_config()
        conn = _make_fake_conn(call_tool_return="ok")
        factory = AsyncMock(return_value=conn)

        await pool.call_tool(config, "t", {}, conn_factory=factory)
        entry = pool._entries[config.id]
        entry.last_used = time.monotonic() - 999999  # far past the idle TTL, whatever the machine's uptime
        await entry.lock.acquire()
        try:
            await pool._reap_idle()
            conn.__aexit__.assert_not_awaited()
        finally:
            entry.lock.release()

    async def test_reap_idle_skips_recently_used_connection(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        conn = _make_fake_conn(call_tool_return="ok")
        factory = AsyncMock(return_value=conn)

        await pool.call_tool(config, "t", {}, conn_factory=factory)
        await pool._reap_idle()

        conn.__aexit__.assert_not_awaited()


class TestMCPSessionPoolOwnerTask(unittest.IsolatedAsyncioTestCase):
    """
    P-06b hardening: owner-task-per-connection. Every pooled connection's
    __aenter__/__aexit__ must happen in the same dedicated task regardless
    of which caller task acquires/evicts/reaps it (see session_pool.py's
    module docstring for why).
    """

    async def test_open_failure_does_not_hang_and_propagates(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        factory = AsyncMock(side_effect=ConnectionError("cannot reach server"))

        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(
                pool.call_tool(config, "t", {}, conn_factory=factory),
                timeout=5,
            )

    async def test_close_is_idempotent(self):
        pool = MCPSessionPool()
        config = _make_fake_config()
        conn = _make_fake_conn(call_tool_return="ok")
        factory = AsyncMock(return_value=conn)

        await pool.call_tool(config, "t", {}, conn_factory=factory)
        entry = pool._entries[config.id]
        await entry.owned.close()
        await entry.owned.close()  # must not raise or hang

        conn.__aexit__.assert_awaited_once()

    async def test_stop_closes_all_pooled_connections_concurrently(self):
        pool = MCPSessionPool()
        configs = [_make_fake_config(f"cfg-{i}") for i in range(3)]
        conns = [_make_fake_conn(call_tool_return="ok") for _ in configs]

        for cfg, conn in zip(configs, conns):
            factory = AsyncMock(return_value=conn)
            await pool.call_tool(cfg, "t", {}, conn_factory=factory)

        await pool.stop()

        for conn in conns:
            conn.__aexit__.assert_awaited_once()
        self.assertEqual(pool._entries, {})

    async def test_cross_task_open_reuse_evict_reap_does_not_deadlock_or_raise(self):
        """
        Direct analogue of the standalone live cross-task stress test run
        against real stdio/SSE/streamable-HTTP servers this session (all
        three passed) -- reproduced here as a fast, deterministic mocked
        unit test: connection opened by one real asyncio.Task, evicted by
        a second, reused by a third, idle-reaped by a fourth.
        """
        pool = MCPSessionPool()
        config = _make_fake_config()
        conn_a = _make_fake_conn(call_tool_return="a")
        conn_b = _make_fake_conn(call_tool_return="b")
        factory = AsyncMock(side_effect=[conn_a, conn_b])

        async def task_a():
            return await pool.call_tool(config, "t", {}, conn_factory=factory)

        async def task_b():
            pool.bump_generation(config.id)
            return await pool.call_tool(config, "t", {}, conn_factory=factory)

        async def task_c():
            return await pool.call_tool(config, "t", {}, conn_factory=factory)

        async def task_d_reap():
            import time
            entry = pool._entries[config.id]
            entry.last_used = time.monotonic() - 999999  # far past the idle TTL, whatever the machine's uptime
            await pool._reap_idle()

        r_a = await asyncio.create_task(task_a(), name="ExecTaskA")
        r_b = await asyncio.create_task(task_b(), name="ExecTaskB")
        r_c = await asyncio.create_task(task_c(), name="ExecTaskC")
        await asyncio.create_task(task_d_reap(), name="ReaperTaskD")

        self.assertEqual(r_a, "a")
        self.assertEqual(r_b, "b")
        self.assertEqual(r_c, "b")  # reused conn_b, no eviction between B and C
        conn_a.__aexit__.assert_awaited_once()  # closed by Task B, opened by Task A
        conn_b.__aexit__.assert_awaited_once()  # closed by the reaper (Task D), opened by Task B


class TestDefaultConnFactoryPassesFullConfig(unittest.IsolatedAsyncioTestCase):
    """
    Guards against the bug fixed alongside M-04/P-06b: the connection
    factory previously omitted env/cwd/env_file when constructing
    MCPConnection, silently dropping stdio server environment configuration.
    """

    async def test_passes_env_cwd_env_file(self):
        config = _make_fake_config()
        config.env = {"KEY": "value"}
        config.cwd = "/some/dir"
        config.env_file = "/some/.env"

        captured_kwargs = {}

        class _FakeConn:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

            async def __aenter__(self):
                return self

        with patch("core.mcp.session_pool.MCPConnection", _FakeConn):
            await _default_conn_factory(config)

        self.assertEqual(captured_kwargs.get("env"), {"KEY": "value"})
        self.assertEqual(captured_kwargs.get("cwd"), "/some/dir")
        self.assertEqual(captured_kwargs.get("env_file"), "/some/.env")


class TestMCPRuntimeUsesSessionPool(unittest.IsolatedAsyncioTestCase):
    """Integration-level check that MCPRuntime.execute() is wired to the pool."""

    async def test_execute_calls_pool_call_tool(self):
        from core.mcp.runtime import MCPRuntime

        fake_config = _make_fake_config()
        fake_tool_meta = MagicMock()
        fake_tool_meta.name = "get_thing"

        db = AsyncMock()
        scalars_config = MagicMock()
        scalars_config.first.return_value = fake_config
        scalars_tool = MagicMock()
        scalars_tool.first.return_value = fake_tool_meta
        db.execute = AsyncMock(side_effect=[
            MagicMock(scalars=lambda: scalars_config),
            MagicMock(scalars=lambda: scalars_tool),
        ])

        fake_result = MagicMock()
        fake_result.content = "done"

        with patch("core.mcp.runtime.mcp_session_pool") as mock_pool:
            mock_pool.call_tool = AsyncMock(return_value=fake_result)
            result = await MCPRuntime.execute(
                db=db, user_id="u1", tool_name="server_a__get_thing", arguments={"x": 1},
            )

        mock_pool.call_tool.assert_awaited_once_with(fake_config, "get_thing", {"x": 1})
        self.assertEqual(result.result, "done")


class TestUserToolRegistryPruneDynamicTools(unittest.TestCase):
    """
    Pruning piece deferred out of M-05: on a confirmed-dead MCP server,
    remove its tools from the active registry so the model stops retrying
    it for the rest of the conversation.
    """

    def _make_registry_with_tools(self, names):
        from core.tools.user_registry import UserToolRegistry
        reg = UserToolRegistry(default_tools=False, enabled_tools=[])
        for name in names:
            cls = MagicMock()
            cls.name = name
            reg.tools[name] = cls
            reg._promoted_tools.add(name)
        return reg

    def test_prunes_only_matching_prefix(self):
        reg = self._make_registry_with_tools([
            "dead_server.tool_a", "dead_server.tool_b", "other_server.tool_c",
        ])
        pruned = reg.prune_dynamic_tools("dead_server.")

        self.assertEqual(set(pruned), {"dead_server.tool_a", "dead_server.tool_b"})
        self.assertNotIn("dead_server.tool_a", reg.tools)
        self.assertNotIn("dead_server.tool_b", reg.tools)
        self.assertIn("other_server.tool_c", reg.tools)
        self.assertNotIn("dead_server.tool_a", reg._promoted_tools)

    def test_also_removes_from_dynamic_registry(self):
        from core.tools.user_registry import UserToolRegistry
        from core.tools.dynamic_registry import DynamicToolRegistry, ToolManifest

        dyn = DynamicToolRegistry()
        dyn.register_tool(ToolManifest(
            name="dead_server.tool_a", description="", input_schema={},
        ))
        reg = UserToolRegistry(default_tools=False, enabled_tools=[], dynamic_registry=dyn)
        reg.tools["dead_server.tool_a"] = MagicMock()

        reg.prune_dynamic_tools("dead_server.")

        self.assertIsNone(dyn.get("dead_server.tool_a"))

    def test_no_match_returns_empty_list(self):
        reg = self._make_registry_with_tools(["other_server.tool_c"])
        pruned = reg.prune_dynamic_tools("dead_server.")
        self.assertEqual(pruned, [])
        self.assertIn("other_server.tool_c", reg.tools)


class TestMCPRuntimePrunesOnConfirmedFailure(unittest.IsolatedAsyncioTestCase):
    """
    MCPRuntime.execute() calls user_registry.prune_dynamic_tools() when the
    session pool's call fails (i.e. survived the pool's own internal
    retry), and tells the model the server is now unavailable.
    """

    async def _run_execute_with_pool_failure(self, user_registry):
        from core.mcp.runtime import MCPRuntime

        fake_config = _make_fake_config()
        fake_tool_meta = MagicMock()
        fake_tool_meta.name = "get_thing"

        db = AsyncMock()
        scalars_config = MagicMock()
        scalars_config.first.return_value = fake_config
        scalars_tool = MagicMock()
        scalars_tool.first.return_value = fake_tool_meta
        db.execute = AsyncMock(side_effect=[
            MagicMock(scalars=lambda: scalars_config),
            MagicMock(scalars=lambda: scalars_tool),
        ])

        with patch("core.mcp.runtime.mcp_session_pool") as mock_pool:
            mock_pool.call_tool = AsyncMock(side_effect=ConnectionError("server dead"))
            return await MCPRuntime.execute(
                db=db, user_id="u1", tool_name="server_a__get_thing", arguments={},
                user_registry=user_registry,
            )

    async def test_prunes_and_notes_unavailability_when_pruned(self):
        from core.tools.user_registry import UserToolRegistry
        reg = UserToolRegistry(default_tools=False, enabled_tools=[])
        reg.tools["server_a__get_thing"] = MagicMock()
        reg._promoted_tools.add("server_a__get_thing")

        result = await self._run_execute_with_pool_failure(reg)

        self.assertNotIn("server_a__get_thing", reg.tools)
        self.assertIn("server dead", result.result["error"])
        self.assertIn("unavailable", result.result["error"])
        self.assertIn("server_a", result.result["error"])

    async def test_none_user_registry_is_safe(self):
        result = await self._run_execute_with_pool_failure(None)
        self.assertIn("server dead", result.result["error"])
        self.assertNotIn("unavailable", result.result["error"])

    async def test_pruning_failure_does_not_break_error_return(self):
        broken_registry = MagicMock()
        broken_registry.prune_dynamic_tools.side_effect = RuntimeError("boom")

        result = await self._run_execute_with_pool_failure(broken_registry)

        self.assertIn("server dead", result.result["error"])


class TestP06aRegistrationDoesNotOpenLiveConnection(unittest.IsolatedAsyncioTestCase):
    """
    P-06a: register_tools_for_user() must be a pure DB-cache read
    (MCPRepo.list_tools_for_config()) -- it must never call
    sync_tools_for_config() (which opens a live connection), since that's
    exactly the per-execution live-connection cost this fix removes.
    """

    async def test_register_tools_for_user_never_calls_sync(self):
        from core.tools.dynamic_registry import DynamicToolRegistry
        from core.mcp.manager import MCPManager

        registry = DynamicToolRegistry()
        manager = MCPManager(registry=registry)

        cfg = MagicMock()
        cfg.name = "server_a"
        cfg.id = "id-server_a"
        cfg.enabled = True

        tool = MagicMock()
        tool.name = "tool_a1"
        tool.description = "a tool"
        tool.input_schema = {}
        tool.output_schema = None

        manager.load_user_configs = AsyncMock(return_value=[cfg])
        manager.sync_tools_for_config = AsyncMock(
            side_effect=AssertionError("sync_tools_for_config must not be called by P-06a's registration path")
        )

        with patch("core.mcp.manager.MCPRepo.list_tools_for_config",
                   new=AsyncMock(return_value=[tool])):
            errors = await manager.register_tools_for_user(db=AsyncMock(), user_id="u1")

        self.assertEqual(errors, {})
        self.assertIn("server_a__tool_a1", [m.name for m in registry.list_tools()])
        manager.sync_tools_for_config.assert_not_called()


class TestP06aCreateConfigSeedsCache(unittest.IsolatedAsyncioTestCase):
    """
    P-06a: POST /mcp/ must populate the tool-metadata cache once at creation
    time (when enabled=True), since registration no longer does a live sync
    itself -- without this, a freshly created config would register zero
    tools until someone manually calls POST /mcp/{id}/sync.
    """

    async def test_create_syncs_when_enabled(self):
        import api.mcp as mcp_api

        cfg = MagicMock()
        cfg.id = "new-config-id"
        cfg.enabled = True

        with patch.object(mcp_api.MCPRepo, "create_config", new=AsyncMock(return_value=cfg)), \
             patch.object(mcp_api.MCPManager, "sync_tools_for_config",
                          new=AsyncMock(return_value=[])) as mock_sync, \
             patch.object(mcp_api, "AsyncSessionLocal") as mock_session_local:
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_db
            mock_session_local.return_value.__aexit__.return_value = None

            payload = mcp_api.MCPConfigCreate(name="server_a", transport="stdio", command="echo", enabled=True)
            await mcp_api.create_mcp_config(payload, auth={"user_id": "u1"})

        mock_sync.assert_awaited_once()

    async def test_create_does_not_sync_when_disabled(self):
        import api.mcp as mcp_api

        cfg = MagicMock()
        cfg.id = "new-config-id"
        cfg.enabled = False

        with patch.object(mcp_api.MCPRepo, "create_config", new=AsyncMock(return_value=cfg)), \
             patch.object(mcp_api.MCPManager, "sync_tools_for_config",
                          new=AsyncMock(return_value=[])) as mock_sync, \
             patch.object(mcp_api, "AsyncSessionLocal") as mock_session_local:
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_db
            mock_session_local.return_value.__aexit__.return_value = None

            payload = mcp_api.MCPConfigCreate(name="server_a", transport="stdio", command="echo", enabled=False)
            await mcp_api.create_mcp_config(payload, auth={"user_id": "u1"})

        mock_sync.assert_not_awaited()

    async def test_create_sync_failure_is_non_fatal(self):
        """A sync failure at creation time must not prevent the config from being returned."""
        import api.mcp as mcp_api

        cfg = MagicMock()
        cfg.id = "new-config-id"
        cfg.enabled = True

        with patch.object(mcp_api.MCPRepo, "create_config", new=AsyncMock(return_value=cfg)), \
             patch.object(mcp_api.MCPManager, "sync_tools_for_config",
                          new=AsyncMock(side_effect=ConnectionError("server unreachable"))), \
             patch.object(mcp_api, "AsyncSessionLocal") as mock_session_local:
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_db
            mock_session_local.return_value.__aexit__.return_value = None

            payload = mcp_api.MCPConfigCreate(name="server_a", transport="stdio", command="echo", enabled=True)
            result = await mcp_api.create_mcp_config(payload, auth={"user_id": "u1"})

        self.assertIs(result, cfg)


if __name__ == "__main__":
    unittest.main()
