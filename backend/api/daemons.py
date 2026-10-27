# api/daemons.py
#
# G-008: Daemon Agent REST API
#
# CRUD endpoints for per-user daemon agent configurations.
# Mirrors the pattern established by api/agents.py (SubagentConfig).
#
# Routes:
#   GET    /daemons                   — list all daemons for the user
#   POST   /daemons                   — create or update (upsert) a daemon
#   GET    /daemons/{name}            — get a single daemon by name
#   PUT    /daemons/{name}            — update an existing daemon (404 if absent)
#   DELETE /daemons/{name}            — delete a daemon
#   POST   /daemons/{name}/trigger    — manually trigger a daemon run immediately

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import get_current_auth
from api._provider_validation import validate_provider_override
from core.daemons.registry import daemon_registry
from core.db.session import AsyncSessionLocal
from core.db.repository import ProjectRepo, ExecutionMetricsRepo
from core.memory.conversation_journal import ConversationJournal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["daemons"])


class DeleteDaemonResponse(BaseModel):
    deleted: bool
    name: str


class PauseDaemonResponse(BaseModel):
    paused: bool
    daemon_name: str
    note: Optional[str] = None


class ResumeDaemonResponse(BaseModel):
    resumed: bool
    daemon_name: str


class ResetDaemonFailuresResponse(BaseModel):
    reset: bool
    daemon_name: str


class CancelDaemonExecutionResponse(BaseModel):
    cancelled: bool
    daemon_name: str
    execution_id: str
    note: str


class DeleteDaemonWebhookResponse(BaseModel):
    deleted: bool
    daemon_name: str


async def _check_project_linkable(project_id: str, user_id: str) -> None:
    """Raise 403/404 if the project cannot accept new linked items."""
    async with AsyncSessionLocal() as db:
        proj = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if proj.status == "archived":
        raise HTTPException(status_code=403, detail="Cannot link a daemon to an archived project.")
    if proj.status == "paused":
        raise HTTPException(
            status_code=403,
            detail="Project is paused. Resume it (set status=active) before linking new daemons.",
        )


async def _check_enable_allowed(project_id: str, user_id: str) -> None:
    """
    Raise 403 if a daemon cannot be enabled because its project is paused or archived.

    Called whenever enabled=True is being set on a daemon that belongs to a project.
    New-daemon creation is already blocked by _check_project_linkable; this guard
    covers updates to existing daemons (PUT /daemons/{name}, POST /daemons/{name}/resume).
    """
    async with AsyncSessionLocal() as db:
        proj = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if proj is None:
        return  # project gone — let the enable proceed rather than silently break
    if proj.status == "archived":
        raise HTTPException(
            status_code=403,
            detail=(
                "Cannot enable a daemon whose project is archived. "
                "Unarchive the project first."
            ),
        )
    if proj.status == "paused":
        raise HTTPException(
            status_code=403,
            detail=(
                "Cannot enable a daemon while its project is paused. "
                "Resume the project (set status=active) first."
            ),
        )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class DaemonConfigRequest(BaseModel):
    name: str = Field(
        description="Unique slug for this daemon within your account (e.g. 'daily-monitor')."
    )
    description: Optional[str] = Field(
        default=None,
        description="Human-readable description of what this daemon does.",
    )
    kind: Literal["standard", "supervisor"] = Field(
        default="standard",
        description=(
            "'standard' (default) — an ordinary daemon. 'supervisor' — a "
            "daemon whose job is managing background processes via the "
            "background_process tool; a semantic label (its default tools "
            "include background_process), not a different execution "
            "mechanic. The 'system' kind (internal/auto-created daemons, "
            "e.g. a project's sync daemon) is server-reserved and cannot "
            "be set through this API."
        ),
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="System prompt for the daemon's AgentEngine.",
    )
    trigger_message: Optional[str] = Field(
        default=None,
        description=(
            "The 'user' message sent to the daemon each time it wakes. "
            "Defaults to a generic activation message if None."
        ),
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
        description="Allowed tool names. None / [] = all user tools.",
    )
    max_steps: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum ReAct iterations per run.",
    )
    run_interval_seconds: Optional[int] = Field(
        default=None,
        ge=60,
        description=(
            "Seconds between automatic runs (minimum 60). "
            "None = manual trigger only (no automatic scheduling)."
        ),
    )
    enabled: bool = Field(
        default=True,
        description="Whether the scheduler should automatically fire this daemon.",
    )
    # ── Retry policy ──────────────────────────────────────────────────────────
    retry_on_failure: bool = Field(
        default=True,
        description="Automatically retry the daemon after a failed run.",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum consecutive auto-retry attempts before permanent failure.",
    )
    retry_delay_seconds: int = Field(
        default=60,
        ge=10,
        description="Base delay in seconds before the first retry (doubles each attempt).",
    )
    project_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional project this daemon belongs to.  "
            "null = user-global daemon not scoped to any project."
        ),
    )
    event_subscriptions: Optional[List[str]] = Field(
        default=None,
        description=(
            "Event types this daemon reacts to (e.g. 'execution.completed'). "
            "null / [] = not event-driven (scheduled/manual trigger only)."
        ),
    )


class DaemonConfigResponse(BaseModel):
    id:                   str
    name:                 str
    kind:                 str = "standard"
    description:          Optional[str]
    system_prompt:        Optional[str]
    trigger_message:      Optional[str]
    model:                Optional[str]
    provider:             Optional[str]
    tools:                Optional[List[str]]
    max_steps:            int
    run_interval_seconds: Optional[int]
    enabled:              bool
    last_run_at:          Optional[datetime]
    next_run_at:          Optional[datetime]
    created_at:           datetime
    updated_at:           Optional[datetime]
    # Lifecycle & retry policy
    lifecycle_status:     str
    current_execution_id: Optional[str]
    retry_on_failure:     bool
    max_retries:          int
    retry_delay_seconds:  int
    failure_count:        int
    last_error:           Optional[str]
    last_failure_at:      Optional[datetime]
    project_id:           Optional[str] = None
    event_subscriptions:  Optional[List[str]] = None


class DaemonStatusResponse(BaseModel):
    """Lightweight status snapshot for GET /daemons/{name}/status."""
    name:                 str
    lifecycle_status:     str
    current_execution_id: Optional[str]
    failure_count:        int
    max_retries:          int
    last_error:           Optional[str]
    last_failure_at:      Optional[datetime]
    last_run_at:          Optional[datetime]
    next_run_at:          Optional[datetime]
    enabled:              bool


class DaemonTriggerResponse(BaseModel):
    triggered:    bool
    daemon_name:  str
    execution_id: Optional[str] = None
    note:         Optional[str] = None


class DaemonCloneRequest(BaseModel):
    new_name: str = Field(description="Slug for the cloned daemon (must not already exist).")
    project_id: Optional[str] = Field(
        default=None,
        description=(
            "Project to assign the clone to.  "
            "If omitted the clone inherits the source daemon's project_id."
        ),
    )


def _strip_kind_if_system(data: dict, existing: Any) -> None:
    """
    DaemonConfigRequest.kind has a Pydantic default ("standard"), so
    body.model_dump() always includes a "kind" key -- even when the client
    never mentioned kind at all. Since the request schema's Literal type
    can't even represent "system" (server-reserved), an update to an
    existing "system"-kind daemon through these endpoints would otherwise
    silently downgrade it back to "standard" on every save, no matter what
    field was actually being changed. Mutates `data` in place, dropping
    "kind" entirely when the target row is already "system" -- the same
    "name is immutable after creation" treatment already applies to.
    """
    if getattr(existing, "kind", None) == "system":
        data.pop("kind", None)


def _to_response(row: Any) -> DaemonConfigResponse:
    return DaemonConfigResponse(
        id=row.id,
        name=row.name,
        kind=getattr(row, "kind", "standard"),
        description=row.description,
        system_prompt=row.system_prompt,
        trigger_message=row.trigger_message,
        model=row.model,
        provider=row.provider,
        tools=row.tools,
        max_steps=row.max_steps,
        run_interval_seconds=row.run_interval_seconds,
        enabled=row.enabled,
        last_run_at=row.last_run_at,
        next_run_at=row.next_run_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        # Lifecycle & retry policy — use getattr with defaults for backward compat
        lifecycle_status     = getattr(row, "lifecycle_status",     "idle"),
        current_execution_id = getattr(row, "current_execution_id", None),
        retry_on_failure     = getattr(row, "retry_on_failure",     True),
        max_retries          = getattr(row, "max_retries",          3),
        retry_delay_seconds  = getattr(row, "retry_delay_seconds",  60),
        failure_count        = getattr(row, "failure_count",        0),
        last_error           = getattr(row, "last_error",           None),
        last_failure_at      = getattr(row, "last_failure_at",      None),
        project_id           = getattr(row, "project_id",           None),
        event_subscriptions  = getattr(row, "event_subscriptions",  None),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/daemons", response_model=List[DaemonConfigResponse])
async def list_daemons(
    project_id: Optional[str] = None,
    auth=Depends(get_current_auth),
):
    """List daemon configs for the authenticated user.

    Pass ?project_id=<id> to filter to daemons belonging to a specific project.
    """
    user_id = auth["user_id"]
    rows = await daemon_registry.list(user_id, project_id=project_id)
    return [_to_response(r) for r in rows]


@router.post("/daemons", response_model=DaemonConfigResponse)
async def upsert_daemon(body: DaemonConfigRequest, auth=Depends(get_current_auth)):
    """Create or update a daemon config (upsert by name)."""
    user_id = auth["user_id"]
    _existing = await daemon_registry.get(user_id, body.name)
    if body.project_id:
        # New daemon: block linking to paused/archived project entirely.
        await _check_project_linkable(body.project_id, user_id)
    elif body.enabled:
        # Existing daemon update via POST (no project_id in body):
        # resolve the effective project from the existing DB row.
        _eff_project = getattr(_existing, "project_id", None) if _existing else None
        if _eff_project:
            await _check_enable_allowed(_eff_project, user_id)
    _provider = validate_provider_override(
        body.provider, body.model,
        existing_provider=getattr(_existing, "provider", None),
    )
    data = body.model_dump()
    data["provider"] = _provider
    _strip_kind_if_system(data, _existing)
    try:
        row = await daemon_registry.upsert(user_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # Write / refresh daemon meta.json in the journal folder (idempotent).
    # row.id is the stable UUID used as the journal folder name.
    ConversationJournal.write_daemon_meta(user_id, row.id, {
        "daemon_id":   row.id,
        "daemon_name": row.name,
        "description": row.description,
        "model":       row.model,
        "provider":    row.provider,
    })
    return _to_response(row)


@router.get("/daemons/{name}", response_model=DaemonConfigResponse)
async def get_daemon(name: str, auth=Depends(get_current_auth)):
    """Get a single daemon config by name."""
    user_id = auth["user_id"]
    row = await daemon_registry.get(user_id, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")
    return _to_response(row)


@router.put("/daemons/{name}", response_model=DaemonConfigResponse)
async def update_daemon(
    name: str,
    body: DaemonConfigRequest,
    auth=Depends(get_current_auth),
):
    """Update an existing daemon. Returns 404 if not found."""
    user_id = auth["user_id"]
    existing = await daemon_registry.get(user_id, name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")
    if body.project_id:
        await _check_project_linkable(body.project_id, user_id)
    # Guard: block enabling a daemon whose project is paused or archived.
    # The effective project is the incoming project_id (if being reassigned),
    # or the daemon's current project_id if unchanged.
    if body.enabled:
        _eff_project = body.project_id or getattr(existing, "project_id", None)
        if _eff_project:
            await _check_enable_allowed(_eff_project, user_id)
    _provider = validate_provider_override(
        body.provider, body.model,
        existing_provider=getattr(existing, "provider", None),
    )
    data = body.model_dump()
    data["provider"] = _provider
    data["name"] = name   # ensure name stays consistent with the path param
    _strip_kind_if_system(data, existing)
    row = await daemon_registry.upsert(user_id, data)
    ConversationJournal.write_daemon_meta(user_id, row.id, {
        "daemon_id":   row.id,
        "daemon_name": row.name,
        "description": row.description,
        "model":       row.model,
        "provider":    row.provider,
    })
    return _to_response(row)


@router.delete("/daemons/{name}", response_model=DeleteDaemonResponse)
async def delete_daemon(name: str, auth=Depends(get_current_auth)):
    """Delete a daemon config. Returns 404 if not found."""
    user_id = auth["user_id"]
    deleted = await daemon_registry.delete(user_id, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")
    return {"deleted": True, "name": name}


@router.post("/daemons/{name}/clone", response_model=DaemonConfigResponse)
async def clone_daemon(name: str, body: DaemonCloneRequest, auth=Depends(get_current_auth)):
    """
    Create a full copy of a daemon under a new name.

    The clone is a one-time snapshot — changes to the source daemon after
    cloning are **not** reflected in the clone, and vice versa.

    - ``new_name``: required slug for the clone (must not already exist).
    - ``project_id``: optional project assignment.  Inherits the source
      daemon's ``project_id`` if omitted.

    The clone always starts **disabled** (``enabled=False``) so it does not
    fire immediately.  Enable it explicitly once the config is reviewed.

    Returns **404** if the source daemon does not exist.
    Returns **409** if ``new_name`` is already taken.
    """
    user_id = auth["user_id"]
    source = await daemon_registry.get(user_id, name)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="new_name must not be empty.")

    existing_target = await daemon_registry.get(user_id, new_name)
    if existing_target is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A daemon named '{new_name}' already exists. Choose a different name.",
        )

    # Resolve project: body override wins, else inherit from source
    target_project_id = body.project_id or getattr(source, "project_id", None)
    if target_project_id:
        await _check_project_linkable(target_project_id, user_id)

    # "system" never propagates to a clone: it's server-reserved and means
    # "the one auto-created instance for this exact purpose" (e.g. a
    # project's sync daemon) -- a user-initiated clone is a new, independent
    # daemon a user can freely reconfigure, so it must not inherit the
    # artifact-updater exclusion runner.py grants "system"-kind daemons.
    # "supervisor" (not reserved) clones through unchanged.
    _source_kind = getattr(source, "kind", "standard")
    _clone_kind = "standard" if _source_kind == "system" else _source_kind

    row = await daemon_registry.upsert(user_id, {
        "name":                 new_name,
        "kind":                 _clone_kind,
        "description":          source.description,
        "system_prompt":        source.system_prompt,
        "trigger_message":      source.trigger_message,
        "model":                source.model,
        "provider":             source.provider,
        "tools":                source.tools,
        "max_steps":            source.max_steps,
        "run_interval_seconds": source.run_interval_seconds,
        "enabled":              False,           # clone starts disabled — enable when ready
        "retry_on_failure":     source.retry_on_failure,
        "max_retries":          source.max_retries,
        "retry_delay_seconds":  source.retry_delay_seconds,
        "project_id":           target_project_id,
    })

    # Write journal meta for the new daemon (mirrors the upsert endpoint behaviour)
    ConversationJournal.write_daemon_meta(user_id, row.id, {
        "daemon_id":   row.id,
        "daemon_name": row.name,
        "description": row.description,
        "model":       row.model,
        "provider":    row.provider,
    })

    logger.info(
        "Daemon cloned",
        extra={"user_id": user_id, "source": name, "clone": new_name},
    )
    return _to_response(row)


@router.post("/daemons/{name}/trigger", response_model=DaemonTriggerResponse)
async def trigger_daemon(name: str, auth=Depends(get_current_auth)):
    """
    Manually trigger an immediate daemon run.

    Returns the execution_id immediately so the caller can poll
    ``GET /executions/{execution_id}/status`` for the run outcome.
    The daemon runs as a background asyncio task.

    Also called directly (as a plain function, not over HTTP) by
    POST /projects/{id}/sync (api/projects.py) — that endpoint just resolves
    a project_id to this daemon's deterministic name and delegates here, so
    both entry points share these exact guards and lifecycle bookkeeping
    without a second trigger mechanism to keep in sync.
    """
    user_id = auth["user_id"]
    daemon = await daemon_registry.get(user_id, name)
    if daemon is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    # Guard 1 — enabled flag (primary gate). A disabled daemon cannot run,
    # whether disabled by the user or by project cascade.
    if not daemon.enabled:
        if getattr(daemon, "lifecycle_status", None) == "project_paused":
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Daemon '{name}' cannot be triggered — its project is paused or archived. "
                    "Resume the project (set status=active) before triggering this daemon."
                ),
            )
        raise HTTPException(
            status_code=403,
            detail=f"Daemon '{name}' is disabled. Enable it before triggering.",
        )

    # Guard 2 — project status authoritative check (belt-and-suspenders),
    # catches enabled=True but the daemon's project paused via a DB
    # inconsistency or a cascade that didn't fully propagate.
    if daemon.project_id:
        async with AsyncSessionLocal() as db:
            proj = await ProjectRepo.get(db, project_id=daemon.project_id, user_id=user_id)
        if proj and proj.status in ("paused", "archived"):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Daemon '{name}' cannot be triggered — its project is {proj.status}. "
                    "Resume the project (set status=active) before triggering this daemon."
                ),
            )

    execution_id = str(uuid.uuid4())
    await daemon_registry.set_lifecycle_status(user_id, name, "running", execution_id=execution_id)

    from core.daemons.runner import run_daemon

    async def _run_and_update() -> None:
        result = await run_daemon(daemon=daemon, execution_id=execution_id)
        success = result.get("success", False)
        error_msg = result.get("error", "")
        try:
            if success:
                await daemon_registry.record_success(user_id=daemon.user_id, name=daemon.name)
            else:
                await daemon_registry.record_failure(user_id=daemon.user_id, name=daemon.name, error=error_msg)
        except Exception as exc:
            logger.error("trigger_daemon: lifecycle update failed: %s", exc)

    asyncio.create_task(_run_and_update(), name=f"daemon-trigger-{name}")

    return DaemonTriggerResponse(
        triggered=True,
        daemon_name=name,
        execution_id=execution_id,
        note="Daemon run started. Poll GET /executions/{execution_id}/status for outcome.",
    )


@router.get("/daemons/{name}/status", response_model=DaemonStatusResponse)
async def get_daemon_status(name: str, auth=Depends(get_current_auth)):
    """
    Lightweight lifecycle status for a daemon — no config payload.

    Returns lifecycle_status, current_execution_id, failure_count, last_error,
    and scheduling fields.  Poll this instead of GET /daemons/{name} when you
    only need runtime state.
    """
    user_id = auth["user_id"]
    row = await daemon_registry.get(user_id, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")
    return DaemonStatusResponse(
        name                 = row.name,
        lifecycle_status     = getattr(row, "lifecycle_status",     "idle"),
        current_execution_id = getattr(row, "current_execution_id", None),
        failure_count        = getattr(row, "failure_count",        0),
        max_retries          = getattr(row, "max_retries",          3),
        last_error           = getattr(row, "last_error",           None),
        last_failure_at      = getattr(row, "last_failure_at",      None),
        last_run_at          = row.last_run_at,
        next_run_at          = row.next_run_at,
        enabled              = row.enabled,
    )


@router.post("/daemons/{name}/pause", response_model=PauseDaemonResponse)
async def pause_daemon(name: str, auth=Depends(get_current_auth)):
    """
    Pause a daemon's automatic scheduling.

    Sets lifecycle_status='paused' and enabled=False.  Any currently-running
    execution completes naturally; no new auto-runs will be scheduled until
    POST /daemons/{name}/resume is called.
    """
    user_id = auth["user_id"]
    row = await daemon_registry.get(user_id, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")
    if getattr(row, "lifecycle_status", "idle") == "paused":
        return {"paused": True, "daemon_name": name, "note": "Already paused."}

    # Disable auto-scheduling and mark as paused
    await daemon_registry.upsert(user_id, {
        "name": name,
        "enabled": False,
    })
    await daemon_registry.set_lifecycle_status(user_id, name, "paused")
    logger.info("Daemon paused", extra={"user_id": user_id, "daemon_name": name})
    return {"paused": True, "daemon_name": name}


@router.post("/daemons/{name}/resume", response_model=ResumeDaemonResponse)
async def resume_daemon(name: str, auth=Depends(get_current_auth)):
    """
    Resume a paused daemon's automatic scheduling.

    Sets lifecycle_status='idle' and enabled=True.  Recalculates next_run_at
    from now if run_interval_seconds is set so the daemon fires at the correct
    next interval.
    """
    user_id = auth["user_id"]
    row = await daemon_registry.get(user_id, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    if getattr(row, "lifecycle_status", "idle") == "permanently_failed":
        raise HTTPException(
            status_code=409,
            detail=(
                "Daemon is permanently failed. "
                "Call POST /daemons/{name}/reset-failures before resuming."
            ),
        )

    # Guard: cannot resume a daemon while its project is paused or archived.
    _resume_project = getattr(row, "project_id", None)
    if _resume_project:
        await _check_enable_allowed(_resume_project, user_id)

    await daemon_registry.upsert(user_id, {
        "name": name,
        "enabled": True,
        # Passing run_interval_seconds triggers next_run_at recalculation
        # inside the registry's upsert logic.
        **({"run_interval_seconds": row.run_interval_seconds}
           if row.run_interval_seconds else {}),
    })
    await daemon_registry.set_lifecycle_status(user_id, name, "idle")
    logger.info("Daemon resumed", extra={"user_id": user_id, "daemon_name": name})
    return {"resumed": True, "daemon_name": name}


@router.post("/daemons/{name}/reset-failures", response_model=ResetDaemonFailuresResponse)
async def reset_daemon_failures(name: str, auth=Depends(get_current_auth)):
    """
    Clear the failure counter and re-enable a permanently-failed daemon.

    Resets failure_count, last_error, lifecycle_status back to 'idle'.
    Does NOT automatically re-enable the daemon's scheduling — call
    POST /daemons/{name}/resume after this to restart auto-runs.
    """
    user_id = auth["user_id"]
    row = await daemon_registry.get(user_id, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    await daemon_registry.record_success(user_id, name)   # reuses the reset logic
    # record_success sets lifecycle_status='idle' and clears failure state.
    # Scheduling (enabled + next_run_at) is NOT touched here — the operator
    # decides whether to resume.
    logger.info(
        "Daemon failure count reset",
        extra={"user_id": user_id, "daemon_name": name},
    )
    return {"reset": True, "daemon_name": name}


# ---------------------------------------------------------------------------
# B-062: GET /daemons/{name}/executions — execution history for a daemon
# ---------------------------------------------------------------------------

class DaemonExecutionSummary(BaseModel):
    """Lightweight summary of a single daemon execution run."""
    execution_id:       str
    status:             str
    triggered_by:       Optional[str] = None   # always "daemon" for daemon runs
    project_id:         Optional[str] = None
    termination_reason: Optional[str] = None
    error_message:      Optional[str] = None   # full exception text; None on success
    duration_ms:        Optional[int] = None
    created_at:         Optional[str] = None   # ISO-8601
    completed_at:       Optional[str] = None   # ISO-8601
    model_used:         Optional[str] = None
    provider_used:      Optional[str] = None
    prompt_tokens:      Optional[int] = None
    completion_tokens:  Optional[int] = None
    reasoning_tokens:   Optional[int] = None


@router.get("/daemons/{name}/executions", response_model=List[DaemonExecutionSummary])
async def list_daemon_executions(
    name: str,
    limit: int = 20,
    auth=Depends(get_current_auth),
):
    """
    Return execution history for a specific daemon, newest first.

    **Query parameters:**
    - `limit` — max number of results (default 20, max 50)

    Each row reflects one daemon run (whether triggered automatically by the
    scheduler or manually via ``POST /daemons/{name}/trigger``).  The last 50
    runs per daemon are retained; older runs are purged automatically (B-058).
    """
    user_id = auth["user_id"]
    daemon = await daemon_registry.get(user_id, name)
    if daemon is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    limit = min(max(1, limit), 50)

    async with AsyncSessionLocal() as db:
        rows = await ExecutionMetricsRepo.list_for_daemon(db, user_id, name, limit=limit)

    return [
        DaemonExecutionSummary(
            execution_id=row.execution_id,
            status=row.status or "unknown",
            triggered_by=getattr(row, "triggered_by", None),
            project_id=getattr(row, "project_id", None),
            termination_reason=getattr(row, "termination_reason", None),
            error_message=getattr(row, "error_message", None),
            duration_ms=row.duration_ms,
            created_at=row.created_at.isoformat() if row.created_at else None,
            completed_at=row.completed_at.isoformat() if row.completed_at else None,
            model_used=getattr(row, "model_used", None),
            provider_used=getattr(row, "provider_used", None),
            prompt_tokens=getattr(row, "prompt_tokens", None),
            completion_tokens=getattr(row, "completion_tokens", None),
            reasoning_tokens=getattr(row, "reasoning_tokens", None),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# B-063: POST /daemons/{name}/cancel — cancel a currently-running daemon run
# ---------------------------------------------------------------------------

@router.post("/daemons/{name}/cancel", response_model=CancelDaemonExecutionResponse)
async def cancel_daemon_execution(name: str, auth=Depends(get_current_auth)):
    """
    Request cancellation of the daemon's currently-running execution.

    Returns 409 if the daemon is not currently running (idle, paused, or
    permanently failed).  The cancellation is best-effort: if the execution
    is already in a terminal state by the time the signal is processed, the
    response will still return ``cancelled: true`` but the execution status
    in the broker / DB may show ``completed`` or ``failed``.

    The daemon's lifecycle_status is reset to ``idle`` so it can be triggered
    again or resume its auto-schedule.
    """
    user_id = auth["user_id"]
    row = await daemon_registry.get(user_id, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    lifecycle = getattr(row, "lifecycle_status", "idle")
    execution_id = getattr(row, "current_execution_id", None)

    if lifecycle != "running" or not execution_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Daemon '{name}' is not currently running "
                f"(lifecycle_status='{lifecycle}'). Nothing to cancel."
            ),
        )

    # Signal cancellation via broker execution registry
    cancelled = False
    try:
        from core.execution.redis_registry import redis_execution_registry
        await redis_execution_registry.mark_execution_cancelled(execution_id)
        cancelled = True
    except Exception as exc:
        logger.warning("cancel_daemon_execution: could not cancel via broker: %s", exc)

    # Reset daemon lifecycle to idle so it can run again
    try:
        await daemon_registry.set_lifecycle_status(user_id, name, "idle")
    except Exception as exc:
        logger.warning("cancel_daemon_execution: could not reset lifecycle: %s", exc)

    # Also update execution_metrics row to cancelled (best-effort)
    try:
        from datetime import datetime, timezone as _tz
        from core.db.models import ExecutionMetrics as _EM
        async with AsyncSessionLocal() as db:
            existing = await db.get(_EM, execution_id)
            if existing and existing.status not in ("completed", "failed", "aborted"):
                existing.status = "cancelled"
                existing.termination_reason = "user_requested"
                existing.completed_at = datetime.now(_tz.utc)
                await db.commit()
    except Exception as exc:
        logger.debug("cancel_daemon_execution: metrics update failed: %s", exc)

    logger.info(
        "Daemon execution cancel requested",
        extra={"user_id": user_id, "daemon_name": name, "execution_id": execution_id},
    )

    return {
        "cancelled":    cancelled,
        "daemon_name":  name,
        "execution_id": execution_id,
        "note": (
            "Cancellation signal sent. "
            "Poll GET /executions/{execution_id}/status for confirmation."
        ),
    }


# ---------------------------------------------------------------------------
# Webhook CRUD schemas
# ---------------------------------------------------------------------------

class DaemonWebhookRequest(BaseModel):
    description: Optional[str] = Field(
        default=None,
        description="Optional label for this webhook (e.g. 'GitHub Actions trigger').",
    )


class DaemonWebhookResponse(BaseModel):
    id:                 str
    daemon_name:        str
    webhook_url:        Optional[str]  # Shown ONCE at creation; None on subsequent reads
    token_hint:         Optional[str]  # First 8 chars of token — for identification only
    description:        Optional[str]
    created_at:         datetime
    last_triggered_at:  Optional[datetime]
    trigger_count:      int


# ---------------------------------------------------------------------------
# Webhook CRUD endpoints
# ---------------------------------------------------------------------------

@router.post("/daemons/{name}/webhook", response_model=DaemonWebhookResponse)
async def create_daemon_webhook(
    name: str,
    body: DaemonWebhookRequest,
    request: Request,
    auth=Depends(get_current_auth),
):
    """
    Create (or replace) a public webhook URL for the daemon.

    Each daemon can have at most one active webhook. Calling this endpoint
    on a daemon that already has a webhook replaces the existing token with
    a new one (the old URL becomes invalid immediately).

    The returned ``webhook_url`` is the full path to POST to from external
    systems. Keep it secret — it acts as the authentication credential.
    """
    from sqlalchemy import select, delete as sa_delete
    from core.db.session import AsyncSessionLocal
    from core.db.models import DaemonWebhook

    user_id = auth["user_id"]
    daemon  = await daemon_registry.get(user_id, name)
    if daemon is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    import hashlib
    plain_token  = str(uuid.uuid4())
    token_hash   = hashlib.sha256(plain_token.encode()).hexdigest()
    token_hint   = plain_token[:8]   # first 8 chars — safe to expose, not guessable

    async with AsyncSessionLocal() as db:
        # Delete any existing webhook for this daemon
        await db.execute(
            sa_delete(DaemonWebhook).where(DaemonWebhook.daemon_id == daemon.id)
        )
        webhook = DaemonWebhook(
            daemon_id    = daemon.id,
            user_id      = user_id,
            daemon_name  = name,
            secret_token = token_hash,   # store SHA-256 hash — never the plain token
            description  = body.description,
        )
        db.add(webhook)
        await db.commit()
        await db.refresh(webhook)

    # Build the webhook URL — returned ONCE at creation time only.
    # The plain token is not stored anywhere; if lost the user must regenerate.
    base_url    = str(request.base_url).rstrip("/")
    webhook_url = f"{base_url}/webhooks/{plain_token}"

    logger.info("Daemon webhook created", extra={"user_id": user_id, "daemon_name": name})

    return DaemonWebhookResponse(
        id                = webhook.id,
        daemon_name       = name,
        webhook_url       = webhook_url,   # shown once only
        token_hint        = token_hint,
        description       = webhook.description,
        created_at        = webhook.created_at,
        last_triggered_at = webhook.last_triggered_at,
        trigger_count     = webhook.trigger_count,
    )


@router.get("/daemons/{name}/webhook", response_model=DaemonWebhookResponse)
async def get_daemon_webhook(
    name: str,
    request: Request,
    auth=Depends(get_current_auth),
):
    """
    Retrieve the active webhook for a daemon.

    Returns the webhook URL (which embeds the secret token) and stats.
    Only the owning user can retrieve this — protect the URL like a password.
    """
    from sqlalchemy import select
    from core.db.session import AsyncSessionLocal
    from core.db.models import DaemonWebhook

    user_id = auth["user_id"]
    daemon  = await daemon_registry.get(user_id, name)
    if daemon is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DaemonWebhook).where(DaemonWebhook.daemon_id == daemon.id)
        )
        webhook = result.scalar_one_or_none()

    if webhook is None:
        raise HTTPException(
            status_code=404, detail=f"Daemon '{name}' has no webhook configured."
        )

    # The plain token is never stored — only its SHA-256 hash is in the DB.
    # The webhook URL cannot be reconstructed after creation.
    # token_hint (first 8 chars) is returned so the user can identify which token
    # they created, but it is not enough to authenticate a webhook call.
    return DaemonWebhookResponse(
        id                = webhook.id,
        daemon_name       = name,
        webhook_url       = None,           # not available after initial creation
        token_hint        = webhook.secret_token[:8],  # first 8 chars of the stored hash
        description       = webhook.description,
        created_at        = webhook.created_at,
        last_triggered_at = webhook.last_triggered_at,
        trigger_count     = webhook.trigger_count,
    )


@router.delete("/daemons/{name}/webhook", response_model=DeleteDaemonWebhookResponse)
async def delete_daemon_webhook(name: str, auth=Depends(get_current_auth)):
    """
    Delete the webhook for a daemon.

    The webhook URL becomes invalid immediately. Returns 404 if no webhook exists.
    """
    from sqlalchemy import select, delete as sa_delete
    from core.db.session import AsyncSessionLocal
    from core.db.models import DaemonWebhook

    user_id = auth["user_id"]
    daemon  = await daemon_registry.get(user_id, name)
    if daemon is None:
        raise HTTPException(status_code=404, detail=f"Daemon '{name}' not found.")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DaemonWebhook).where(DaemonWebhook.daemon_id == daemon.id)
        )
        webhook = result.scalar_one_or_none()
        if webhook is None:
            raise HTTPException(
                status_code=404, detail=f"Daemon '{name}' has no webhook configured."
            )
        _token_hash = webhook.secret_token   # already a SHA-256 hash
        await db.execute(
            sa_delete(DaemonWebhook).where(DaemonWebhook.daemon_id == daemon.id)
        )
        await db.commit()

    # Evict the rate-limit log entry so the in-process dict doesn't grow unboundedly
    try:
        from api.webhooks import _webhook_call_log, _rate_limit_lock
        async with _rate_limit_lock:
            _webhook_call_log.pop(_token_hash, None)
    except Exception:
        pass   # non-fatal — entry evicts naturally when the deque ages out

    logger.info("Daemon webhook deleted", extra={"user_id": user_id, "daemon_name": name})
    return {"deleted": True, "daemon_name": name}
