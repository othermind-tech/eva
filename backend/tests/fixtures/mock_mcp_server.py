"""Minimal real MCP stdio server exposing one tool with a distinctive,
non-hallucinatable output. Used by tests/test_behaviour.py (B18) to verify
end-to-end that a model actually discovers and calls a live MCP tool,
rather than trust that EVA's MCP sync/registration machinery alone proves
the model-facing path works.

Uses the official `mcp` SDK (already a project dependency) -- this is a
genuine MCP protocol server child process, not a stub of EVA's own code.

V6.59 Phase 0: ported to the mcp 2.x server API (MCPServer). The 1.x
low-level `Server` decorators no longer exist, so the old version of this
fixture crashed on import and the MCP tool was never actually offered.
"""
from mcp.server.mcpserver import MCPServer

SECRET_CODE = "zephyr-banana-42"

server = MCPServer("mock-secret-server")


@server.tool(
    name="get_secret_code",
    description="Returns a secret code word. Call this to find out the secret code.",
)
def get_secret_code() -> str:
    return SECRET_CODE


if __name__ == "__main__":
    server.run("stdio")
