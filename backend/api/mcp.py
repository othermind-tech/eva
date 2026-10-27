# api/mcp.py

import logging
import time
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from core.mcp.manager import MCPManager
from core.mcp.connection import MCPConnection
from core.mcp.naming import MCP_TOOL_NAME_DELIMITER, is_valid_mcp_config_name
from core.mcp.session_pool import mcp_session_pool
from core.mcp.credentials import (
    extract_and_store_secrets_for_field,
    refs_for_field,
    clear_field_secrets,
    resolve_secrets,
    delete_credentials,
)
from core.db.models import MCPToolMeta, new_uuid
from core.db.session import AsyncSessionLocal
from core.db.repository import MCPRepo, ProjectRepo
from core.db.models import MCPConfig
from core.auth.dependencies import get_current_auth
from sqlalchemy import select
from api.schemas.responses import StatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["MCP"])

# =========================
# API Request/Response Models
# =========================

class MCPConfigBase(BaseModel):
    name: str = Field(..., description="Friendly name for the MCP server")
    transport: str = Field(
        ...,
        description="Transport type: stdio | http | sse",
        pattern="^(stdio|http|sse)$",
    )
    enabled: bool = Field(True, description="Enable or disable this MCP config")

    # Project scope — None = global user-level MCP, UUID = scoped to that project
    project_id: Optional[str] = Field(
        None,
        description="Project UUID this MCP is scoped to. Omit (or null) for a global MCP.",
    )

    # STDIO fields
    command: Optional[str] = Field(None, description="Command to launch STDIO MCP")
    args: Optional[List[str]] = Field(None, description="Arguments for STDIO MCP")
    env: Optional[Dict[str, str]] = Field(None, description="Environment variables")
    cwd: Optional[str] = Field(None, description="Working directory for STDIO")
    env_file: Optional[str] = Field(None, description="Optional .env file")

    # HTTP/SSE fields
    url: Optional[str] = Field(None, description="HTTP or SSE endpoint URL")
    headers: Optional[Dict[str, str]] = Field(None, description="Optional headers")

    @model_validator(mode="after")
    def validate_transport(self):
        # STDIO must have command
        if self.transport == "stdio" and not self.command:
            raise ValueError("STDIO transport requires 'command'")
        # HTTP/SSE must have URL
        if self.transport in ("http", "sse") and not self.url:
            raise ValueError(f"{self.transport.upper()} transport requires 'url'")
        return self

    @model_validator(mode="after")
    def validate_name_delimiter(self):
        # Tool names are built as build_mcp_tool_name(config.name, tool.name)
        # (core/mcp/naming.py) and parsed back apart on the first occurrence
        # of MCP_TOOL_NAME_DELIMITER -- a config name containing the
        # delimiter itself would make that split resolve to the wrong
        # config_name/tool_name pair. Reject it here so the split is always
        # unambiguous by construction, not by luck.
        if not is_valid_mcp_config_name(self.name):
            raise ValueError(
                f"MCP config name must not contain '{MCP_TOOL_NAME_DELIMITER}' "
                f"(reserved as the tool-name delimiter)"
            )
        return self


class MCPConfigCreate(MCPConfigBase):
    pass


class MCPConfigUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    project_id: Optional[str] = None   # use "" or explicit null to clear scope
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    env_file: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None

    @model_validator(mode="after")
    def validate_update(self):
        if not any([
            self.name is not None,
            self.enabled is not None,
            self.project_id is not None,
            self.command is not None,
            self.args is not None,
            self.env is not None,
            self.cwd is not None,
            self.env_file is not None,
            self.url is not None,
            self.headers is not None,
        ]):
            raise ValueError("At least one field must be set to update")
        return self

    @model_validator(mode="after")
    def validate_name_delimiter(self):
        # See MCPConfigBase.validate_name_delimiter -- same rationale.
        if self.name is not None and not is_valid_mcp_config_name(self.name):
            raise ValueError(
                f"MCP config name must not contain '{MCP_TOOL_NAME_DELIMITER}' "
                f"(reserved as the tool-name delimiter)"
            )
        return self


class MCPConfigOut(BaseModel):
    id: str
    name: str
    transport: str
    enabled: bool
    project_id: Optional[str] = None
    project_paused: bool = False
    command: Optional[str]
    args: Optional[List[str]]
    env: Optional[Dict[str, str]]
    cwd: Optional[str]
    env_file: Optional[str]
    url: Optional[str]
    headers: Optional[Dict[str, str]]
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── Q-03: response models for the remaining endpoints below ──────────────────

class MCPHealthItem(BaseModel):
    config_id: str
    name: str
    status: str  # "healthy" | "unhealthy" | "disabled" | "project_paused"
    latency_ms: Optional[int] = None
    tool_count: Optional[int] = None
    error: Optional[str] = None


class UpdateMCPConfigResponse(BaseModel):
    id: str
    name: str
    transport: str
    enabled: bool
    command: Optional[str]
    args: Optional[List[str]]
    env: Optional[Dict[str, str]]
    cwd: Optional[str]
    env_file: Optional[str]
    url: Optional[str]
    headers: Optional[Dict[str, str]]
    tool_count: Optional[int] = None
    sync_error: Optional[str] = None


class MCPToolItem(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    last_synced_at: Optional[datetime] = None


class MCPResourceItem(BaseModel):
    uri: str
    name: Optional[str] = None
    description: Optional[str] = None
    mime_type: Optional[str] = None
    last_synced_at: Optional[datetime] = None


class MCPPromptItem(BaseModel):
    name: str
    description: Optional[str] = None
    arguments: Optional[Any] = None
    last_synced_at: Optional[datetime] = None


class SyncMCPToolsResponse(BaseModel):
    tools: List[MCPToolItem]
    resources: List[MCPResourceItem]
    prompts: List[MCPPromptItem]
    # B-100: a connection failure used to be an unhandled exception -> a raw 500 with no body
    # the frontend could render. update_mcp_config()'s auto-sync-on-enable already reports the
    # identical failure this way (as UpdateMCPConfigResponse.sync_error); mirrored here so a
    # manual "Sync" click on an unreachable server behaves the same as an enable that fails.
    sync_error: Optional[str] = None


class MCPResourceContentItem(BaseModel):
    uri: str
    mime_type: Optional[str] = None
    text: Optional[str] = None
    blob: Optional[str] = None


class ReadMCPResourceResponse(BaseModel):
    uri: str
    contents: List[MCPResourceContentItem]


class RenderedPromptMessage(BaseModel):
    role: str
    content: Optional[str] = None


class RenderMCPPromptResponse(BaseModel):
    name: str
    description: Optional[str] = None
    messages: List[RenderedPromptMessage]


# =========================
# CRUD Endpoints
# =========================

@router.get("/", response_model=List[MCPConfigOut])
async def list_mcp_configs(
    project_id: Optional[str] = Query(
        None,
        description="Filter by project UUID. Omit to return all configs (global + project-scoped).",
    ),
    auth = Depends(get_current_auth),
):
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        return await MCPRepo.list_configs(db, user_id, project_id=project_id)


@router.post("/", response_model=MCPConfigOut, status_code=status.HTTP_201_CREATED)
async def create_mcp_config(
    payload: MCPConfigCreate,
    auth = Depends(get_current_auth),
):
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        # Validate project scope: project must exist and belong to this user
        if payload.project_id:
            project = await ProjectRepo.get(db, project_id=payload.project_id, user_id=user_id)
            if not project:
                raise HTTPException(
                    status_code=404,
                    detail=f"Project '{payload.project_id}' not found",
                )
            if project.status == "archived":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot add an MCP to an archived project",
                )

        # S-11: extract secret-shaped env/header values to keyring *before*
        # the row is ever inserted, so the real value is never written to
        # the DB at all -- not even transiently. The config_id is generated
        # here (rather than left to the model's default=new_uuid) because
        # the keyring reference name (mcp:{config_id}:{field}:{key}) needs
        # it up front.
        data = payload.model_dump(exclude_none=True)
        config_id = new_uuid()
        data["id"] = config_id
        credential_refs: List[Dict[str, str]] = []
        if "env" in data:
            data["env"], env_refs = extract_and_store_secrets_for_field(
                config_id, "env", data["env"],
            )
            credential_refs.extend(env_refs)
        if "headers" in data:
            data["headers"], header_refs = extract_and_store_secrets_for_field(
                config_id, "headers", data["headers"],
            )
            credential_refs.extend(header_refs)
        if "env" in data or "headers" in data:
            data["credential_refs"] = credential_refs

        cfg = await MCPRepo.create_config(
            db,
            user_id,
            data,
        )

        # P-06a: populate the tool-metadata cache once, here, so
        # register_tools_for_user() can be a pure DB read on every execution
        # start from now on instead of opening a live connection to every
        # enabled server just to return a manifest. Non-fatal -- if the
        # server happens to be unreachable right now, the config still
        # saves; POST /mcp/{id}/sync fills the cache in later.
        if cfg.enabled:
            try:
                await MCPManager().sync_tools_for_config(db, cfg)
            except Exception as sync_exc:
                logger.warning(
                    "MCP config created but initial tool sync failed (non-fatal)",
                    exc_info=sync_exc,
                    extra={"config_id": cfg.id, "user_id": user_id},
                )

        return cfg


class SeedDefaultMCPsPendingInstall(BaseModel):
    name: str
    missing: List[str]


class SeedDefaultMCPsResponse(BaseModel):
    status: str
    mcps_seeded: int
    mcps_skipped: int
    mcps_pending_install: List[SeedDefaultMCPsPendingInstall]
    errors: List[str]


# Registered before the /{config_id} routes below, same reason as
# /agents/seed-defaults: a static path must not be shadowed by a parameterised one.
@router.post("/seed-defaults", response_model=SeedDefaultMCPsResponse)
async def seed_default_mcp_configs(auth = Depends(get_current_auth)):
    """
    (Re)seed the default MCP configs (default_mcps/*.yaml, e.g. Playwright
    browser automation) for the current user. Insert-only and idempotent: a
    config whose name already exists is skipped, never overwritten. Seeded
    configs are created disabled and never connect to their server here --
    enable one with PUT /mcp/{id} {"enabled": true}, which syncs its tools.

    `mcps_pending_install` lists seeded configs whose required executable
    (e.g. npx for Playwright) is not on PATH.
    """
    from core.defaults.seeder import seed_default_mcps  # noqa: PLC0415
    result = await seed_default_mcps(auth["user_id"])
    return {"status": "ok", **result}


@router.get("/health", response_model=List[MCPHealthItem])
async def get_all_mcp_health(auth = Depends(get_current_auth)):
    """
    Test connectivity to ALL MCP configs for this user in parallel.
    Disabled configs are included with status="disabled" (no connection attempted).
    Always returns HTTP 200; connection status is in the response body.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        configs = await MCPRepo.list_configs(db, user_id)

    results = []
    for cfg in configs:
        if cfg.project_paused:
            results.append({
                "config_id": cfg.id,
                "name": cfg.name,
                "status": "project_paused",
                "latency_ms": None,
                "tool_count": None,
                "error": None,
            })
        elif not cfg.enabled:
            results.append({
                "config_id": cfg.id,
                "name": cfg.name,
                "status": "disabled",
                "latency_ms": None,
                "tool_count": None,
                "error": None,
            })
        else:
            results.append(await _check_config_health(cfg))

    return results


@router.get("/{config_id}", response_model=MCPConfigOut)
async def get_mcp_config(
    config_id: str,
    auth = Depends(get_current_auth),
):
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return cfg


@router.put("/{config_id}", response_model=UpdateMCPConfigResponse)
async def update_mcp_config(
    config_id: str,
    payload: MCPConfigUpdate,
    auth = Depends(get_current_auth),
):
    """
    Update an MCP config.  If the config is enabled after the update, tools
    are automatically re-synced from the server so the cache stays fresh.
    Returns the updated config plus tool_count from the sync (or None if the
    config is disabled / the server was unreachable).
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")

        update_data = payload.model_dump(exclude_unset=True)
        # S-11: only touch a field's keyring entries when the client
        # actually included that field in the request (exclude_unset) --
        # three distinct cases per field, not two:
        #   1. field absent from update_data           -> don't touch it or
        #      its existing keyring entries/refs at all.
        #   2. field present but explicitly None (clear) -> wipe all its
        #      existing keyring entries; new_refs for it is empty.
        #   3. field present with a real dict            -> normal
        #      extract-and-redact, diffed against its existing refs.
        # Collapsing 1 and 2 together (as an earlier draft of this did) is
        # the bug: passing None as "not touched" would leave the OTHER
        # field's untouched entries alone but incorrectly wipe this field's
        # keyring entries the moment any update touched the other field.
        if "env" in update_data or "headers" in update_data:
            new_refs: List[Dict[str, str]] = list(cfg.credential_refs or [])

            if "env" in update_data:
                new_refs = [r for r in new_refs if r.get("field") != "env"]
                if update_data["env"] is None:
                    clear_field_secrets(refs_for_field(cfg.credential_refs, "env"))
                else:
                    update_data["env"], env_refs = extract_and_store_secrets_for_field(
                        config_id, "env", update_data["env"],
                        refs_for_field(cfg.credential_refs, "env"),
                    )
                    new_refs.extend(env_refs)

            if "headers" in update_data:
                new_refs = [r for r in new_refs if r.get("field") != "headers"]
                if update_data["headers"] is None:
                    clear_field_secrets(refs_for_field(cfg.credential_refs, "headers"))
                else:
                    update_data["headers"], header_refs = extract_and_store_secrets_for_field(
                        config_id, "headers", update_data["headers"],
                        refs_for_field(cfg.credential_refs, "headers"),
                    )
                    new_refs.extend(header_refs)

            update_data["credential_refs"] = new_refs

        updated = await MCPRepo.update_config(
            db,
            cfg,
            update_data,
        )

        # P-06b: mark-and-sweep eviction. Any update could have changed a
        # connection-relevant field (url/headers/command/args/env/...) --
        # bump unconditionally rather than diff old vs new. This doesn't
        # force-close an in-flight pooled connection (see session_pool.py's
        # bump_generation() docstring); the next acquirer just recreates.
        mcp_session_pool.bump_generation(updated.id)

        # Auto-sync tools if config is now enabled
        tool_count: Optional[int] = None
        sync_error: Optional[str] = None
        if updated.enabled:
            try:
                mgr = MCPManager(registry=None)
                tools = await mgr.sync_tools_for_config(db, updated)
                tool_count = len(tools)
            except Exception as exc:
                sync_error = str(exc)

        out = {
            "id": updated.id,
            "name": updated.name,
            "transport": updated.transport,
            "enabled": updated.enabled,
            "command": updated.command,
            "args": updated.args,
            "env": updated.env,
            "cwd": updated.cwd,
            "env_file": updated.env_file,
            "url": updated.url,
            "headers": updated.headers,
            "tool_count": tool_count,
            "sync_error": sync_error,
        }
        return out


@router.delete("/{config_id}", response_model=StatusResponse)
async def delete_mcp_config(
    config_id: str,
    auth = Depends(get_current_auth),
):
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")
        await MCPRepo.delete_tools_for_config(db, cfg.id)
        # DB-07: resource/prompt metadata caches were never cleaned up here --
        # delete_tools_for_config's siblings for the other two MCP metadata
        # tables existed but were never called from this endpoint.
        await MCPRepo.delete_resources_for_config(db, cfg.id)
        await MCPRepo.delete_prompts_for_config(db, cfg.id)
        # S-11: keyring has no cascade-delete of its own -- without this,
        # deleting a config would leave its real secret values sitting in
        # the OS credential store forever with nothing left in EVA that
        # references them.
        delete_credentials(cfg.credential_refs)
        await MCPRepo.delete_config(db, cfg)

    # P-06b: drop the pooled entry entirely (bump first so any in-flight
    # acquirer that hasn't yet checked the generation still sees a
    # mismatch, then discard so nothing sits in the pool for the config
    # that no longer exists).
    mcp_session_pool.bump_generation(config_id)
    await mcp_session_pool.discard(config_id)
    return {"status": "deleted"}


@router.post("/{config_id}/sync", status_code=status.HTTP_200_OK, response_model=SyncMCPToolsResponse)
async def sync_mcp_tools(
    config_id: str,
    auth = Depends(get_current_auth),
):
    """
    Manually trigger a full sync of tools, resources, and prompts from the
    MCP server.  Returns lists of all three capability types discovered.
    """
    user_id = auth["user_id"]
    mgr = MCPManager(registry=None)
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")
        if not cfg.enabled:
            raise HTTPException(status_code=400, detail="MCP config must be enabled to sync")

        # Sync tools (required capability). B-100: this used to be unwrapped, so a connection
        # failure (server down, network error) propagated as an unhandled exception -> FastAPI
        # 500 with no body the frontend could render. update_mcp_config()'s auto-sync-on-enable
        # already catches the identical failure this way; mirrored here for the manual "Sync"
        # button. Resources/prompts are skipped on a tools-sync failure -- same connection, so
        # they would almost certainly fail too, and both already return [] rather than raise
        # (see their own "never raise" docstrings), which would otherwise mask sync_error by
        # making the response look partially successful.
        sync_error: Optional[str] = None
        try:
            tools = await mgr.sync_tools_for_config(db, cfg)
            resources = await mgr.sync_resources_for_config(db, cfg)
            prompts = await mgr.sync_prompts_for_config(db, cfg)
        except Exception as exc:
            sync_error = str(exc)
            tools, resources, prompts = [], [], []

        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "output_schema": t.output_schema,
                    "last_synced_at": t.last_synced_at,
                }
                for t in tools
            ],
            "resources": [
                {
                    "uri": r.uri,
                    "name": r.name,
                    "description": r.description,
                    "mime_type": r.mime_type,
                    "last_synced_at": r.last_synced_at,
                }
                for r in resources
            ],
            "prompts": [
                {
                    "name": p.name,
                    "description": p.description,
                    "arguments": p.arguments,
                    "last_synced_at": p.last_synced_at,
                }
                for p in prompts
            ],
            "sync_error": sync_error,
        }


# =========================
# Health Check Endpoints
# =========================

async def _check_config_health(cfg: MCPConfig) -> dict:
    """
    Open a connection to a single MCP config, call list_tools(), and
    return a health dict.  Never raises — errors are returned as status=unhealthy.
    """
    t0 = time.monotonic()
    try:
        # S-11: env/headers on the row are redacted placeholders -- resolve
        # the real values from keyring only here, at actual connection time.
        real_env, real_headers = resolve_secrets(cfg)
        conn = MCPConnection(
            name=cfg.name,
            transport=cfg.transport,
            command=cfg.command,
            args=cfg.args,
            env=real_env,
            cwd=cfg.cwd,
            env_file=cfg.env_file,
            url=cfg.url,
            headers=real_headers,
        )
        async with conn as session:
            resp = await session.list_tools()
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "config_id": cfg.id,
            "name": cfg.name,
            "status": "healthy",
            "latency_ms": latency_ms,
            "tool_count": len(resp.tools),
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "config_id": cfg.id,
            "name": cfg.name,
            "status": "unhealthy",
            "latency_ms": latency_ms,
            "tool_count": None,
            "error": str(exc),
        }


@router.get("/{config_id}/health", response_model=MCPHealthItem)
async def get_mcp_config_health(
    config_id: str,
    auth = Depends(get_current_auth),
):
    """
    Test connectivity to a single MCP server.

    Opens a real connection, runs list_tools(), and returns:
    - status: "healthy" | "unhealthy"
    - latency_ms: round-trip time in milliseconds
    - tool_count: number of tools discovered (null on failure)
    - error: error message (null on success)
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")

    return await _check_config_health(cfg)


# =========================
# Tool Endpoints
# =========================

@router.get("/{config_id}/tools", response_model=List[MCPToolItem])
async def list_mcp_tools(
    config_id: str,
    auth = Depends(get_current_auth),
):
    """
    Return cached tool metadata for an MCP config from the DB.
    No live connection — reads the last-synced state.
    Call POST /{config_id}/sync first to refresh if needed.
    Returns an empty list if no sync has been run yet.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")

        tools = await MCPRepo.list_tools_for_config(db, config_id)
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "output_schema": t.output_schema,
                "last_synced_at": t.last_synced_at,
            }
            for t in tools
        ]


# =========================
# Resource Endpoints
# =========================

@router.get("/{config_id}/resources", response_model=List[MCPResourceItem])
async def list_mcp_resources(
    config_id: str,
    auth = Depends(get_current_auth),
):
    """
    Return cached resource metadata for an MCP config.
    Runs a live sync first so the list is always fresh.
    Returns an empty list if the server does not support the resources capability.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")
        if not cfg.enabled:
            raise HTTPException(status_code=400, detail="MCP config is disabled")

        mgr = MCPManager(registry=None)
        resources = await mgr.sync_resources_for_config(db, cfg)

        return [
            {
                "uri": r.uri,
                "name": r.name,
                "description": r.description,
                "mime_type": r.mime_type,
                "last_synced_at": r.last_synced_at,
            }
            for r in resources
        ]


@router.get("/{config_id}/resources/read", response_model=ReadMCPResourceResponse)
async def read_mcp_resource(
    config_id: str,
    uri: str,
    auth = Depends(get_current_auth),
):
    """
    Fetch the content of a specific resource by URI.

    Query parameter:
        uri — the resource URI as returned by GET /mcp/{id}/resources

    Returns the raw content and MIME type reported by the server.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")
        if not cfg.enabled:
            raise HTTPException(status_code=400, detail="MCP config is disabled")

    try:
        # S-11: resolve real secret values from keyring only at connection time.
        real_env, real_headers = resolve_secrets(cfg)
        conn = MCPConnection(
            name=cfg.name,
            transport=cfg.transport,
            command=cfg.command,
            args=cfg.args,
            env=real_env,
            cwd=cfg.cwd,
            env_file=cfg.env_file,
            url=cfg.url,
            headers=real_headers,
        )
        async with conn as session:
            result = await session.read_resource(uri)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to read resource '{uri}': {exc}",
        )

    # Normalise result — MCP read_resource returns a ReadResourceResult
    # with a list of content items (text or blob).
    content_items = []
    for item in getattr(result, "contents", []):
        content_items.append({
            "uri": str(getattr(item, "uri", uri)),
            "mime_type": getattr(item, "mime_type", None),
            "text": getattr(item, "text", None),
            "blob": getattr(item, "blob", None),
        })

    return {"uri": uri, "contents": content_items}


# =========================
# Prompt Endpoints
# =========================

@router.get("/{config_id}/prompts", response_model=List[MCPPromptItem])
async def list_mcp_prompts(
    config_id: str,
    auth = Depends(get_current_auth),
):
    """
    Return cached prompt template metadata for an MCP config.
    Runs a live sync first so the list is always fresh.
    Returns an empty list if the server does not support the prompts capability.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")
        if not cfg.enabled:
            raise HTTPException(status_code=400, detail="MCP config is disabled")

        mgr = MCPManager(registry=None)
        prompts = await mgr.sync_prompts_for_config(db, cfg)

        return [
            {
                "name": p.name,
                "description": p.description,
                "arguments": p.arguments,
                "last_synced_at": p.last_synced_at,
            }
            for p in prompts
        ]


@router.post("/{config_id}/prompts/{prompt_name}", response_model=RenderMCPPromptResponse)
async def render_mcp_prompt(
    config_id: str,
    prompt_name: str,
    arguments: Optional[Dict[str, str]] = None,
    auth = Depends(get_current_auth),
):
    """
    Render a named prompt template with optional string arguments.

    Request body (optional JSON object):
        { "key": "value", ... }  — argument values for the template

    Returns the list of rendered messages from the MCP server.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config(db, user_id, config_id)
        if not cfg:
            raise HTTPException(status_code=404, detail="MCP config not found")
        if not cfg.enabled:
            raise HTTPException(status_code=400, detail="MCP config is disabled")

    try:
        # S-11: resolve real secret values from keyring only at connection time.
        real_env, real_headers = resolve_secrets(cfg)
        conn = MCPConnection(
            name=cfg.name,
            transport=cfg.transport,
            command=cfg.command,
            args=cfg.args,
            env=real_env,
            cwd=cfg.cwd,
            env_file=cfg.env_file,
            url=cfg.url,
            headers=real_headers,
        )
        async with conn as session:
            result = await session.get_prompt(prompt_name, arguments=arguments or {})
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to render prompt '{prompt_name}': {exc}",
        )

    # Normalise result — MCP get_prompt returns a GetPromptResult with messages
    messages = []
    for msg in getattr(result, "messages", []):
        content = getattr(msg, "content", None)
        messages.append({
            "role": getattr(msg, "role", "user"),
            "content": getattr(content, "text", str(content)) if content else None,
        })

    return {
        "name": prompt_name,
        "description": getattr(result, "description", None),
        "messages": messages,
    }
