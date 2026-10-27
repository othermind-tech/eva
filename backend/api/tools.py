# api/tools.py
#
# Read-only Tool Manifest API.
#
# Exposes the full set of tools available to an authenticated user across
# three sources:  default (always-on), custom (user-enabled), and mcp
# (server-discovered tools cached in the DB).
#
# Endpoints
# ---------
#   GET /tools          – all sources combined
#   GET /tools/default  – default tools only
#   GET /tools/mcp      – MCP-cached tools only

from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from api._etag import etag_response

from core.auth.dependencies import get_current_auth
from config.settings import get_user_session
from core.db.repository import MCPRepo
from core.db.session import AsyncSessionLocal
from core.mcp.naming import build_mcp_tool_name
from core.tools.registry import DefaultToolRegistry, CustomToolRegistry

router = APIRouter(prefix="/tools", tags=["Tools"])


# ---------------------------------------------------------------------------
# Authority lookup table
#
# ToolBase carries no structured `authority` attribute — the classification
# lives only in module-level comments inside each tool file.  We mirror that
# classification here as a static map so the endpoint stays read-only and
# does not touch any existing tool or registry code.
#
# Anything not listed defaults to "unclassified".
# ---------------------------------------------------------------------------
TOOL_AUTHORITY_MAP: dict[str, str] = {
    # file tools
    "read_file":       "read-only",
    "read_file_range": "read-only",
    "list_files":      "read-only",
    "stat_file":       "read-only",
    "write_file":      "mutating",
    "delete_file":     "destructive",
    # shell
    "shell":           "mutating",
    # RAG
    "rag_retrieve":    "read-only",
    # web search
    "web_search":        "read-only",
    # memory
    "memory_recall":     "read-only",
    "memory_store":      "mutating",
    "memory_forget":     "destructive",
    # blackboard
    "blackboard_read":   "read-only",
    "blackboard_write":  "mutating",
    # delegation (G-003 / G-005 / G-006)
    "delegate_to_agent":    "read-only",
    "parallel_delegate":    "read-only",
    "coordinate":           "read-only",
    # inter-agent messaging (G-007)
    "send_agent_message":   "mutating",
    "read_agent_messages":  "read-only",
    # daemon signalling (G-009)
    "trigger_daemon":       "mutating",
}


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ToolManifestItem(BaseModel):
    name: str
    description: str
    authority: str   # "read-only" | "mutating" | "destructive" | "unclassified"
    source: str      # "default" | "custom" | "mcp"
    enabled: bool



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _authority(tool_name: str) -> str:
    return TOOL_AUTHORITY_MAP.get(tool_name, "unclassified")


def _default_tool_items() -> List[ToolManifestItem]:
    """Build manifest entries for all registered default tools."""
    items = []
    for name, tool_cls in DefaultToolRegistry.all().items():
        items.append(
            ToolManifestItem(
                name=name,
                description=getattr(tool_cls, "description", "") or "",
                authority=_authority(name),
                source="default",
                enabled=True,   # default tools are always loaded unconditionally
            )
        )
    return items


def _custom_tool_items(enabled_tool_names: List[str]) -> List[ToolManifestItem]:
    """Build manifest entries for all registered custom tools."""
    enabled_set = set(enabled_tool_names)
    items = []
    for name, tool_cls in CustomToolRegistry.all().items():
        items.append(
            ToolManifestItem(
                name=name,
                description=getattr(tool_cls, "description", "") or "",
                authority=_authority(name),
                source="custom",
                enabled=(name in enabled_set),
            )
        )
    return items


async def _mcp_tool_items(user_id: str) -> List[ToolManifestItem]:
    """
    Build manifest entries for all MCP tools cached in the DB for this user.

    Tool names follow the MCP naming convention in core/mcp/naming.py
    (``<config_name><delimiter><tool_name>``). The ``enabled`` flag
    mirrors the parent MCPConfig's ``enabled`` column.
    """
    items = []
    async with AsyncSessionLocal() as db:
        configs = await MCPRepo.list_configs(db, user_id)
        for config in configs:
            tools = await MCPRepo.list_tools_for_config(db, config.id)
            for tool in tools:
                items.append(
                    ToolManifestItem(
                        name=build_mcp_tool_name(config.name, tool.name),
                        description=tool.description or "",
                        authority="unclassified",   # MCP tools carry no authority metadata
                        source="mcp",
                        enabled=config.enabled,
                    )
                )
    return items


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=List[ToolManifestItem], summary="List all tools available to this user")
async def list_all_tools(request: Request, auth=Depends(get_current_auth)):
    """
    Returns every tool the authenticated user can invoke, across all sources:

    - **default** – system tools always loaded by the worker (file I/O, web search, shell, RAG)
    - **custom**  – user-registered custom tools; `enabled` reflects the current session
    - **mcp**     – tools discovered from the user's MCP server configs and cached in the DB

    Supports ETag / ``If-None-Match`` conditional requests (B-020).
    """
    user_id = auth["user_id"]
    session = get_user_session(user_id)
    enabled_custom = session.get("tools", [])

    data = (
        _default_tool_items()
        + _custom_tool_items(enabled_custom)
        + await _mcp_tool_items(user_id)
    )
    return etag_response(request, jsonable_encoder(data))


@router.get("/default", response_model=List[ToolManifestItem], summary="List default (system) tools")
async def list_default_tools(request: Request, auth=Depends(get_current_auth)):
    """
    Returns only the default system tools that are always available to every user.
    No DB query is required — results come entirely from the in-memory registry.

    Supports ETag / ``If-None-Match`` conditional requests (B-020).
    """
    return etag_response(request, jsonable_encoder(_default_tool_items()))


@router.get("/mcp", response_model=List[ToolManifestItem], summary="List MCP-discovered tools")
async def list_mcp_tools(auth=Depends(get_current_auth)):
    """
    Returns tools discovered from the user's MCP server configurations.

    Only tools that have been synced (via ``POST /mcp/{config_id}/sync``) appear
    here.  The ``enabled`` field mirrors the parent config's ``enabled`` flag —
    a tool from a disabled config will be listed with ``enabled=false``.
    """
    user_id = auth["user_id"]
    return await _mcp_tool_items(user_id)
