# core/mcp/connection.py

"""
MCP connection handling with robust logging and cancel-scope safe cleanup.

Supported transports
--------------------
- stdio       : subprocess (e.g. `npx @modelcontextprotocol/server-filesystem`)
- sse         : legacy SSE protocol (MCP spec ≤2024-11-05) — used by servers
                like @modelcontextprotocol/server-github.  Client: sse_client.
                Connects to /sse, receives an "endpoint" event, then POSTs to
                that separate URL.
- http        : new Streamable HTTP protocol (MCP spec 2025-03-26).  Client:
                streamable_http_client.  POSTs requests; SSE responses are
                inline on the same connection.

Transport vs protocol table
---------------------------
transport     client                    tuple returned
----------    -----                     -----
stdio         stdio_client              (read, write)                  2-tuple
sse           sse_client                (read, write)                  2-tuple
http          streamable_http_client    (read, write)                  2-tuple

mcp 2.x migration notes (V6.59)
-------------------------------
- streamablehttp_client was renamed streamable_http_client and no longer takes
  headers / timeout / sse_read_timeout: those now live on an httpx2.AsyncClient
  passed as http_client=.  When the caller supplies the client the transport
  does NOT close it, so MCPConnection owns and closes it (_close_http_client).
- The get_session_id callback (3rd tuple element) was removed.
- mcp 2.x depends on httpx2, not httpx.

Why asyncio.wait_for() is banned for sse/http operations
---------------------------------------------------------
On Python ≤3.11, asyncio.wait_for(coro) wraps the coroutine in a NEW asyncio
Task via ensure_future().  Both sse_client and streamable_http_client use
anyio.create_task_group() internally.  The anyio CancelScope records
_host_task = <the wait_for sub-task>.  When __aexit__ later runs in the
ORIGINAL task, anyio 4.x raises "Attempted to exit cancel scope in a different
task than it was entered in."

Fix: use anyio.fail_after() for all timeouts on sse/http operations.
anyio.fail_after() is a context manager that cancels the CURRENT task within
its own scope — no new Task is ever created.  The cancel-scope invariant holds.

For STDIO transport asyncio.wait_for() is still safe because stdio_client does
not use anyio task groups internally.

Why except BaseException is needed in cleanup
----------------------------------------------
When an SSE connection fails, the anyio task group collects the exception and
raises BaseExceptionGroup on __aexit__.  On Python 3.10, BaseExceptionGroup
inherits from BaseException — NOT Exception.  A plain "except Exception" misses
it, leaving the generator unclosed and triggering async-generator finalizer
Task-N errors.

Fix: catch BaseException in all cleanup/exit paths, re-raise only
KeyboardInterrupt and SystemExit.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Dict

import anyio  # anyio.fail_after() — same-task timeout, no cross-task issues
import httpx2  # mcp 2.x's HTTP stack (replaces httpx); used to configure streamable HTTP

from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client                         # legacy SSE protocol
from mcp.client.streamable_http import streamable_http_client  # new Streamable HTTP protocol
from mcp import ClientSession, StdioServerParameters

from config.settings import (
    MCP_CONNECT_TIMEOUT_SECONDS,
    MCP_INIT_TIMEOUT_SECONDS,
    MCP_CALL_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

DEFAULT_ENTER_TIMEOUT = MCP_CONNECT_TIMEOUT_SECONDS
DEFAULT_INIT_TIMEOUT  = MCP_INIT_TIMEOUT_SECONDS
DEFAULT_CALL_TIMEOUT  = MCP_CALL_TIMEOUT_SECONDS


def _suppress_cleanup_exc(exc: BaseException) -> bool:
    """
    Return True if the exception should be silently suppressed during
    connection teardown.  KeyboardInterrupt and SystemExit are system-level
    signals and must always propagate.  Everything else — including
    BaseExceptionGroup from anyio task groups, CancelledError from cancel
    scopes, and transport-level connection errors — is safe to suppress
    when we are already in a cleanup path.
    """
    return not isinstance(exc, (KeyboardInterrupt, SystemExit))


def _mcp_cancelled_to_connection_error(exc: BaseException, label: str) -> None:
    """
    Convert an anyio-internal CancelledError to a ConnectionError so that
    callers using 'except Exception' (e.g. _check_config_health) can catch it.

    When an SSE server is unreachable, the background task inside
    streamable_http_client's anyio task group fails with ConnectError.  The
    task group's cancel scope then injects CancelledError (with message
    "Cancelled by cancel scope <hex>") into the current task.  This
    CancelledError is a BaseException — it bypasses any 'except Exception'
    block and propagates all the way up to the ASGI handler, causing HTTP 500.

    We only convert CancelledErrors that originate from an anyio cancel scope
    (identified by "cancel scope" in the message).  CancelledErrors from
    external task cancellation (e.g. request timeout) are left unchanged.
    """
    if isinstance(exc, asyncio.CancelledError) and "cancel scope" in str(exc).lower():
        raise ConnectionError(
            f"[MCP] {label}: transport failed — connection interrupted by "
            f"internal anyio cancel scope: {exc}"
        ) from exc


class MCPConnection:
    """
    Handles MCP connections with debug logging and safe cleanup.

    Usage:
        async with MCPConnection(...) as session:
            result = await session.list_tools()
    """

    def __init__(
        self,
        *,
        name: str,
        transport: str,
        command: Optional[str] = None,
        args: Optional[list] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        env_file: Optional[str] = None,
        url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.name = name
        self.transport = transport

        # STDIO parameters
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd
        self.env_file = env_file

        # HTTP/SSE parameters
        self.url = url
        self.headers = headers or {}

        self._client_ctx = None
        self._http_client = None   # httpx2.AsyncClient for the "http" transport; closed by us
        self._session_ctx = None
        self.session = None

    async def __aenter__(self):
        """
        Connect and initialize MCP session.
        For STDIO proxies, a handshake timeout is non-fatal (proxy may need
        the first real message before it wakes up).
        """
        logger.info(f"[MCP '{self.name}'] Attempting connect via {self.transport}")

        # ------------------------------------------------------------------
        # Transport creation
        # ------------------------------------------------------------------
        if self.transport == "stdio":
            logger.info(
                f"[MCP '{self.name}'] Starting STDIO process: "
                f"command={self.command}, args={self.args}"
            )
            if self.env:
                logger.debug(f"[MCP '{self.name}'] STDIO env vars: {self.env}")
            if self.cwd:
                logger.debug(f"[MCP '{self.name}'] STDIO cwd: {self.cwd}")
            if self.env_file:
                logger.debug(f"[MCP '{self.name}'] STDIO env_file: {self.env_file}")

            params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=self.env,
                cwd=self.cwd,
                envFile=self.env_file,
            )
            self._client_ctx = stdio_client(params)

        elif self.transport == "sse":
            # Legacy SSE protocol (e.g. @modelcontextprotocol/server-github).
            # sse_client uses anyio internally — do NOT wrap in asyncio.wait_for().
            logger.info(f"[MCP '{self.name}'] Connecting legacy SSE: {self.url}")
            self._client_ctx = sse_client(
                self.url,
                headers=self.headers,
                timeout=float(DEFAULT_ENTER_TIMEOUT),
                sse_read_timeout=float(DEFAULT_CALL_TIMEOUT),
            )

        elif self.transport == "http":
            # New Streamable HTTP protocol (MCP spec 2025-03-26).
            # streamable_http_client uses anyio internally — same rules apply.
            # mcp 2.x: headers and timeouts are configured on the httpx2 client
            # we hand it (same values the 1.x keywords carried); we own its
            # lifecycle because a caller-supplied client is not closed by the SDK.
            logger.info(f"[MCP '{self.name}'] Connecting streamable HTTP: {self.url}")
            self._http_client = httpx2.AsyncClient(
                headers=self.headers or None,
                timeout=httpx2.Timeout(
                    float(DEFAULT_ENTER_TIMEOUT), read=float(DEFAULT_CALL_TIMEOUT)
                ),
            )
            self._client_ctx = streamable_http_client(
                self.url,
                http_client=self._http_client,
            )

        else:
            raise ValueError(f"[MCP '{self.name}'] Unsupported transport '{self.transport}'")

        # ------------------------------------------------------------------
        # Enter transport context
        #
        # STDIO  : asyncio.wait_for() is safe — no anyio cancel scopes inside.
        # HTTP/SSE: call __aenter__ directly; httpx owns the timeout; do NOT
        #           use asyncio.wait_for() — see module docstring.
        #
        # Catch BaseException (not just Exception) so that BaseExceptionGroup
        # from anyio task groups is always caught and the generator is always
        # closed before we leave __aenter__, preventing async-generator
        # finalizer Task-N errors.
        # ------------------------------------------------------------------
        try:
            if self.transport == "stdio":
                # stdio_client: no anyio task groups — asyncio.wait_for() is safe.
                # Returns 2-tuple (read, write).
                transport_result = await asyncio.wait_for(
                    self._client_ctx.__aenter__(),
                    timeout=DEFAULT_ENTER_TIMEOUT,
                )
                self.read, self.write = transport_result

            elif self.transport == "sse":
                # sse_client: uses anyio internally — do NOT use asyncio.wait_for().
                # Returns 2-tuple (read, write).
                transport_result = await self._client_ctx.__aenter__()
                self.read, self.write = transport_result

            else:  # "http" — streamable_http_client
                # streamable_http_client: uses anyio internally — no asyncio.wait_for().
                # Returns 2-tuple (read, write) in mcp 2.x.
                transport_result = await self._client_ctx.__aenter__()
                self.read, self.write = transport_result[0], transport_result[1]

            logger.info(f"[MCP '{self.name}'] Transport connection established")

        except BaseException as exc:
            if not _suppress_cleanup_exc(exc):
                raise
            logger.exception(f"[MCP '{self.name}'] Transport connect failed")
            await self._cleanup_transport_on_error()
            _mcp_cancelled_to_connection_error(exc, f"'{self.name}' transport connect")
            raise

        # ------------------------------------------------------------------
        # MCP Session setup
        #
        # ClientSession wraps the anyio-managed streams from streamable_http_client.
        # Do NOT use asyncio.wait_for() here — it creates a new Task that
        # interacts with those streams, breaking the anyio cancel-scope invariant.
        # Call __aenter__ directly; httpx controls the underlying timeout.
        # ------------------------------------------------------------------
        self._session_ctx = ClientSession(self.read, self.write)
        try:
            self.session = await self._session_ctx.__aenter__()
            logger.debug(f"[MCP '{self.name}'] MCP session context opened")
        except BaseException as exc:
            if not _suppress_cleanup_exc(exc):
                raise
            logger.exception(f"[MCP '{self.name}'] Session open failed")
            await self._cleanup_both()
            _mcp_cancelled_to_connection_error(exc, f"'{self.name}' session open")
            raise

        # ------------------------------------------------------------------
        # Initialize handshake
        #
        # anyio.fail_after() runs in the CURRENT task — no new Task created,
        # no cancel-scope cross-task issue.  Non-fatal on timeout: STDIO
        # proxies often need a first real message before they start.
        # Any non-timeout error (e.g. ConnectError on SSE) is fatal: close
        # everything and re-raise so the caller sees a clean error.
        # ------------------------------------------------------------------
        try:
            logger.info(f"[MCP '{self.name}'] Sending initialize handshake")
            with anyio.fail_after(DEFAULT_INIT_TIMEOUT):
                await self.session.initialize()
            logger.info(f"[MCP '{self.name}'] MCP initialize OK")

        except TimeoutError:
            # Non-fatal: proxy may need traffic to wake up
            logger.warning(
                f"[MCP '{self.name}'] Handshake timeout ({DEFAULT_INIT_TIMEOUT}s); "
                f"continuing — proxy may require traffic to start"
            )

        except BaseException as exc:
            if not _suppress_cleanup_exc(exc):
                raise
            # Connection-level error (e.g. SSE server not available)
            logger.exception(f"[MCP '{self.name}'] Initialize failed")
            await self._cleanup_both()
            _mcp_cancelled_to_connection_error(exc, f"'{self.name}' initialize")
            raise

        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        """
        Gracefully close session and transport.

        Catches BaseException (not just Exception) so that BaseExceptionGroup
        raised by anyio task groups during teardown is always suppressed and
        the generator is always fully closed.
        """
        logger.info(f"[MCP '{self.name}'] Closing MCP connection")

        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(exc_type, exc, tb)
            except BaseException as e:
                if not _suppress_cleanup_exc(e):
                    raise
                logger.debug(
                    f"[MCP '{self.name}'] Session exit suppressed: {type(e).__name__}: {e}"
                )

        if self._client_ctx:
            try:
                await self._client_ctx.__aexit__(exc_type, exc, tb)
            except BaseException as e:
                if not _suppress_cleanup_exc(e):
                    raise
                logger.debug(
                    f"[MCP '{self.name}'] Transport exit suppressed: {type(e).__name__}: {e}"
                )

        await self._close_http_client()

    # --------------------------------------------------------------------------
    # MCP Calls
    #
    # All methods use anyio.fail_after() instead of asyncio.wait_for().
    # anyio.fail_after() is a context manager that cancels the CURRENT task
    # (no new Task), so the anyio cancel-scope invariant is always satisfied
    # regardless of transport type.
    # --------------------------------------------------------------------------

    async def list_tools(self):
        """List tools exposed by the MCP server."""
        if not self.session:
            raise RuntimeError("No MCP session initialized")
        logger.debug(f"[MCP '{self.name}'] Invoking list_tools()")
        try:
            with anyio.fail_after(DEFAULT_CALL_TIMEOUT):
                return await self.session.list_tools()
        except TimeoutError:
            logger.error(
                f"[MCP '{self.name}'] list_tools() timeout after {DEFAULT_CALL_TIMEOUT}s"
            )
            raise
        except asyncio.CancelledError as exc:
            raise ConnectionError(
                f"[MCP '{self.name}'] list_tools() cancelled by transport: {exc}"
            ) from exc

    async def call_tool(self, tool_name: str, args: dict):
        """Call a named MCP tool via JSON-RPC."""
        if not self.session:
            raise RuntimeError("No MCP session established")
        logger.debug(f"[MCP '{self.name}'] Calling tool '{tool_name}' with args: {args}")
        try:
            with anyio.fail_after(DEFAULT_CALL_TIMEOUT):
                return await self.session.call_tool(tool_name, args)
        except TimeoutError:
            logger.error(
                f"[MCP '{self.name}'] call_tool('{tool_name}') timeout after {DEFAULT_CALL_TIMEOUT}s"
            )
            raise
        except asyncio.CancelledError as exc:
            raise ConnectionError(
                f"[MCP '{self.name}'] call_tool('{tool_name}') cancelled by transport: {exc}"
            ) from exc

    async def list_resources(self):
        """
        List resources exposed by the MCP server.
        Raises on error so the caller can decide whether to treat it as
        unsupported (MCPManager.sync_resources_for_config catches and returns []).
        """
        if not self.session:
            raise RuntimeError("No MCP session initialized")
        logger.debug(f"[MCP '{self.name}'] Invoking list_resources()")
        try:
            with anyio.fail_after(DEFAULT_CALL_TIMEOUT):
                return await self.session.list_resources()
        except TimeoutError:
            logger.error(
                f"[MCP '{self.name}'] list_resources() timeout after {DEFAULT_CALL_TIMEOUT}s"
            )
            raise
        except asyncio.CancelledError as exc:
            raise ConnectionError(
                f"[MCP '{self.name}'] list_resources() cancelled by transport: {exc}"
            ) from exc
        except Exception as exc:
            logger.warning(
                f"[MCP '{self.name}'] list_resources() unsupported or failed: {exc}"
            )
            raise

    async def read_resource(self, uri: str):
        """Fetch the content of a specific resource by URI."""
        if not self.session:
            raise RuntimeError("No MCP session initialized")
        logger.debug(f"[MCP '{self.name}'] Invoking read_resource(uri={uri!r})")
        try:
            with anyio.fail_after(DEFAULT_CALL_TIMEOUT):
                return await self.session.read_resource(uri)
        except TimeoutError:
            logger.error(
                f"[MCP '{self.name}'] read_resource('{uri}') timeout after {DEFAULT_CALL_TIMEOUT}s"
            )
            raise
        except asyncio.CancelledError as exc:
            raise ConnectionError(
                f"[MCP '{self.name}'] read_resource('{uri}') cancelled by transport: {exc}"
            ) from exc

    async def list_prompts(self):
        """
        List prompt templates exposed by the MCP server.
        Raises on error so the caller can decide whether to treat it as unsupported.
        """
        if not self.session:
            raise RuntimeError("No MCP session initialized")
        logger.debug(f"[MCP '{self.name}'] Invoking list_prompts()")
        try:
            with anyio.fail_after(DEFAULT_CALL_TIMEOUT):
                return await self.session.list_prompts()
        except TimeoutError:
            logger.error(
                f"[MCP '{self.name}'] list_prompts() timeout after {DEFAULT_CALL_TIMEOUT}s"
            )
            raise
        except asyncio.CancelledError as exc:
            raise ConnectionError(
                f"[MCP '{self.name}'] list_prompts() cancelled by transport: {exc}"
            ) from exc
        except Exception as exc:
            logger.warning(
                f"[MCP '{self.name}'] list_prompts() unsupported or failed: {exc}"
            )
            raise

    async def get_prompt(self, name: str, arguments: Optional[Dict[str, str]] = None):
        """Render a prompt template by name with optional string arguments."""
        if not self.session:
            raise RuntimeError("No MCP session initialized")
        logger.debug(
            f"[MCP '{self.name}'] Invoking get_prompt(name={name!r}, arguments={arguments})"
        )
        try:
            with anyio.fail_after(DEFAULT_CALL_TIMEOUT):
                return await self.session.get_prompt(name, arguments=arguments or {})
        except TimeoutError:
            logger.error(
                f"[MCP '{self.name}'] get_prompt('{name}') timeout after {DEFAULT_CALL_TIMEOUT}s"
            )
            raise
        except asyncio.CancelledError as exc:
            raise ConnectionError(
                f"[MCP '{self.name}'] get_prompt('{name}') cancelled by transport: {exc}"
            ) from exc

    # --------------------------------------------------------------------------
    # Cleanup helpers
    # --------------------------------------------------------------------------

    async def _close_http_client(self):
        """
        Close the httpx2 client we created for the "http" transport.
        mcp 2.x does not close a caller-supplied client, so without this the
        connection pool and its sockets would leak on every connect/disconnect.
        Idempotent; same BaseException-suppression rationale as the other
        cleanup helpers.
        """
        client, self._http_client = self._http_client, None
        if client is None:
            return
        try:
            await client.aclose()
        except BaseException as e:
            if not _suppress_cleanup_exc(e):
                raise
            logger.debug(
                f"[MCP '{self.name}'] HTTP client close suppressed: {type(e).__name__}: {e}"
            )

    async def _cleanup_transport_on_error(self):
        """
        Emergency cleanup when transport entry fails mid-way.
        Catches BaseException so BaseExceptionGroup from anyio task groups
        is always suppressed — the generator must be closed before control
        returns to the caller to prevent async-generator finalizer Task-N errors.
        """
        if self._client_ctx:
            try:
                await self._client_ctx.__aexit__(None, None, None)
            except BaseException as e:
                if not _suppress_cleanup_exc(e):
                    raise
        await self._close_http_client()

    async def _cleanup_both(self):
        """
        Emergency cleanup of both session and transport on error.
        Same BaseException handling rationale as _cleanup_transport_on_error.
        """
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except BaseException as e:
                if not _suppress_cleanup_exc(e):
                    raise

        if self._client_ctx:
            try:
                await self._client_ctx.__aexit__(None, None, None)
            except BaseException as e:
                if not _suppress_cleanup_exc(e):
                    raise
        await self._close_http_client()
