# api/projects.py
"""
Project Management API (B-045).

Endpoints for creating and managing Projects (Scope 4 — project-level workspace).

The DB holds the project registry (identity, workspace path, status).
All rich artifacts (tasks, knowledge, journal, errors) live in .eva files
under blob_storage/projects/{id}/.eva/ — internal to EVA, not exposed directly.

Routes:
  POST   /projects                         — create a new project
  GET    /projects                         — list all projects for current user
  GET    /projects/{project_id}            — get a single project
  POST   /projects/{project_id}/sync       — re-trigger this project's sync daemon (Phase C)
  PATCH  /projects/{project_id}            — update name / description / synopsis / status
  DELETE /projects/{project_id}            — archive (soft-delete) a project

  GET    /projects/{project_id}/context    — get the LLM context block (tier: compact|focused|full)
  GET    /projects/{project_id}/artifacts/{filename}  — raw .eva file content (internal tool use)
  GET    /projects/{project_id}/conversations          — list conversations linked to this project
  GET    /projects/{project_id}/agents                 — list agents linked to this project
  GET    /projects/{project_id}/daemons                — list daemons linked to this project

  POST   /projects/{project_id}/knowledge              — upload + RAG-index a document (B-052)
  DELETE /projects/{project_id}/knowledge/{filename}   — remove knowledge file + vectors
  POST   /projects/{project_id}/files                  — upload a file to the workspace (no RAG)
  DELETE /projects/{project_id}/files                  — delete a file from the workspace
  GET    /projects/{project_id}/files                  — list all files in the project workspace

File storage layout:
  projects/{id}/knowledge/  — RAG-indexed document blobs (POST /knowledge)
  projects/{id}/files/      — workspace blob storage (POST /files, no indexing)

Protected directories (403 on delete attempts):
  .eva/  conversations/  .git/  knowledge/
"""

import asyncio
import os
import threading
import uuid as _uuid
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, func

from core.auth.dependencies import get_current_auth
from core.db.session import AsyncSessionLocal
from core.db.repository import ProjectRepo, MCPRepo
from core.db.models import Conversation, SubagentConfig, DaemonConfig
from core.project.manager import ProjectManager
from core.metrics.service import MetricsService
from core.agents.registry import subagent_registry
from core.daemons.registry import daemon_registry, sync_daemon_name
from utils.file_utils import INDEXABLE_EXTENSIONS
from config.settings import PROJECT_FILES_MAX_WORKSPACE_ENTRIES

logger = getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    synopsis: Optional[str] = None
    allowed_mcp_servers: Optional[List[str]] = None   # B-053
    workspace_path: Optional[str] = None  # B-051: external folder to use as workspace root


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    synopsis: Optional[str] = None
    status: Optional[str] = None   # active | paused | archived
    allowed_mcp_servers: Optional[List[str]] = None   # B-053
    workspace_path: Optional[str] = None  # B-051: reassign workspace root


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _is_external_workspace(workspace_path: Optional[str]) -> bool:
    """True when workspace_path points outside EVA's managed directory."""
    if not workspace_path:
        return False
    from config.settings import WORKSPACE_ROOT
    try:
        return not os.path.abspath(workspace_path).startswith(os.path.abspath(WORKSPACE_ROOT))
    except Exception as exc:
        logger.warning("_is_external_workspace check failed for %r: %s — treating as internal", workspace_path, exc)
        return False


# ── Q-03: response models ────────────────────────────────────────────────────

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    synopsis: Optional[str] = None
    status: str
    workspace_path: Optional[str] = None
    is_external: bool
    allowed_mcp_servers: Optional[List[str]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SyncProjectResponse(BaseModel):
    triggered: bool
    project_id: str
    execution_id: Optional[str] = None
    note: str


class ProjectContextResponse(BaseModel):
    tier: str
    token_estimate: int
    context: str


class ArtifactResponse(BaseModel):
    filename: str
    content: str


class ProjectConversationItem(BaseModel):
    id: str
    title: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UploadProjectKnowledgeResponse(BaseModel):
    job_id: str
    status: str
    filename: str
    project_id: str


class DeleteProjectKnowledgeResponse(BaseModel):
    status: str
    filename: str
    project_id: str
    deleted: Dict[str, Any]


class UploadProjectFileResponse(BaseModel):
    status: str
    filename: str
    stored_path: str
    size: int


class DeleteProjectFileResponse(BaseModel):
    status: str
    deleted_path: str


class ProjectFileEntry(BaseModel):
    path: str
    source: str  # "knowledge" | "uploaded" | "workspace"
    purpose: str  # "knowledge" | "workspace"
    size: int
    modified_at: str
    storage_path: str


class ListProjectFilesResponse(BaseModel):
    files: List[ProjectFileEntry]
    total: int
    workspace_root: str
    # V6.60: True only when the project's workspace had more files than
    # PROJECT_FILES_MAX_WORKSPACE_ENTRIES and the listing was cut at that ceiling
    # (knowledge/uploaded files are never cut). Additive; older clients ignore it.
    truncated: bool = False
    truncated_message: Optional[str] = None


class ProjectAgentItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None


class ProjectDaemonItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    enabled: bool
    lifecycle_status: str
    last_run_at: Optional[str] = None


def _project_dict(p: Any) -> Dict[str, Any]:
    return {
        "id":                   p.id,
        "name":                 p.name,
        "description":          p.description,
        "synopsis":             p.synopsis,
        "status":               p.status,
        "workspace_path":       p.workspace_path,
        "is_external":          _is_external_workspace(p.workspace_path),
        "allowed_mcp_servers":  getattr(p, "allowed_mcp_servers", None),
        "created_at":           _dt(p.created_at),
        "updated_at":           _dt(p.updated_at),
    }


def _build_sync_trigger_message(user_id: str) -> str:
    """
    Build the trigger message for a project-sync daemon run.

    Deliberately short and static -- this exact wording (delegate to
    skill-runner, point it at the skill, nothing else) is the one thing
    that's been reliable across every live test in this investigation.
    Earlier attempts to also embed the current knowledge.eva content
    directly in this message (so the subagent wouldn't have to fetch it
    itself) failed in live testing: the daemon's own relay step silently
    dropped the embedded block when constructing its delegate_to_agent
    call, and inlining unbounded file content into a message doesn't scale
    for a long-running project anyway. That responsibility now belongs to
    the skill-runner subagent itself, via the project_artifact_read tool
    (tools/default/project_tools.py) called from within the skill
    (skills/project-context/SKILL.md, Phase 3) -- fetched on demand, at the
    point it's actually needed, not pre-loaded here.
    """
    from utils.workspace import get_user_skills_dir

    skill_path = get_user_skills_dir(user_id) / "project-context" / "SKILL.md"
    return (
        "Sync this project. Call delegate_to_agent with agent_name='skill-runner' "
        f"and task='Read the skill at {skill_path} and execute it to explore and "
        "record orientation knowledge for this project.'"
    )


async def _ensure_sync_daemon(project: Any, user_id: str) -> DaemonConfig:
    """
    Create (or return the existing) auto-orientation sync daemon for a
    project. Idempotent via daemon_registry.upsert()'s (user_id, name)
    keying, so it's safe to call even when a row already exists.

    Used both at project-creation time (create_project()) and lazily by
    sync_project() for a project that predates this feature, or whose
    original daemon creation silently failed -- there is exactly one
    place that builds this config, not two that could drift apart.
    """
    from config.settings import PROJECT_SYNC_DAEMON_MAX_STEPS

    return await daemon_registry.upsert(user_id, {
        "name": sync_daemon_name(project.id),
        "kind": "system",
        "description": f"Auto-orientation scan for project '{project.name}'.",
        "project_id": project.id,
        "run_interval_seconds": None,  # manual-trigger-only, never scheduler-fired
        "enabled": True,
        "tools": ["delegate_to_agent"],
        "max_steps": PROJECT_SYNC_DAEMON_MAX_STEPS,
        "system_prompt": (
            "Your only job is to delegate to the skill-runner subagent, "
            "then relay its result."
        ),
        "trigger_message": _build_sync_trigger_message(user_id),
    })


def _validate_external_path(path: str) -> str:
    """
    Validate a user-supplied workspace path. Returns the normalised absolute path.
    Raises HTTPException if the path is relative or does not exist on disk.
    """
    if not os.path.isabs(path):
        raise HTTPException(
            status_code=400,
            detail="workspace_path must be an absolute path (e.g. /home/user/my-app).",
        )
    if not os.path.isdir(path):
        raise HTTPException(
            status_code=400,
            detail=f"workspace_path does not exist or is not a directory: {path}",
        )
    return os.path.normpath(path)


# ── File / Knowledge security ─────────────────────────────────────────────────

# Top-level directories under the project workspace that EVA manages internally.
# Delete and write operations targeting any of these are blocked with 403.
PROTECTED_PROJECT_PATHS: frozenset = frozenset({
    ".eva",
    "conversations",
    ".git",
    "knowledge",
})

# File extensions accepted by POST /knowledge (text-extractable formats).
# Binary-only formats (images, archives, executables) are rejected with 415.
# Moved to utils/file_utils.py (Phase D) so tools/default/rag_tool.py's
# rag_ingest can share the same allowlist instead of duplicating it.
_KNOWLEDGE_EXTENSIONS: frozenset = INDEXABLE_EXTENSIONS


def _assert_path_safe(workspace_root: Path, rel_path: str) -> Path:
    """
    Validate a user-supplied relative path for workspace file operations.

    Raises HTTPException 400 when:
      - ``rel_path`` is absolute
      - ``rel_path`` contains ``..`` components (traversal attempt)
      - The resolved path escapes ``workspace_root``

    Raises HTTPException 403 when the top-level directory component is in
    ``PROTECTED_PROJECT_PATHS`` (.eva/, conversations/, .git/, knowledge/).

    Returns the resolved absolute ``Path`` (parent dirs may not exist yet).
    """
    if os.path.isabs(rel_path):
        raise HTTPException(
            status_code=400,
            detail="path must be relative, not absolute.",
        )
    if ".." in Path(rel_path).parts:
        raise HTTPException(
            status_code=400,
            detail="Path traversal ('..') is not allowed.",
        )

    resolved    = (workspace_root / rel_path).resolve()
    ws_resolved = workspace_root.resolve()

    try:
        resolved.relative_to(ws_resolved)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Path escapes the project workspace.",
        )

    # Guard against protected top-level directories
    rel_parts = resolved.relative_to(ws_resolved).parts
    if rel_parts and rel_parts[0] in PROTECTED_PROJECT_PATHS:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Access to '{rel_parts[0]}/' is restricted — "
                "EVA-managed directory cannot be modified via this endpoint."
            ),
        )

    return resolved


# ── Schemas (file operations) ─────────────────────────────────────────────────

class _FileDeleteRequest(BaseModel):
    """Body for DELETE /projects/{id}/files."""
    path: str  # relative path within workspace root


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("", status_code=201, response_model=ProjectResponse)
async def create_project(
    body: ProjectCreateRequest,
    auth=Depends(get_current_auth),
):
    """
    Create a new project, initialise its .eva workspace, and return the registry entry.

    workspace_path (optional): absolute path to an existing folder to use as the
    project workspace. EVA will create a .eva/ subdirectory inside it. If omitted,
    a managed folder under ~/eva_workspace/{user_id}/projects/ is created.
    """
    user_id = auth["user_id"]

    external_path: Optional[str] = None
    if body.workspace_path:
        external_path = _validate_external_path(body.workspace_path)

    # Enforce unique name per user
    async with AsyncSessionLocal() as db:
        existing = await ProjectRepo.get_by_name(db, user_id=user_id, name=body.name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A project named '{body.name}' already exists. Choose a different name.",
        )

    # Create the DB row first (to get the generated id)
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.create(
            db,
            user_id=user_id,
            name=body.name,
            description=body.description,
            synopsis=body.synopsis,
            allowed_mcp_servers=body.allowed_mcp_servers,
        )

    # Initialise .eva workspace files (inside external_path if provided)
    workspace_path = ProjectManager.init_workspace(
        user_id=user_id,
        project_id=project.id,
        name=body.name,
        description=body.description,
        synopsis=body.synopsis,
        external_path=external_path,
    )

    # Persist the workspace_path back to DB
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.update(
            db,
            project_id=project.id,
            user_id=user_id,
            updates={"workspace_path": workspace_path},
        )

    # Phase B: fire-and-forget project-sync daemon. A per-project daemon
    # (manual-trigger-only) whose only job is to delegate to the shared
    # skill-runner subagent, pointed at the project-context skill -- which
    # explores the project and records orientation knowledge directly into
    # knowledge.eva via project_artifact_write. Non-blocking: this endpoint
    # already returns below regardless of scan duration, and any failure
    # here is logged, never raised -- it can't turn into a project-creation
    # error. Also gives free resync later via POST /daemons/{name}/trigger
    # or POST /projects/{id}/sync.
    #
    # Kicks off via trigger_daemon() (api/daemons.py), not a bare
    # run_daemon() call -- calling run_daemon() directly here skipped
    # trigger_daemon()'s record_success()/record_failure() lifecycle
    # bookkeeping entirely, so GET /daemons/{name}/status kept showing
    # lifecycle_status="idle"/last_run_at=null forever, even for a daemon
    # that had already run and succeeded (confirmed live: the daemon's own
    # log showed "Daemon run finished, success=True" while the status
    # endpoint never moved off its initial idle state). trigger_daemon()
    # already sets lifecycle_status="running" synchronously before
    # kicking off its own background task, and updates it correctly on
    # completion either way -- reusing it here, exactly as sync_project()
    # already does below, means there's only ever one place that does this
    # bookkeeping, not two that can drift apart.
    try:
        from api.daemons import trigger_daemon

        daemon_row = await _ensure_sync_daemon(project, user_id)
        await trigger_daemon(name=daemon_row.name, auth=auth)
    except Exception as exc:
        logger.warning(
            "Phase B: could not start project-sync daemon (non-fatal): %s",
            exc,
            extra={"project_id": project.id},
        )

    return _project_dict(project)


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ProjectResponse])
async def list_projects(
    status: Optional[str] = Query(None, description="Filter by status: active | paused | archived"),
    auth=Depends(get_current_auth),
):
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        projects = await ProjectRepo.list_for_user(db, user_id=user_id, status=status)
    return [_project_dict(p) for p in projects]


# ── Get one ──────────────────────────────────────────────────────────────────

@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    auth=Depends(get_current_auth),
):
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_dict(project)


# ── Sync (Phase C) ──────────────────────────────────────────────────────────

@router.post("/{project_id}/sync", response_model=SyncProjectResponse)
async def sync_project(
    project_id: str,
    auth=Depends(get_current_auth),
):
    """
    Manually re-trigger this project's sync daemon (the same one Phase B
    creates automatically at project creation). Thin wrapper that calls the
    existing POST /daemons/{name}/trigger handler directly as a plain
    function -- reuses its guards and lifecycle bookkeeping as-is rather
    than introducing a second trigger mechanism, just addressed by
    project_id instead of the internal daemon-naming convention.

    The daemon's name is fully deterministic from project_id (see
    core.daemons.registry.sync_daemon_name) -- project_id itself is
    stable/immutable once a project is created, so no DB column is needed
    to remember the name; it's recomputed the same way create_project()
    computed it originally.

    Self-healing: a project whose daemon creation silently failed at
    creation time, or one that predates this feature entirely, has no row
    under that name. Rather than 404ing forever, this creates the daemon
    on the spot (via the same _ensure_sync_daemon() helper create_project()
    uses) before triggering it -- the first manual sync on an old project
    just works instead of repeating the same permanent failure.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    daemon_name = sync_daemon_name(project_id)
    existing = await daemon_registry.get(user_id, daemon_name)
    if existing is None:
        await _ensure_sync_daemon(project, user_id)

    from api.daemons import trigger_daemon
    result = await trigger_daemon(name=daemon_name, auth=auth)

    return {
        "triggered": result.triggered,
        "project_id": project_id,
        "execution_id": result.execution_id,
        "note": "Sync started. Poll GET /executions/{execution_id}/status for outcome.",
    }


# ── Update ───────────────────────────────────────────────────────────────────

@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: ProjectUpdateRequest,
    auth=Depends(get_current_auth),
):
    user_id = auth["user_id"]
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Validate status value if provided
    if "status" in updates and updates["status"] not in ("active", "paused", "archived"):
        raise HTTPException(status_code=400, detail="status must be: active | paused | archived")

    # Enforce unique name per user when renaming
    if "name" in updates:
        async with AsyncSessionLocal() as db:
            existing = await ProjectRepo.get_by_name(
                db, user_id=user_id, name=updates["name"], exclude_id=project_id
            )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"A project named '{updates['name']}' already exists. Choose a different name.",
            )

    # Validate and resolve new workspace_path if provided
    if "workspace_path" in updates:
        updates["workspace_path"] = _validate_external_path(updates["workspace_path"])

    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.update(
            db, project_id=project_id, user_id=user_id, updates=updates
        )
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

    # If workspace was reassigned, init .eva/ in the new location
    if "workspace_path" in updates:
        async with AsyncSessionLocal() as db:
            project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
        ProjectManager.init_workspace(
            user_id=user_id,
            project_id=project_id,
            name=project.name,
            description=project.description,
            synopsis=project.synopsis,
            external_path=updates["workspace_path"],
        )

    # Mirror status into project.eva if workspace exists
    if "status" in updates:
        ProjectManager.update_status_in_eva(
            user_id, project_id, updates["status"],
            workspace_path=project.workspace_path,
        )

    # ── Daemon + MCP lifecycle cascade on project status change ─────────────
    # When a project is paused or archived, disable all its active daemons and
    # project-scoped MCPs so they don't run while the project is on hold.
    # When the project goes back to active, restore those that were cascade-paused
    # (but not ones the user manually paused — those stay paused/disabled).
    new_status = updates.get("status")
    if new_status in ("paused", "archived"):
        await daemon_registry.pause_for_project(user_id, project_id)
        async with AsyncSessionLocal() as db:
            await MCPRepo.pause_mcp_for_project(db, user_id, project_id)
    elif new_status == "active":
        await daemon_registry.resume_for_project(user_id, project_id)
        async with AsyncSessionLocal() as db:
            await MCPRepo.resume_mcp_for_project(db, user_id, project_id)

    return _project_dict(project)


# ── Archive (soft-delete) ────────────────────────────────────────────────────

@router.delete("/{project_id}", status_code=204)
async def archive_project(
    project_id: str,
    auth=Depends(get_current_auth),
):
    """
    Archive a project (sets status=archived).
    Does NOT delete .eva files — workspace is preserved for reference.
    Use status=archived filter to list archived projects.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.update(
            db,
            project_id=project_id,
            user_id=user_id,
            updates={"status": "archived"},
        )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    ProjectManager.update_status_in_eva(
        user_id, project_id, "archived", workspace_path=project.workspace_path
    )
    # Cascade: pause all active daemons and project-scoped MCPs
    await daemon_registry.pause_for_project(user_id, project_id)
    async with AsyncSessionLocal() as db:
        await MCPRepo.pause_mcp_for_project(db, user_id, project_id)


# ── Context injection ────────────────────────────────────────────────────────

@router.get("/{project_id}/context", response_model=ProjectContextResponse)
async def get_project_context(
    project_id: str,
    tier: str = Query("compact", description="compact | focused | full"),
    auth=Depends(get_current_auth),
):
    """
    Return the assembled LLM context block for this project.
    The block is ready for injection into a system prompt.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if tier not in ("compact", "focused", "full"):
        raise HTTPException(status_code=400, detail="tier must be: compact | focused | full")

    context = ProjectManager.load_context(
        user_id=user_id, project_id=project_id, tier=tier,
        workspace_path=project.workspace_path,
    )
    token_estimate = len(context) // 4  # rough estimate
    return {"tier": tier, "token_estimate": token_estimate, "context": context}


# ── Raw artifact access ──────────────────────────────────────────────────────

_ALLOWED_ARTIFACTS = {"project.eva", "tasks.eva", "knowledge.eva", "journal.eva", "errors.eva", "policies.eva"}

@router.get("/{project_id}/artifacts/{filename}", response_model=ArtifactResponse)
async def get_artifact(
    project_id: str,
    filename: str,
    auth=Depends(get_current_auth),
):
    """
    Return the raw content of a named .eva file.
    Allowed: project.eva, tasks.eva, knowledge.eva, journal.eva, errors.eva, policies.eva
    """
    if filename not in _ALLOWED_ARTIFACTS:
        raise HTTPException(status_code=400, detail=f"Unknown artifact. Allowed: {sorted(_ALLOWED_ARTIFACTS)}")

    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    content = ProjectManager.read_artifact(
        user_id, project_id, filename, workspace_path=project.workspace_path
    )
    return {"filename": filename, "content": content}


# ── Linked conversations ──────────────────────────────────────────────────────

@router.get("/{project_id}/conversations", response_model=List[ProjectConversationItem])
async def list_project_conversations(
    project_id: str,
    auth=Depends(get_current_auth),
):
    """Return all conversations linked to this project."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        conversations = await ProjectRepo.list_conversations(
            db, project_id=project_id, user_id=user_id
        )

    return [
        {
            "id":         c.id,
            "title":      c.title,
            "created_at": _dt(c.created_at),
            "updated_at": _dt(c.updated_at),
        }
        for c in conversations
    ]


# ── Knowledge upload + RAG ingestion (B-052) ─────────────────────────────────

@router.post("/{project_id}/knowledge", status_code=202, response_model=UploadProjectKnowledgeResponse)
async def upload_project_knowledge(
    project_id: str,
    file: UploadFile,
    auth=Depends(get_current_auth),
):
    """
    Upload a text document and RAG-index it into the project's knowledge scope (B-052).

    Accepted formats: PDF, DOCX, CSV/XLS/XLSX, plain text, Markdown, JSON, YAML,
    source code, and other text-extractable files.  Binary-only formats (images,
    archives, executables) are rejected with 415 — use POST /projects/{id}/files
    for those.

    Blob is saved to projects/{id}/knowledge/ and ChromaDB vectors are tagged with
    project_id so rag_retrieve can filter results to this project.

    Returns a job_id for polling via GET /rag/ingest/{job_id}/status.
    """
    user_id = auth["user_id"]

    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status == "archived":
        raise HTTPException(
            status_code=403, detail="Cannot upload knowledge to an archived project."
        )
    if project.status == "paused":
        raise HTTPException(
            status_code=403,
            detail="Project is paused. Resume it (set status=active) before uploading knowledge.",
        )

    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _KNOWLEDGE_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '.{ext}' for knowledge indexing. "
                f"Accepted formats: {', '.join(sorted(_KNOWLEDGE_EXTENSIONS))}. "
                f"To store binary blobs use POST /projects/{project_id}/files."
            ),
        )

    raw_bytes = await file.read()

    # Save to projects/{id}/knowledge/ (flat — no path parameter, filenames only)
    from utils.workspace import get_project_knowledge_dir as _get_knowledge_dir
    knowledge_dir = _get_knowledge_dir(user_id, project_id)
    blob_path = knowledge_dir / filename
    blob_path.write_bytes(raw_bytes)

    job_id = str(_uuid.uuid4())

    from api.rag import _run_ingestion_job, _set_rag_job_state
    await _set_rag_job_state(job_id, {
        "status":     "queued",
        "filename":   filename,
        "chunks_done":  0,
        "total_chunks": 0,
        "error":      None,
        "project_id": project_id,
    })

    asyncio.create_task(
        _run_ingestion_job(job_id, user_id, filename, raw_bytes, str(blob_path), project_id)
    )

    return {
        "job_id":     job_id,
        "status":     "queued",
        "filename":   filename,
        "project_id": project_id,
    }


@router.delete("/{project_id}/knowledge/{filename}", status_code=200, response_model=DeleteProjectKnowledgeResponse)
async def delete_project_knowledge(
    project_id: str,
    filename: str,
    auth=Depends(get_current_auth),
):
    """
    Delete a project knowledge file: removes ChromaDB vectors and the blob.

    ``filename`` must be a bare filename (no path separators or ``..``).
    Returns ``vectors_deleted`` and ``blob_deleted`` flags.
    """
    user_id = auth["user_id"]

    # Reject any path traversal hidden in the filename segment
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(
            status_code=400,
            detail="filename must be a bare filename — path separators are not allowed.",
        )

    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from core.rag.deletion import delete_project_knowledge_file
    result = delete_project_knowledge_file(
        tenant_id=user_id,
        project_id=project_id,
        filename=filename,
    )

    return {
        "status":     "ok",
        "filename":   filename,
        "project_id": project_id,
        "deleted":    result,
    }


# ── Workspace file upload / delete ────────────────────────────────────────────

@router.post("/{project_id}/files", status_code=200, response_model=UploadProjectFileResponse)
async def upload_project_file(
    project_id: str,
    file: UploadFile,
    path: Optional[str] = Form(default=None),
    auth=Depends(get_current_auth),
):
    """
    Upload a file to the project workspace (no RAG indexing).

    Accepts any format — images, archives, binary blobs, and text files alike.
    For text documents that should be searchable via RAG, use POST /knowledge.

    path (optional): relative path within the workspace where the file should
    land.  Examples:
      - ``"Financial data/bank_statement.csv"``  →  places the file inside that
        subdirectory of the workspace root (directory is created if absent).
      - ``"Financial data/"``  →  directory prefix; uses the uploaded filename.
    When omitted the file is stored flat in projects/{id}/files/.

    Protected directories (.eva/, conversations/, .git/, knowledge/) cannot be
    targeted — returns 403.

    Returns the stored path and byte size immediately (synchronous write, no
    job_id).
    """
    user_id = auth["user_id"]

    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status == "archived":
        raise HTTPException(
            status_code=403, detail="Cannot upload files to an archived project."
        )
    if project.status == "paused":
        raise HTTPException(
            status_code=403,
            detail="Project is paused. Resume it (set status=active) before uploading files.",
        )

    filename = file.filename or "upload"
    raw_bytes = await file.read()

    from utils.workspace import get_project_dir as _get_project_dir
    managed_dir    = _get_project_dir(user_id, project_id)
    workspace_root = Path(project.workspace_path) if project.workspace_path else managed_dir

    if path:
        # If path ends with a separator it's a directory prefix — append filename.
        effective_path = (
            path.rstrip("/\\") + "/" + filename
            if path.endswith("/") or path.endswith("\\")
            else path
        )
        target_path = _assert_path_safe(workspace_root, effective_path)
    else:
        # Default: flat storage in managed projects/{id}/files/
        files_dir = managed_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        target_path = files_dir / filename

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(raw_bytes)

    # Return stored_path relative to workspace_root when possible
    try:
        stored_path = target_path.relative_to(workspace_root).as_posix()
    except ValueError:
        stored_path = target_path.relative_to(managed_dir).as_posix()

    return {
        "status":      "ok",
        "filename":    filename,
        "stored_path": stored_path,
        "size":        len(raw_bytes),
    }


@router.delete("/{project_id}/files", status_code=200, response_model=DeleteProjectFileResponse)
async def delete_project_file(
    project_id: str,
    body: _FileDeleteRequest,
    auth=Depends(get_current_auth),
):
    """
    Delete a file from the project workspace.

    Body: ``{"path": "relative/path/to/file.ext"}``

    The path is validated to stay within the workspace root and must not target
    protected directories (.eva/, conversations/, .git/, knowledge/).
    Returns 404 if the file does not exist, 400 if path points to a directory.
    """
    user_id = auth["user_id"]

    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from utils.workspace import get_project_dir as _get_project_dir
    managed_dir    = _get_project_dir(user_id, project_id)
    workspace_root = Path(project.workspace_path) if project.workspace_path else managed_dir

    target_path = _assert_path_safe(workspace_root, body.path)

    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")
    if target_path.is_dir():
        raise HTTPException(
            status_code=400, detail="path points to a directory, not a file."
        )

    target_path.unlink()

    return {
        "status":       "ok",
        "deleted_path": body.path,
    }


class _ResolveCache:
    """
    `str(entry.resolve())` without paying for it on every file.

    Path.resolve() is by far the most expensive thing the file listing does (about
    70% of its time): on Windows each call opens a handle on the file, and the
    original code made two per file. Resolving a file that is not itself a symlink
    gives exactly `resolve(parent) / name`, so each DIRECTORY is resolved once
    (together with the set of symlink names it contains, from one scandir -- no
    per-file syscall) and files are joined onto it. Anything unusual -- a symlinked
    file, an unreadable directory -- falls back to the original per-entry
    `entry.resolve()`, so the result is identical in every case.
    """

    def __init__(self):
        self._dirs: dict = {}

    def resolve(self, entry: Path) -> str:
        parent = entry.parent
        info = self._dirs.get(parent)
        if info is None:
            try:
                resolved_parent = str(parent.resolve())
                with os.scandir(parent) as it:
                    symlinks = {e.name for e in it if e.is_symlink()}
                info = (resolved_parent, symlinks)
            except OSError:
                info = (None, None)
            self._dirs[parent] = info
        resolved_parent, symlinks = info
        if resolved_parent is None or entry.name in symlinks:
            return str(entry.resolve())
        return os.path.join(resolved_parent, entry.name)


def _collect_project_files(
    workspace_root: Path,
    uploads_dir: Path,
    knowledge_dir: Path,
    is_external: bool,
    stop: threading.Event,
    max_workspace_entries: int,
):
    """
    Blocking, recursive listing of a project's files: the knowledge dir, the
    workspace (recursively, minus `.eva/`) and, for external workspaces, the
    managed uploads dir. Returns (files, truncated).

    This is the body of GET /projects/{id}/files, unchanged in behaviour, moved out
    of the async handler (V6.60): a recursive walk of a real codebase -- thousands to
    hundreds of thousands of entries, each sorted, resolved and stat()ed -- ran ON
    THE EVENT LOOP and froze every user's requests until it finished. The handler now
    runs this in a worker thread.

    Two additions, neither changing what an ordinary project returns:
      * `stop` (set when the request is cancelled) ends the walk promptly;
      * at most `max_workspace_entries` WORKSPACE-source files are returned (path
        order); knowledge and uploaded files are never cut. Hitting it sets truncated.
    """
    # EVA internal metadata directory — never shown to the user
    eva_dir = workspace_root / ".eva"

    files: list[dict] = []
    seen_abs: set[str] = set()  # absolute paths already added (dedup guard)
    workspace_count = 0
    truncated = False

    resolver = _ResolveCache()

    def _stat_entry(entry: Path, abs_str: str, rel_path: str, source: str, purpose: str) -> dict:
        stat = entry.stat()
        return {
            "path":         rel_path,
            "source":       source,
            "purpose":      purpose,
            "size":         stat.st_size,
            "modified_at":  datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "storage_path": abs_str,        # the same value the dedup guard computed; resolved once, not twice
        }

    # ── 1. Knowledge dir (RAG-indexed docs) ──────────────────────────────────
    #       Scanned first so knowledge files appear at the top of the listing
    #       and are correctly tagged even when knowledge_dir is inside workspace_root.
    if knowledge_dir.exists():
        for entry in sorted(knowledge_dir.rglob("*")):
            if stop.is_set():
                return files, truncated
            if not entry.is_file():
                continue
            abs_str = resolver.resolve(entry)
            if abs_str in seen_abs:
                continue
            rel_path = entry.relative_to(knowledge_dir).as_posix()
            files.append(_stat_entry(entry, abs_str, rel_path, "knowledge", "knowledge"))
            seen_abs.add(abs_str)

    # ── 2. Scan workspace directory ──────────────────────────────────────────
    uploads_exist = uploads_dir.exists()        # once, not once per file
    if workspace_root.exists():
        for entry in sorted(workspace_root.rglob("*")):
            if stop.is_set():
                return files, truncated
            if not entry.is_file():
                continue

            # Exclude .eva/ (EVA internal metadata)
            try:
                entry.relative_to(eva_dir)
                continue
            except ValueError:
                pass

            abs_str = resolver.resolve(entry)
            # Knowledge files already in seen_abs — skip via dedup guard
            if abs_str in seen_abs:
                continue

            rel_path = entry.relative_to(workspace_root).as_posix()

            # Files inside the managed uploads subdir are tagged "uploaded";
            # everything else (agent output, user files, existing codebase) is "workspace".
            source = "workspace"
            if not is_external and uploads_exist:
                try:
                    entry.relative_to(uploads_dir)
                    source = "uploaded"
                except ValueError:
                    pass

            if source == "workspace":
                if workspace_count >= max_workspace_entries:
                    truncated = True
                    continue            # keep walking: uploaded files later in path order still count
                workspace_count += 1

            files.append(_stat_entry(entry, abs_str, rel_path, source, "workspace"))
            seen_abs.add(abs_str)

    # ── 3. For external workspaces, uploads_dir is outside workspace_root ────
    #       Scan it separately so uploaded files still appear in the listing.
    if is_external and uploads_dir.exists():
        for entry in sorted(uploads_dir.rglob("*")):
            if stop.is_set():
                return files, truncated
            if not entry.is_file():
                continue
            abs_str = resolver.resolve(entry)
            if abs_str in seen_abs:
                continue
            rel_path = entry.relative_to(uploads_dir).as_posix()
            files.append(_stat_entry(entry, abs_str, rel_path, "uploaded", "workspace"))
            seen_abs.add(abs_str)

    # Sort: knowledge first, then uploaded, then workspace — alphabetically within each group
    _sort_order = {"knowledge": 0, "uploaded": 1, "workspace": 2}
    files.sort(key=lambda f: (_sort_order.get(f["source"], 3), f["path"]))
    return files, truncated


@router.get("/{project_id}/files", response_model=ListProjectFilesResponse)
async def list_project_files(
    project_id: str,
    auth=Depends(get_current_auth),
):
    """
    List all files in the project workspace.

    Returns a merged, tagged list combining three sources:

    1. **knowledge** — files uploaded via POST /projects/{id}/knowledge (RAG-indexed).
       Stored in projects/{id}/knowledge/.  Tagged ``source="knowledge"``,
       ``purpose="knowledge"``.

    2. **uploaded** — files uploaded via POST /projects/{id}/files (workspace blobs,
       no RAG indexing).  Stored in projects/{id}/files/.  Tagged
       ``source="uploaded"``, ``purpose="workspace"``.

    3. **workspace** — files present inside the project's workspace directory
       (agent output, existing codebase for external workspaces, etc.).  Tagged
       ``source="workspace"``, ``purpose="workspace"``.

    The ``.eva/`` subdirectory (EVA internal metadata) is always excluded.

    Response shape:
        {
          "files": [
            {"path": "spec.pdf",    "source": "knowledge", "purpose": "knowledge",
             "size": 89012, "modified_at": "2026-04-01T09:00:00+00:00"},
            {"path": "report.csv",  "source": "uploaded",  "purpose": "workspace",
             "size": 1234,  "modified_at": "2026-04-01T10:00:00+00:00"},
            {"path": "src/main.py", "source": "workspace", "purpose": "workspace",
             "size": 456,   "modified_at": "2026-04-01T12:00:00+00:00"}
          ],
          "total": 3,
          "workspace_root": "/path/to/workspace"
        }
    """
    user_id = auth["user_id"]

    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from utils.workspace import (
        get_project_dir as _get_project_dir,
        get_project_knowledge_dir as _get_knowledge_dir,
    )

    # Managed project dir — always exists; holds uploads under files/ and knowledge/
    managed_dir   = _get_project_dir(user_id, project_id)
    uploads_dir   = managed_dir / "files"
    knowledge_dir = _get_knowledge_dir(user_id, project_id)

    # Workspace root: external path (if set) or the managed dir itself.
    # NOTE: project.workspace_path is always set (even for managed projects it is
    # saved back as the managed dir by init_workspace), so use _is_external_workspace()
    # rather than bool() to distinguish truly external projects.
    is_external    = _is_external_workspace(project.workspace_path)
    workspace_root = Path(project.workspace_path) if project.workspace_path else managed_dir

    # The recursive walk is blocking disk work -- run it off the event loop (V6.60).
    stop = threading.Event()
    try:
        files, truncated = await asyncio.to_thread(
            _collect_project_files,
            workspace_root, uploads_dir, knowledge_dir, is_external,
            stop, PROJECT_FILES_MAX_WORKSPACE_ENTRIES,
        )
    except asyncio.CancelledError:
        stop.set()          # a thread cannot be killed; tell it to stop walking
        raise
    finally:
        stop.set()          # harmless once finished

    response = {
        "files":          files,
        "total":          len(files),
        "workspace_root": str(workspace_root),
    }
    if truncated:
        response["truncated"] = True
        response["truncated_message"] = (
            f"The workspace has more than {PROJECT_FILES_MAX_WORKSPACE_ENTRIES} files; only the first "
            f"{PROJECT_FILES_MAX_WORKSPACE_ENTRIES} (in path order) are listed. Knowledge and uploaded files are all included."
        )
    return response


# ── Linked agents ─────────────────────────────────────────────────────────────

@router.get("/{project_id}/agents", response_model=List[ProjectAgentItem])
async def list_project_agents(
    project_id: str,
    auth=Depends(get_current_auth),
):
    """Return all subagent configs linked to this project."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
    agents = await subagent_registry.list(user_id, project_id=project_id)
    return [
        {
            "id":          a.id,
            "name":        a.name,
            "description": a.description,
            "model":       a.model,
            "provider":    a.provider,
        }
        for a in agents
    ]


# ── Linked daemons ────────────────────────────────────────────────────────────

@router.get("/{project_id}/daemons", response_model=List[ProjectDaemonItem])
async def list_project_daemons(
    project_id: str,
    auth=Depends(get_current_auth),
):
    """Return all daemon configs linked to this project."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
    daemons = await daemon_registry.list(user_id, project_id=project_id)
    return [
        {
            "id":          d.id,
            "name":        d.name,
            "description": d.description,
            "enabled":     d.enabled,
            "lifecycle_status": getattr(d, "lifecycle_status", "idle"),
            "last_run_at": _dt(d.last_run_at),
        }
        for d in daemons
    ]


# ── Project Metrics ── response schemas (inline — consistent with file style) ──

class _ProviderModelRow(BaseModel):
    provider:     str
    model:        str
    executions:   int
    total_tokens: int
    cost_usd:     Optional[float]

class _TokenTotals(BaseModel):
    prompt:     int
    completion: int
    reasoning:  int
    total:      int

class _ExecutionSummary(BaseModel):
    total:        int
    successful:   int
    failed:       int
    cancelled:    int
    success_rate: Optional[float]     # None when total == 0
    by_trigger:   Dict[str, int]      # {"conversation": N, "daemon": N}

class _DaemonHealth(BaseModel):
    count:               int
    healthy:             int          # idle | running | retrying
    degraded:            int          # paused | project_paused
    failed:              int          # permanently_failed
    total_failure_count: int
    last_error:          Optional[str]

class _TaskCounts(BaseModel):
    pending: int
    active:  int
    blocked: int
    done:    int
    open:    int                      # pending + active + blocked
    total:   int

class ProjectMetricsResponse(BaseModel):
    project_id:        str
    project_name:      str
    status:            str
    created_at:        Optional[str]
    last_active_at:    Optional[str]  # MAX(execution_metrics.completed_at)

    executions:        _ExecutionSummary
    tokens:            _TokenTotals
    cost_usd:          Optional[float]
    cost_is_partial:   bool
    duration_total_ms: Optional[int]

    provider_breakdown: List[_ProviderModelRow]

    conversation_count: int
    agent_count:        int
    daemons:            _DaemonHealth
    tasks:              _TaskCounts


@router.get("/{project_id}/metrics", response_model=ProjectMetricsResponse)
async def get_project_metrics(
    project_id: str,
    auth=Depends(get_current_auth),
):
    """
    Return a single-request metrics overview for a project.

    Aggregates: execution stats, token/cost totals, provider+model
    breakdown, conversation count, agent count, daemon health, and
    open task count from tasks.eva.
    """
    user_id = auth["user_id"]

    async with AsyncSessionLocal() as db:
        # ── 1. Ownership check ─────────────────────────────────────
        project = await ProjectRepo.get(db, project_id=project_id, user_id=user_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # ── 2. Conversation count ──────────────────────────────────
        conv_result = await db.execute(
            select(func.count(Conversation.id))
            .where(Conversation.project_id == project_id)
        )
        conv_count = conv_result.scalar() or 0

        # ── 3. Agent count ─────────────────────────────────────────
        agent_result = await db.execute(
            select(func.count(SubagentConfig.id))
            .where(
                SubagentConfig.user_id == user_id,
                SubagentConfig.project_id == project_id,
            )
        )
        agent_count = agent_result.scalar() or 0

        # ── 4. Daemon health ───────────────────────────────────────
        daemon_result = await db.execute(
            select(
                DaemonConfig.lifecycle_status,
                DaemonConfig.failure_count,
                DaemonConfig.last_error,
            )
            .where(
                DaemonConfig.user_id == user_id,
                DaemonConfig.project_id == project_id,
            )
        )
        daemon_rows = daemon_result.all()

    _HEALTHY  = {"idle", "running", "retrying"}
    _DEGRADED = {"paused", "project_paused"}
    _FAILED   = {"permanently_failed"}

    d_healthy  = sum(1 for d in daemon_rows if d.lifecycle_status in _HEALTHY)
    d_degraded = sum(1 for d in daemon_rows if d.lifecycle_status in _DEGRADED)
    d_failed   = sum(1 for d in daemon_rows if d.lifecycle_status in _FAILED)
    d_total_failures = sum(d.failure_count or 0 for d in daemon_rows)
    # Most recent non-null last_error (highest failure_count wins as proxy)
    d_last_error = next(
        (
            d.last_error
            for d in sorted(daemon_rows, key=lambda x: x.failure_count or 0, reverse=True)
            if d.last_error
        ),
        None,
    )

    # ── 5. Execution metrics aggregate ────────────────────────────
    metrics = await MetricsService.for_project(project_id)

    # ── 6. Task counts ────────────────────────────────────────────
    task_counts = ProjectManager.count_tasks_by_status(
        user_id, project_id, getattr(project, "workspace_path", None)
    )

    # ── 7. Assemble ───────────────────────────────────────────────
    sc          = metrics.get("status_counts", {})
    last_active = metrics.get("last_active_at")

    return ProjectMetricsResponse(
        project_id      = project_id,
        project_name    = project.name,
        status          = project.status or "active",
        created_at      = project.created_at.isoformat() if project.created_at else None,
        last_active_at  = last_active.isoformat() if last_active else None,

        executions = _ExecutionSummary(
            total        = metrics["total_executions"],
            successful   = metrics["successful"],
            failed       = sc.get("failed", 0),
            cancelled    = sc.get("cancelled", 0),
            success_rate = metrics["success_rate"],
            by_trigger   = metrics.get("trigger_counts", {}),
        ),

        tokens = _TokenTotals(
            prompt     = metrics["prompt_tokens"],
            completion = metrics["completion_tokens"],
            reasoning  = metrics["reasoning_tokens"],
            total      = metrics["total_tokens"],
        ),
        cost_usd          = metrics["total_cost_usd"],
        cost_is_partial   = metrics["cost_is_partial"],
        duration_total_ms = metrics["duration_total_ms"],

        provider_breakdown = [
            _ProviderModelRow(
                provider     = p["provider"],
                model        = p["model"],
                executions   = p["executions"],
                total_tokens = p["total_tokens"],
                cost_usd     = p["cost_usd"],
            )
            for p in metrics["provider_breakdown"]
        ],

        conversation_count = conv_count,
        agent_count        = agent_count,

        daemons = _DaemonHealth(
            count               = len(daemon_rows),
            healthy             = d_healthy,
            degraded            = d_degraded,
            failed              = d_failed,
            total_failure_count = d_total_failures,
            last_error          = d_last_error,
        ),

        tasks = _TaskCounts(**task_counts),
    )
