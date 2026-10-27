# tests/test_mcp_tool_rejection_unit.py
"""
V6.59 (mcp 2.x): a healthy MCP server that refuses a tool call with a protocol-level
error must NOT be treated as a dead connection.

Background: mcp 1.x returned a normal result (isError=True) for a tool that rejects with
an MCPError; mcp 2.x raises MCPError on the client instead. core/mcp/session_pool.py
treated EVERY exception as a dead connection -- evict, reconnect, retry once -- so under
2.x a rejection (a) re-executed the tool on a fresh connection (observed live: 2 real
executions of 1 call, a double side effect for any non-idempotent tool) and (b) made the
runtime prune the whole server's tools for the rest of the conversation.

Fix: after an MCPError the pool pings the connection. If it still answers, the server
heard us and said no -> MCPToolRejected, no retry, no churn. The SDK ALSO reports a dead
transport as an MCPError (and the code spaces overlap -- -32000 is both "connection
closed" and a perfectly valid application code), so liveness, not the code, decides.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from mcp import MCPError

from core.mcp.session_pool import MCPSessionPool, MCPToolRejected


class _Conn:
    """Stand-in for a connected MCPConnection, as _OwnedConnection drives it."""

    def __init__(self, log, on_call, ping_ok):
        self._log, self._on_call = log, on_call

        async def send_ping():
            if not ping_ok:
                raise ConnectionError("transport is gone")
            return {}

        self.session = SimpleNamespace(send_ping=send_ping)

    async def call_tool(self, name, args):
        self._log["executions"] += 1
        return self._on_call(self._log["executions"])

    async def __aexit__(self, *a):
        return None


def _pool_env(on_call, ping_ok=lambda n_conn: True):
    log = {"executions": 0, "connections": 0}

    async def factory(config):
        log["connections"] += 1
        return _Conn(log, on_call, ping_ok(log["connections"]))

    return log, factory


_CFG = SimpleNamespace(id="cfg-1", name="demo")


class TestPoolDistinguishesRejectionFromDeadConnection(unittest.IsolatedAsyncioTestCase):

    async def _call(self, pool, factory):
        return await pool.call_tool(_CFG, "some_tool", {}, conn_factory=factory)

    async def asyncSetUp(self):
        self.pool = MCPSessionPool()
        await self.pool.start()

    async def asyncTearDown(self):
        await self.pool.stop()

    async def test_a_rejection_from_a_live_server_is_surfaced_and_not_rerun(self):
        def reject(n):
            raise MCPError(-32000, "rejected-by-server")

        log, factory = _pool_env(reject)
        with self.assertRaises(MCPToolRejected) as ctx:
            await self._call(self.pool, factory)

        self.assertEqual(log["executions"], 1, "the tool must execute exactly once")
        self.assertEqual(log["connections"], 1, "the healthy connection must not be replaced")
        self.assertEqual(ctx.exception.code, -32000)
        self.assertEqual(ctx.exception.message, "rejected-by-server")
        self.assertIn("some_tool", str(ctx.exception))

    async def test_a_dead_transport_reported_as_an_MCPError_is_still_reconnected_and_retried(self):
        # first connection: call raises MCPError AND the ping fails (transport gone);
        # second connection: healthy and succeeds.
        def call(n):
            if n == 1:
                raise MCPError(-32000, "Connection closed")
            return "ok-after-reconnect"

        log, factory = _pool_env(call, ping_ok=lambda n_conn: n_conn != 1)
        self.assertEqual(await self._call(self.pool, factory), "ok-after-reconnect")
        self.assertEqual(log["connections"], 2)
        self.assertEqual(log["executions"], 2)

    async def test_an_ordinary_exception_is_still_reconnected_and_retried_once(self):
        def call(n):
            if n == 1:
                raise ConnectionError("socket reset")
            return "recovered"

        log, factory = _pool_env(call)
        self.assertEqual(await self._call(self.pool, factory), "recovered")
        self.assertEqual((log["connections"], log["executions"]), (2, 2))

    async def test_a_persistent_failure_still_propagates_after_the_single_retry(self):
        def call(n):
            raise ConnectionError("still down")

        log, factory = _pool_env(call)
        with self.assertRaises(ConnectionError):
            await self._call(self.pool, factory)
        self.assertEqual(log["executions"], 2)  # original + exactly one retry, unchanged behaviour


class TestRuntimeReportsRejectionWithoutPruningTools(unittest.IsolatedAsyncioTestCase):

    async def _execute(self, pool_exception):
        from core.mcp.runtime import MCPRuntime

        def result_with(obj):
            r = MagicMock()
            r.scalars.return_value.first.return_value = obj
            return r

        db = MagicMock()
        db.execute = AsyncMock(side_effect=[
            result_with(SimpleNamespace(id="cfg-1", name="evatest")),   # MCPConfig
            result_with(SimpleNamespace(name="echo")),                  # MCPToolMeta
        ])
        registry = MagicMock()
        registry.prune_dynamic_tools.return_value = ["evatest__echo", "evatest__image"]

        with patch("core.mcp.runtime.mcp_session_pool.call_tool", new=AsyncMock(side_effect=pool_exception)):
            result = await MCPRuntime.execute(
                db=db, user_id="u", tool_name="evatest__echo", arguments={}, user_registry=registry,
            )
        return result.result, registry

    async def test_a_rejection_is_an_ordinary_tool_error_and_keeps_the_servers_tools(self):
        out, registry = await self._execute(MCPToolRejected("echo", -32000, "bad input"))
        self.assertIn("rejected the call", out["error"])
        self.assertIn("bad input", out["error"])
        self.assertNotIn("unavailable", out["error"])
        registry.prune_dynamic_tools.assert_not_called()

    async def test_a_dead_server_still_prunes_its_tools(self):
        out, registry = await self._execute(ConnectionError("down"))
        self.assertIn("now unavailable", out["error"])
        registry.prune_dynamic_tools.assert_called_once()


if __name__ == "__main__":
    unittest.main()
