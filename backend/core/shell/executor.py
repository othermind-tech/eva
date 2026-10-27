# core/shell/executor.py
"""
ShellExecutor — stateful command runner (B-049, Option A+E).

Wraps asyncio.create_subprocess_exec() with per-conversation shell state so
that cwd and environment variables persist naturally across tool calls
within a session. V6.15 (S-09): a real asyncio.subprocess.Process handle is
used (not subprocess.run() wrapped in asyncio.to_thread()) specifically so
an in-flight command can actually be terminated on a cancel signal -- see
the cancellation block in _run() for the mechanics.

How it works
------------
1. Load ShellSession from the broker for this conversation_id.
2. Determine effective cwd  (explicit override → session.cwd → user workspace).
3. Build effective environment (os.environ merged with session.env_vars).
4. Handle special commands BEFORE spawning a subprocess:
     - cd [dir]            → path-resolve and update session.cwd, no subprocess
     - export VAR=value    → update session.env_vars, no subprocess
     - unset VAR           → remove from session.env_vars, no subprocess
5. For bash/sh -c "..."  commands: inject a state-capture sentinel so the final
   cwd and any VIRTUAL_ENV / PATH changes are captured after the script runs.
6. Run the (possibly modified) command via subprocess.
7. Parse sentinel markers from stdout, update session state.
8. Save updated session to the broker.
9. Return result dict (cmd, cwd, exit_code, stdout, stderr, session_state).

Option E integration
--------------------
For long-running background tasks the agent should use trigger_daemon()
rather than the shell tool.  The shell tool is for synchronous,
bounded-time commands.  This is documented in the tool description so
the LLM routes correctly.

Platform notes
--------------
- State-capture injection is only applied when cmd[0] is "bash" or "sh"
  (Unix-only shells).  On Windows, cmd.exe / PowerShell commands run
  without injection but cwd is still tracked via explicit parse of "cd"
  and "Set-Location" prefixes.
- cd-only handling works on all platforms via Python pathlib.
"""

from __future__ import annotations

import asyncio
import locale
import logging
import os
import platform
import re
import signal
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil

from config.settings import SHELL_DEFAULT_TIMEOUT_SECONDS, SHELL_MAX_OUTPUT_BYTES
from core.shell.session import ShellSession

logger = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    """
    Cross-platform liveness check for a PID this process did not itself
    start_new_session-spawn a child-watcher for (a detached background
    process has no asyncio.subprocess.Process handle to .poll() -- it was
    intentionally never communicate()'d on, see start_background()).

    2026-09-29: was a hand-rolled ctypes (Windows) / os.kill (POSIX)
    branch -- replaced with psutil.pid_exists(), which already handles the
    same Windows edge case that ctypes implementation existed for (a
    process's kernel object can outlive actual termination for a short
    window, so a naive OpenProcess-succeeds check is not sufficient) inside
    one call that's correct on Windows, Linux and macOS alike, instead of
    three separate hand-written implementations to keep in sync.

    A bare liveness check only answers "does some process have this PID
    right now" -- it cannot tell that process apart from an unrelated one
    the OS has since reused the PID for. Use _pid_matches_process() instead
    wherever a BackgroundProcess row's own identity (not just "is any PID
    alive") is what actually matters -- see that function's docstring.
    """
    return psutil.pid_exists(pid)


def _pid_matches_process(pid: int, expected_create_time: Optional[float]) -> bool:
    """
    Identity check, not just liveness: confirms `pid` still refers to the
    SAME process recorded at start_background() time (by creation
    timestamp), not a different process the OS has since reused that PID
    for. Needed because a BackgroundProcess row can outlive a backend
    restart or sit for hours -- long enough for PID reuse to be a real,
    not theoretical, risk on any of the three target platforms.

    expected_create_time is None for rows created before this field
    existed (or on the rare failure to capture it at spawn time) -- falls
    back to a plain liveness check in that case, exactly matching the
    previous identity-blind behaviour for those rows. New rows always get
    a real value going forward, so this fallback only ever narrows over
    time as old rows age out (see BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS).
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    if expected_create_time is None:
        return True
    try:
        # A small tolerance, not exact equality -- defensive against any
        # float round-trip difference between capture and a later read
        # (SQLite's REAL column is the same 8-byte IEEE754 float Python
        # uses, so an exact round-trip is expected in practice, but two
        # unrelated processes starting within the same second on a busy
        # machine is implausible enough that 1s of slack costs nothing).
        return abs(proc.create_time() - expected_create_time) < 1.0
    except psutil.NoSuchProcess:
        return False


async def _kill_process_tree(pid: int, grace_seconds: float) -> None:
    """
    Kill pid and its whole process tree -- a dev server command commonly
    forks children (e.g. `npm run dev` spawning a `node` process) that a
    single-PID kill would leave running. Graceful first, escalate to a
    hard kill after grace_seconds if it's still alive.

    2026-09-29: was a hand-rolled `taskkill /T /F` (Windows) / os.killpg
    (POSIX) branch -- replaced with psutil, one implementation for all
    three target platforms (Process.children(recursive=True) walks the
    real tree the same way regardless of OS). All of the actual psutil
    calls are synchronous/blocking, including wait_procs()'s up-to-
    grace_seconds wait, so the whole sequence runs inside asyncio.to_thread
    rather than blocking the event loop for the entire grace period --
    the previous POSIX branch achieved the same non-blocking behaviour via
    its own asyncio.sleep() polling loop; to_thread is the equivalent here
    since psutil has no native asyncio API of its own.
    """
    def _do_kill() -> None:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        try:
            procs = proc.children(recursive=True) + [proc]
        except psutil.NoSuchProcess:
            return
        for p in procs:
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
        _gone, alive = psutil.wait_procs(procs, timeout=grace_seconds)
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass

    await asyncio.to_thread(_do_kill)

async def _kill_descendants_best_effort(proc) -> None:
    """
    Kill everything a timed-out or cancelled foreground command started, not just the command.

    proc.terminate()/kill() reach only the direct child. A wrapper such as
    ``powershell -c "cd app; python -m http.server 8000"`` or ``npm run dev`` dies while the
    process it launched keeps running, orphaned, and keeps its port (found live 2026-09-21: an
    agent ran a dev server through `shell`, the 30 s timeout "killed" it, and the server stayed up
    after the run ended). Best effort by design: a failure here must not turn a clean timeout into
    an error, and a process that has already exited has nothing to kill. Skipped for objects that
    are not a real OS process (unit-test fakes have no integer pid).
    """
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or getattr(proc, "returncode", None) is not None:
        return
    try:
        if platform.system() == "Windows":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/T", "/F", "/PID", str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=10)
        else:
            # The shell child is spawned with start_new_session=True, so pid is a group leader.
            os.killpg(pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001 -- best effort, see docstring
        logger.debug("shell: could not kill the descendants of pid %s", pid, exc_info=True)


# Sentinel tokens injected into bash -c wrappers
_SENTINEL_CWD   = "__EVA_CWD__:"
_SENTINEL_VENV  = "__EVA_VENV__:"
_SENTINEL_PATH  = "__EVA_PATH__:"

# Vars captured after bash -c execution
_CAPTURE_VARS = ["VIRTUAL_ENV", "CONDA_DEFAULT_ENV", "NODE_ENV"]

_DEFAULT_TIMEOUT  = SHELL_DEFAULT_TIMEOUT_SECONDS
_MAX_OUTPUT_BYTES = SHELL_MAX_OUTPUT_BYTES

# V6.15 (S-09): how often to poll cancel_token while a command is running,
# and how long to wait after a graceful terminate() before escalating to
# kill(). See the cancellation block in _run() below.
_CANCEL_POLL_SECONDS = 0.15
_KILL_GRACE_SECONDS  = 5.0


class ShellExecutor:
    """
    Stateful shell command executor.
    All public methods are async.
    """

    @staticmethod
    async def run(
        cmd: List[str],
        conversation_id: str,
        user_id: str,
        explicit_cwd: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
        project_workspace: Optional[str] = None,
        cancel_token: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Execute a command with persistent session state.

        cancel_token (V6.15, S-09): duck-typed (checks a `.cancelled`
        attribute) rather than importing CancellationToken from
        core.execution.models, to avoid coupling this module to the
        execution layer -- pass execution.cancel_token from the caller.
        When cancellation fires mid-command, the process is terminated
        (not just abandoned) -- see _run() below.

        Returns a dict with keys:
          cmd, cwd, exit_code, stdout, stderr, session
        """
        session = await ShellSession.load(conversation_id)
        executor = ShellExecutor()
        return await executor._run(
            cmd=cmd,
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            explicit_cwd=explicit_cwd,
            timeout=timeout,
            project_workspace=project_workspace,
            cancel_token=cancel_token,
        )

    @staticmethod
    async def get_session(conversation_id: str) -> Dict[str, Any]:
        """Return the current session state dict (for inspect/debug)."""
        session = await ShellSession.load(conversation_id)
        return session.to_dict()

    @staticmethod
    async def reset_session(conversation_id: str) -> None:
        """Clear all session state for a conversation."""
        session = await ShellSession.load(conversation_id)
        await session.reset()

    # ------------------------------------------------------------------
    # Background process management (2026-08-18)
    #
    # Unlike run() above, these three methods do not wait on the spawned
    # process at all -- start_background() returns as soon as the process
    # exists, not when it exits. This is the primitive the strategic gap
    # analysis identified as missing: run()'s own communicate()-based
    # design (see _run() below) has no path that returns while leaving the
    # process alive, so a dev server started via run() gets killed at
    # SHELL_MAX_TIMEOUT_SECONDS regardless of caller (including daemons,
    # which call this same ShellExecutor). Persistence is tracked in the DB
    # (core.db.models.BackgroundProcess / core.db.repository.ProcessRepo),
    # not in-memory, so a process that outlives a backend restart isn't
    # silently lost -- see main.py's boot-time reconciliation sweep.
    # ------------------------------------------------------------------

    @staticmethod
    async def start_background(
        cmd: List[str],
        user_id: str,
        conversation_id: Optional[str] = None,
        project_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        explicit_cwd: Optional[str] = None,
        project_workspace: Optional[str] = None,
        keep_alive_on_app_shutdown: bool = False,
    ) -> Dict[str, Any]:
        """
        Spawn cmd detached: its own process group/session, stdout+stderr
        redirected to a log file (not PIPE -- nothing will drain a pipe
        after this call returns), and returns immediately with
        {process_id, pid, log_path} without waiting on the process at all.

        keep_alive_on_app_shutdown: False (default) means this process is
        session-scoped -- EVA quitting stops it, same as the old pre-
        background-process-feature behaviour. True is the opt-in exception
        for a process that should deliberately survive an app quit.

        Returns {"error": "..."} on spawn failure (command not found, etc.)
        -- same convention as run()'s _error_result().
        """
        effective_cwd = ShellExecutor._resolve_cwd(
            explicit=explicit_cwd,
            session_cwd="",
            user_id=user_id,
            conversation_id=conversation_id,
            project_workspace=project_workspace,
        )

        from utils.workspace import get_user_workspace
        import uuid as _uuid

        log_dir = Path(get_user_workspace(user_id)) / "_background_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / f"{_uuid.uuid4()}.log")

        _is_windows = platform.system() == "Windows"
        from utils.child_env import child_environment
        spawn_kwargs: Dict[str, Any] = {"env": child_environment()}
        if _is_windows:
            # CREATE_BREAKAWAY_FROM_JOB (V6.58 Phase 3 live-testing finding):
            # CREATE_NEW_PROCESS_GROUP alone only exempts the child from
            # this backend's console/Ctrl+C signal group -- it says nothing
            # about Windows Job Object membership, a separate kernel
            # concept. If this backend's own process is inside a job with
            # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (common -- VS Code,
            # Windows Terminal, and conda-activated shells often create
            # one), every descendant dies with it regardless of process
            # group, which silently defeated keep_alive_on_app_shutdown:
            # confirmed live -- a process opted in still died when the
            # backend's process tree was torn down, even though
            # /internal/shutdown correctly never touched its DB row.
            # CREATE_BREAKAWAY_FROM_JOB asks Windows to exempt the child
            # from that job entirely. See the except OSError branch below
            # for the required fallback -- confirmed live that this flag
            # fails outright (WinError 5, Access is denied) when the
            # ambient job doesn't set JOB_OBJECT_LIMIT_BREAKAWAY_OK.
            spawn_kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            )
        else:
            spawn_kwargs["start_new_session"] = True

        async def _spawn(kwargs: Dict[str, Any]):
            with open(log_path, "wb") as _log_fh:
                return await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=effective_cwd,
                    stdout=_log_fh,
                    stderr=asyncio.subprocess.STDOUT,
                    **kwargs,
                )

        try:
            proc = await _spawn(spawn_kwargs)
        except FileNotFoundError:
            return {"error": f"command not found: {cmd[0]!r}"}
        except OSError as exc:
            # Fall back to spawning without CREATE_BREAKAWAY_FROM_JOB rather
            # than failing the whole call -- the process just won't survive
            # this backend's own process tree being torn down by that job
            # (the same limitation as before this fix), instead of not
            # starting at all.
            if _is_windows and (
                spawn_kwargs["creationflags"] & getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            ):
                logger.warning(
                    "[background_process] CREATE_BREAKAWAY_FROM_JOB denied (%s) -- this "
                    "backend's process is inside a Windows Job Object that doesn't allow "
                    "breakaway, so this process will NOT survive the backend's own process "
                    "tree being torn down even if keep_alive_on_app_shutdown=true. Falling "
                    "back to spawning without it.", exc,
                )
                fallback_kwargs = dict(spawn_kwargs)
                fallback_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                try:
                    proc = await _spawn(fallback_kwargs)
                except Exception as exc2:
                    return {"error": str(exc2)}
            else:
                return {"error": str(exc)}
        except Exception as exc:
            return {"error": str(exc)}

        # Capture the real OS creation timestamp right after spawn, for the
        # identity check every later consumer of this row uses (see
        # _pid_matches_process()) -- best-effort: a process this call just
        # spawned successfully failing this immediately after is exceedingly
        # unlikely, but if it does, the row still gets created (None here
        # means "fall back to plain liveness", same as any pre-existing row).
        try:
            pid_create_time = psutil.Process(proc.pid).create_time()
        except psutil.NoSuchProcess:
            pid_create_time = None

        from core.db.session import AsyncSessionLocal as _ASL
        from core.db.repository import ProcessRepo as _ProcRepo

        async with _ASL() as _db:
            row = await _ProcRepo.create(
                _db,
                user_id=user_id,
                command=cmd,
                cwd=effective_cwd,
                log_path=log_path,
                pid=proc.pid,
                pid_create_time=pid_create_time,
                keep_alive_on_app_shutdown=keep_alive_on_app_shutdown,
                conversation_id=conversation_id,
                project_id=project_id,
                execution_id=execution_id,
            )

        return {
            "process_id": row.id,
            "pid": proc.pid,
            "log_path": log_path,
            "cwd": effective_cwd,
            "keep_alive_on_app_shutdown": keep_alive_on_app_shutdown,
            "status": "running",
        }

    @staticmethod
    async def stop_background(
        process_id: str, user_id: str, grace_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Kill the process tree (not just the single PID -- a dev server
        command commonly forks children, e.g. `npm run dev` spawning a
        `node` process the shell wrapper itself isn't). Terminates
        gracefully first, escalates to a hard kill after grace_seconds,
        mirroring _run()'s own terminate-then-kill pattern below.

        grace_seconds: override for BACKGROUND_PROCESS_KILL_GRACE_SECONDS.
        None (the default, used by every interactive caller -- the
        background_process tool and api/processes.py's Stop button) keeps
        the existing, more patient default. api/internal.py's app-quit
        shutdown endpoint passes BACKGROUND_PROCESS_SHUTDOWN_KILL_GRACE_SECONDS
        instead, since it kills every non-persistent process concurrently
        and needs to stay well inside Electron's own fallback-kill timeout.
        """
        from core.db.session import AsyncSessionLocal as _ASL
        from core.db.repository import ProcessRepo as _ProcRepo
        from config.settings import BACKGROUND_PROCESS_KILL_GRACE_SECONDS

        effective_grace = (
            BACKGROUND_PROCESS_KILL_GRACE_SECONDS if grace_seconds is None else grace_seconds
        )

        async with _ASL() as _db:
            row = await _ProcRepo.get(_db, process_id)
            if row is None or row.user_id != user_id:
                return {"error": f"No background process '{process_id}' found."}
            if row.status != "running":
                return {"error": f"Process '{process_id}' is already {row.status}."}

            # Identity check before killing anything: if this PID no longer
            # refers to the process this row was created for (it exited on
            # its own, or -- the actual hazard this guards against -- the OS
            # has since reused the PID for something else entirely), there
            # is nothing real left for us to kill. Marking "crashed" here
            # (not "stopped", which would claim we successfully stopped a
            # real running thing) matches boot reconciliation's own
            # convention for the same "found not actually alive" case.
            if not _pid_matches_process(row.pid, row.pid_create_time):
                await _ProcRepo.mark_status(_db, process_id, "crashed")
                already_gone = True
            else:
                await _kill_process_tree(row.pid, effective_grace)
                await _ProcRepo.mark_status(_db, process_id, "stopped")
                already_gone = False

        # Phase 3: notify the frontend's Activity panel live (SSE), rather
        # than only relying on its ~20s poll -- this is the single shared
        # code path for both a user-initiated stop (api/processes.py) and
        # the daily cleanup sweep's force-stop of a stale process
        # (core/tasks/cleanup.py), so both correctly surface the row
        # disappearing from "running". Non-fatal, same convention as every
        # other publish_user_event() call site.
        final_status = "crashed" if already_gone else "stopped"
        try:
            from core.events_publisher import publish_user_event
            await publish_user_event(
                user_id=user_id,
                event=f"process.{final_status}",
                execution_id=process_id,
                summary=(
                    f"Background process was already gone: {' '.join(row.command)}"
                    if already_gone else
                    f"Background process stopped: {' '.join(row.command)}"
                ),
            )
        except Exception:
            pass

        return {"process_id": process_id, "status": final_status}

    @staticmethod
    async def tail_background(process_id: str, user_id: str) -> Dict[str, Any]:
        """
        Return output written to log_path since the last tail call (byte-
        offset tracked in the DB row, mirrors the incremental-read pattern
        this exact feature uses in comparable tools), plus a liveness check
        against the real OS process -- not just whatever status the DB row
        currently says, since the process may have exited/crashed between
        calls without anyone calling stop_background().
        """
        from core.db.session import AsyncSessionLocal as _ASL
        from core.db.repository import ProcessRepo as _ProcRepo
        from config.settings import BACKGROUND_PROCESS_TAIL_MAX_BYTES

        async with _ASL() as _db:
            row = await _ProcRepo.get(_db, process_id)
            if row is None or row.user_id != user_id:
                return {"error": f"No background process '{process_id}' found."}

            new_text = ""
            try:
                with open(row.log_path, "rb") as f:
                    f.seek(row.last_output_offset)
                    chunk = f.read(BACKGROUND_PROCESS_TAIL_MAX_BYTES)
                    new_offset = f.tell()
                _encoding = locale.getpreferredencoding(False)
                new_text = chunk.decode(_encoding, errors="replace")
                if new_offset != row.last_output_offset:
                    await _ProcRepo.update_offset(_db, process_id, new_offset)
            except FileNotFoundError:
                pass

            alive = _pid_matches_process(row.pid, row.pid_create_time)
            reported_status = row.status
            if row.status == "running" and not alive:
                # Process exited/crashed since the last check without
                # anyone calling stop_background() -- reconcile the row now
                # rather than reporting a status we already know is stale.
                await _ProcRepo.mark_status(_db, process_id, "exited")
                reported_status = "exited"

        return {
            "process_id": process_id,
            "status": reported_status,
            "new_output": new_text,
            "pid": row.pid,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(
        self,
        cmd: List[str],
        session: ShellSession,
        user_id: str,
        explicit_cwd: Optional[str],
        timeout: int,
        conversation_id: Optional[str] = None,
        project_workspace: Optional[str] = None,
        cancel_token: Optional[Any] = None,
    ) -> Dict[str, Any]:

        if not cmd:
            return self._error_result(cmd, session, "cmd must be a non-empty list")

        # Record history
        session.add_history(" ".join(str(c) for c in cmd))

        # ── Determine effective cwd ─────────────────────────────────────
        effective_cwd = self._resolve_cwd(
            explicit=explicit_cwd,
            session_cwd=session.cwd,
            user_id=user_id,
            conversation_id=conversation_id,
            project_workspace=project_workspace,
        )

        # ── Handle pure state-change commands (no subprocess needed) ───
        if self._is_cd(cmd):
            return await self._handle_cd(cmd, session, effective_cwd)

        if self._is_export(cmd):
            return await self._handle_export(cmd, session, effective_cwd)

        if self._is_unset(cmd):
            return await self._handle_unset(cmd, session, effective_cwd)

        # ── Prepare subprocess command (inject sentinels if applicable) ─
        run_cmd, injected = self._maybe_inject_sentinels(cmd)

        # ── Build environment ───────────────────────────────────────────
        env = session.effective_env()

        # ── Execute ─────────────────────────────────────────────────────
        # V6.15 (S-09): asyncio.create_subprocess_exec() -- NOT
        # asyncio.to_thread(subprocess.run, ...) -- specifically because a
        # thread-wrapped blocking call cannot be interrupted once started
        # (cancelling the wrapping asyncio task only stops us from waiting
        # on it, not the thread's actual work). This gives us a real
        # asyncio.subprocess.Process handle we can .terminate()/.kill().
        try:
            proc = await asyncio.create_subprocess_exec(
                *run_cmd,
                cwd=effective_cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Own process group on POSIX so a timeout/cancel can signal the whole tree
                # (see _kill_descendants_best_effort); Windows uses taskkill /T instead.
                start_new_session=(platform.system() != "Windows"),
            )
        except FileNotFoundError:
            await session.save()
            return self._error_result(
                cmd, session,
                f"command not found: {cmd[0]!r}",
                cwd=effective_cwd,
            )
        except Exception as exc:
            await session.save()
            return self._error_result(cmd, session, str(exc), cwd=effective_cwd)

        # Poll-race: check for a cancel signal or our own timeout every
        # _CANCEL_POLL_SECONDS while communicate() runs, instead of blocking
        # on it uninterruptibly. Worst-case cancellation latency is one poll
        # interval (~150ms) -- a massive improvement over "never".
        communicate_task = asyncio.ensure_future(proc.communicate())
        elapsed = 0.0
        outcome: Optional[str] = None  # "cancelled" | "timeout" | None (normal)
        try:
            while True:
                done, _ = await asyncio.wait({communicate_task}, timeout=_CANCEL_POLL_SECONDS)
                if communicate_task in done:
                    stdout_bytes, stderr_bytes = communicate_task.result()
                    break
                elapsed += _CANCEL_POLL_SECONDS
                if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                    outcome = "cancelled"
                    break
                if elapsed >= timeout:
                    outcome = "timeout"
                    break
        except asyncio.CancelledError:
            # V6.15.1 (S-09 follow-up): a caller *outside* this function
            # (agent_engine.py's generic _execute_tool_cancellable() wrapper)
            # can also call task.cancel() on us. That injects CancelledError
            # directly into the `await asyncio.wait(...)` above -- it does
            # NOT go through the cancel_token check-and-break logic. Left
            # unhandled, this unwinds the coroutine immediately and skips
            # the cleanup block below entirely, orphaning the real OS
            # process (confirmed live: UI reports "cancelled" while the
            # subprocess keeps running to completion). We must kill the
            # process here too, then re-raise so the outer wrapper's own
            # cancellation handling still applies.
            await _kill_descendants_best_effort(proc)
            try:
                proc.kill()
            except ProcessLookupError:
                pass  # the tree kill above already took it; still re-raise the cancellation below
            try:
                await proc.wait()
            except Exception:
                pass
            communicate_task.cancel()
            try:
                await communicate_task
            except (asyncio.CancelledError, Exception):
                pass
            await session.save()
            raise

        if outcome is not None:
            # Graceful terminate() first (SIGTERM on POSIX; on Windows this
            # is already an immediate TerminateProcess -- no separate
            # graceful phase exists there), escalate to kill() if it
            # doesn't exit within the grace period.
            await _kill_descendants_best_effort(proc)
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # already gone (the tree kill above took it too)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            communicate_task.cancel()
            try:
                await communicate_task
            except (asyncio.CancelledError, Exception):
                pass
            await session.save()
            # Note: unlike the previous subprocess.run(timeout=...) path,
            # partial stdout/stderr captured before termination is not
            # recovered here -- accepted tradeoff for correct process
            # termination (the safety-critical part of this fix).
            error_msg = (
                "cancelled by user" if outcome == "cancelled"
                else f"timeout after {timeout}s"
            )
            return {
                "cmd":       cmd,
                "cwd":       effective_cwd,
                "exit_code": -1,
                "stdout":    "",
                "stderr":    "",
                "error":     error_msg,
                "cancelled": outcome == "cancelled",
                "session":   session.to_dict(),
            }

        _encoding = locale.getpreferredencoding(False)
        stdout = (stdout_bytes or b"").decode(_encoding, errors="replace")
        stderr = (stderr_bytes or b"").decode(_encoding, errors="replace")
        returncode = proc.returncode

        # ── Parse sentinel output (only if we injected) ─────────────────
        if injected:
            stdout, new_cwd, env_changes = self._extract_sentinels(stdout)
            if new_cwd:
                session.set_cwd(new_cwd)
                effective_cwd = session.cwd
            for k, v in env_changes.items():
                if v:
                    session.set_env(k, v)
                else:
                    session.unset_env(k)

        # ── Truncate oversized output ────────────────────────────────────
        if len(stdout) > _MAX_OUTPUT_BYTES:
            stdout = stdout[:_MAX_OUTPUT_BYTES] + f"\n[...truncated — output exceeded {_MAX_OUTPUT_BYTES // 1024}KB]"

        if len(stderr) > _MAX_OUTPUT_BYTES:
            stderr = stderr[:_MAX_OUTPUT_BYTES] + "\n[...truncated]"

        await session.save()

        return {
            "cmd":       cmd,
            "cwd":       effective_cwd,
            "exit_code": returncode,
            "stdout":    stdout,
            "stderr":    stderr,
            "session":   session.to_dict(),
        }

    # ------------------------------------------------------------------
    # Special command handlers
    # ------------------------------------------------------------------

    async def _handle_cd(
        self,
        cmd: List[str],
        session: ShellSession,
        current_cwd: str,
    ) -> Dict[str, Any]:
        target = cmd[1] if len(cmd) > 1 else str(Path.home())

        # Handle ~ expansion
        target = os.path.expanduser(target)

        new_cwd = str((Path(current_cwd) / target).resolve()) \
            if not Path(target).is_absolute() else str(Path(target).resolve())

        if not Path(new_cwd).is_dir():
            await session.save()
            return {
                "cmd":       cmd,
                "cwd":       current_cwd,
                "exit_code": 1,
                "stdout":    "",
                "stderr":    f"cd: {target}: No such file or directory",
                "session":   session.to_dict(),
            }

        session.set_cwd(new_cwd)
        await session.save()
        return {
            "cmd":       cmd,
            "cwd":       new_cwd,
            "exit_code": 0,
            "stdout":    "",
            "stderr":    "",
            "session":   session.to_dict(),
        }

    async def _handle_export(
        self,
        cmd: List[str],
        session: ShellSession,
        current_cwd: str,
    ) -> Dict[str, Any]:
        """
        Handle:  export VAR=value
                 export VAR=value VAR2=value2  (multiple)
        """
        parts = cmd[1:]  # drop "export"
        parsed: List[Tuple[str, str]] = []
        errors: List[str] = []

        for part in parts:
            if "=" in part:
                key, _, value = part.partition("=")
                key = key.strip()
                if key and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                    session.set_env(key, value)
                    parsed.append((key, value))
                else:
                    errors.append(f"invalid variable name: {key!r}")
            else:
                errors.append(f"export: not in VAR=value format: {part!r}")

        await session.save()

        if errors:
            return {
                "cmd":       cmd,
                "cwd":       current_cwd,
                "exit_code": 1,
                "stdout":    "\n".join(f"export: {e}" for e in errors),
                "stderr":    "",
                "session":   session.to_dict(),
            }

        stdout = "\n".join(f"export {k}={v}" for k, v in parsed)
        return {
            "cmd":       cmd,
            "cwd":       current_cwd,
            "exit_code": 0,
            "stdout":    stdout,
            "stderr":    "",
            "session":   session.to_dict(),
        }

    async def _handle_unset(
        self,
        cmd: List[str],
        session: ShellSession,
        current_cwd: str,
    ) -> Dict[str, Any]:
        for var in cmd[1:]:
            session.unset_env(var.strip())
        await session.save()
        return {
            "cmd":       cmd,
            "cwd":       current_cwd,
            "exit_code": 0,
            "stdout":    "",
            "stderr":    "",
            "session":   session.to_dict(),
        }

    # ------------------------------------------------------------------
    # Sentinel injection for bash/sh -c commands
    # ------------------------------------------------------------------

    def _maybe_inject_sentinels(
        self, cmd: List[str]
    ) -> Tuple[List[str], bool]:
        """
        If cmd is `bash -c "..."` or `sh -c "..."`, append state-capture
        lines so we can track cwd and VIRTUAL_ENV / PATH changes.

        Returns (modified_cmd, injected_bool).
        """
        if len(cmd) < 3:
            return cmd, False
        if cmd[0] not in ("bash", "sh", "/bin/bash", "/bin/sh"):
            return cmd, False
        if cmd[1] != "-c":
            return cmd, False

        original_script = cmd[2]

        suffix = (
            "\n__eva_exit=$?"
            f'\necho "{_SENTINEL_CWD}$(pwd)"'
            "\n" + "\n".join(
                f'echo "{_SENTINEL_VENV}{v}:${{{v}}}"'
                for v in _CAPTURE_VARS
            ) +
            f'\necho "{_SENTINEL_PATH}$PATH"'
            "\nexit $__eva_exit"
        )

        modified_script = original_script + suffix
        modified_cmd = list(cmd)
        modified_cmd[2] = modified_script
        return modified_cmd, True

    def _extract_sentinels(
        self, stdout: str
    ) -> Tuple[str, Optional[str], Dict[str, str]]:
        """
        Parse sentinel lines from stdout.
        Returns (clean_stdout, new_cwd_or_None, env_changes_dict).
        """
        clean_lines: List[str] = []
        new_cwd: Optional[str] = None
        env_changes: Dict[str, str] = {}

        for line in stdout.splitlines():
            if line.startswith(_SENTINEL_CWD):
                new_cwd = line[len(_SENTINEL_CWD):].strip()
            elif line.startswith(_SENTINEL_VENV):
                rest = line[len(_SENTINEL_VENV):]
                # format: VARNAME:value
                if ":" in rest:
                    var, _, val = rest.partition(":")
                    env_changes[var.strip()] = val.strip()
            elif line.startswith(_SENTINEL_PATH):
                new_path = line[len(_SENTINEL_PATH):].strip()
                if new_path:
                    env_changes["PATH"] = new_path
            else:
                clean_lines.append(line)

        clean_stdout = "\n".join(clean_lines)
        # Remove trailing empty line added by injection
        clean_stdout = clean_stdout.rstrip("\n")

        return clean_stdout, new_cwd, env_changes

    # ------------------------------------------------------------------
    # Command classifiers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_cd(cmd: List[str]) -> bool:
        return bool(cmd) and cmd[0].lower() in ("cd", "chdir")

    @staticmethod
    def _is_export(cmd: List[str]) -> bool:
        return bool(cmd) and cmd[0].lower() == "export"

    @staticmethod
    def _is_unset(cmd: List[str]) -> bool:
        return bool(cmd) and cmd[0].lower() == "unset"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_cwd(
        explicit: Optional[str],
        session_cwd: str,
        user_id: str,
        conversation_id: Optional[str] = None,
        project_workspace: Optional[str] = None,
    ) -> str:
        """
        Resolve effective cwd with priority:
          1. Explicit cwd argument (agent override). A RELATIVE one is resolved the way the file
             tools resolve their paths -- against the active project's folder, else the
             conversation's folder -- never against the EVA server's own working directory.
          2. Session-tracked cwd (user ran 'cd' earlier in this shell session)
          3. Active project workspace root (B-051)
          4. Conversation-scoped directory
          5. User workspace default

        (Found live 2026-09-21: an agent passed cwd="todo_probe" to background_process. It was
        handed to the OS as-is, resolved against the server's folder, did not exist, and failed
        with "[WinError 267] The directory name is invalid" -- while cwd="." "worked" only because
        the server's own folder exists. A plan step then failed and the re-plan dropped the rest of
        the task.)
        """
        if explicit:
            if os.path.isabs(explicit):
                return explicit
            try:
                from utils.workspace import resolve_path_for_user
                return os.path.normpath(
                    resolve_path_for_user(user_id, explicit, conversation_id, project_workspace)
                )
            except Exception:
                return explicit
        if session_cwd:
            return session_cwd
        try:
            if project_workspace:
                return project_workspace
            if conversation_id:
                from utils.workspace import get_conversation_dir
                return str(get_conversation_dir(user_id, conversation_id))
            from utils.workspace import get_user_workspace
            return get_user_workspace(user_id)
        except Exception:
            return str(Path.cwd())

    @staticmethod
    def _error_result(
        cmd: List[str],
        session: ShellSession,
        message: str,
        cwd: str = "",
    ) -> Dict[str, Any]:
        return {
            "cmd":       cmd,
            "cwd":       cwd,
            "exit_code": -1,
            "stdout":    "",
            "stderr":    message,
            "error":     message,
            "session":   session.to_dict(),
        }
