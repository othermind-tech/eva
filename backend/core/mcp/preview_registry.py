# core/mcp/preview_registry.py
"""
In-memory registry of live CDP endpoints for EVA's embedded browser preview
pane (Electron BrowserView, built separately from this backend work).

Not a DB table on purpose: a registered ws:// URL is only valid for the
current app run (the relay's port differs every launch), so nothing here
should survive a restart -- a plain in-process dict, matching how other
process-local state is kept (e.g. config.settings._USER_SESSIONS), gives
that "gone on restart" behaviour for free.

Deliberately kept separate from SessionConfig / config.settings.py's
_USER_SESSIONS: this is infrastructure plumbing set once by Electron's own
relay process, not a user preference -- it must not ride along on
GET/POST /session or be editable through the normal settings-save path.

Keyed by name (not a single global field) even though v1 only ever
registers one entry ("preview") -- a future multi-session testing-agent
feature will want several concurrent named CDP endpoints, and keying by
name from day one avoids a data-model migration later for free.

Consumers
---------
core.mcp.session_pool._default_conn_factory and core.mcp.manager
.MCPManager._connection both call resolve_connection_args() below in place
of using config.args directly -- the only two sites that actually turn an
MCPConfig row into a live subprocess connection. Checking the registry at
that call site (not once at seed time) is what makes a pane open/close or
an app restart take effect on the very next connection attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.db.models import MCPConfig

# name -> registration. Process-local; never persisted.
_ENDPOINTS: Dict[str, "PreviewEndpoint"] = {}


@dataclass
class PreviewEndpoint:
    name: str
    cdp_endpoint: str
    registered_at: datetime


def register(name: str, cdp_endpoint: str) -> PreviewEndpoint:
    """Upsert a named CDP endpoint registration. Overwrites any existing entry for this name."""
    entry = PreviewEndpoint(
        name=name,
        cdp_endpoint=cdp_endpoint,
        registered_at=datetime.now(timezone.utc),
    )
    _ENDPOINTS[name] = entry
    return entry


def unregister(name: str) -> bool:
    """Remove a named registration. Idempotent: returns False (not an error) if it wasn't there."""
    return _ENDPOINTS.pop(name, None) is not None


def get(name: str) -> Optional[PreviewEndpoint]:
    return _ENDPOINTS.get(name)


def list_all() -> List[PreviewEndpoint]:
    return list(_ENDPOINTS.values())


# ---------------------------------------------------------------------------
# Args resolution -- the one v1 consumer, hardcoded (not generic dependency
# tracking; see api/session.py's preview-endpoints handlers for the sibling
# hardcoded relationship on the resync side).
# ---------------------------------------------------------------------------

# v1 only ever attaches the "preview" pane to the "playwright" MCP config.
# A future multi-session feature would need a real name->config mapping
# instead of these two constants -- deliberately not built until there's a
# second consumer (see module docstring).
_PREVIEW_ENDPOINT_NAME = "preview"
_PLAYWRIGHT_CONFIG_NAME = "playwright"


def resolve_connection_args(config: MCPConfig) -> list:
    """
    Returns the args list to actually use when connecting to this config's
    subprocess right now.

    For every config except "playwright", always returns config.args
    unchanged -- this function is a no-op for the rest of EVA's MCP
    ecosystem. For "playwright" specifically: config.args unchanged when no
    "preview" endpoint is currently registered (today's existing
    standalone-browser behaviour, baked in at seed time by
    core/defaults/seeder.py -- see its _mcp_placeholders() for the
    {workspace}/{browser_channel} placeholders that already produced this
    list), or a completely different CDP-attach args list when one is.

    Not a flat {cdp_endpoint} string substitution into the stored args:
    the two modes are mutually exclusive arg sets (--browser/--isolated are
    meaningless once attaching to an existing browser via --cdp-endpoint),
    so this branches on the whole list rather than patching one token.

    config.cwd is reused for --output-dir rather than re-deriving
    {workspace} from scratch -- it's already the resolved per-user
    workspace path from seed time, and stays correct regardless of which
    args branch is active.
    """
    if config.name != _PLAYWRIGHT_CONFIG_NAME:
        return config.args

    entry = get(_PREVIEW_ENDPOINT_NAME)
    if entry is None:
        return config.args

    output_dir = f"{config.cwd}/_browser_output" if config.cwd else "_browser_output"
    return [
        "-y",
        "@playwright/mcp@0.0.82",
        "--cdp-endpoint",
        entry.cdp_endpoint,
        "--image-responses",
        "omit",
        "--file-paths",
        "absolute",
        "--output-dir",
        output_dir,
    ]
