# api/internal.py
#
# Internal, localhost-only lifecycle endpoint -- NOT part of the public API
# surface the frontend/agent talk to. The caller is Electron's own main
# process shutting down its own spawned backend child, not a remote client,
# so there is no user JWT here (there's no single "current user" for an
# app-wide quit anyway -- see the scope note below). Guarded instead by a
# same-machine check, since this endpoint has real teardown power.
#
# Session-scoped background processes (2026-09-29), Phase 3: replaces the
# old signal-based shutdown. Node's `child.kill('SIGTERM')` on Windows only
# delivers a real, catchable signal between two Node.js processes -- for
# the packaged `eva-backend.exe` (a non-Node PyInstaller child), Windows
# has no signal delivery at all and silently falls back to
# TerminateProcess(), an instant hard kill with no chance for cleanup. An
# HTTP call + a self-directed signal.raise_signal(SIGINT)
# sidesteps that entirely -- the signal is delivered within the same
# process, so it behaves identically on Windows/Linux/macOS.
#
# IMPORTANT: this must be signal.raise_signal(), NOT os.kill(getpid(), ...).
# They are not equivalent on Windows: CPython's os.kill() on Windows only
# special-cases signal.CTRL_C_EVENT / signal.CTRL_BREAK_EVENT -- any other
# signal value (including SIGINT) falls through to TerminateProcess(), an
# unconditional hard kill that never reaches uvicorn's signal.signal()
# -registered handler (uvicorn/server.py's install_signal_handlers() uses
# plain signal.signal() on every platform, deliberately not
# loop.add_signal_handler(), which Windows' asyncio doesn't support).
# signal.raise_signal() (Python 3.8+) instead calls the C runtime's raise()
# directly against the current process, which correctly and synchronously
# invokes that same registered handler on every platform -- confirmed the
# hard way: an earlier version of this endpoint using os.kill() passed
# every unit test (which mocks the call and never actually executes it) but
# hard-killed the real dev server with zero graceful-shutdown log lines when
# actually exercised against it, live.

from __future__ import annotations

import asyncio
import logging
import signal

from fastapi import APIRouter, HTTPException, Request, status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal"])

# 127.0.0.1 / ::1 cover TCP loopback; "testclient" is TestClient's/httpx's
# synthesized client host when a test issues a request without a real
# socket (confirmed against FastAPI's own default -- there is no real
# network client to compare against in that case).
_LOCALHOST_HOSTS = {"127.0.0.1", "::1", "testclient"}

# Delay between sending the HTTP response and firing the self-directed
# SIGINT -- long enough for uvicorn to actually flush the response onto
# the socket before this process starts tearing down, short enough to stay
# well inside Electron's own fallback-kill timeout.
_SELF_SIGNAL_DELAY_SECONDS = 0.3


def _require_localhost(request: Request) -> None:
    client_host = request.client.host if request.client else None
    if client_host not in _LOCALHOST_HOSTS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="localhost only")


async def _delayed_self_sigint() -> None:
    await asyncio.sleep(_SELF_SIGNAL_DELAY_SECONDS)
    logger.info("[SHUTDOWN] Raising self-directed SIGINT to trigger graceful ASGI shutdown.")
    signal.raise_signal(signal.SIGINT)


@router.post("/shutdown")
async def graceful_shutdown(request: Request):
    """
    Called once by Electron's main.js at app-quit time.

    1. Stops every currently-running background process that is NOT opted
       into keep_alive_on_app_shutdown, across ALL users -- app-wide quit,
       confirmed scope (EVA is single-machine/single-user in practice
       today, but the DB itself is multi-user). Reuses
       ShellExecutor.stop_background() for each row -- same identity check,
       status transition and SSE event as an interactive Stop, just
       triggered here for every eligible row at once, concurrently (not
       sequentially) so total time doesn't scale with how many processes
       happen to be running.
    2. Schedules a short-delayed self-directed SIGINT via signal.raise_signal()
       (see _delayed_self_sigint above -- NOT os.kill(), which hard-kills on
       Windows instead of invoking uvicorn's handler) and returns immediately
       -- the actual ASGI graceful-shutdown block (core/broker, MCP session
       pool, etc., in main.py's lifespan) runs after this handler has already
       returned its HTTP response.
    """
    _require_localhost(request)

    from core.db.session import AsyncSessionLocal
    from core.db.repository import ProcessRepo
    from core.shell.executor import ShellExecutor
    from config.settings import BACKGROUND_PROCESS_SHUTDOWN_KILL_GRACE_SECONDS

    async with AsyncSessionLocal() as db:
        rows = await ProcessRepo.list_all_running(db)

    to_stop = [r for r in rows if not r.keep_alive_on_app_shutdown]
    kept_alive_count = len(rows) - len(to_stop)

    async def _stop_one(row) -> None:
        try:
            await ShellExecutor.stop_background(
                row.id, row.user_id, grace_seconds=BACKGROUND_PROCESS_SHUTDOWN_KILL_GRACE_SECONDS
            )
        except Exception:
            logger.exception("[SHUTDOWN] Failed to stop background process %s", row.id)

    if to_stop:
        await asyncio.gather(*(_stop_one(row) for row in to_stop))

    logger.info(
        "[SHUTDOWN] Graceful shutdown requested -- stopped %d background process(es), "
        "%d kept alive (keep_alive_on_app_shutdown=true).",
        len(to_stop), kept_alive_count,
    )

    asyncio.create_task(_delayed_self_sigint())

    return {
        "status": "shutting_down",
        "processes_stopped": len(to_stop),
        "processes_kept_alive": kept_alive_count,
    }
