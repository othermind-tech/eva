# tools/default/process_tools.py
"""
background_process tool (2026-08-18).

The strategic gap this closes: the shell tool (shell_tool.py) always waits
for a command to exit or kills it at SHELL_MAX_TIMEOUT_SECONDS (5 min
default) -- there was no way to start a dev server, leave it running, and
check on it later. trigger_daemon() does not solve this either: a daemon's
own tool calls go through this exact same shell tool.

This tool is the sanctioned, tracked alternative: action="start" spawns a
genuinely detached process (its own process group, output redirected to a
log file) and returns immediately; action="list"/"tail"/"stop" manage it
afterward. Every process is recorded in the BackgroundProcess DB table
(core/db/models.py) via ProcessRepo -- not in-memory -- so it survives a
backend restart's bookkeeping (though not an actual machine reboot).

Processes are force-stopped after BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS
by the daily cleanup sweep (core/tasks/cleanup.py) even if nobody ever
calls action="stop" -- a background process is not "fire and forget
forever."
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.registry import default_tool
from core.shell.executor import ShellExecutor
from core.shell.safety import ShellSafety, SafetyAction


# Well inside the dispatcher's per-tool timeout (TOOL_EXECUTION_TIMEOUT_SECONDS, 300 s default).
MAX_TAIL_WAIT_SECONDS = 60
_TAIL_POLL_INTERVAL_SECONDS = 0.5


class BackgroundProcessArgs(BaseModel):
    action: Literal["start", "list", "tail", "stop"] = Field(
        description=(
            "'start' launches a new detached process (requires `command`). "
            "'list' shows your currently-running background processes. "
            "'tail' returns new output since the last tail call for one "
            "process (requires `process_id`). "
            "'stop' kills a process and its child processes (requires "
            "`process_id`)."
        )
    )
    command: Optional[List[str]] = Field(
        default=None,
        description=(
            "Only for action='start'. Argument list, e.g. "
            "[\"npm\", \"run\", \"dev\"]. Do not use shell operators like "
            "&, |, or > here -- this runs the command directly, not through "
            "a shell."
        ),
    )
    cwd: Optional[str] = Field(
        default=None,
        description=(
            "Only for action='start'. A relative path resolves like the file "
            "tools' paths (active project's folder, else this conversation's folder)."
        ),
    )
    process_id: Optional[str] = Field(
        default=None, description="Only for action='tail' or action='stop'."
    )
    wait_seconds: Optional[int] = Field(
        default=0,
        ge=0,
        le=MAX_TAIL_WAIT_SECONDS,
        description=(
            "Only for action='tail'. Wait up to this many seconds (max "
            f"{MAX_TAIL_WAIT_SECONDS}) for new output or for the process to exit, "
            "returning as soon as either happens. 0 (default) returns immediately. "
            "Use it to wait for a build, an install or a server to become ready "
            "instead of polling tail repeatedly."
        ),
    )
    keep_alive_on_app_shutdown: bool = Field(
        default=False,
        description=(
            "Only for action='start'. False (default): this process is stopped "
            "automatically when the user quits EVA, same as any other background "
            "process. Set True only when the process should deliberately keep "
            "running after EVA closes -- e.g. a dev server the user explicitly "
            "asked to leave running. The user can also change this later from "
            "the Activity panel, so default to False unless there's a clear "
            "reason this specific process should outlive the app."
        ),
    )


@default_tool(
    name="background_process",
    description=(
        "Start, list, tail, or stop a long-running background process -- anything "
        "that never exits on its own or may run longer than the shell tool's 300 "
        "second limit: `npm run dev`, `vite`, `flask run`, `python -m http.server`, "
        "a --watch mode or `pytest-watch`, `tail -f`, `docker compose up`. Unlike the "
        "shell tool (which waits for the command to finish), this returns immediately "
        "and the process keeps running independently. It OUTLIVES this run: it is not stopped when the run "
        "ends, pauses or is cancelled -- stop it yourself when you are done, unless "
        "the user should keep using it (for example a dev server they want to open). "
        "The user can also stop it from the Activity panel. "
        "By default it does NOT outlive the app itself -- EVA stops every background "
        "process automatically when the user quits, same as any normal foreground work. "
        "Set keep_alive_on_app_shutdown=true only when this specific process should "
        "deliberately keep running after EVA closes; the user can also toggle this later "
        "from the Activity panel. "
        "action='start' returns a process_id immediately (`command` is an argument "
        "list run directly, no shell operators). Starting the same command in the same "
        "folder again does not start a second copy: it returns the one already running, "
        "marked already_running=true. "
        "action='tail' returns output since your last tail call plus whether the "
        "process is still alive; set wait_seconds (max 60) to block until new output "
        "arrives or the process exits, instead of polling repeatedly. "
        "action='list' shows every background process you currently have running. "
        "action='stop' kills the process and any processes it spawned. "
        "Background processes are also force-stopped automatically after several hours."
    ),
    args_schema=BackgroundProcessArgs,
    authority=ToolAuthority.MUTATING,
    category="shell",
    summary="Start/list/tail/stop a long-running background process (dev server, watch build) that outlives this tool call.",
)
class BackgroundProcessTool(ToolBase):

    @staticmethod
    async def _find_live_duplicate(*, user_id, command, explicit_cwd, conversation_id, project_workspace):
        """A still-alive background process of this user with the same command in the same folder."""
        import os

        from core.db.repository import ProcessRepo
        from core.db.session import AsyncSessionLocal
        from core.shell.executor import _pid_matches_process

        def norm(path):
            return os.path.normcase(os.path.normpath(str(path))) if path else ""

        wanted_cwd = norm(ShellExecutor._resolve_cwd(
            explicit=explicit_cwd,
            session_cwd="",
            user_id=user_id,
            conversation_id=conversation_id,
            project_workspace=project_workspace,
        ))
        async with AsyncSessionLocal() as db:
            rows = await ProcessRepo.list_running_for_user(db, user_id)
        for row in rows:
            # Identity check (2026-09-29), not just liveness -- a stale row
            # whose PID has since been reused by an unrelated process must
            # not be treated as "this exact command is already running".
            if (
                list(row.command or []) == list(command)
                and norm(row.cwd) == wanted_cwd
                and _pid_matches_process(row.pid, row.pid_create_time)
            ):
                return row
        return None

    async def run(self, args: dict) -> ToolResult:
        parsed = BackgroundProcessArgs(**args)
        action = parsed.action

        user_id = getattr(self.execution, "user_id", "default")
        conversation_id = getattr(self.execution, "conversation_id", None)
        project_id = getattr(self.execution, "parent_project_id", None)
        execution_id = getattr(self.execution, "execution_id", None)
        project_workspace = getattr(self.execution, "project_workspace", None)

        if action == "start":
            if not parsed.command:
                return ToolResult(result={"error": "command is required for action='start'."})

            from config.settings import get_user_session
            from utils.workspace import get_user_workspace

            session = get_user_session(user_id)
            tool_approval = session.get("tool_approval_mode", "auto")
            shell_readonly = bool(session.get("shell_readonly", False))
            workspace_root = project_workspace or get_user_workspace(user_id)

            safety = ShellSafety.check(
                cmd=parsed.command,
                workspace_root=workspace_root,
                tool_approval_mode=tool_approval,
                shell_readonly=shell_readonly,
            )
            if safety.action == SafetyAction.BLOCK:
                return ToolResult(result={
                    "error": f"[EVA SHELL SAFETY] {safety.reason}",
                    "classification": safety.classification.value,
                    "blocked": True,
                    "cmd": parsed.command,
                })

            # A model that loses track of what it started (or retries after a confusing result)
            # will issue the same start again. A second copy of a server can never bind its port,
            # only leaves one more row to clean up -- seen live: eight starts of one server, four
            # rows still "running" at the end. Reuse the live one instead.
            existing = await self._find_live_duplicate(
                user_id=user_id,
                command=parsed.command,
                explicit_cwd=parsed.cwd,
                conversation_id=conversation_id,
                project_workspace=project_workspace,
            )
            if existing is not None:
                return ToolResult(result={
                    "process_id": existing.id,
                    "pid": existing.pid,
                    "log_path": existing.log_path,
                    "cwd": existing.cwd,
                    "status": "running",
                    "already_running": True,
                    "keep_alive_on_app_shutdown": existing.keep_alive_on_app_shutdown,
                    "note": (
                        "This exact command is already running in this folder, so no new process "
                        "was started -- this is the existing one. Use action='tail' to read its "
                        "output or action='stop' to end it."
                    ),
                })

            result = await ShellExecutor.start_background(
                cmd=parsed.command,
                user_id=user_id,
                conversation_id=conversation_id,
                project_id=project_id,
                execution_id=execution_id,
                explicit_cwd=parsed.cwd,
                project_workspace=project_workspace,
                keep_alive_on_app_shutdown=parsed.keep_alive_on_app_shutdown,
            )
            return ToolResult(result=result)

        if action == "list":
            from core.db.session import AsyncSessionLocal
            from core.db.repository import ProcessRepo

            async with AsyncSessionLocal() as db:
                rows = await ProcessRepo.list_running_for_user(db, user_id)

            return ToolResult(result={
                "processes": [
                    {
                        "process_id": r.id,
                        "pid": r.pid,
                        "command": r.command,
                        "cwd": r.cwd,
                        "status": r.status,
                        "started_at": r.started_at.isoformat() if r.started_at else None,
                        "keep_alive_on_app_shutdown": r.keep_alive_on_app_shutdown,
                    }
                    for r in rows
                ]
            })

        if action == "tail":
            if not parsed.process_id:
                return ToolResult(result={"error": "process_id is required for action='tail'."})
            wait = parsed.wait_seconds or 0
            started = time.monotonic()
            while True:
                result = await ShellExecutor.tail_background(parsed.process_id, user_id)
                elapsed = time.monotonic() - started
                if (
                    wait <= 0
                    or "error" in result
                    or result.get("new_output")
                    or result.get("status") != "running"
                    or elapsed >= wait
                ):
                    break
                await asyncio.sleep(min(_TAIL_POLL_INTERVAL_SECONDS, max(wait - elapsed, 0.05)))
            if wait > 0 and "error" not in result:
                result["waited_seconds"] = round(time.monotonic() - started, 1)
            return ToolResult(result=result)

        if action == "stop":
            if not parsed.process_id:
                return ToolResult(result={"error": "process_id is required for action='stop'."})
            result = await ShellExecutor.stop_background(parsed.process_id, user_id)
            return ToolResult(result=result)

        return ToolResult(result={"error": f"Unknown action: {action!r}"})
