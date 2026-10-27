# core/mcp/session_pool.py

"""
MCPSessionPool -- P-06b: persistent per-user MCP connection pool.

Holds one live MCPConnection per MCPConfig.id, reused across tool calls
instead of opening a fresh connection every time (200-800ms cold start for
stdio servers, 50-200ms for SSE/HTTP). Mirrors core/broker/local_broker.py's
shape: a process-local singleton with a background idle-TTL reaper -- not
core/shell/session.py's serialize-to-broker pattern, since a live MCP
connection is a stateful OS resource (subprocess/socket), not inert data
that can be reconstructed from a dict.

Lifecycle
---------
- Created lazily, on first tool call needing a given config.id -- pre-
  warming all enabled configs at login/startup would just reintroduce
  P-06a's exact problem (unwanted live connections) one layer down.
- Reused across calls, tool calls, and executions for the same
  user/config -- scoped per-user-session across executions, not per-
  execution or per-conversation (per-execution reuse buys nothing over a
  fresh connection; per-conversation binds a live subprocess/socket to
  hours of idle time for no reuse benefit).
- Evicted by:
  - Idle-TTL reaper (_IDLE_TTL_SECONDS) -- nothing has used it recently.
  - Config-change mark-and-sweep -- bump_generation() is called by
    api/mcp.py's update/delete handlers; the next acquirer sees the
    generation mismatch and recreates rather than reusing a possibly
    stale-credentialed connection. Does NOT force-cancel an in-flight
    call -- connection.py documents anyio cancel-scope fragility at
    length; the current lock-holder finishes normally on the still-valid
    connection.
  - A failed pre-acquisition send_ping() -- the connection died silently
    while idle (server restart, network blip, idle timeout) and is
    replaced before the caller ever sees it (OpenHands SDK's
    is_connected()-before-call pattern; EVA's equivalent primitive is the
    official mcp SDK's ClientSession.send_ping()).
  - A failed call_tool() -- evict-and-retry-once, same shape as M-04's
    original call_with_reconnect (since removed -- see note below).

Concurrency
-----------
Each pooled entry has its own asyncio.Lock, held for the full duration of
one call. Two executions for the same user (e.g. two conversations, or a
parent + subagent) can run as concurrent asyncio.Tasks and could otherwise
hit the same pooled connection at once -- nothing in the MCP client
guarantees a shared ClientSession is safe for concurrent in-flight calls.
This serializes same-server calls (a small queueing cost) in exchange for
correctness; still a large net win (200-800ms reconnect eliminated per call).

Owner-task-per-connection
--------------------------
Every pooled connection is owned by exactly one dedicated asyncio.Task
(_OwnedConnection._run()). That task, and only that task, ever calls
__aenter__()/__aexit__() or touches the underlying MCPConnection -- every
other caller (whichever execution/task needs a call made) communicates
with it by putting a request on a queue and awaiting a Future, never by
calling methods on the connection object directly.

This exists because two of EVA's MCP transports (sse_client,
streamable_http_client -- see core/mcp/connection.py's own docstring) use
anyio task groups internally, and anyio enforces that a task group must be
entered and exited by the same task. Without an owner task, the pool's own
cross-execution reuse would open a connection in whichever task first
needed it and could later evict/close it (bump_generation, idle reap) from
a different task entirely -- structurally the same "different task"
category of bug seen earlier with SSE connections (there triggered by
asyncio.wait_for() wrapping __aenter__ in a throwaway task; here the
trigger would be legitimate cross-execution connection reuse instead).
Live-tested directly against real SSE and streamable-HTTP servers with the
pre-owner-task design and did not reproduce a crash with the currently
installed mcp/anyio versions -- but the failure mode is exactly the kind
that depends on library-version internals, so the owner-task pattern is
kept as a structural guarantee rather than relying on that empirical result
holding forever.

Why core.mcp.runtime.call_with_reconnect (M-04) is not reused
-----------------------------------------------------------------
The original M-04 design assumed the pool would substitute its own
evict-and-replace factory into call_with_reconnect unchanged. In practice
that function always closed its connection in a `finally` block once the
call finished -- correct for M-04's fresh-connection-per-call model, but it
would have closed every pooled connection after a single use, defeating
pooling entirely. The retry-on-failure *shape* (try, evict, retry once,
propagate on second failure) is mirrored in call_tool() below instead;
call_with_reconnect itself was removed once this pool became the only
caller of MCPRuntime's connection-acquisition path.

Crash / restart behaviour (intentionally out of scope here)
-------------------------------------------------------------
If EVA's process is killed ungracefully (crash, Task Manager, power loss),
no cleanup code runs and a stdio server's child process can be left
running. Deliberately not addressed by automated PID tracking/reaping --
evaluated and rejected: detecting "our" child process reliably from outside
requires a before/after process snapshot that races against any other
concurrent subprocess spawn in the app (other MCP servers opening at the
same time, the shell tool, etc.), and mcp SDK servers built on the
standard stdio_server() helper already self-exit when they see their stdin
pipe close (which happens automatically when EVA's process dies) --
covering the common case without that risk. A graceful shutdown (normal
app quit) is handled correctly by stop() below.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from mcp import MCPError

from core.db.models import MCPConfig
from core.mcp.connection import MCPConnection
from core.mcp.credentials import resolve_secrets
from core.mcp.preview_registry import resolve_connection_args

logger = logging.getLogger(__name__)


class MCPToolRejected(Exception):
    """
    The MCP server is alive and answered a tool call with a protocol-level error
    (a JSON-RPC error response) -- the server refused the call; the connection is fine.

    V6.59 (mcp 2.x): the SDK now raises MCPError on the client for a tool that rejects
    with one, where mcp 1.x returned a normal result with isError=True. The pool used to
    treat EVERY exception as a dead connection, so under 2.x such a rejection discarded
    the healthy connection and RE-EXECUTED the tool on a fresh one (observed: two real
    executions of one call -- a double side effect for any non-idempotent tool), and the
    runtime then pruned the whole server's tools for the conversation.
    """

    def __init__(self, tool_name: str, code: Optional[int], message: str) -> None:
        self.tool_name = tool_name
        self.code = code
        self.message = message
        super().__init__(f"MCP tool '{tool_name}' rejected the call: {message}")

# How long a pooled connection may sit idle before the reaper closes it.
# Upper end of the 10-15 minute design range.
_IDLE_TTL_SECONDS = 15 * 60

# How often the reaper checks for idle entries.
_REAPER_INTERVAL_SECONDS = 60

# Built generically from day one (not because OAuth needs it now) --
# retrofitting a factory abstraction onto a pool already built around a
# hardcoded construction path is expensive later; parameterizing now is cheap.
ConnFactory = Callable[[MCPConfig], Awaitable[MCPConnection]]


async def _default_conn_factory(config: MCPConfig) -> MCPConnection:
    # S-11: config.env/.headers hold redacted placeholders at rest --
    # resolve_secrets() swaps in the real values from keyring only here,
    # at actual connection time. This is the real tool-call path (the
    # pooled connection every tool invocation reuses), so it's the one
    # site among the five where getting this right matters most.
    #
    # CDP preview pane: resolve_connection_args() is a no-op for every
    # config except "playwright" -- see core/mcp/preview_registry.py. Same
    # "resolve at actual connection time, not once at seed time" reasoning
    # as the secrets resolution above: a registered CDP endpoint can
    # appear/disappear over the app's lifetime (pane opens/closes), so this
    # must be checked on every connection attempt, not baked into the
    # pooled entry once.
    real_env, real_headers = resolve_secrets(config)
    conn = MCPConnection(
        name=config.name,
        transport=config.transport,
        command=config.command,
        args=resolve_connection_args(config),
        env=real_env,
        cwd=config.cwd,
        env_file=config.env_file,
        url=config.url,
        headers=real_headers,
    )
    await conn.__aenter__()
    return conn


class _PoolRequest:
    """One message sent to an _OwnedConnection's owner task."""
    __slots__ = ("kind", "tool_name", "arguments", "future")

    def __init__(self, kind: str, tool_name: str = "", arguments: Optional[dict] = None,
                 future: Optional[asyncio.Future] = None) -> None:
        self.kind = kind  # "call" | "ping" | "close"
        self.tool_name = tool_name
        self.arguments = arguments
        self.future = future


class _OwnedConnection:
    """
    Owns exactly one MCPConnection for its entire lifetime via a single
    dedicated asyncio.Task. See the module docstring's "Owner-task-per-
    connection" section for why this exists. Callers never touch the
    underlying MCPConnection directly -- only call_tool()/ping()/close(),
    which route through the owner task via a queue.
    """

    def __init__(self, config: MCPConfig, conn_factory: ConnFactory) -> None:
        self.config = config
        self._conn_factory = conn_factory
        self._queue: "asyncio.Queue[_PoolRequest]" = asyncio.Queue()
        self._ready = asyncio.Event()
        self._open_error: Optional[BaseException] = None
        self._task: asyncio.Task = asyncio.create_task(
            self._run(), name=f"mcp-owner-{config.id}"
        )

    async def _run(self) -> None:
        try:
            conn = await self._conn_factory(self.config)
        except Exception as exc:
            self._open_error = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            while True:
                req = await self._queue.get()
                if req.kind == "close":
                    if req.future is not None and not req.future.done():
                        req.future.set_result(None)
                    return
                try:
                    if req.kind == "call":
                        result = await conn.call_tool(req.tool_name, req.arguments or {})
                    else:  # "ping"
                        result = await conn.session.send_ping()
                    if req.future is not None and not req.future.done():
                        req.future.set_result(result)
                except Exception as exc:
                    if req.future is not None and not req.future.done():
                        req.future.set_exception(exc)
        finally:
            # Always the SAME task that called __aenter__() above.
            try:
                await conn.__aexit__(None, None, None)
            except Exception:
                pass

    async def _request(self, kind: str, tool_name: str = "", arguments: Optional[dict] = None):
        await self._ready.wait()
        if self._open_error is not None:
            raise self._open_error
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._queue.put(_PoolRequest(kind, tool_name, arguments, fut))
        return await fut

    async def call_tool(self, tool_name: str, arguments: dict):
        return await self._request("call", tool_name, arguments)

    async def ping(self):
        return await self._request("ping")

    async def close(self) -> None:
        """Idempotent -- safe to call on an already-closed/failed-to-open connection."""
        if self._task.done():
            return
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._queue.put(_PoolRequest("close", future=fut))
        try:
            await fut
        except Exception:
            pass
        await self._task


@dataclass
class _PoolEntry:
    config_id: str
    owned: Optional[_OwnedConnection] = None
    generation: int = -1  # generation this connection was created against; -1 = none yet
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)


class MCPSessionPool:
    """Process-local singleton -- see module docstring."""

    def __init__(self) -> None:
        self._entries: Dict[str, _PoolEntry] = {}
        self._generations: Dict[str, int] = {}
        self._reaper_task: Optional[asyncio.Task] = None
        self._started = False

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(), name="mcp-session-pool-reaper"
        )
        logger.info("[MCPSessionPool] started")

    async def stop(self) -> None:
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        # Close every pooled connection CONCURRENTLY so total shutdown time
        # doesn't scale with the number of pooled servers -- sequential
        # closing (each stdio close can take up to ~2s for its own
        # graceful-then-SIGKILL sequence) risked exceeding the desktop
        # app's SIGTERM grace window (6s, see build/main.js) once a user
        # had 3+ MCP servers pooled, causing Electron to SIGKILL the whole
        # backend mid-cleanup and orphan whichever servers hadn't finished
        # closing yet.
        closers = [
            entry.owned.close() for entry in self._entries.values()
            if entry.owned is not None
        ]
        if closers:
            await asyncio.gather(*closers, return_exceptions=True)
        self._entries.clear()
        self._started = False
        logger.info("[MCPSessionPool] stopped")

    # ── Config-change eviction (mark-and-sweep) ─────────────────────────

    def bump_generation(self, config_id: str) -> None:
        """
        Call from api/mcp.py's update handler whenever a config's
        connection-relevant fields change. See module docstring for why
        this doesn't force-close an in-flight connection.
        """
        self._generations[config_id] = self._generations.get(config_id, 0) + 1

    async def discard(self, config_id: str) -> None:
        """
        Call from api/mcp.py's delete handler (after bump_generation) to
        close and drop the entry entirely, rather than leave a
        generation-orphaned one sitting in the pool until the idle reaper
        gets to it -- or, worse, leaked forever since a discarded config_id
        is no longer visited by the reaper's scan at all once removed from
        _entries. Safe to call even if the config was never pooled.

        Synchronizes on the entry's own lock -- the same lock _acquire_entry()
        holds for a call_tool()'s full duration -- before touching it. A
        naive pop-then-close (the original implementation) could race an
        in-flight call_tool() on this exact config_id: closing the
        connection out from under it causes the owner task
        (_OwnedConnection._run()) to see the "close" request and return
        immediately, WITHOUT ever processing the "call" request the in-flight
        call already queued -- that call's awaited future then never
        resolves, and it hangs until its own outer timeout eventually gives
        up on it (found live: a resync fired by the CDP preview pane
        registering mid-navigate left a browser_navigate call hung for the
        full 300s TOOL_EXECUTION_TIMEOUT_SECONDS before its automatic retry
        recovered it). Waiting for the lock instead means discard() only
        ever closes a connection that is genuinely idle -- an in-flight call
        finishes normally first, and discard() simply queues up behind it.
        """
        entry = self._entries.get(config_id)
        if entry is None:
            return
        async with entry.lock:
            # Only remove it if it's still the SAME entry object -- a
            # concurrent discard() may have already popped and closed it
            # while this call was waiting for the lock.
            if self._entries.get(config_id) is entry:
                self._entries.pop(config_id, None)
                self._generations.pop(config_id, None)
            if entry.owned is not None:
                await entry.owned.close()
                entry.owned = None

    # ── Acquire (internal) ──────────────────────────────────────────────

    @asynccontextmanager
    async def _acquire_entry(
        self, config: MCPConfig, conn_factory: ConnFactory,
    ) -> AsyncIterator[_PoolEntry]:
        entry = self._entries.setdefault(config.id, _PoolEntry(config_id=config.id))
        async with entry.lock:
            current_gen = self._generations.get(config.id, 0)
            need_fresh = entry.owned is None or entry.generation != current_gen

            if not need_fresh:
                # Pre-acquisition liveness check -- a pooled connection can
                # die silently while idle for up to _IDLE_TTL_SECONDS;
                # don't find out only when the real call fails.
                try:
                    await entry.owned.ping()
                except Exception:
                    need_fresh = True

            if need_fresh:
                if entry.owned is not None:
                    await entry.owned.close()
                entry.owned = _OwnedConnection(config, conn_factory)
                entry.generation = current_gen

            entry.last_used = time.monotonic()
            try:
                yield entry
            finally:
                entry.last_used = time.monotonic()

    @staticmethod
    async def _still_answers(entry: "_PoolEntry") -> bool:
        """True if the pooled connection still responds to a ping."""
        try:
            await entry.owned.ping()
            return True
        except Exception:
            return False

    # ── Public API ───────────────────────────────────────────────────────

    async def call_tool(
        self,
        config: MCPConfig,
        tool_name: str,
        arguments: Dict[str, Any],
        conn_factory: ConnFactory = _default_conn_factory,
    ):
        """
        Call a tool on this config's pooled connection, opening/reusing/
        replacing it as needed. On a call failure, evicts the now-
        confirmed-dead connection and retries once with a fresh one before
        propagating (M-04's original retry shape, adapted so the
        connection stays open across calls instead of closing after each
        one -- see module docstring).
        """
        async with self._acquire_entry(config, conn_factory) as entry:
            try:
                return await entry.owned.call_tool(tool_name, arguments)
            except Exception as exc:
                # An MCPError means a JSON-RPC error came back -- but the SDK also reports
                # a dead transport ("connection closed", request timeout) as an MCPError,
                # and the code spaces overlap, so the code alone cannot tell the two apart.
                # Liveness can: if the connection still answers a ping, the server heard us
                # and refused the call -- surface that, do NOT re-run the tool.
                if isinstance(exc, MCPError) and await self._still_answers(entry):
                    raise MCPToolRejected(
                        tool_name, getattr(exc, "code", None), getattr(exc, "message", str(exc))
                    ) from exc
                await entry.owned.close()
                entry.owned = _OwnedConnection(config, conn_factory)
                entry.generation = self._generations.get(config.id, 0)
                return await entry.owned.call_tool(tool_name, arguments)

    # ── Background idle-TTL reaper ──────────────────────────────────────

    async def _reaper_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_REAPER_INTERVAL_SECONDS)
                await self._reap_idle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("[MCPSessionPool] reaper error (non-fatal): %s", exc)

    async def _reap_idle(self) -> None:
        now = time.monotonic()
        stale_ids = [
            cid for cid, entry in self._entries.items()
            if entry.owned is not None
            and not entry.lock.locked()
            and (now - entry.last_used) > _IDLE_TTL_SECONDS
        ]
        for cid in stale_ids:
            entry = self._entries.get(cid)
            if entry is None or entry.owned is None:
                continue
            await entry.owned.close()
            entry.owned = None
            entry.generation = -1
        if stale_ids:
            logger.debug("[MCPSessionPool] reaped %d idle connections", len(stale_ids))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

mcp_session_pool = MCPSessionPool()
