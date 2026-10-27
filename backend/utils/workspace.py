import json as _json
import os
import pathlib
import shutil
import time
from typing import Dict, List, Optional

from config.settings import WORKSPACE_ROOT


# ---------------------------------------------------------------------------
# Root helpers
# ---------------------------------------------------------------------------

def get_user_root(user_id: str) -> pathlib.Path:
    """
    ~/eva_workspace/{user_id}/ — root of all user-owned data.
    Creates the directory on first call.
    """
    root = pathlib.Path(WORKSPACE_ROOT) / str(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Sub-directories
# ---------------------------------------------------------------------------

def get_user_workspace(user_id: str) -> str:
    """
    ~/eva_workspace/{user_id}/workspace/ — free-form file sandbox.
    Default CWD for shell execution and file tools.
    Returns a string (matches original API).
    """
    ws = get_user_root(user_id) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return str(ws)


def get_user_projects_root(user_id: str) -> pathlib.Path:
    """~/eva_workspace/{user_id}/projects/ — parent of all project dirs."""
    p = get_user_root(user_id) / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_conversation_dir(
    user_id: str,
    conversation_id: str,
    project_id: Optional[str] = None,
) -> pathlib.Path:
    """
    Return the conversation's working directory.

    When ``project_id`` is explicitly provided the project-scoped path is used:
        ~/eva_workspace/{user_id}/projects/{project_id}/conversations/{conversation_id}/

    Otherwise the standard path is used:
        ~/eva_workspace/{user_id}/conversations/{conversation_id}/

    If the conversation was previously promoted via
    ``move_conversation_workspace_to_project()``, a ``.project_link`` redirect
    file is present at the standard location.  This function reads it and
    automatically returns the project-scoped path so that all callers (journal,
    shell executor, git tool) transparently resolve the correct directory after
    promotion without needing to be updated individually.
    """
    if project_id:
        p = get_user_root(user_id) / "projects" / project_id / "conversations" / conversation_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Check for a promotion redirect at the standard location
    standard = get_user_root(user_id) / "conversations" / conversation_id
    link_file = standard / ".project_link"
    if standard.exists() and link_file.exists():
        try:
            data = _json.loads(link_file.read_text(encoding="utf-8"))
            pid = data.get("project_id")
            if pid:
                p = get_user_root(user_id) / "projects" / pid / "conversations" / conversation_id
                p.mkdir(parents=True, exist_ok=True)
                return p
        except Exception:
            pass  # corrupt redirect — fall through to standard path

    standard.mkdir(parents=True, exist_ok=True)
    return standard


def get_project_dir(user_id: str, project_id: str) -> pathlib.Path:
    """~/eva_workspace/{user_id}/projects/{project_id}/"""
    p = get_user_projects_root(user_id) / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_project_knowledge_dir(user_id: str, project_id: str) -> pathlib.Path:
    """
    ~/eva_workspace/{user_id}/projects/{project_id}/knowledge/

    Stores RAG-indexed document blobs uploaded via POST /projects/{id}/knowledge.
    Kept separate from files/ (workspace blobs) so the two are never conflated.
    """
    p = get_project_dir(user_id, project_id) / "knowledge"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_rag_dir(user_id: str) -> pathlib.Path:
    """~/eva_workspace/{user_id}/rag/ — RAG document blobs."""
    p = get_user_root(user_id) / "rag"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_memory_dir(user_id: str) -> pathlib.Path:
    """~/eva_workspace/{user_id}/memory/ — user fact summaries."""
    p = get_user_root(user_id) / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_skills_dir(user_id: str) -> pathlib.Path:
    """~/eva_workspace/{user_id}/skills/ — user's personal skill library."""
    p = get_user_root(user_id) / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_hooks_dir(user_id: str) -> pathlib.Path:
    """~/eva_workspace/{user_id}/hooks/ — user-uploaded hook scripts (B-064)."""
    p = get_user_root(user_id) / "hooks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_subagent_journal_dir(user_id: str, conversation_id: str) -> pathlib.Path:
    """
    ~/eva_workspace/{user_id}/conversations/{conversation_id}/subagents/

    One JSONL file per subagent: {agent_id}.jsonl
    Isolated from parent conversation journal (Option B) so large subagent
    output doesn't pollute the parent's readable timeline.
    """
    p = get_conversation_dir(user_id, conversation_id) / "subagents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_files_dir(user_id: str, project_id: Optional[str] = None) -> pathlib.Path:
    """
    Raw file/image library — non-RAG attachments (B-066).

    When project_id is set, files are scoped to the project workspace:
        ~/eva_workspace/{user_id}/projects/{project_id}/files/

    Otherwise falls back to the user-global library:
        ~/eva_workspace/{user_id}/files/
    """
    if project_id:
        p = get_project_dir(user_id, project_id) / "files"
    else:
        p = get_user_root(user_id) / "files"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Workspace lifecycle helpers (B-075)
# ---------------------------------------------------------------------------

def find_stale_conversation_dirs(user_id: str, ttl_days: int) -> List[str]:
    """
    Return a list of conversation IDs whose scratch dir has not been accessed
    (i.e. no file mtime newer than ttl_days ago) and is therefore eligible for
    deletion by the daily cleanup task.

    Only considers ~/eva_workspace/{user_id}/conversations/{conv_id}/ subdirs.
    Never touches workspace/, projects/, rag/, memory/, skills/, or hooks/.
    """
    conv_root = get_user_root(user_id) / "conversations"
    if not conv_root.exists():
        return []

    cutoff = time.time() - (ttl_days * 86400)
    stale: List[str] = []

    for entry in conv_root.iterdir():
        if not entry.is_dir():
            continue
        # Scan for the most-recent file mtime inside the dir
        latest_mtime = 0.0
        for p in entry.rglob("*"):
            if p.is_file():
                try:
                    latest_mtime = max(latest_mtime, p.stat().st_mtime)
                except OSError:
                    pass
        # Fall back to the directory's own mtime when it contains no files
        if latest_mtime == 0.0:
            try:
                latest_mtime = entry.stat().st_mtime
            except OSError:
                continue
        if latest_mtime < cutoff:
            stale.append(entry.name)

    return stale


def delete_conversation_workspace(user_id: str, conversation_id: str) -> int:
    """
    Delete the scratch directory for a single conversation.

    If the conversation was previously promoted (a ``.project_link`` redirect
    exists at the standard path), the project-scoped directory is also deleted.

    Returns the number of bytes freed (0 if no directories existed).
    Uses ignore_errors=True — a partial delete is acceptable; the cleanup task
    will retry on the next daily run.
    """
    standard = get_user_root(user_id) / "conversations" / conversation_id
    freed = 0

    # Handle promoted conversations — delete both the project-scoped dir and
    # the redirect stub at the standard path.
    link_file = standard / ".project_link"
    if standard.exists() and link_file.exists():
        try:
            data = _json.loads(link_file.read_text(encoding="utf-8"))
            pid = data.get("project_id")
            if pid:
                promoted_dir = (
                    get_user_root(user_id) / "projects" / pid / "conversations" / conversation_id
                )
                if promoted_dir.exists():
                    freed += sum(
                        p.stat().st_size
                        for p in promoted_dir.rglob("*")
                        if p.is_file()
                    )
                    shutil.rmtree(promoted_dir, ignore_errors=True)
        except Exception:
            pass
        # Remove the redirect stub directory
        shutil.rmtree(standard, ignore_errors=True)
        return freed

    if not standard.exists():
        return 0

    freed = sum(p.stat().st_size for p in standard.rglob("*") if p.is_file())
    shutil.rmtree(standard, ignore_errors=True)
    return freed


def move_conversation_workspace_to_project(
    user_id: str,
    conversation_id: str,
    project_id: str,
) -> Dict[str, object]:
    """
    Physically move the conversation workspace from the standard location to the
    project-scoped location:

        conversations/{conv_id}/   →   projects/{project_id}/conversations/{conv_id}/

    A ``.project_link`` redirect file is left at the standard location so that
    ``get_conversation_dir()`` — and therefore every caller (journal, shell,
    git tool) — transparently resolves the new path without requiring changes.

    Safe to call when no workspace directory exists yet (new conversation that
    has never had any file activity): the project-scoped directory is simply
    created so future writes land in the right place.

    Returns a dict with keys:
        moved        — True (always; idempotent)
        files_moved  — number of entries relocated
        old_path     — absolute string of the standard conversations/ dir
        new_path     — absolute string of the new project-scoped dir
    """
    old_path = get_user_root(user_id) / "conversations" / conversation_id
    new_path = (
        get_user_root(user_id) / "projects" / project_id / "conversations" / conversation_id
    )
    new_path.mkdir(parents=True, exist_ok=True)

    files_moved = 0
    if old_path.exists() and old_path.is_dir():
        for item in list(old_path.iterdir()):
            if item.name == ".project_link":
                continue  # skip any existing redirect stub
            target = new_path / item.name
            try:
                shutil.move(str(item), str(target))
                files_moved += 1
            except (PermissionError, OSError):
                # On Windows a file may be locked by another process (e.g. the
                # execution worker writing conversation.jsonl).  Fall back to
                # copy-then-delete: copy2 opens the source read-only which
                # Windows allows even when the file is open for appending.
                try:
                    shutil.copy2(str(item), str(target))
                    files_moved += 1
                    try:
                        item.unlink()
                    except (PermissionError, OSError):
                        pass  # leave original in place; redirect stub handles future access
                except Exception:
                    pass  # cannot read either — skip this file entirely

        # Write the redirect stub so get_conversation_dir() finds the new path
        (old_path / ".project_link").write_text(
            _json.dumps({"project_id": project_id}),
            encoding="utf-8",
        )
    else:
        # No existing workspace — create stub so future callers redirect correctly
        old_path.mkdir(parents=True, exist_ok=True)
        (old_path / ".project_link").write_text(
            _json.dumps({"project_id": project_id}),
            encoding="utf-8",
        )

    return {
        "moved": True,
        "files_moved": files_moved,
        "old_path": str(old_path),
        "new_path": str(new_path),
    }


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_path_for_user(
    user_id: str,
    path: str,
    conversation_id: Optional[str] = None,
    project_workspace: Optional[str] = None,
) -> str:
    """
    Convert a relative path into an absolute path.

    Resolution order for relative paths:
      1. project_workspace (active project root) — when a project is active
      2. conversation_id dir — conversation-scoped scratch space
      3. user workspace root — legacy fallback

    Absolute paths are returned as-is.
    """
    if os.path.isabs(path):
        return path
    if project_workspace:
        return os.path.join(project_workspace, path)
    if conversation_id:
        base = str(get_conversation_dir(user_id, conversation_id))
    else:
        base = get_user_workspace(user_id)
    return os.path.join(base, path)


def is_path_in_workspace(
    user_id: str,
    path: str,
    conversation_id: Optional[str] = None,
    project_workspace: Optional[str] = None,
) -> bool:
    """
    True if `path`, resolved exactly as resolve_path_for_user() would resolve
    it, falls inside the applicable workspace root for this call (same
    3-tier priority: project_workspace > conversation dir > user workspace
    root). Handles absolute paths correctly too -- an absolute path that
    happens to land inside the root still counts as "in workspace".

    Used by core/tools/dispatcher.py's out-of-workspace approval gate
    (2026-08-18, B2-1.1):
    MUTATING/DESTRUCTIVE file-tool calls targeting a path outside this
    boundary get gated for human approval regardless of tool_approval_mode.

    Deliberately does not restrict what resolve_path_for_user() itself is
    allowed to resolve to -- EVA is meant to manage files anywhere on the
    user's machine, not just a sandboxed workspace, by design. This
    function only classifies in/out for the approval gate to act on.

    Fails closed: any resolution error (permission issue, malformed path,
    etc.) returns False ("outside") so an ambiguous path never silently
    skips the gate.
    """
    try:
        resolved = pathlib.Path(
            resolve_path_for_user(user_id, path, conversation_id, project_workspace)
        ).resolve()
    except Exception:
        return False

    if project_workspace:
        root = project_workspace
    elif conversation_id:
        root = str(get_conversation_dir(user_id, conversation_id))
    else:
        root = get_user_workspace(user_id)

    try:
        root_resolved = pathlib.Path(root).resolve()
        return resolved.is_relative_to(root_resolved)
    except Exception:
        return False
