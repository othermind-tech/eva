# core/execution/wakeup_scheduler.py
#
# P-19 Phase 1: WakeupScheduler
#
# A lightweight async background loop, same shape as
# core/daemons/scheduler.py's DaemonScheduler:
#   1. Wakes up every WAKEUP_SCHEDULER_POLL_SECONDS
#   2. Finds scheduled_wakeups rows that are due -- wake_at has elapsed, or
#      (for rows with watch_execution_id set) the watched execution has
#      reached a terminal state
#   3. Resolves each by starting a brand-new execution that continues the
#      paused conversation (checkpoint model, not a resumed live object:
#      nothing in EVA serializes a live in-flight ReAct loop, every
#      entry point already rebuilds an AgentEngine from persisted message
#      history, and that's also what makes this restart-safe)
#
# Design notes:
#   - Poll-based resolution only in this first build pass, deliberately: an
#     event hook into the generic execution-completion path would give
#     near-instant resolution for watch_execution_id, but touches shared,
#     sensitive completion-path code for a latency improvement (at most one
#     poll interval, ~30s default), not a correctness one. Flagged as a
#     fast-follow in the plan doc, not built here.
#   - A poll failure never crashes the loop permanently, matching
#     DaemonScheduler's own "never let the poll loop crash" behavior.
#   - A single wakeup's resolution failure (e.g. its conversation was
#     deleted between listing and resolving) is caught and logged per-row
#     so it doesn't block every other due wakeup in the same poll tick.

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.settings import WAKEUP_SCHEDULER_POLL_SECONDS

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = WAKEUP_SCHEDULER_POLL_SECONDS


def _as_aware_utc(dt: datetime) -> datetime:
    """
    SQLite (via aiosqlite) round-trips DateTime(timezone=True) columns as
    offset-naive Python datetimes regardless of the column type -- every
    value this codebase writes into one is already UTC (see WakeupRepo.create
    and the tool's own wake_at computation), so a naive value read back is
    safely assumed to be UTC, not re-interpreted in local time.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _build_resume_trigger_text(row, watched_status: Optional[str]) -> str:
    lines = [
        "[Scheduled wakeup fired]",
        f'You previously paused this execution and scheduled a wakeup with '
        f'this reason: "{row.reason}"',
    ]
    if row.watch_execution_id:
        if watched_status is not None:
            lines.append(
                f"The execution you were watching (id {row.watch_execution_id}) "
                f"reached status '{watched_status}'."
            )
        else:
            lines.append(
                f"The execution you were watching (id {row.watch_execution_id}) "
                "had not finished as of this wakeup -- it fired because the "
                "fallback time limit was reached instead. Check its current "
                "status directly if you still need its result."
            )
    lines.append("Continue from here.")
    return "\n".join(lines)


async def resume_from_wakeup(row) -> str:
    """
    Start a new execution continuing row's conversation. Returns the new
    execution_id. Mirrors api/chat.py's own turn-kickoff sequence exactly
    (persist user message -> create_execution_record -> enqueue_job) so the
    existing ExecutionWorker picks it up and runs it identically to a real
    chat turn -- no AgentEngine orchestration is duplicated here.
    """
    from core.db.session import AsyncSessionLocal
    from core.db.repository import DBRepo, ExecutionMetricsRepo
    from core.execution import execution_registry
    from core.execution.redis_registry import redis_execution_registry

    watched_status: Optional[str] = None
    if row.watch_execution_id:
        async with AsyncSessionLocal() as db:
            metrics_row = await ExecutionMetricsRepo.get(db, row.watch_execution_id)
        if metrics_row is not None:
            watched_status = metrics_row.status

    trigger_text = _build_resume_trigger_text(row, watched_status)

    async with AsyncSessionLocal() as db:
        await DBRepo.add_message(
            db=db,
            conversation_id=row.conversation_id,
            role="user",
            content=trigger_text,
            chunked=False,
        )

    execution = await execution_registry.create_execution_record(
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        session_id="",
        execution_mode="conversational",
        initial_message=trigger_text,
    )
    await redis_execution_registry.enqueue_job(execution.execution_id)

    logger.info(
        "[WakeupScheduler] resumed execution",
        extra={
            "wakeup_id": row.id,
            "original_execution_id": row.execution_id,
            "new_execution_id": execution.execution_id,
            "conversation_id": row.conversation_id,
        },
    )
    return execution.execution_id


async def resolve_wakeup_row(row) -> str:
    """
    Resolve one due ScheduledWakeup row: create the continuation execution,
    mark the row resolved, and transition the original paused execution out
    of AWAITING_WAKEUP. Returns the new execution_id.

    Module-level, not a WakeupScheduler method (never used self) -- shared
    by the poll loop and POST /wakeups/{id}/resume (api/wakeups.py, P-19
    Tier A), which forces this same resolution immediately instead of
    waiting for the next poll tick or the watch/wake_at trigger.
    """
    from core.db.session import AsyncSessionLocal
    from core.db.repository import WakeupRepo, ExecutionMetricsRepo
    from core.execution import execution_registry
    from core.execution.redis_registry import redis_execution_registry

    new_execution_id = await resume_from_wakeup(row)

    async with AsyncSessionLocal() as db:
        await WakeupRepo.mark_resolved(db, row.id, new_execution_id)

    # Live-found during Tier A verification: the original execution's
    # execution_metrics.status was written once, at pause time, as
    # "awaiting_wakeup" (core/execution/pev_runner.py's/runner.py's
    # finalizer runs before the AWAITING_WAKEUP check) -- and nothing ever
    # updated it afterward. GET /executions (history) would show this
    # execution stuck at "awaiting_wakeup" forever even after it genuinely
    # resolved. The live broker status (GET /executions/{id}/status) was
    # never wrong -- only this persisted historical record was stale.
    try:
        async with AsyncSessionLocal() as db:
            await ExecutionMetricsRepo.upsert(
                db, {"execution_id": row.execution_id, "status": "completed"}
            )
    except Exception as exc:
        logger.debug(
            "[WakeupScheduler] could not update original execution's "
            "metrics status (non-fatal): %s",
            exc, extra={"execution_id": row.execution_id},
        )

    # P-19 Phase 3: let the frontend's live task-tray know this wakeup
    # fired -- covers both the poll loop and Tier A's manual resume
    # endpoint, since both funnel through this one function.
    # execution_id is deliberately the ORIGINAL paused execution
    # (row.execution_id), not new_execution_id -- that's the id the
    # frontend already has displayed (from GET /wakeups /
    # GET /executions/running while it was awaiting_wakeup), so this is
    # what lets it find and remove/update that exact row. The
    # continuation's id is carried in the summary text instead.
    try:
        from core.events_publisher import publish_user_event
        await publish_user_event(
            user_id=row.user_id,
            event="wakeup.resolved",
            execution_id=row.execution_id,
            summary=f"Wakeup resolved: {row.reason} (continues as {new_execution_id})",
        )
    except Exception:
        pass

    # Best-effort: transition the original (paused) execution out of
    # AWAITING_WAKEUP so GET /executions/running stops listing it
    # forever. The important side effect -- a new execution was created
    # and enqueued -- already happened above regardless of whether this
    # succeeds.
    execution_registry.resolve_wakeup(row.execution_id)
    try:
        await redis_execution_registry.mark_execution_finished(row.execution_id)
    except Exception as exc:
        logger.debug(
            "[WakeupScheduler] could not mark original execution finished "
            "in broker (non-fatal): %s",
            exc,
            extra={"execution_id": row.execution_id},
        )

    logger.info(
        "[WakeupScheduler] wakeup resolved",
        extra={
            "wakeup_id": row.id,
            "original_execution_id": row.execution_id,
            "new_execution_id": new_execution_id,
        },
    )
    return new_execution_id


class WakeupScheduler:
    """
    Background scheduler that resolves scheduled_wakeups rows on their
    configured trigger, same lifecycle shape as DaemonScheduler.

    Usage (in main.py lifespan):
        scheduler = WakeupScheduler()
        task = asyncio.create_task(scheduler.run())
        yield
        task.cancel()
    """

    def __init__(self) -> None:
        pass

    async def run(self) -> None:
        logger.info(
            "[WakeupScheduler] Started (poll interval: %ds)", POLL_INTERVAL_SECONDS
        )
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                await self._poll()
            except asyncio.CancelledError:
                logger.info("[WakeupScheduler] Shutting down.")
                break
            except Exception as exc:
                logger.error("[WakeupScheduler] Poll error (continuing): %s", exc)

    async def _poll(self) -> None:
        from core.db.session import AsyncSessionLocal
        from core.db.repository import WakeupRepo, ExecutionMetricsRepo

        async with AsyncSessionLocal() as db:
            pending = await WakeupRepo.list_pending(db)

        if not pending:
            return

        now = datetime.now(timezone.utc)

        for row in pending:
            try:
                due = False

                # Event trigger takes priority: a metrics row for
                # watch_execution_id only exists once that execution has
                # reached a terminal state (ExecutionMetricsRepo's own
                # docstring: "Written by the worker after run_execution()
                # returns"), so its mere presence means "done".
                if row.watch_execution_id:
                    async with AsyncSessionLocal() as db:
                        watched = await ExecutionMetricsRepo.get(db, row.watch_execution_id)
                    if watched is not None:
                        due = True

                # Fixed-time trigger -- always the fallback ceiling even
                # when watch_execution_id is set (see schedule_wakeup tool).
                if not due and row.wake_at is not None and _as_aware_utc(row.wake_at) <= now:
                    due = True

                if not due:
                    continue

                await resolve_wakeup_row(row)
            except Exception as exc:
                logger.error(
                    "[WakeupScheduler] failed to resolve wakeup %s (will retry next poll): %s",
                    row.id,
                    exc,
                )
