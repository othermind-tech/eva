"""Minimal real MCP stdio server whose one tool's *description* embeds the
value of an environment variable it received at launch.

Used by tests/test_mcp_credential_live.py (S-11) to prove -- fast,
deterministically, with no LLM call needed -- that a secret extracted to
keyring at config-save time is correctly resolved back to its real value
and actually delivered to the connected subprocess, not just that the
DB/API layer redacts it correctly.

Embedding the value in the tool's own description (rather than requiring
an actual tool *call*) means a single POST /mcp/{id}/sync + GET
/mcp/{id}/tools round-trip is decisive proof: the tool list is only
returned as part of the real MCP protocol handshake, which only succeeds if
MCPConnection was actually launched with the real env value E2E through
core/mcp/credentials.py::resolve_secrets().

Uses the official `mcp` SDK (already a project dependency), same as
tests/fixtures/mock_mcp_server.py -- a genuine MCP protocol server child
process, not a stub of EVA's own code.

V6.59 Phase 0: ported to the mcp 2.x server API (MCPServer). The 1.x
low-level `Server` decorators (`@app.list_tools()` / `@app.call_tool()`) no
longer exist, so the old version of this fixture crashed on import and the
sync returned zero tools.
"""
import os

from mcp.server.mcpserver import MCPServer

# Read once at process start: the environment is fixed for the life of this
# child process, so the description is the same whenever it is listed.
_VALUE = os.environ.get("TEST_SECRET_TOKEN", "ENV_VAR_NOT_SET")

server = MCPServer("mock-credential-server")


@server.tool(name="noop", description=f"received_secret={_VALUE}")
def noop() -> str:
    return "noop"


if __name__ == "__main__":
    server.run("stdio")
