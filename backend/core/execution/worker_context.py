# core/execution/worker_context.py
#
# Q-01 Phase 4 (worker.py decomposition).
# Flat sibling of
# worker.py, matching the convention already used by every other file in
# core/execution/ (no subdirectories anywhere in this package). Holds the
# pure, stateless helpers _process_execution's prompt-assembly phase calls
# -- none of these touch `self`, the DB, or the broker; they're plain
# string-building functions with no shared-state risk, which is exactly why
# this was the first (and lowest-risk) piece pulled out of worker.py.
#
# worker.py keeps a re-export shim (`from core.execution.worker_context
# import _build_manifest, _detect_shell, _build_environment_context`) so
# every existing `patch("core.execution.worker._build_manifest", ...)` /
# `from core.execution.worker import _build_manifest` test-patch target and
# import site keeps resolving unchanged.

import os
import platform
import sys
from datetime import datetime, timezone
from typing import Optional


def _build_manifest(tools: dict, mode: str) -> str:
    """
    Build a lightweight manifest string for the system prompt.

    Lists each tool as one line: • name (category): summary
    This replaces sending the full JSON schemas to the LLM per turn in
    progressive disclosure mode — the model reads the manifest and calls
    describe_tool(name) to get the full schema and activate the tool.

    Parameters
    ----------
    tools : dict
        {name: ToolClass} — the filtered set available for this mode.
    mode  : str
        Execution mode (used for logging only; filtering is done upstream).

    Returns
    -------
    str  Formatted manifest block ready for system prompt injection.
    """
    if not tools:
        return ""

    lines = []
    # Group by category for readability.
    by_category: dict = {}
    for name, cls in tools.items():
        cat = getattr(cls, "category", "general")
        by_category.setdefault(cat, []).append((name, cls))

    for cat in sorted(by_category.keys()):
        for name, cls in sorted(by_category[cat]):
            summary = (
                getattr(cls, "summary", None)
                or (cls.description[:80].rstrip() + "…" if len(cls.description) > 80 else cls.description)
            )
            lines.append(f"  • {name} [{cat}]: {summary}")

    header = (
        "## Available Tools\n\n"
        "You have access to the following tools to complete tasks accurately. "
        "Each entry shows the tool name and what it does.\n\n"
        "A tool that is not yet active needs `describe_tool(\"name\")` first — using the "
        "exact name from the list below — which returns its parameters and activates it for "
        "your next turn. Tools you have already described or used in this run are active: "
        "call them directly; describing an active tool again only wastes a turn.\n"
    )
    return header + "\n".join(lines)


# ---------------------------------------------------------------------------
# _build_environment_context  (V5.5)
# ---------------------------------------------------------------------------
# Builds a compact "## 🖥 Environment" block injected into every system
# prompt at execution time.  Two categories of data:
#
#   Auto-detected (server-side, zero config):
#     • Current date & time  — computed fresh on every execution
#     • OS name & version    — platform.system() / platform.release()
#     • Shell type           — detected from environment variables
#     • Python version       — sys.version_info
#
#   User-configured (from session, stored in user_preferences):
#     • Timezone   — IANA string (e.g. "Asia/Kolkata"); used to convert UTC
#                    to local time.  Falls back to UTC if unset.
#     • Location   — free text (e.g. "Mumbai, India"); omitted if not set.
#     • Language   — BCP-47 tag (e.g. "en-IN"); omitted if not set.
#
# The block is always non-empty (date/time + OS are always present) so the
# injection code can rely on a truthy return value.
# ---------------------------------------------------------------------------

def _detect_shell() -> str:
    """Return a short shell name for the current process environment."""
    # Unix: $SHELL env var is set by the parent shell (bash, zsh, fish…)
    unix_shell = os.environ.get("SHELL", "")
    if unix_shell:
        return unix_shell.split("/")[-1]          # /bin/bash → bash
    # Windows: PSModulePath is set by PowerShell; COMSPEC points to cmd.exe
    if "PSModulePath" in os.environ:
        return "PowerShell"
    comspec = os.environ.get("COMSPEC", "")
    if comspec:
        return os.path.basename(comspec)          # C:\Windows\…\cmd.exe → cmd.exe
    return "unknown"


def _build_environment_context(session: dict, working_folder_kind: Optional[str] = None) -> str:
    """
    Return the formatted ## 🖥 Environment block for system prompt injection.

    Always returns a non-empty string — OS and date/time are included even
    when no user-specific config has been set.

    working_folder_kind: "project" or "conversation" -- which folder relative paths land in
    (the file tools and a command's `cwd` resolve them the same way). Without it the model has to
    guess "your workspace": live (2026-09-21) an agent asked to work on `todo_probe/` tried the
    user's root folder, path-style globs and shell `dir` for about fourteen calls (minutes on a slow
    model) before finding a folder that a relative path would have reached at once.

    Deliberately the KIND of folder, not its absolute path: the path carries the local OS user name
    and the user's id, this block goes into every system prompt (chats that use no tools included)
    and on to cloud providers, and the model only needs to know that relative paths work.
    """
    # ── Date & time ──────────────────────────────────────────────────────
    user_tz = (session.get("user_timezone") or "").strip() or None
    date_str = ""
    time_str = ""
    tz_hint  = ""

    if user_tz:
        try:
            import zoneinfo
            tz      = zoneinfo.ZoneInfo(user_tz)
            dt      = datetime.now(tz)
            tz_abbr = dt.strftime("%Z")           # e.g. "IST"
            offset  = dt.strftime("%z")           # e.g. "+0530"
            # Format "+0530" → "UTC+5:30"
            if len(offset) == 5:
                sign  = offset[0]
                hours = offset[1:3].lstrip("0") or "0"
                mins  = offset[3:5]
                utc_label = f"UTC{sign}{hours}:{mins}"
            else:
                utc_label = f"UTC{offset}"
            date_str = dt.strftime("%A, %d %B %Y")
            time_str = f"{dt.strftime('%H:%M')} {tz_abbr} ({utc_label})"
        except Exception:
            # Bad tz string — fall back to UTC silently
            user_tz = None

    if not user_tz:
        dt       = datetime.now(timezone.utc)
        date_str = dt.strftime("%A, %d %B %Y")
        time_str = f"{dt.strftime('%H:%M')} UTC"
        tz_hint  = " (set user_timezone in settings for local time)"

    # ── OS & shell ───────────────────────────────────────────────────────
    os_system = platform.system()     # "Windows", "Linux", "Darwin"
    if os_system == "Darwin":
        mac_ver = platform.mac_ver()[0]
        os_display = f"macOS {mac_ver}" if mac_ver else "macOS"
    elif os_system == "Windows":
        os_display = f"Windows {platform.release()}"
    elif os_system == "Linux":
        os_display = f"Linux ({platform.release()})"
    else:
        os_display = os_system or "Unknown"

    shell_display = _detect_shell()
    py_ver        = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # ── Optional user-configured fields ──────────────────────────────────
    location = (session.get("user_location") or "").strip() or None
    language = (session.get("user_language") or "").strip() or None

    # ── Assemble block ────────────────────────────────────────────────────
    lines = [
        "---",
        "## \U0001f5a5️ Environment",
        "",
        f"- **Date:** {date_str}",
        f"- **Time:** {time_str}{tz_hint}",
        f"- **OS:** {os_display}",
        f"- **Shell:** {shell_display}",
        f"- **Python:** {py_ver}",
    ]
    if working_folder_kind in ("project", "conversation"):
        which = "the active project's folder" if working_folder_kind == "project" else "this conversation's folder"
        lines.append(
            f"- **Working folder:** {which} -- relative paths given to the file tools and to a "
            f"command's `cwd` resolve here. Use relative paths (for example `src/app.py`) rather "
            f"than guessing absolute ones."
        )
    if location:
        lines.append(f"- **Location:** {location}")
    if language:
        lines.append(f"- **Language:** {language}")
    lines.append("---")

    return "\n".join(lines)
