# tools/default/shell_tool.py
"""
Shell tool — B-049 (Option A+E).

Enhancements over the original bare subprocess.run():

  Option A — Stateful shell session
    cwd and environment variables persist across calls within a conversation.
    The agent no longer needs to pass --cwd on every invocation; once it
    `cd`s into a directory or `export`s a variable, subsequent calls
    automatically inherit that state.

  Option E — Long-running process note
    Long-running background processes (dev servers, slow builds, test watchers)
    must be started via the background_process tool, NOT via this tool.
    This tool is for synchronous, time-bounded commands and always waits for
    the command to exit (or kills it at the timeout).  The LLM is informed
    of this boundary in the description below.  (It used to point at
    trigger_daemon(), which cannot help: a daemon calls this same tool.)

Special commands handled WITHOUT spawning a subprocess:
  cd [dir]           — resolve and persist new cwd
  export VAR=value   — persist env var for this session
  unset VAR          — remove env var from session

bash -c "..." commands get state-capture sentinels injected so that
virtual-environment activation and directory changes inside the script
are tracked correctly.

Extra actions (action arg):
  "run"            — default: run the command
  "session"        — return current session state (cwd, env_vars, history)
  "reset_session"  — clear all session state and return confirmation
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field

from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.registry import default_tool
from core.shell.executor import ShellExecutor
from core.shell.safety import ShellSafety, SafetyAction
from config.settings import SHELL_MAX_TIMEOUT_SECONDS


class ShellArgs(BaseModel):
    cmd: Optional[List[str]] = None
    cwd: Optional[str] = None           # explicit cwd override (optional)
    # V6.15 (S-09): upper bound now actually enforced (previously the field
    # had no `le=` at all, so a tool call could request an effectively
    # unbounded timeout -- see SHELL_MAX_TIMEOUT_SECONDS in config/settings.py).
    timeout: Optional[int] = Field(default=30, le=SHELL_MAX_TIMEOUT_SECONDS)
    action: Optional[Literal["run", "session", "reset_session"]] = "run"


@default_tool(
    name="shell",
    description=(
        "Run a finite shell command and wait for its result -- tests, installs, "
        "builds, git, scripts. "
        "`cmd` is a list of arguments, e.g. [\"python\", \"-m\", \"pytest\"]. "
        "cwd and environment variables persist across calls — use `cd [dir]` "
        "to change directory and `export VAR=value` to set env vars; "
        "both update the session without spawning a subprocess. "
        "Returns stdout, stderr, exit_code, and current session state. "
        "The default timeout is 30 seconds; pass `timeout` (max 300) for slower "
        "commands such as installs and builds -- the command is killed when it expires. "
        "Do NOT use this tool for anything that never exits or that may run longer "
        "than 300 seconds -- for example `npm run dev`, `vite`, `flask run`, "
        "`python -m http.server`, `pytest-watch` or any --watch mode, `tail -f`, "
        "`docker compose up`: use background_process (action='start') for those, "
        "which returns immediately and keeps the process running. "
        "action='session' returns current cwd/env without running anything. "
        "action='reset_session' clears all session state."
    ),
    args_schema=ShellArgs,
    authority=ToolAuthority.MUTATING,
    category="shell",
    summary="Execute a shell command with persistent session state (cwd/env across calls).",
)
class ShellTool(ToolBase):

    async def run(self, args: dict) -> ToolResult:
        action  = args.get("action") or "run"
        cmd     = args.get("cmd")
        cwd     = args.get("cwd")
        timeout = args.get("timeout") or 30

        conversation_id = getattr(self.execution, "conversation_id", "default")
        user_id         = getattr(self.execution, "user_id", "default")

        # ── action: session ─────────────────────────────────────────────
        if action == "session":
            state = await ShellExecutor.get_session(conversation_id)
            return ToolResult(result={"session": state})

        # ── action: reset_session ────────────────────────────────────────
        if action == "reset_session":
            await ShellExecutor.reset_session(conversation_id)
            return ToolResult(result={
                "reset": True,
                "message": "Shell session cleared — cwd and env vars reset.",
            })

        # ── action: run ──────────────────────────────────────────────────
        if not cmd or not isinstance(cmd, list):
            return ToolResult(result={"error": "cmd must be a non-empty list of strings."})

        project_workspace = getattr(self.execution, "project_workspace", None)

        # ── Safety check (B-052) ─────────────────────────────────────────
        from config.settings import get_user_session
        from utils.workspace import get_user_workspace
        session          = get_user_session(user_id)
        tool_approval    = session.get("tool_approval_mode", "auto")
        shell_readonly   = bool(session.get("shell_readonly", False))
        workspace_root   = project_workspace or get_user_workspace(user_id)

        safety = ShellSafety.check(
            cmd=cmd,
            workspace_root=workspace_root,
            tool_approval_mode=tool_approval,
            shell_readonly=shell_readonly,
        )

        if safety.action == SafetyAction.BLOCK:
            return ToolResult(result={
                "error":          f"[EVA SHELL SAFETY] {safety.reason}",
                "classification": safety.classification.value,
                "blocked":        True,
                "cmd":            cmd,
            })

        result = await ShellExecutor.run(
            cmd=cmd,
            conversation_id=conversation_id,
            user_id=user_id,
            explicit_cwd=cwd,
            timeout=timeout,
            project_workspace=project_workspace,
            # V6.15 (S-09): lets ShellExecutor actually terminate this
            # command if the user cancels while it's running.
            cancel_token=getattr(self.execution, "cancel_token", None),
        )

        # Prepend safety warning to stdout when action == WARN
        if safety.action == SafetyAction.WARN and safety.warning:
            result["stdout"] = safety.warning + "\n" + result.get("stdout", "")
            result["safety_warning"] = safety.warning

        return ToolResult(result=result)
