# api/skills.py
"""
REST API for Skills management — file-system scoped (V5.6 overhaul).

Skills are stored per-user at ~/eva_workspace/{user_id}/skills/.
Each user has a fully isolated skill library.

A skill is a directory containing at minimum a SKILL.md file (Anthropic format).
Complex skills may contain additional subdirectories and files (phases, templates,
checklists, scripts, etc.) packaged as a .skill ZIP bundle.

Endpoints
─────────
  GET    /skills                                 List caller's skills (metadata only)
  POST   /skills                                 Create a skill from raw SKILL.md text
  POST   /skills/install                         Install a skill from a .skill ZIP upload
  POST   /skills/reload                          Reload caller's skills from disk
  DELETE /skills/{name}                          Delete entire skill (registry + disk)

  GET    /skills/{name}/files                    File tree for a skill (all files/dirs)
  GET    /skills/{name}/files/{filepath}         Read content of a specific file
  PUT    /skills/{name}/files/{filepath}         Write or create a specific file
  DELETE /skills/{name}/files/{filepath}         Delete a specific file (not SKILL.md)

All endpoints require Bearer authentication.

Removed in V5.6 (vs V3.1):
  GET /skills/{name}   — returned only SKILL.md content.
                         Use GET /skills/{name}/files/SKILL.md instead.
"""

import io
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from api._etag import etag_response
from api.schemas.responses import DeleteByNameResponse
from core.auth.dependencies import get_current_auth
from core.skills.loader import SkillLoader, SkillMeta
from core.skills.registry import get_skill_registry, reset_skill_registry
from utils.workspace import get_user_skills_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class ReloadSkillsResponse(BaseModel):
    status: str
    count: int
    skills: List[str]


class DeleteSkillFileResponse(BaseModel):
    status: str
    name: str
    filepath: str


# ── Request / Response schemas ─────────────────────────────────────────────────

class SkillCreateRequest(BaseModel):
    """Create a simple single-file skill from raw SKILL.md text."""
    name: str
    content: str  # Full SKILL.md text (YAML frontmatter + markdown body)


class SkillResponse(BaseModel):
    """Skill metadata returned by the list and create endpoints."""
    name: str
    description: str
    path: str                        # absolute path to skill directory
    allowed_tools: List[str] = []
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: Dict[str, Any] = {}


class SkillFileEntry(BaseModel):
    """One node in the skill file tree."""
    path: str                                       # relative to skill root
    type: str                                       # "file" | "directory"
    size: Optional[int] = None                      # bytes; None for directories
    children: Optional[List["SkillFileEntry"]] = None  # populated for directories


class SkillFilesResponse(BaseModel):
    """Response for GET /skills/{name}/files."""
    name: str
    skill_dir: str                  # absolute path to skill root (for reference)
    files: List[SkillFileEntry]     # top-level entries, directories before files


class SkillFileContentResponse(BaseModel):
    """Response for GET /skills/{name}/files/{filepath}."""
    name: str
    filepath: str                   # relative path that was requested
    content: str


class SkillFileWriteRequest(BaseModel):
    """Body for PUT /skills/{name}/files/{filepath}."""
    content: str


class SkillFileWriteResponse(BaseModel):
    """Response for PUT /skills/{name}/files/{filepath}."""
    name: str
    filepath: str
    size: int
    registry_reloaded: bool         # True when SKILL.md was edited + registry updated


# ── Internal helpers ───────────────────────────────────────────────────────────

def _meta_to_response(meta: SkillMeta) -> dict:
    """Convert SkillMeta to SkillResponse dict."""
    return {
        "name": meta.name,
        "description": meta.description,
        "path": str(meta.path),
        "allowed_tools": meta.allowed_tools,
        "license": meta.license or None,
        "compatibility": meta.compatibility or None,
        "metadata": meta.metadata or {},
    }


def _build_file_tree(directory: Path, root: Path) -> List[dict]:
    """
    Recursively build a file tree for a skill directory.

    Returns entries sorted directories-first, then alphabetically by path.
    All `path` values are relative to *root* and use forward slashes.
    """
    entries = []
    try:
        children = sorted(directory.iterdir())
    except PermissionError:
        return entries

    for item in children:
        rel = item.relative_to(root)
        rel_str = rel.as_posix()   # always forward slashes regardless of OS

        if item.is_dir():
            entries.append({
                "path": rel_str,
                "type": "directory",
                "size": None,
                "children": _build_file_tree(item, root),
            })
        elif item.is_file():
            try:
                size = item.stat().st_size
            except OSError:
                size = None
            entries.append({
                "path": rel_str,
                "type": "file",
                "size": size,
                "children": None,
            })

    # Directories first, then files; alphabetical within each group
    entries.sort(key=lambda e: (0 if e["type"] == "directory" else 1, e["path"]))
    return entries


def _safe_resolve(skill_dir: Path, filepath: str) -> Path:
    """
    Resolve *filepath* relative to *skill_dir* and assert it stays inside.

    Raises HTTP 400 on path traversal attempts.
    """
    # Normalise: strip leading slashes that would make Path treat it as absolute
    filepath = filepath.lstrip("/").lstrip("\\")
    try:
        target = (skill_dir / filepath).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file path")

    skill_dir_resolved = skill_dir.resolve()
    # target must equal skill_dir (root itself) or be a child of it
    try:
        target.relative_to(skill_dir_resolved)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Path traversal not allowed — filepath must stay within the skill directory",
        )
    return target


# ── List endpoint ──────────────────────────────────────────────────────────────

@router.get("", response_model=List[SkillResponse])
async def list_skills(request: Request, auth=Depends(get_current_auth)):
    """
    List all skills registered for the calling user — metadata only.

    Supports ETag / If-None-Match conditional requests.
    """
    user_id = auth["user_id"]
    registry = get_skill_registry(user_id)
    data = [_meta_to_response(m) for m in registry.list_skills()]
    return etag_response(request, jsonable_encoder(data))


# ── Create / install endpoints ─────────────────────────────────────────────────

@router.post("", response_model=SkillResponse)
async def create_skill(body: SkillCreateRequest, auth=Depends(get_current_auth)):
    """
    Create a simple single-file skill from raw SKILL.md text.

    Creates ~/eva_workspace/{user_id}/skills/{name}/SKILL.md on disk, parses
    and validates the frontmatter, and registers the skill.

    For complex multi-file skills use POST /skills/install (ZIP upload) instead,
    then use PUT /skills/{name}/files/{filepath} to add individual files.

    Returns 422 if the SKILL.md content is invalid.
    """
    user_id = auth["user_id"]
    skills_dir = get_user_skills_dir(user_id)
    registry = get_skill_registry(user_id)
    loader = SkillLoader(skills_dir)

    skill_dir = skills_dir / body.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(body.content, encoding="utf-8")

    try:
        meta = loader.parse_skill_md(skill_file, skill_dir)
    except Exception as exc:
        try:
            skill_file.unlink(missing_ok=True)
            skill_dir.rmdir()
        except Exception:
            pass
        raise HTTPException(status_code=422, detail=str(exc))

    if meta is None:
        try:
            skill_file.unlink(missing_ok=True)
            skill_dir.rmdir()
        except Exception:
            pass
        raise HTTPException(
            status_code=422,
            detail=(
                "SKILL.md validation failed. Check: name (kebab-case, ≤64 chars), "
                "description (≤1024 chars, no < or >), frontmatter format."
            ),
        )

    registry.register(meta)
    logger.info("[SKILLS] Created skill via API: %s (user: %s)", meta.name, user_id)
    return _meta_to_response(meta)


@router.post("/install", response_model=SkillResponse)
async def install_skill_zip(
    file: UploadFile = File(...),
    auth=Depends(get_current_auth),
):
    """
    Install a skill from a .skill ZIP archive.

    The ZIP must contain SKILL.md at its root (or one subdirectory deep).
    All other files in the ZIP are extracted alongside SKILL.md — this is
    how multi-file skills (phases/, templates/, checklists/) are packaged.

    Returns 422 if the archive is invalid or SKILL.md is missing/invalid.
    """
    user_id = auth["user_id"]
    skills_dir = get_user_skills_dir(user_id)
    registry = get_skill_registry(user_id)
    loader = SkillLoader(skills_dir)

    raw = await file.read()

    try:
        zipfile.ZipFile(io.BytesIO(raw)).close()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Uploaded file is not a valid ZIP archive")

    tmp_zip = skills_dir / f"_upload_{file.filename or 'skill'}"
    try:
        tmp_zip.write_bytes(raw)
        unpacked = loader._unpack_skill_zip(tmp_zip)
        if unpacked is None:
            raise HTTPException(
                status_code=422,
                detail="ZIP archive does not contain a valid SKILL.md file",
            )
        skill_file = unpacked / "SKILL.md"
        meta = loader.parse_skill_md(skill_file, unpacked)
    finally:
        try:
            tmp_zip.unlink(missing_ok=True)
        except Exception:
            pass

    if meta is None:
        raise HTTPException(
            status_code=422,
            detail="SKILL.md validation failed after extraction.",
        )

    registry.register(meta)
    logger.info("[SKILLS] Installed skill via ZIP: %s (user: %s)", meta.name, user_id)
    return _meta_to_response(meta)


# ── Reload endpoint ────────────────────────────────────────────────────────────

@router.post("/reload", response_model=ReloadSkillsResponse)
async def reload_skills(auth=Depends(get_current_auth)):
    """
    Reload the calling user's skills from disk.

    Evicts the cached registry and re-scans the skills directory.
    Call this after manually editing files on disk or after bulk changes.
    """
    user_id = auth["user_id"]
    reset_skill_registry(user_id)
    registry = get_skill_registry(user_id)
    logger.info(
        "[SKILLS] Registry reloaded — %d skill(s) (user: %s)", registry.count, user_id
    )
    return {
        "status": "reloaded",
        "count": registry.count,
        "skills": [m.name for m in registry.list_skills()],
    }


# ── Delete skill endpoint ──────────────────────────────────────────────────────

@router.delete("/{name}", response_model=DeleteByNameResponse)
async def delete_skill(name: str, auth=Depends(get_current_auth)):
    """
    Remove a skill from the registry and delete its entire directory from disk.

    Returns 404 if the skill is not registered for this user.
    Disk deletion is best-effort: failure is logged but does not block the response.
    """
    user_id = auth["user_id"]
    registry = get_skill_registry(user_id)
    meta = registry.get_skill(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    registry.remove(name)

    try:
        if meta.path.exists():
            shutil.rmtree(meta.path)
            logger.info("[SKILLS] Deleted skill directory: %s (user: %s)", meta.path, user_id)
    except Exception as exc:
        logger.warning(
            "[SKILLS] Could not delete skill directory (non-fatal): %s — %s",
            meta.path,
            exc,
        )

    return {"status": "deleted", "name": name}


# ── File-system endpoints ──────────────────────────────────────────────────────
# These endpoints treat the skill directory as a scoped mini-filesystem.
# All paths are validated to prevent traversal outside the skill directory.
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/{name}/files", response_model=SkillFilesResponse)
async def get_skill_files(name: str, auth=Depends(get_current_auth)):
    """
    Return the complete file tree for a skill.

    Directories are listed before files at each level; entries are sorted
    alphabetically within each group.  All paths are relative to the skill
    root and use forward slashes regardless of server OS.

    Example response:
        {
          "name": "sdlc-orchestrator",
          "skill_dir": "/home/user/eva_workspace/.../skills/sdlc-orchestrator",
          "files": [
            {"path": "SKILL.md", "type": "file", "size": 1240, "children": null},
            {"path": "phases", "type": "directory", "size": null, "children": [
              {"path": "phases/00-preflight.md", "type": "file", "size": 890, "children": null}
            ]},
            ...
          ]
        }
    """
    user_id = auth["user_id"]
    registry = get_skill_registry(user_id)
    meta = registry.get_skill(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    skill_dir = meta.path
    if not skill_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Skill directory not found on disk: {skill_dir}",
        )

    tree = _build_file_tree(skill_dir, skill_dir)
    return {
        "name": name,
        "skill_dir": str(skill_dir),
        "files": tree,
    }


@router.get("/{name}/files/{filepath:path}", response_model=SkillFileContentResponse)
async def get_skill_file(name: str, filepath: str, auth=Depends(get_current_auth)):
    """
    Read the content of a specific file within a skill.

    *filepath* is relative to the skill root, e.g.:
      - SKILL.md
      - phases/01-planning.md
      - checklists/definition-of-done.md

    Returns 404 if the skill or file does not exist.
    Returns 400 on path traversal attempts.
    Returns 422 if the file is binary (not UTF-8 text).
    """
    user_id = auth["user_id"]
    registry = get_skill_registry(user_id)
    meta = registry.get_skill(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    target = _safe_resolve(meta.path, filepath)

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File '{filepath}' not found in skill '{name}'",
        )
    if not target.is_file():
        raise HTTPException(
            status_code=422,
            detail=f"'{filepath}' is a directory, not a file",
        )

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422,
            detail=f"File '{filepath}' is not valid UTF-8 text",
        )

    return {
        "name": name,
        "filepath": Path(filepath).as_posix(),
        "content": content,
    }


@router.put("/{name}/files/{filepath:path}", response_model=SkillFileWriteResponse)
async def write_skill_file(
    name: str,
    filepath: str,
    body: SkillFileWriteRequest,
    auth=Depends(get_current_auth),
):
    """
    Write or create a file within an existing skill.

    - Creates parent directories automatically (e.g. writing to
      `phases/new-phase.md` creates the `phases/` directory if needed).
    - If *filepath* is ``SKILL.md``, the file is saved and the skill's
      registry entry is re-parsed so metadata changes (name, description,
      allowed-tools) take effect immediately without calling /reload.
    - Returns 404 if the skill does not exist.
    - Returns 400 on path traversal attempts.

    Typical uses:
      - Edit SKILL.md (update description, allowed-tools, procedure)
      - Add a new phase file: PUT /skills/sdlc-orchestrator/files/phases/06-deploy.md
      - Edit a template: PUT /skills/sdlc-orchestrator/files/templates/report.md
    """
    user_id = auth["user_id"]
    registry = get_skill_registry(user_id)
    meta = registry.get_skill(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    target = _safe_resolve(meta.path, filepath)

    # Create parent directories as needed
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write the file
    target.write_text(body.content, encoding="utf-8")
    size = target.stat().st_size

    registry_reloaded = False
    filepath_posix = Path(filepath).as_posix()

    # If SKILL.md was edited, re-parse frontmatter and update the registry entry
    if filepath_posix == "SKILL.md":
        loader = SkillLoader(meta.path.parent)
        try:
            updated_meta = loader.parse_skill_md(target, meta.path)
            if updated_meta:
                registry.register(updated_meta)
                registry_reloaded = True
                logger.info(
                    "[SKILLS] SKILL.md updated + registry entry refreshed: %s (user: %s)",
                    name,
                    user_id,
                )
            else:
                logger.warning(
                    "[SKILLS] SKILL.md written but re-parse returned None — "
                    "registry entry NOT updated (user: %s, skill: %s)",
                    user_id,
                    name,
                )
        except Exception as exc:
            logger.warning(
                "[SKILLS] SKILL.md written but re-parse failed (non-fatal): %s", exc
            )
    else:
        logger.info(
            "[SKILLS] File written: %s in skill %s (user: %s)", filepath_posix, name, user_id
        )

    return {
        "name": name,
        "filepath": filepath_posix,
        "size": size,
        "registry_reloaded": registry_reloaded,
    }


@router.delete("/{name}/files/{filepath:path}", response_model=DeleteSkillFileResponse)
async def delete_skill_file(name: str, filepath: str, auth=Depends(get_current_auth)):
    """
    Delete a specific file within a skill.

    Constraints:
    - ``SKILL.md`` cannot be deleted — it is the skill's entry point.
      Delete the whole skill via DELETE /skills/{name} instead.
    - Directories cannot be deleted via this endpoint (only individual files).
      Delete the skill entirely and reinstall if you need to restructure.
    - Returns 404 if the skill or file does not exist.
    - Returns 400 on path traversal or if SKILL.md is targeted.
    """
    user_id = auth["user_id"]
    registry = get_skill_registry(user_id)
    meta = registry.get_skill(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    filepath_posix = Path(filepath).as_posix()
    if filepath_posix == "SKILL.md":
        raise HTTPException(
            status_code=400,
            detail=(
                "SKILL.md cannot be deleted — it is the skill entry point. "
                "Use DELETE /skills/{name} to remove the entire skill."
            ),
        )

    target = _safe_resolve(meta.path, filepath)

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File '{filepath}' not found in skill '{name}'",
        )
    if target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{filepath}' is a directory. "
                "Only individual files can be deleted via this endpoint."
            ),
        )

    target.unlink()
    logger.info(
        "[SKILLS] File deleted: %s in skill %s (user: %s)", filepath_posix, name, user_id
    )

    return {"status": "deleted", "name": name, "filepath": filepath_posix}
