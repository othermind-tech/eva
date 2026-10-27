"""
Search tools for EVA (T-02).

Unlike tools/default/file_tools.py, these are recursive by design (that's
the whole point -- list_files is deliberately non-recursive, these are its
recursive counterparts) and therefore need an explicit result cap that the
single-file tools don't -- a recursive regex walk or glob is inherently
unbounded in a way "read one file" isn't.

Authority classification:
- search_files → read-only
- find_files   → read-only

V6.60 -- must not block the server. Both tools used to be `async def` whose bodies
walked the tree and read files synchronously, i.e. ON THE EVENT LOOP. One model-
initiated search over a big tree froze every request from every user until it
finished (observed: ~30 s, during which unrelated /set-session and /chat calls
timed out). Now:
  * the scan runs in a worker thread (asyncio.to_thread), so the loop keeps serving;
  * the WORK is bounded (wall-clock budget, files visited, file size, line length),
    not just the result count -- a search that finds nothing in a huge tree used to
    run to the end; now it stops, returns what it has, and says so;
  * well-known noise folders (.git, node_modules, .venv, ...) are not descended into;
  * a cancelled execution (user cancel, tool timeout) stops the scan promptly,
    instead of leaving a thread grinding through the disk.
"""

import asyncio
import fnmatch
import os
import re
import threading
import time
from dataclasses import dataclass
from pydantic import BaseModel
from typing import Iterator, List, Optional, Tuple

from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.registry import default_tool
from utils.workspace import resolve_path_for_user
from config.settings import (
    SEARCH_TOOL_MAX_RESULTS,
    SEARCH_TOOL_TIME_BUDGET_SECONDS,
    SEARCH_TOOL_MAX_FILES_VISITED,
    SEARCH_TOOL_MAX_FILE_BYTES,
    SEARCH_TOOL_MAX_LINE_CHARS,
    SEARCH_TOOL_SKIP_DIRS,
)

# How often (lines) a long file is checked for stop/budget. Small enough to react
# within milliseconds, large enough not to cost anything.
_LINE_CHECK_INTERVAL = 2000


@dataclass
class _Scan:
    """Limits for one scan plus what happened (worker-thread local; no sharing)."""
    max_results: int
    deadline: float                       # time.monotonic() value
    max_files: int
    max_file_bytes: int
    max_line_chars: int
    skip_dirs: frozenset
    stop: threading.Event                 # set by the awaiting coroutine on cancel
    cancel_token: object = None           # the execution's CancellationToken (may be None)
    visited: int = 0
    hit_result_cap: bool = False
    stopped_reason: Optional[str] = None  # "time" | "files" | "cancelled"
    budget_seconds: float = 0.0

    def should_stop(self) -> bool:
        """True (and records why) once the scan must end early."""
        if self.stopped_reason:
            return True
        # `is True`: only a real cancellation counts, not any truthy attribute.
        if self.stop.is_set() or getattr(self.cancel_token, "cancelled", False) is True:
            self.stopped_reason = "cancelled"
        elif time.monotonic() > self.deadline:
            self.stopped_reason = "time"
        return self.stopped_reason is not None


def _walk(path: str, scan: _Scan) -> Iterator[Tuple[str, str]]:
    """(root, filename) for every file under `path`, honouring the scan's limits."""
    for root, dirs, files in os.walk(path):
        # Prune in place so os.walk never enters them.
        dirs[:] = [d for d in dirs if d not in scan.skip_dirs]
        for name in files:
            if scan.should_stop():
                return
            if scan.visited >= scan.max_files:
                scan.stopped_reason = "files"
                return
            scan.visited += 1
            yield root, name


def _scan_content(path: str, regex, normalized_exts, scan: _Scan) -> List[dict]:
    """Blocking content search; runs in a worker thread."""
    matches: List[dict] = []
    for root, name in _walk(path, scan):
        if normalized_exts:
            if "." not in name or name.rsplit(".", 1)[-1].lower() not in normalized_exts:
                continue
        full_path = os.path.join(root, name)
        try:
            if os.path.getsize(full_path) > scan.max_file_bytes:
                continue
            with open(full_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    if line_num % _LINE_CHECK_INTERVAL == 0 and scan.should_stop():
                        return matches
                    if regex.search(line[:scan.max_line_chars]):
                        matches.append({
                            "path": full_path,
                            "line": line_num,
                            "text": line.rstrip("\n")[:scan.max_line_chars],
                        })
                        if len(matches) >= scan.max_results:
                            scan.hit_result_cap = True
                            return matches
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
    return matches


def _scan_names(path: str, pattern: str, scan: _Scan) -> List[str]:
    """Blocking filename glob; runs in a worker thread."""
    found: List[str] = []
    for root, name in _walk(path, scan):
        if fnmatch.fnmatch(name, pattern):
            found.append(os.path.join(root, name))
            if len(found) >= scan.max_results:
                scan.hit_result_cap = True
                break
    return found


def _new_scan(execution, stop: threading.Event) -> _Scan:
    # Module globals are read here, at call time, so they can be patched/overridden.
    budget = float(SEARCH_TOOL_TIME_BUDGET_SECONDS)
    return _Scan(
        max_results=SEARCH_TOOL_MAX_RESULTS,
        deadline=time.monotonic() + budget,
        max_files=SEARCH_TOOL_MAX_FILES_VISITED,
        max_file_bytes=SEARCH_TOOL_MAX_FILE_BYTES,
        max_line_chars=SEARCH_TOOL_MAX_LINE_CHARS,
        skip_dirs=frozenset(SEARCH_TOOL_SKIP_DIRS),
        stop=stop,
        cancel_token=getattr(execution, "cancel_token", None),
        budget_seconds=budget,
    )


async def _run_off_loop(scan: _Scan, fn, *args):
    """
    Run a blocking scan in a worker thread. If the awaiting task is cancelled
    (user cancel, tool timeout) the thread is told to stop -- asyncio cannot kill
    a thread, so without this it would keep walking the disk with nobody listening.
    """
    try:
        return await asyncio.to_thread(fn, *args, scan)
    except asyncio.CancelledError:
        scan.stop.set()
        raise
    finally:
        scan.stop.set()      # harmless once finished


def _early_stop_note(scan: _Scan, what: str) -> Optional[str]:
    if scan.stopped_reason == "time":
        return (f"Stopped after the {scan.budget_seconds:g}s search budget "
                f"({scan.visited} files looked at) -- {what} may be incomplete; narrow the path.")
    if scan.stopped_reason == "files":
        return (f"Stopped after looking at {scan.visited} files -- {what} may be incomplete; "
                f"narrow the path.")
    if scan.stopped_reason == "cancelled":
        return f"Search cancelled -- {what} are partial."
    return None


# ─────────────────────────────────────────────
# Search Files (content / regex)
# ─────────────────────────────────────────────

class SearchFilesArgs(BaseModel):
    path: str = "."
    pattern: str
    extensions: Optional[List[str]] = None
    case_sensitive: bool = True


@default_tool(
    name="search_files",
    description=(
        "Recursively search file contents for a regex pattern, returning "
        "file:line matches. Use this instead of reading files one by one to "
        "find where something is defined or used. Relative paths resolve against "
        "the active project's folder, else this conversation's folder. Results are "
        "capped -- narrow the pattern or path if you hit the limit. Skips noise "
        "folders (.git, node_modules, .venv, __pycache__) and very large files, and "
        "stops after a time budget (reported as truncated), so prefer a specific `path`."
    ),
    args_schema=SearchFilesArgs,
    authority=ToolAuthority.READ_ONLY,
    category="search",
    summary="Recursively search file contents for a regex pattern.",
)
class SearchFilesTool(ToolBase):

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args.get("path", "."), getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))
        pattern = args["pattern"]
        extensions = args.get("extensions")
        case_sensitive = args.get("case_sensitive", True)

        if not os.path.exists(path):
            return ToolResult(result={"error": f"Path does not exist: {path}"})
        if not os.path.isdir(path):
            return ToolResult(result={"error": f"Path is not a directory: {path}"})

        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(result={"error": f"Invalid regex pattern: {e}"})

        normalized_exts = None
        if extensions:
            normalized_exts = {ext.lower().lstrip(".") for ext in extensions}

        scan = _new_scan(self.execution, threading.Event())
        try:
            matches = await _run_off_loop(scan, _scan_content, path, regex, normalized_exts)
        except Exception as e:
            return ToolResult(result={"error": str(e)})

        result = {"path": path, "pattern": pattern, "matches": matches, "match_count": len(matches)}
        if scan.hit_result_cap:
            result["truncated"] = True
            result["message"] = f"Results truncated at {scan.max_results} matches -- narrow the pattern or path."
        else:
            note = _early_stop_note(scan, "the matches")
            if note:
                result["truncated"] = True
                result["message"] = note
        return ToolResult(result=result)


# ─────────────────────────────────────────────
# Find Files (glob)
# ─────────────────────────────────────────────

class FindFilesArgs(BaseModel):
    path: str = "."
    pattern: str


@default_tool(
    name="find_files",
    description=(
        "Recursively find files whose NAME matches a glob pattern (e.g. '*.py', "
        "'test_*.py', 'index.html'), returning file paths. The pattern is matched against "
        "the file name only, never the path -- 'src/*.py' or '**/dir/**' will match nothing; "
        "to search inside a folder, set `path` to that folder. It returns files, never "
        "folders (to find a folder, use list_files). Unlike list_files "
        "(single directory, non-recursive), this searches the full directory tree. "
        "Relative paths resolve against the active project's folder, else this conversation's folder. Results are "
        "capped -- narrow the pattern or path if you hit the limit. Skips noise folders "
        "(.git, node_modules, .venv, __pycache__) and stops after a time budget (reported "
        "as truncated), so prefer a specific `path`."
    ),
    args_schema=FindFilesArgs,
    authority=ToolAuthority.READ_ONLY,
    category="search",
    summary="Recursively find files matching a glob pattern.",
)
class FindFilesTool(ToolBase):

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args.get("path", "."), getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))
        pattern = args["pattern"]

        if not os.path.exists(path):
            return ToolResult(result={"error": f"Path does not exist: {path}"})
        if not os.path.isdir(path):
            return ToolResult(result={"error": f"Path is not a directory: {path}"})

        scan = _new_scan(self.execution, threading.Event())
        try:
            found = await _run_off_loop(scan, _scan_names, path, pattern)
        except Exception as e:
            return ToolResult(result={"error": str(e)})

        result = {"path": path, "pattern": pattern, "files": found, "file_count": len(found)}
        if scan.hit_result_cap:
            result["truncated"] = True
            result["message"] = f"Results truncated at {scan.max_results} files -- narrow the pattern or path."
        else:
            note = _early_stop_note(scan, "the file list")
            if note:
                result["truncated"] = True
                result["message"] = note
        return ToolResult(result=result)
