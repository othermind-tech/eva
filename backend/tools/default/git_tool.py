"""
Git tool suite for EVA.

Provides eight git operations sandboxed to the user's workspace directory.
All tools follow the same ToolBase pattern as shell_tool.py, returning a
structured result dict with exit_code, stdout, and stderr.

Tools:
    git_status   (READ_ONLY)   — show working-tree status
    git_diff     (READ_ONLY)   — show changes not yet staged or staged diff
    git_log      (READ_ONLY)   — show commit history
    git_add      (MUTATING)    — stage files
    git_commit   (MUTATING)    — record staged changes as a new commit
    git_checkout (MUTATING)    — switch branch or restore files
    git_branch   (MUTATING)    — list, create, or delete branches
    git_push     (DESTRUCTIVE) — push commits to a remote repository

All write/destructive tools require Human-in-the-Loop approval when the
user's tool_approval_mode is "always_ask" or "per_conversation".
"""

import asyncio
import locale
from typing import List, Optional

from pydantic import BaseModel, Field

from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.registry import default_tool
from utils.workspace import get_user_workspace, get_conversation_dir


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _get_cwd(provided_cwd: Optional[str], execution) -> str:
    """Resolve the working directory for git commands.

    Priority:
      1. Explicit cwd argument
      2. Active project workspace root (B-051)
      3. Conversation-scoped dir
      4. User workspace root
    """
    if provided_cwd:
        return provided_cwd
    user_id = getattr(execution, "user_id", None) if execution else None
    project_workspace = getattr(execution, "project_workspace", None) if execution else None
    if project_workspace:
        return project_workspace
    conversation_id = getattr(execution, "conversation_id", None) if execution else None
    if conversation_id and user_id:
        return str(get_conversation_dir(user_id, conversation_id))
    return get_user_workspace(user_id or "unknown")


async def _run_git(cmd: List[str], cwd: str, timeout: int = 30) -> dict:
    """Execute a git command and return a structured result dict.

    B2-1.3 (2026-08-18): was a blocking subprocess.run() call, invoked
    directly from async def run() with no await -- every git operation
    (including git_push's 60s timeout) froze the entire FastAPI process,
    including all other users' requests and SSE streams, for the command's
    full duration. Now uses asyncio.create_subprocess_exec() +
    asyncio.wait_for(), matching core/shell/executor.py's ShellExecutor
    (see that module's own docstring for why a thread-wrapped blocking call
    isn't equivalent -- a real subprocess handle can be .kill()ed on
    timeout, a blocked OS thread cannot). Return-value shape is unchanged
    from the previous subprocess.run()-based implementation -- every
    caller keeps working without modification.

    Returns:
        dict with keys: cmd, cwd, exit_code, stdout, stderr
              — or on timeout/error: cmd, cwd, error, [stdout, stderr]
    """
    try:
        from utils.child_env import child_environment
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=child_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return {
            "cmd": cmd,
            "cwd": cwd,
            "error": str(exc),
        }

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        # Partial stdout/stderr captured before the kill is not recovered
        # here -- same accepted tradeoff core/shell/executor.py's own
        # timeout path documents, in exchange for actually being able to
        # terminate the process rather than leaving it running unbounded.
        return {
            "cmd": cmd,
            "cwd": cwd,
            "error": "timeout",
            "timeout_seconds": timeout,
            "stdout": "",
            "stderr": "",
        }

    _encoding = locale.getpreferredencoding(False)
    return {
        "cmd": cmd,
        "cwd": cwd,
        "exit_code": proc.returncode,
        "stdout": stdout_bytes.decode(_encoding, errors="replace"),
        "stderr": stderr_bytes.decode(_encoding, errors="replace"),
    }


# ---------------------------------------------------------------------------
# Args schemas
# ---------------------------------------------------------------------------

class GitStatusArgs(BaseModel):
    cwd: Optional[str] = Field(None, description="Working directory (defaults to the active project's folder, else this conversation's folder)")


class GitDiffArgs(BaseModel):
    cached: bool = Field(False, description="Show staged changes (--cached / --staged)")
    ref: Optional[str] = Field(None, description="Compare against this ref or commit hash")
    cwd: Optional[str] = Field(None, description="Working directory (defaults to the active project's folder, else this conversation's folder)")


class GitLogArgs(BaseModel):
    max_count: int = Field(10, description="Maximum number of commits to show", ge=1, le=200)
    oneline: bool = Field(False, description="Use compact one-line format (--oneline)")
    cwd: Optional[str] = Field(None, description="Working directory (defaults to the active project's folder, else this conversation's folder)")


class GitAddArgs(BaseModel):
    paths: Optional[List[str]] = Field(None, description="Paths to stage (relative to workspace)")
    all: bool = Field(False, description="Stage all changes including untracked files (-A)")
    cwd: Optional[str] = Field(None, description="Working directory (defaults to the active project's folder, else this conversation's folder)")


class GitCommitArgs(BaseModel):
    message: str = Field(..., description="Commit message (required)")
    author: Optional[str] = Field(
        None, description="Author override: 'Name <email>' format"
    )
    cwd: Optional[str] = Field(None, description="Working directory (defaults to the active project's folder, else this conversation's folder)")


class GitCheckoutArgs(BaseModel):
    ref: str = Field(..., description="Branch name, tag, or commit hash to checkout")
    create: bool = Field(False, description="Create a new branch with this name (-b)")
    cwd: Optional[str] = Field(None, description="Working directory (defaults to the active project's folder, else this conversation's folder)")


class GitBranchArgs(BaseModel):
    action: str = Field(
        "list",
        description="Action to perform: 'list' (show branches), 'create' (new branch), 'delete' (remove branch)",
    )
    name: Optional[str] = Field(None, description="Branch name (required for create / delete)")
    cwd: Optional[str] = Field(None, description="Working directory (defaults to the active project's folder, else this conversation's folder)")


class GitPushArgs(BaseModel):
    remote: str = Field("origin", description="Remote name to push to")
    branch: Optional[str] = Field(None, description="Branch to push (defaults to current branch)")
    force: bool = Field(
        False,
        description="Force push using --force-with-lease (safer than --force)",
    )
    cwd: Optional[str] = Field(None, description="Working directory (defaults to the active project's folder, else this conversation's folder)")


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------

@default_tool(
    name="git_status",
    description=(
        "Show the working-tree status of a git repository. "
        "Returns staged, unstaged, and untracked files. "
        "Equivalent to: git status --short"
    ),
    args_schema=GitStatusArgs,
    authority=ToolAuthority.READ_ONLY,
    category="git",
    summary="Show working-tree status: staged, unstaged, and untracked files.",
)
class GitStatusTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        cwd = _get_cwd(args.get("cwd"), getattr(self, "execution", None))
        result = await _run_git(["git", "status", "--short", "--branch"], cwd)
        return ToolResult(result=result)


@default_tool(
    name="git_diff",
    description=(
        "Show differences in a git repository. "
        "By default shows unstaged changes. "
        "Set cached=true to show staged changes. "
        "Optionally compare against a specific ref."
    ),
    args_schema=GitDiffArgs,
    authority=ToolAuthority.READ_ONLY,
    category="git",
    summary="Show file diffs — unstaged changes by default; staged with cached=true.",
)
class GitDiffTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        cwd = _get_cwd(args.get("cwd"), getattr(self, "execution", None))
        cmd = ["git", "diff"]
        if args.get("cached"):
            cmd.append("--cached")
        if ref := args.get("ref"):
            cmd.append(ref)
        result = await _run_git(cmd, cwd)
        return ToolResult(result=result)


@default_tool(
    name="git_log",
    description=(
        "Show the commit history of a git repository. "
        "Returns up to max_count commits. "
        "Use oneline=true for compact output."
    ),
    args_schema=GitLogArgs,
    authority=ToolAuthority.READ_ONLY,
    category="git",
    summary="Show commit history with messages, authors, and timestamps.",
)
class GitLogTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        cwd = _get_cwd(args.get("cwd"), getattr(self, "execution", None))
        cmd = ["git", "log", f"-n{args.get('max_count', 10)}"]
        if args.get("oneline"):
            cmd.append("--oneline")
        result = await _run_git(cmd, cwd)
        return ToolResult(result=result)


@default_tool(
    name="git_add",
    description=(
        "Stage changes for the next commit. "
        "Provide a list of paths to stage specific files, "
        "or set all=true to stage everything (git add -A)."
    ),
    args_schema=GitAddArgs,
    authority=ToolAuthority.MUTATING,
    category="git",
    summary="Stage files for the next commit; use all=true to stage everything.",
)
class GitAddTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        cwd = _get_cwd(args.get("cwd"), getattr(self, "execution", None))
        cmd = ["git", "add"]
        if args.get("all"):
            cmd.append("-A")
        else:
            paths = args.get("paths") or []
            if not paths:
                return ToolResult(result={"error": "Provide at least one path or set all=true."})
            cmd.extend(paths)
        result = await _run_git(cmd, cwd)
        return ToolResult(result=result)


@default_tool(
    name="git_commit",
    description=(
        "Record staged changes as a new commit. "
        "Requires a non-empty commit message. "
        "Optionally set a custom author ('Name <email>')."
    ),
    args_schema=GitCommitArgs,
    authority=ToolAuthority.MUTATING,
    category="git",
    summary="Create a commit from staged changes with a message.",
)
class GitCommitTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        cwd = _get_cwd(args.get("cwd"), getattr(self, "execution", None))
        message = args.get("message", "").strip()
        if not message:
            return ToolResult(result={"error": "Commit message is required."})
        cmd = ["git", "commit", "-m", message]
        if author := args.get("author"):
            cmd.extend(["--author", author])
        result = await _run_git(cmd, cwd)
        return ToolResult(result=result)


@default_tool(
    name="git_checkout",
    description=(
        "Switch to a branch, tag, or commit — or create a new branch. "
        "Set create=true to create a new branch (-b). "
        "WARNING: uncommitted changes may be lost."
    ),
    args_schema=GitCheckoutArgs,
    authority=ToolAuthority.MUTATING,
    category="git",
    summary="Switch to a branch, tag, or commit; create=true makes a new branch.",
)
class GitCheckoutTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        cwd = _get_cwd(args.get("cwd"), getattr(self, "execution", None))
        ref = args.get("ref", "").strip()
        if not ref:
            return ToolResult(result={"error": "ref (branch, tag, or commit) is required."})
        cmd = ["git", "checkout"]
        if args.get("create"):
            cmd.append("-b")
        cmd.append(ref)
        result = await _run_git(cmd, cwd)
        return ToolResult(result=result)


@default_tool(
    name="git_branch",
    description=(
        "Manage git branches. "
        "action='list' — show all local and remote branches. "
        "action='create' — create a new branch (requires name). "
        "action='delete' — delete a branch (requires name; branch must be merged)."
    ),
    args_schema=GitBranchArgs,
    authority=ToolAuthority.MUTATING,
    category="git",
    summary="List, create, or delete branches (action: list | create | delete).",
)
class GitBranchTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        cwd = _get_cwd(args.get("cwd"), getattr(self, "execution", None))
        action = args.get("action", "list")

        if action == "list":
            cmd = ["git", "branch", "-a"]
        elif action == "create":
            name = (args.get("name") or "").strip()
            if not name:
                return ToolResult(result={"error": "Branch name is required for action='create'."})
            cmd = ["git", "branch", name]
        elif action == "delete":
            name = (args.get("name") or "").strip()
            if not name:
                return ToolResult(result={"error": "Branch name is required for action='delete'."})
            cmd = ["git", "branch", "-d", name]
        else:
            return ToolResult(
                result={"error": f"Unknown action '{action}'. Use: list, create, or delete."}
            )

        result = await _run_git(cmd, cwd)
        return ToolResult(result=result)


@default_tool(
    name="git_push",
    description=(
        "Push local commits to a remote repository. "
        "Defaults to 'origin'. "
        "Set force=true to use --force-with-lease (safer than --force). "
        "WARNING: this modifies the remote repository."
    ),
    args_schema=GitPushArgs,
    authority=ToolAuthority.DESTRUCTIVE,
    category="git",
    summary="Push local commits to a remote; force=true uses --force-with-lease.",
)
class GitPushTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        cwd = _get_cwd(args.get("cwd"), getattr(self, "execution", None))
        remote = args.get("remote", "origin") or "origin"
        cmd = ["git", "push"]
        if args.get("force"):
            cmd.append("--force-with-lease")
        cmd.append(remote)
        if branch := args.get("branch"):
            cmd.append(branch.strip())
        result = await _run_git(cmd, cwd, timeout=60)   # push can take longer
        return ToolResult(result=result)
