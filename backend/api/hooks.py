# api/hooks.py
"""
Hook script management API (B-064).

Provides CRUD endpoints for user-uploaded hook scripts.
Scripts are stored at ~/eva_workspace/{user_id}/hooks/{filename}.
Metadata (name, description, size) is persisted in the user_hook_scripts table.

Hook script contract (matches Claude Code):
  - Receives a JSON payload on stdin (see core/hooks/schemas.py HookPayload)
  - Exits 0 to allow, 2 to block
  - May write JSON to stdout for structured decisions (decision, reason,
    updated_input, additional_context)
"""

import os
import stat
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from config.settings import HOOK_SCRIPT_MAX_BYTES
from core.auth.dependencies import get_current_auth
from core.db.repository import DBRepo
from core.db.session import AsyncSessionLocal
from core.shell.safety import ShellSafety
from utils.workspace import get_user_hooks_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hooks", tags=["hooks"])

_MAX_SCRIPT_BYTES = HOOK_SCRIPT_MAX_BYTES

# Characters not allowed in script names
_INVALID_NAME_CHARS = set('/\\:*?"<>|')


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class HookScriptResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    filename: str
    size_bytes: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class HookScriptListResponse(BaseModel):
    scripts: List[HookScriptResponse]
    total: int


class DeleteHookScriptResponse(BaseModel):
    status: str
    deleted_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_script_name(name: str) -> None:
    """Raise HTTPException if name is not a safe plain filename."""
    if not name:
        raise HTTPException(status_code=422, detail="Script name must not be empty.")
    if any(c in _INVALID_NAME_CHARS for c in name):
        raise HTTPException(
            status_code=422,
            detail=f"Script name contains invalid characters. Disallowed: {sorted(_INVALID_NAME_CHARS)}",
        )
    if ".." in name:
        raise HTTPException(status_code=422, detail="Script name must not contain '..'.")
    if len(name) > 128:
        raise HTTPException(status_code=422, detail="Script name must be 128 characters or fewer.")


def _make_executable(path: Path) -> None:
    """Add owner execute bit (chmod +x equivalent)."""
    current = path.stat().st_mode
    path.chmod(current | stat.S_IXUSR | stat.S_IXGRP)


def _warn_if_dangerous(content: bytes, name: str) -> Optional[str]:
    """
    Run the B-052 deny-list against script content lines.
    Returns a warning string if any dangerous pattern is found, else None.
    This is advisory only — scripts are not blocked at upload.
    """
    try:
        text = content.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            result = ShellSafety.check(
                cmd=line.split(),
                workspace_root="/",   # widest possible scope for static scan
            )
            if result.blocked:
                return (
                    f"Script '{name}' contains a potentially dangerous pattern "
                    f"on line '{line[:80]}': {result.reason}"
                )
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/scripts", response_model=HookScriptResponse, status_code=201)
async def upload_hook_script(
    name: str = Form(..., description="Unique script name (used in HookDefinition.command)"),
    description: Optional[str] = Form(None),
    file: UploadFile = File(..., description="Script file content"),
    auth=Depends(get_current_auth),
):
    """
    Upload a new hook script.

    The script is stored at ~/eva_workspace/{user_id}/hooks/{name} and made
    executable.  The `name` value is what you put in HookDefinition.command.

    Returns 409 if a script with that name already exists — use PUT to update.
    """
    user_id = auth["user_id"]
    _validate_script_name(name)

    content = await file.read()
    if len(content) > _MAX_SCRIPT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Script exceeds maximum size of {_MAX_SCRIPT_BYTES // 1024} KB.",
        )

    async with AsyncSessionLocal() as db:
        existing = await DBRepo.get_hook_script(db, user_id, name)
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"A script named '{name}' already exists. Use PUT /hooks/scripts/{name} to update it.",
            )

        hooks_dir = get_user_hooks_dir(user_id)
        script_path = hooks_dir / name
        script_path.write_bytes(content)
        _make_executable(script_path)

        # Advisory safety scan — warn in logs, never block
        warning = _warn_if_dangerous(content, name)
        if warning:
            logger.warning("[HOOK UPLOAD] %s", warning, extra={"user_id": user_id})

        script = await DBRepo.create_hook_script(
            db,
            user_id=user_id,
            name=name,
            filename=name,
            size_bytes=len(content),
            description=description,
        )

    logger.info(
        "Hook script uploaded",
        extra={"user_id": user_id, "script_name": name, "size_bytes": len(content)},
    )
    return script


@router.get("/scripts", response_model=HookScriptListResponse)
async def list_hook_scripts(auth=Depends(get_current_auth)):
    """List all hook scripts for the current user."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        scripts = await DBRepo.list_hook_scripts(db, user_id)
    return HookScriptListResponse(scripts=scripts, total=len(scripts))


@router.get("/scripts/{name}", response_model=HookScriptResponse)
async def get_hook_script(name: str, auth=Depends(get_current_auth)):
    """Get metadata for a specific hook script."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        script = await DBRepo.get_hook_script(db, user_id, name)
    if not script:
        raise HTTPException(status_code=404, detail=f"Hook script '{name}' not found.")
    return script


@router.get("/scripts/{name}/content", response_class=PlainTextResponse)
async def get_hook_script_content(name: str, auth=Depends(get_current_auth)):
    """
    Return the raw script content as plain text.
    Useful for inspecting or downloading the script body.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        script = await DBRepo.get_hook_script(db, user_id, name)
    if not script:
        raise HTTPException(status_code=404, detail=f"Hook script '{name}' not found.")

    hooks_dir = get_user_hooks_dir(user_id)
    script_path = hooks_dir / script.filename
    if not script_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Script file for '{name}' not found on disk. The record may be stale.",
        )

    return PlainTextResponse(script_path.read_text(errors="replace"))


@router.put("/scripts/{name}", response_model=HookScriptResponse)
async def update_hook_script(
    name: str,
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    auth=Depends(get_current_auth),
):
    """
    Overwrite an existing hook script with new content.
    Creates the script if it does not yet exist (upsert behaviour).
    """
    user_id = auth["user_id"]
    _validate_script_name(name)

    content = await file.read()
    if len(content) > _MAX_SCRIPT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Script exceeds maximum size of {_MAX_SCRIPT_BYTES // 1024} KB.",
        )

    async with AsyncSessionLocal() as db:
        existing = await DBRepo.get_hook_script(db, user_id, name)

        hooks_dir = get_user_hooks_dir(user_id)
        script_path = hooks_dir / name
        script_path.write_bytes(content)
        _make_executable(script_path)

        warning = _warn_if_dangerous(content, name)
        if warning:
            logger.warning("[HOOK UPLOAD] %s", warning, extra={"user_id": user_id})

        if existing:
            script = await DBRepo.update_hook_script(
                db, existing, size_bytes=len(content), description=description
            )
        else:
            script = await DBRepo.create_hook_script(
                db,
                user_id=user_id,
                name=name,
                filename=name,
                size_bytes=len(content),
                description=description,
            )

    logger.info(
        "Hook script updated",
        extra={"user_id": user_id, "script_name": name, "size_bytes": len(content)},
    )
    return script


@router.delete("/scripts/{name}", response_model=DeleteHookScriptResponse)
async def delete_hook_script(name: str, auth=Depends(get_current_auth)):
    """Delete a hook script (DB record + file on disk)."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        script = await DBRepo.get_hook_script(db, user_id, name)
        if not script:
            raise HTTPException(status_code=404, detail=f"Hook script '{name}' not found.")

        # Remove file from disk (best-effort — don't fail if already gone)
        hooks_dir = get_user_hooks_dir(user_id)
        script_path = hooks_dir / script.filename
        try:
            script_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(
                "Could not delete hook script file",
                extra={"user_id": user_id, "path": str(script_path), "error": str(e)},
            )

        await DBRepo.delete_hook_script(db, script)

    logger.info("Hook script deleted", extra={"user_id": user_id, "script_name": name})
    return DeleteHookScriptResponse(status="deleted", deleted_name=name)
