# core/execution/worker_finalize.py
#
# Q-01 Phase 4 (worker.py decomposition).
# Flat sibling of
# worker.py, matching the convention already used by every other file in
# core/execution/ (no subdirectories anywhere in this package). Mirrors
# wakeup_scheduler.py's own shape: a focused, single-responsibility module
# operating on already-resolved objects, not partial construction state --
# the most self-contained of Q-01 Phase 3's extracted methods, which is
# exactly why it's the one moved out to its own file.
#
# Neither function here needs `self` -- confirmed during Phase 4 by
# checking that ExecutionWorker's only instance state (self.worker_id,
# self._running, self._active_tasks) is used exclusively by start()/stop()/
# _watchdog_loop()/_safe_process_execution(), none of which live here.
# _finalize_execution() calls _flush_metrics() directly (same module, no
# `self.` needed) rather than through ExecutionWorker.
#
# worker.py keeps a re-export shim (`from core.execution.worker_finalize
# import _finalize_execution, _flush_metrics`) so any existing reference to
# `core.execution.worker._finalize_execution` / `._flush_metrics` keeps
# resolving unchanged -- though as of this move nothing in the test suite
# actually patches either as an instance method (confirmed by grep before
# this file was created), so this shim exists purely for import-site
# compatibility (e.g. `from core.execution.worker import _flush_metrics`),
# not to preserve any patch target.

import asyncio
from datetime import datetime, timezone
from logging import getLogger
from typing import Optional

from core.execution.models import ConversationExecution, ExecutionStatus
from core.db.session import AsyncSessionLocal
from core.db.repository import DBRepo, ExecutionMetricsRepo
from core.memory.extractor import memory_extractor
from core.project.artifact_updater import project_artifact_updater
from core.execution import title_generator
from core.metrics.collector import metrics_collector
from core.hooks import HookRunner
from utils.workspace import get_user_workspace

logger = getLogger(__name__)


# ------------------------------------------------------------
# Post-Execution Finalize
# ------------------------------------------------------------
async def _finalize_execution(
    *,
    execution_id: str,
    execution: ConversationExecution,
    conversation_id: str,
    user_id: str,
    execution_mode: str,
    session: dict,
    resolved_project_id: Optional[str],
    project_status: Optional[str],
    captured_context: dict,
    first_tool_step_msg_id: list,
) -> None:
    """
    Post-execution finalize: roll back cancelled/aborted tool messages,
    fire the three post-execution side effects (memory extraction,
    title generation, project artifact update), flush metrics, and
    fire stop hooks. Q-01 Phase 3, Step 5 extraction; moved to this
    module in Phase 4.

    _process_execution calls this unconditionally in a `finally` clause
    wrapping the construction-through-run span (added alongside the
    exception-skips-metrics-flush fix, 2026-08-17) -- so this now runs on
    every path, including when construction itself raises before
    run_execution()/run_pev_execution() is ever reached.
    """
    # ------------------------------------------------------------
    # Cancelled/aborted execution rollback
    # If the execution was cancelled or aborted mid-ReAct-loop, the
    # tool-call and tool-result messages already persisted to the DB
    # must be deleted.  Without this, the next execution on the same
    # conversation would inherit stale tool history and hallucinate
    # continuing the cancelled task (e.g. "Mumbai weather" leaking
    # into a fresh "Hi" message after model switch + cancel).
    # ------------------------------------------------------------
    _is_terminal_non_success = execution.status in (
        ExecutionStatus.CANCELLED,
        ExecutionStatus.ABORTED,
    )
    if _is_terminal_non_success and first_tool_step_msg_id:
        try:
            async with AsyncSessionLocal() as _rb_db:
                _n_deleted = await DBRepo.delete_messages_from(
                    _rb_db,
                    conversation_id,
                    first_tool_step_msg_id[0],
                )
                logger.info(
                    "Rolled back cancelled execution tool messages",
                    extra={
                        "execution_id": execution_id,
                        "conversation_id": conversation_id,
                        "messages_deleted": _n_deleted,
                        "first_msg_id": first_tool_step_msg_id[0],
                    },
                )
        except Exception as _rb_exc:
            logger.warning(
                "Failed to roll back cancelled execution messages",
                extra={
                    "execution_id": execution_id,
                    "error": str(_rb_exc),
                },
            )

    # ------------------------------------------------------------
    # Fire async memory extraction (non-blocking, post-execution)
    # Extracts user facts (Scope 2) and agent entries (Scope 3)
    # from this conversation.  Never blocks the response.
    # Skip on cancel/abort — there's nothing useful to extract from
    # an incomplete execution, and the messages are now rolled back.
    # ------------------------------------------------------------
    if not _is_terminal_non_success:
        asyncio.create_task(
            memory_extractor.extract_and_store(
                conversation_id=conversation_id,
                user_id=user_id,
                session=session,
                execution_id=execution_id,
                execution_mode=execution_mode,
            )
        )

    # ------------------------------------------------------------
    # Auto-generate conversation title on first message (B-058).
    # Only runs when the conversation title is None / "Untitled"
    # and this is the first user message.  Non-blocking, non-fatal.
    # Publishes a `conversation.titled` SSE event on completion so
    # the frontend sidebar updates live without polling.
    # Title generation is still useful even on cancel (the user did
    # send a real message), so we don't gate this on success.
    # ------------------------------------------------------------
    if not conversation_id.startswith("daemon:"):
        asyncio.create_task(
            title_generator.generate_and_save(
                conversation_id=conversation_id,
                user_id=user_id,
                session=session,
            )
        )

    # ------------------------------------------------------------
    # Fire project artifact update if conversation is in an ACTIVE
    # project.  Updates tasks.eva, knowledge.eva, errors.eva, journal.eva.
    # Never blocks the response.
    # Skip on cancel/abort — partial tool-call results are not useful
    # artifacts, and the messages have been rolled back.
    # B-056 fix: use resolved_project_id (from convo DB row).
    # B-057 fix: only run when project_status == "active".
    # ------------------------------------------------------------
    if not _is_terminal_non_success and resolved_project_id and project_status == "active":
        asyncio.create_task(
            project_artifact_updater.update(
                project_id=resolved_project_id,
                conversation_id=conversation_id,
                user_id=user_id,
                session=session,
                execution_id=execution_id,
                workspace_path=getattr(execution, "project_workspace", None),
            )
        )

    # ------------------------------------------------------------
    # Flush execution metrics to DB
    # Runs after run_execution() regardless of outcome (completed /
    # failed / cancelled).  Non-fatal: a metrics write failure must
    # never surface to the user.
    # ------------------------------------------------------------
    await _flush_metrics(
        execution_id=execution_id,
        conversation_id=conversation_id,
        user_id=user_id,
        execution=execution,
        session=session,
        captured_context=captured_context,
        project_id=resolved_project_id,   # B-060
    )

    # Fire Stop hooks — non-blocking, never delays the response (B-064)
    _hook_defs = session.get("tool_hooks") or []
    if _hook_defs:
        asyncio.create_task(
            HookRunner.fire_event(
                event="stop",
                hook_definitions=_hook_defs,
                user_id=user_id,
                session_id=conversation_id,
                workspace_root=get_user_workspace(user_id),
                extra={"execution_id": execution_id},
            ),
            name=f"hook-stop-{execution_id}",
        )


# ------------------------------------------------------------
# Metrics Flush
# ------------------------------------------------------------
async def _flush_metrics(
    *,
    execution_id: str,
    conversation_id: str,
    user_id: str,
    execution: ConversationExecution,
    session: dict,
    captured_context: dict,
    project_id: Optional[str] = None,   # B-060
) -> None:
    """
    Pop the in-memory metrics snapshot and persist it to the DB.

    Designed to be non-fatal: if the DB write fails, we log a warning
    and move on so that the worker loop is not disrupted.
    """
    try:
        # --- Collect accumulated event-bus metrics ---
        snapshot = metrics_collector.pop_snapshot(execution_id)

        # --- Build tool_metrics list ---
        tool_metrics_list = []
        if snapshot and snapshot.tool_stats:
            tool_metrics_list = [
                {
                    "tool_name":          t.tool_name,
                    "call_count":         t.call_count,
                    "avg_latency_ms":     t.avg_latency_ms,
                    "failure_count":      t.failure_count,
                    "success_count":      t.success_count,
                    "last_denial_reason": t.last_denial_reason,  # Gap 9
                }
                for t in snapshot.tool_stats.values()
            ]

        # --- Token counts from finalizer ---
        usage            = captured_context.get("usage", {})
        prompt_tokens    = usage.get("prompt_tokens")    or 0
        completion_tokens = usage.get("completion_tokens") or 0
        reasoning_tokens  = usage.get("reasoning_tokens")  or 0
        cached_tokens     = usage.get("cached_tokens")     or 0

        # --- Cost intelligence (V4.3) ---
        computed_cost_usd: Optional[float] = None
        try:
            from core.providers.resolver import resolver as _resolver
            # Price the model that actually SERVED the response (usage), not the
            # session's configured primary: after a fallback (or a Phase 5
            # promotion) they differ, and the primary's price was applied to
            # the fallback's tokens.
            _provider = usage.get("provider") or session.get("provider")
            _model    = usage.get("model")    or session.get("model")
            if _provider and _model and (prompt_tokens or completion_tokens):
                computed_cost_usd = _resolver.compute_call_cost(
                    _provider, _model, prompt_tokens, completion_tokens,
                    cached_tokens=cached_tokens,
                )
        except Exception as cost_exc:
            logger.debug("Cost computation failed (non-fatal): %s", cost_exc)

        # --- Final execution status ---
        final_status = execution.status.value if execution.status else "unknown"

        # --- started_at / completed_at from execution object ---
        started_at: Optional[datetime] = None
        if execution.started_at:
            started_at = datetime.fromtimestamp(
                execution.started_at, tz=timezone.utc
            )

        completed_at: Optional[datetime] = None
        if execution.completed_at:
            completed_at = datetime.fromtimestamp(
                execution.completed_at, tz=timezone.utc
            )

        metrics_data = {
            "execution_id":       execution_id,
            "conversation_id":    conversation_id,
            "user_id":            user_id,
            "status":             final_status,
            "termination_reason": snapshot.termination_reason if snapshot else None,
            "error_message":      snapshot.error_message if snapshot else None,
            "duration_ms":        snapshot.duration_ms if snapshot else None,
            # Prefer the actual serving provider/model from the last
            # UsageChunk seen (usage) over the session's configured
            # primary -- when a fallback candidate served the response,
            # `usage` correctly reflects that (each handler stamps its
            # own UsageChunk with provider=PROVIDER_NAME, model=self.model),
            # while `session` is a static snapshot of what was originally
            # configured and never reflects an in-execution switch. Falls
            # back to session config only when usage is empty (e.g. a
            # genuinely empty response with no UsageChunk at all).
            "model_used":         usage.get("model")    or session.get("model"),
            "provider_used":      usage.get("provider") or session.get("provider"),
            "prompt_tokens":      prompt_tokens,
            "completion_tokens":  completion_tokens,
            "reasoning_tokens":   reasoning_tokens,
            "cached_tokens":      cached_tokens,
            "total_steps":        snapshot.total_steps if snapshot else 0,
            "fallbacks_triggered": snapshot.fallbacks_triggered if snapshot else 0,
            "fallback_details":   snapshot.fallback_details if snapshot else None,  # Gap 8
            "subagent_calls":     snapshot.subagent_calls if snapshot else 0,       # Gap 10
            "subagent_failures":  snapshot.subagent_failures if snapshot else 0,    # Gap 10
            "tool_metrics":       tool_metrics_list or None,
            "started_at":         started_at,                                        # Gap 11
            "completed_at":       completed_at,
            "computed_cost_usd":  computed_cost_usd,
            # B-057 / B-060 — execution source / lineage
            "triggered_by":       "conversation",
            "project_id":         project_id,
        }

        async with AsyncSessionLocal() as metrics_db:
            await ExecutionMetricsRepo.upsert(metrics_db, metrics_data)

        # Progressive-disclosure cost, to see how large the promoted set really gets in long runs
        # (it only grows within an execution) and how many turns describe_tool takes.
        _promoted: list = []
        try:
            _reg = getattr(execution, "user_registry", None)
            if _reg is not None:
                _promoted = sorted(getattr(_reg, "_promoted_tools", None) or [])
        except Exception:
            _promoted = []
        _describe_calls = sum(
            t["call_count"] for t in tool_metrics_list if t["tool_name"] == "describe_tool"
        )
        logger.info(
            "Execution metrics persisted",
            extra={
                "execution_id": execution_id,
                "status":       final_status,
                "duration_ms":  metrics_data["duration_ms"],
                "total_steps":  metrics_data["total_steps"],
                "tool_count":   len(tool_metrics_list),
                "promoted_count":      len(_promoted),
                "promoted_tools":      _promoted[:40],
                "describe_tool_calls": _describe_calls,
                "prompt_tokens":       prompt_tokens,
                "completion_tokens":   completion_tokens,
            },
        )

    except Exception as exc:
        # Metrics flush is non-fatal — log and continue
        logger.warning(
            "Failed to flush execution metrics (non-fatal)",
            exc_info=exc,
            extra={"execution_id": execution_id},
        )
        # Clean up in-memory state even on failure
        metrics_collector.clear(execution_id)
