# api/wakeups.py
#
# P-19 Tier A — manual pause/resume/cancel for scheduled_wakeups.
#
# GET  /wakeups              — list the caller's still-pending paused
#                               executions, enriched with the watched
#                               execution's current status when known.
# POST /wakeups/{id}/resume  — force-resolve now instead of waiting for
#                               the scheduler's next poll or the wake_at/
#                               watch_execution_id trigger.
# POST /wakeups/{id}/cancel  — never resolve; no continuation execution is
#                               created. Only the paused execution itself
#                               is cancelled — never whatever it may have
#                               been watching via watch_execution_id, which
#                               is an independent execution with its own
#                               lifecycle and may have value on its own.
#
# Ownership: every row is scoped to auth["user_id"] before any action.

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from core.auth.dependencies import get_current_auth
from core.db.repository import WakeupRepo, ExecutionMetricsRepo, DBRepo
from core.db.session import AsyncSessionLocal
from api.schemas.responses import StatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wakeups", tags=["Wakeups"])


class WakeupSummary(BaseModel):
    id: str
    execution_id: str
    conversation_id: str
    # Human-readable label for "which chat is this" -- raw IDs aren't
    # meaningful to a user. None if title generation (async, runs after
    # the first turn) genuinely hasn't finished yet -- rare for a wakeup
    # specifically, since AWAITING_WAKEUP is reached several turns in, but
    # not impossible; the frontend should fall back to something like
    # "Untitled conversation" rather than assume this is always present.
    conversation_title: Optional[str] = None
    reason: str
    wake_at: Optional[str] = None
    watch_execution_id: Optional[str] = None
    # Only set once the watched execution has reached a terminal state
    # (see ExecutionMetricsRepo.get) -- None means "still going, or its
    # metrics row doesn't exist yet/anymore" (a restart can orphan a wakeup);
    # the frontend should render that distinctly from a normal countdown.
    watched_status: Optional[str] = None
    # Human-readable label for what watch_execution_id actually is, when
    # it's a daemon run (the common case -- backgrounding a daemon and
    # watching it is exactly what this is for). None if not watching a
    # daemon, or its metrics row isn't found -- same as watched_status.
    watch_daemon_name: Optional[str] = None
    created_at: Optional[str] = None


class ResumeWakeupResponse(BaseModel):
    status: str
    new_execution_id: str


async def _get_owned_wakeup(wakeup_id: str, user_id: str):
    async with AsyncSessionLocal() as db:
        row = await WakeupRepo.get(db, wakeup_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Wakeup not found"
        )
    return row


@router.get("", response_model=List[WakeupSummary])
async def list_wakeups(auth=Depends(get_current_auth)):
    """List the caller's still-pending paused executions."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        rows = await WakeupRepo.list_pending_for_user(db, user_id)
        summaries: List[WakeupSummary] = []
        for row in rows:
            conversation_title = None
            conversation = await DBRepo.get_conversation(db, row.conversation_id)
            if conversation is not None:
                conversation_title = conversation.title

            watched_status = None
            watch_daemon_name = None
            if row.watch_execution_id:
                metrics_row = await ExecutionMetricsRepo.get(db, row.watch_execution_id)
                if metrics_row is not None:
                    watched_status = metrics_row.status
                    watch_daemon_name = metrics_row.daemon_name

            summaries.append(WakeupSummary(
                id=row.id,
                execution_id=row.execution_id,
                conversation_id=row.conversation_id,
                conversation_title=conversation_title,
                reason=row.reason,
                wake_at=row.wake_at.isoformat() if row.wake_at else None,
                watch_execution_id=row.watch_execution_id,
                watched_status=watched_status,
                watch_daemon_name=watch_daemon_name,
                created_at=row.created_at.isoformat() if row.created_at else None,
            ))
    return summaries


@router.post("/{wakeup_id}/resume", response_model=ResumeWakeupResponse)
async def resume_wakeup_now(wakeup_id: str, auth=Depends(get_current_auth)):
    """Force-resolve immediately instead of waiting for the scheduler."""
    row = await _get_owned_wakeup(wakeup_id, auth["user_id"])
    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Wakeup is already {row.status}, not pending.",
        )

    from core.execution.wakeup_scheduler import resolve_wakeup_row
    new_execution_id = await resolve_wakeup_row(row)

    logger.info(
        "[wakeups] manually resumed",
        extra={"wakeup_id": wakeup_id, "user_id": auth["user_id"],
               "new_execution_id": new_execution_id},
    )
    return {"status": "resumed", "new_execution_id": new_execution_id}


@router.post("/{wakeup_id}/cancel", response_model=StatusResponse)
async def cancel_wakeup(wakeup_id: str, auth=Depends(get_current_auth)):
    """Never resolve. No continuation execution is created. Does not touch
    whatever the wakeup may have been watching via watch_execution_id."""
    row = await _get_owned_wakeup(wakeup_id, auth["user_id"])
    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Wakeup is already {row.status}, not pending.",
        )

    async with AsyncSessionLocal() as db:
        await WakeupRepo.mark_cancelled(db, wakeup_id)
        # Same staleness fix as resolve_wakeup_row's (core/execution/
        # wakeup_scheduler.py) -- the metrics row was written once, at
        # pause time, as "awaiting_wakeup" and nothing else updates it.
        try:
            await ExecutionMetricsRepo.upsert(
                db, {"execution_id": row.execution_id, "status": "cancelled"}
            )
        except Exception as exc:
            logger.debug(
                "[wakeups] could not update execution's metrics status "
                "after cancel (non-fatal): %s",
                exc, extra={"execution_id": row.execution_id},
            )

    from core.execution import execution_registry
    from core.execution.redis_registry import redis_execution_registry
    execution_registry.cancel_wakeup(row.execution_id)
    try:
        await redis_execution_registry.mark_execution_cancelled(
            row.execution_id, reason="user_cancelled_wakeup",
        )
    except Exception as exc:
        logger.debug(
            "[wakeups] could not mark execution cancelled in broker (non-fatal): %s",
            exc, extra={"execution_id": row.execution_id},
        )

    logger.info(
        "[wakeups] manually cancelled",
        extra={"wakeup_id": wakeup_id, "user_id": auth["user_id"],
               "execution_id": row.execution_id},
    )
    return {"status": "cancelled"}
