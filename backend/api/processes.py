# api/processes.py
#
# Background process management API (Phase 3 of the background-process
# supervision plan).
#
# The DB-tracked background-process registry (core/shell/executor.py's
# start_background/stop_background/tail_background, core/db/models.py's
# BackgroundProcess) is already fully built and usable by the LLM directly
# via the `background_process` tool (tools/default/process_tools.py).
#
# This router is the *human*-facing counterpart: a plain REST endpoint the
# frontend's Activity panel can call directly (a Stop button on a running
# background process row), without needing a chat turn/tool call to stop
# something the user can already see is running. Matches the
# one-router-per-concern convention already used by api/wakeups.py.
#
# Ownership: every row is scoped to auth["user_id"] before any action --
# ProcessRepo.get() has no built-in ownership filter (same as WakeupRepo.get()
# in api/wakeups.py), so _get_owned_process() below replicates the exact
# manual check core/shell/executor.py's own stop_background()/
# tail_background() already do internally.

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from core.auth.dependencies import get_current_auth
from core.db.repository import ProcessRepo
from core.shell.executor import ShellExecutor
from api.schemas.requests import SetKeepAliveOnAppShutdownRequest
from api.schemas.responses import StatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/processes", tags=["Background Processes"])


async def _get_owned_process(process_id: str, user_id: str):
    """Fetch a BackgroundProcess row and verify ownership, or raise 404.

    Mirrors api/wakeups.py's _get_owned_wakeup() -- ProcessRepo.get() has no
    ownership filter of its own.

    Local import (not module-top-level) to match every other call site in
    this feature (core/shell/executor.py, core/tasks/cleanup.py, main.py's
    boot reconciliation) -- see _DbHarnessMixin's docstring in
    tests/test_background_process_unit.py for why this matters: a
    top-level import binds the real AsyncSessionLocal at module-import
    time, before tests get a chance to patch core.db.session's attribute.
    """
    from core.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        row = await ProcessRepo.get(db, process_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Background process not found"
        )
    return row


@router.post("/{process_id}/stop", response_model=StatusResponse)
async def stop_process(process_id: str, auth=Depends(get_current_auth)):
    """
    Stop a running background process -- kills the real OS process tree
    (core/shell/executor.py::stop_background(), which also publishes the
    process.stopped event picked up by the frontend's Activity panel), not
    just the DB row. The human-facing counterpart to the background_process
    tool's action="stop" for the LLM.
    """
    user_id = auth["user_id"]
    # 404 before touching the OS process at all -- an unowned/nonexistent
    # process_id must never reach ShellExecutor.
    await _get_owned_process(process_id, user_id)

    result = await ShellExecutor.stop_background(process_id, user_id)
    if result.get("error"):
        # stop_background() returns {"error": ...} for "not found" (already
        # excluded by the ownership check above) and "already <status>" --
        # only the latter is realistically reachable here, a genuine
        # conflict rather than a client mistake.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["error"])

    return {"status": result.get("status", "stopped")}


@router.patch("/{process_id}/keep-alive", response_model=StatusResponse)
async def set_process_keep_alive(
    process_id: str,
    payload: SetKeepAliveOnAppShutdownRequest,
    auth=Depends(get_current_auth),
):
    """
    Session-scoped background processes (2026-09-29): the Activity panel's
    per-process "keep running after EVA closes" toggle. Pure DB state --
    does not touch the OS process at all (only the app-quit cleanup sweep
    reads this field), so unlike /stop there is no real/error distinction
    to surface here beyond ownership.
    """
    user_id = auth["user_id"]
    row = await _get_owned_process(process_id, user_id)
    if row.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Process '{process_id}' is already {row.status} -- nothing to keep alive.",
        )

    from core.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await ProcessRepo.set_keep_alive_on_app_shutdown(
            db, process_id, payload.keep_alive_on_app_shutdown
        )

    return {"status": "ok"}
