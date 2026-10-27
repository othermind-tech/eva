# api/agents.py
#
# G-003: Subagent Configuration API
#
# Provides CRUD endpoints for managing per-user subagent configurations.
# Subagents are named specialised AgentEngine variants that the orchestrator
# can delegate tasks to via the `delegate_to_agent` tool.
#
# Endpoints:
#   GET    /agents                — list all subagents for the current user
#   POST   /agents                — create or update a subagent config (upsert)
#   GET    /agents/{name}         — get a single subagent config by name
#   PUT    /agents/{name}         — update a subagent config by name
#   DELETE /agents/{name}         — delete a subagent config by name
#   POST   /agents/seed-defaults  — (re)seed Tier 1 default library (B-074)
#
# Auth is required on every request.

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.auth.dependencies import get_current_auth
from api._provider_validation import validate_provider_override
from core.agents.registry import subagent_registry
from core.db.session import AsyncSessionLocal
from core.db.repository import ProjectRepo
from api.schemas.responses import DeleteByNameResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Agents"])


class SeedDefaultsResponse(BaseModel):
    status: str
    agents_seeded: int
    agents_skipped: int
    skills_seeded: int
    skills_skipped: int
    errors: List[str]


async def _check_project_linkable(project_id: str, user_id: str) -> None:
    """Raise 403/404 if the project cannot accept new linked items."""
    async with AsyncSessionLocal() as db:
        proj = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if proj.status == "archived":
        raise HTTPException(status_code=403, detail="Cannot link an agent to an archived project.")
    if proj.status == "paused":
        raise HTTPException(
            status_code=403,
            detail="Project is paused. Resume it (set status=active) before linking new agents.",
        )


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SubagentConfigRequest(BaseModel):
    name: str = Field(description="Unique slug for this subagent (e.g. 'researcher')")
    description: Optional[str] = Field(
        default=None,
        description="What this subagent does — shown to the orchestrator.",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="Specialised system prompt for the child agent.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Model override (None = inherit from user session).",
    )
    provider: Optional[str] = Field(
        default=None,
        description="Provider override (None = inherit from user session).",
    )
    tools: Optional[List[str]] = Field(
        default=None,
        description=(
            "Whitelist of tool names the subagent may use.  "
            "null / [] means the subagent inherits all tools the user has enabled."
        ),
    )
    max_steps: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum ReAct steps the child agent may take (default 5).",
    )
    allowed_mcp_servers: Optional[List[str]] = Field(
        default=None,
        description=(
            "B-053: Whitelist of MCP server names this subagent may use.  "
            "null / [] means the subagent inherits all MCP servers the user has enabled."
        ),
    )
    project_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional project this agent belongs to.  "
            "null = user-global agent available across all projects."
        ),
    )
    is_default: bool = Field(
        default=False,
        description=(
            "Read-only flag set by the seeder for built-in default agents.  "
            "Passing true on a create/update request while also setting project_id "
            "is rejected (R4 guard)."
        ),
    )


class SubagentConfigResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str]
    system_prompt: Optional[str]
    model: Optional[str]
    provider: Optional[str]
    tools: Optional[List[str]]
    max_steps: int
    allowed_mcp_servers: Optional[List[str]] = None
    project_id: Optional[str] = None
    is_default: bool = False


def _to_response(row) -> SubagentConfigResponse:
    return SubagentConfigResponse(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        description=row.description,
        system_prompt=row.system_prompt,
        model=row.model,
        provider=row.provider,
        tools=row.tools,
        max_steps=row.max_steps,
        allowed_mcp_servers=getattr(row, "allowed_mcp_servers", None),
        project_id=getattr(row, "project_id", None),
        is_default=bool(getattr(row, "is_default", False)),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=List[SubagentConfigResponse],
    summary="List all subagent configs for the current user",
)
async def list_agents(
    project_id: Optional[str] = None,
    auth=Depends(get_current_auth),
):
    """Return subagent configurations for the authenticated user.

    Pass ?project_id=<id> to filter to agents belonging to a specific project.
    Omit the parameter to return all agents regardless of project.
    """
    user_id = auth["user_id"]
    rows = await subagent_registry.list(user_id, project_id=project_id)
    return [_to_response(r) for r in rows]


@router.post(
    "",
    response_model=SubagentConfigResponse,
    summary="Create or update a subagent config (upsert by name)",
)
async def upsert_agent(
    body: SubagentConfigRequest,
    auth=Depends(get_current_auth),
):
    """
    Create a new subagent or replace an existing one with the same name.

    The `name` field is the unique key within the user's namespace.
    Posting with an existing name performs an in-place update.
    """
    user_id = auth["user_id"]
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty.")

    # R4: default agents are global system templates — they cannot be project-scoped.
    # The user must clone first (POST /agents/{name}/clone) to get an editable copy.
    if body.project_id and getattr(body, "is_default", False):
        raise HTTPException(
            status_code=400,
            detail="Default agents cannot be assigned to a project. Clone the agent first.",
        )

    if body.project_id:
        await _check_project_linkable(body.project_id, user_id)

    # V6.59 Phase 4b: a provider override must be usable (known, chat-ready,
    # configured) and come with a model; an unchanged provider on an existing
    # agent is accepted as-is. Stores the canonical provider name.
    _existing = await subagent_registry.get(user_id, name)
    _provider = validate_provider_override(
        body.provider, body.model,
        existing_provider=getattr(_existing, "provider", None),
    )

    _data = body.dict()
    _data["provider"] = _provider
    _data.pop("is_default", None)  # is_default is managed by the seeder only; never written via API
    row = await subagent_registry.upsert(user_id, _data)
    logger.info("Subagent upserted via API", extra={"user_id": user_id, "agent_name": name})
    return _to_response(row)


# ---------------------------------------------------------------------------
# V5.0 Sprint 5: Default library management (B-074)
# IMPORTANT: this route MUST be registered before the /{name} parameterised
# routes below.  FastAPI matches routes in declaration order; if /{name} is
# registered first, POST /agents/seed-defaults would match /{name} and return
# 405 Method Not Allowed instead of reaching this handler.
# ---------------------------------------------------------------------------

@router.post(
    "/seed-defaults",
    response_model=SeedDefaultsResponse,
    summary="(Re)seed the Tier 1 default agent library for the current user",
)
async def seed_defaults(force: bool = False, auth=Depends(get_current_auth)):
    """
    Copy all Tier 1 default agents and skills into the current user's workspace.

    Safe to call multiple times.

    - **Default behaviour** (`force=false`): agents that already exist are skipped
      so existing customisations are preserved.  Skills are only copied if the
      target directory does not yet exist.

    - **Force mode** (`?force=true`): all default agent configs are overwritten
      from the YAML definitions.  Use this after a EVA version upgrade that
      changes default agent tool lists or system prompts.  Skills are never
      force-overwritten — user skill edits are always preserved.

    Returns a summary of what was seeded vs skipped.
    """
    from core.defaults.seeder import seed_user_defaults  # noqa: PLC0415
    user_id = auth["user_id"]
    result = await seed_user_defaults(user_id, force=force)
    logger.info(
        "Manual default seed triggered",
        extra={
            "user_id": user_id,
            "agents_seeded": result["agents_seeded"],
            "skills_seeded": result["skills_seeded"],
        },
    )
    return {
        "status": "ok",
        "agents_seeded":  result["agents_seeded"],
        "agents_skipped": result["agents_skipped"],
        "skills_seeded":  result["skills_seeded"],
        "skills_skipped": result["skills_skipped"],
        "errors":         result["errors"],
    }


# ---------------------------------------------------------------------------
# Parameterised /{name} routes — must come AFTER all static sub-paths above
# ---------------------------------------------------------------------------

@router.get(
    "/{name}",
    response_model=SubagentConfigResponse,
    summary="Get a single subagent config by name",
)
async def get_agent(name: str, auth=Depends(get_current_auth)):
    """Return the subagent config with the given name, or 404 if not found."""
    user_id = auth["user_id"]
    row = await subagent_registry.get(user_id, name)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No subagent named '{name}' found for this user.",
        )
    return _to_response(row)


@router.put(
    "/{name}",
    response_model=SubagentConfigResponse,
    summary="Update an existing subagent config by name",
)
async def update_agent(
    name: str,
    body: SubagentConfigRequest,
    auth=Depends(get_current_auth),
):
    """
    Update an existing subagent config.  Returns 404 if the name does not exist.
    For create-or-update semantics use POST /agents instead.

    **Default agent restriction:** the `tools` field is read-only for default agents
    (is_default=true).  Attempting to change it returns HTTP 400.  All other fields
    (description, system_prompt, model, provider, max_steps) remain editable.
    To fully customise a default agent's tools, use POST /agents/{name}/clone first.
    """
    user_id = auth["user_id"]
    existing = await subagent_registry.get(user_id, name)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"No subagent named '{name}' found. Use POST /agents to create it.",
        )

    # Protect tool list on default agents
    if getattr(existing, "is_default", False) and body.tools is not None:
        existing_tools = existing.tools or []
        requested_tools = body.tools or []
        if set(existing_tools) != set(requested_tools):
            raise HTTPException(
                status_code=400,
                detail=(
                    "The tool list of a default agent is managed by EVA and cannot be "
                    "changed directly. Use POST /agents/{name}/clone to create an editable "
                    "copy, or POST /agents/{name}/reset to restore the original tool list."
                ),
            )

    # R4: default agents cannot be project-scoped — clone first.
    if body.project_id and getattr(existing, "is_default", False):
        raise HTTPException(
            status_code=400,
            detail="Default agents cannot be assigned to a project. Clone the agent first.",
        )

    if body.project_id:
        await _check_project_linkable(body.project_id, user_id)
    _provider = validate_provider_override(
        body.provider, body.model,
        existing_provider=getattr(existing, "provider", None),
    )
    data = body.dict()
    data["provider"] = _provider
    data["name"] = name  # path param wins over body name
    data.pop("is_default", None)  # Never let API calls strip/set is_default — managed by seeder only
    row = await subagent_registry.upsert(user_id, data)
    return _to_response(row)


@router.delete(
    "/{name}",
    response_model=DeleteByNameResponse,
    summary="Delete a subagent config by name",
)
async def delete_agent(name: str, auth=Depends(get_current_auth)):
    """
    Permanently remove the subagent config with the given name.
    Returns 404 if it does not exist.
    """
    user_id = auth["user_id"]
    deleted = await subagent_registry.delete(user_id, name)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"No subagent named '{name}' found for this user.",
        )
    logger.info(
        "Subagent deleted via API",
        extra={"user_id": user_id, "agent_name": name},
    )
    return {"status": "deleted", "name": name}


# ---------------------------------------------------------------------------
# Default-agent management: reset and clone
# These routes MUST stay below /{name} DELETE to avoid route shadowing.
# FastAPI registers routes in declaration order; placing these after DELETE
# is safe because they use sub-paths (/{name}/reset, /{name}/clone).
# ---------------------------------------------------------------------------

class CloneRequest(BaseModel):
    new_name: str = Field(description="Slug for the cloned agent (must not already exist).")


@router.post(
    "/{name}/reset",
    response_model=SubagentConfigResponse,
    summary="Reset a default agent to its shipped YAML definition",
)
async def reset_agent(name: str, auth=Depends(get_current_auth)):
    """
    Restore a default agent's full configuration (including tool list) from the
    original YAML file in `default_agents/`.

    - Returns **400** if the agent is not a default agent (`is_default=false`).
    - Returns **404** if no YAML definition exists for this name.
    - Preserves `project_id` — the agent remains linked to any project it was in.
    """
    from core.defaults.seeder import DEFAULT_AGENTS_DIR, _parse_agent_yaml  # noqa: PLC0415

    user_id = auth["user_id"]
    existing = await subagent_registry.get(user_id, name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No subagent named '{name}' found.")
    if not getattr(existing, "is_default", False):
        raise HTTPException(
            status_code=400,
            detail=f"'{name}' is a custom agent and cannot be reset. Only default agents support reset.",
        )

    # Find the matching YAML file (filename contains the agent name)
    yaml_file = None
    for f in sorted(DEFAULT_AGENTS_DIR.glob("*.yaml")):
        data = _parse_agent_yaml(f)
        if data and data.get("name") == name:
            yaml_file = (f, data)
            break

    if yaml_file is None:
        raise HTTPException(
            status_code=404,
            detail=f"No default YAML definition found for '{name}'. Cannot reset.",
        )

    _, yaml_data = yaml_file
    row = await subagent_registry.upsert(user_id, {
        "name":          name,
        "description":   yaml_data.get("description", ""),
        "system_prompt": yaml_data.get("system_prompt", ""),
        "model":         yaml_data.get("model") or None,
        "provider":      yaml_data.get("provider") or None,
        "tools":         yaml_data.get("tools") or None,
        "max_steps":     int(yaml_data.get("max_steps", 10)),
        "is_default":    True,
        # Preserve project linkage
        "project_id":    getattr(existing, "project_id", None),
    })
    logger.info("Default agent reset from YAML", extra={"user_id": user_id, "agent_name": name})
    return _to_response(row)


@router.post(
    "/{name}/clone",
    response_model=SubagentConfigResponse,
    summary="Clone an agent (default or custom) as a new user-owned agent",
)
async def clone_agent(name: str, body: CloneRequest, auth=Depends(get_current_auth)):
    """
    Create a full copy of an agent under a new name.  The clone has
    `is_default=false` and is fully editable, including its tool list.

    Use this to customise a default agent's tool scope without losing
    the ability to reset the original.

    - Returns **404** if the source agent does not exist.
    - Returns **409** if `new_name` is already taken.
    """
    user_id = auth["user_id"]
    source = await subagent_registry.get(user_id, name)
    if source is None:
        raise HTTPException(status_code=404, detail=f"No subagent named '{name}' found.")

    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="new_name must not be empty.")

    existing_target = await subagent_registry.get(user_id, new_name)
    if existing_target is not None:
        raise HTTPException(
            status_code=409,
            detail=f"An agent named '{new_name}' already exists. Choose a different name.",
        )

    row = await subagent_registry.upsert(user_id, {
        "name":                new_name,
        "description":         source.description,
        "system_prompt":       source.system_prompt,
        "model":               source.model,
        "provider":            source.provider,
        "tools":               source.tools,
        "max_steps":           source.max_steps,
        "allowed_mcp_servers": source.allowed_mcp_servers,
        "project_id":          source.project_id,
        "is_default":          False,  # clone is always user-owned
    })
    logger.info(
        "Agent cloned",
        extra={"user_id": user_id, "source": name, "clone": new_name},
    )
    return _to_response(row)
