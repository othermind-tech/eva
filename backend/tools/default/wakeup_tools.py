# tools/default/wakeup_tools.py
#
# P-19 Phase 1: schedule_wakeup — persisted "resume me later" tool.
#
# Unlike trigger_daemon(wait=True) (a live coroutine block, capped at 5
# minutes, dies on restart), this tool ends the calling execution cleanly
# right now and persists a scheduled_wakeups row. A background poll loop
# (core/execution/wakeup_scheduler.py) resolves it later -- either when
# wake_at elapses or when watch_execution_id (if given) reaches a terminal
# state, whichever comes first -- by starting a brand-new execution that
# continues this conversation (a checkpoint model, not a resumed live call).
#
# Not chat_excluded: unlike trigger_daemon (which needs caller_agent_name,
# a multi-agent-orchestration concept plain chat doesn't have), this tool
# only needs execution_id/conversation_id/user_id, which every execution
# carries regardless of mode -- "remind me about this later" is as valid
# a request in plain conversation as in an agent-mode run.

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from core.tools.base import ToolBase, ToolAuthority, ToolResult
from core.tools.registry import default_tool

logger = logging.getLogger(__name__)


class ScheduleWakeupArgs(BaseModel):
    delay_seconds: Optional[int] = Field(
        default=None,
        description=(
            "Seconds from now to wake up. Required unless watch_execution_id "
            "is given. Bounded by WAKEUP_MAX_DELAY_SECONDS (default 48 hours)."
        ),
    )
    watch_execution_id: Optional[str] = Field(
        default=None,
        description=(
            "Resume as soon as this execution_id (e.g. one returned by "
            "trigger_daemon with wait=False) reaches a terminal state, "
            "instead of waiting for a fixed delay. If delay_seconds is also "
            "given, it acts as a fallback ceiling in case the watched "
            "execution never finishes. If omitted, WAKEUP_MAX_DELAY_SECONDS "
            "is used as that ceiling automatically."
        ),
    )
    reason: str = Field(
        description=(
            "What matters for when you wake up -- stated explicitly, since "
            "this execution ends now and a new one continues the "
            "conversation later. Be specific: this text is the only thing "
            "carried forward, not your in-progress reasoning."
        ),
    )


@default_tool(
    name="schedule_wakeup",
    description=(
        "Pause this execution and schedule an automatic resume later, instead of "
        "blocking. Use this for anything that takes longer than a few minutes to "
        "become ready -- waiting on a backgrounded daemon (trigger_daemon with "
        "wait=False), a slow background_process (a long build or install; use "
        "delay_seconds), or a fixed delay ('check back in 2 hours'). Ends this "
        "execution cleanly now; a new execution continues this same conversation "
        "when the wakeup fires, primed with the reason you give here, with a fresh "
        "step budget. "
        "Restart-safe: the wakeup is persisted, not held in memory."
    ),
    args_schema=ScheduleWakeupArgs,
    authority=ToolAuthority.MUTATING,
    category="general",
    summary="Pause and schedule an automatic resume later (time-based or watching another execution).",
)
class ScheduleWakeupTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        from config.settings import WAKEUP_MAX_DELAY_SECONDS, WAKEUP_MAX_PENDING_PER_USER

        parsed = ScheduleWakeupArgs(**args)
        reason = (parsed.reason or "").strip()
        if not reason:
            return ToolResult(result={"error": "reason must not be empty."})

        delay_seconds = parsed.delay_seconds
        watch_execution_id = (parsed.watch_execution_id or "").strip() or None

        if delay_seconds is None and watch_execution_id is None:
            return ToolResult(result={
                "error": "Provide at least one of delay_seconds or watch_execution_id."
            })
        if delay_seconds is not None and (delay_seconds <= 0 or delay_seconds > WAKEUP_MAX_DELAY_SECONDS):
            return ToolResult(result={
                "error": (
                    f"delay_seconds must be between 1 and {WAKEUP_MAX_DELAY_SECONDS} "
                    f"(WAKEUP_MAX_DELAY_SECONDS)."
                )
            })

        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={"error": "schedule_wakeup: no execution context."})

        user_id = getattr(execution, "user_id", "")
        conversation_id = getattr(execution, "conversation_id", "")
        execution_id = getattr(execution, "execution_id", "")
        if not (user_id and conversation_id and execution_id):
            return ToolResult(result={"error": "schedule_wakeup: incomplete execution context."})

        from core.db.session import AsyncSessionLocal
        from core.db.repository import WakeupRepo, ExecutionMetricsRepo
        from core.execution import execution_registry

        async with AsyncSessionLocal() as db:
            pending_count = await WakeupRepo.count_pending_for_user(db, user_id)
            if pending_count >= WAKEUP_MAX_PENDING_PER_USER:
                return ToolResult(result={
                    "error": (
                        f"You already have {pending_count} pending wakeups "
                        f"(max {WAKEUP_MAX_PENDING_PER_USER}). Wait for one to "
                        "resolve, or this call is a sign the work should just "
                        "continue synchronously instead."
                    )
                })

            if watch_execution_id is not None:
                owned = False
                metrics_row = await ExecutionMetricsRepo.get(db, watch_execution_id)
                if metrics_row is not None:
                    owned = metrics_row.user_id == user_id
                else:
                    owned = any(
                        e.execution_id == watch_execution_id
                        for e in execution_registry.list_running_for_user(user_id)
                    )
                if not owned:
                    return ToolResult(result={
                        "error": (
                            f"watch_execution_id '{watch_execution_id}' was not found "
                            "among your executions, running or completed."
                        )
                    })

            now = datetime.now(timezone.utc)
            effective_delay = delay_seconds if delay_seconds is not None else WAKEUP_MAX_DELAY_SECONDS
            wake_at = now + timedelta(seconds=effective_delay)

            try:
                row = await WakeupRepo.create(
                    db,
                    execution_id=execution_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    wake_at=wake_at,
                    reason=reason,
                    watch_execution_id=watch_execution_id,
                )
            except IntegrityError:
                # execution_id is unique -- this execution already called
                # schedule_wakeup once. Normally unreachable now that PEV's
                # step loop stops the plan the moment execution.status
                # becomes AWAITING_WAKEUP (core/execution/pev_runner.py),
                # but this is the difference between a clean, explainable
                # error and a raw DB traceback surfacing as an
                # ToolErrorType.INTERNAL_BUG if that ever regresses or a
                # future caller reaches this some other way.
                await db.rollback()
                return ToolResult(result={
                    "error": (
                        "This execution already has a pending wakeup scheduled. "
                        "An execution can only call schedule_wakeup once."
                    )
                })

        logger.info(
            "[schedule_wakeup] scheduled",
            extra={
                "execution_id": execution_id,
                "conversation_id": conversation_id,
                "wakeup_id": row.id,
                "wake_at": wake_at.isoformat(),
                "watch_execution_id": watch_execution_id,
            },
        )

        return ToolResult(result={
            "ok": True,
            "wakeup_id": row.id,
            "wake_at": wake_at.isoformat(),
            "watch_execution_id": watch_execution_id,
            "message": (
                "Wakeup scheduled. This execution ends now; a new execution "
                "will continue this conversation when the wakeup fires."
            ),
        })
