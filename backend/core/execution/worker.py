# core/execution/worker.py

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import getLogger
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.execution.redis_registry import redis_execution_registry
from core.execution.models import ConversationExecution, ExecutionStatus
from core.execution.runner import run_execution
from core.execution.pev_runner import run_pev_execution

from core.db.session import AsyncSessionLocal
from core.db.repository import DBRepo, ExecutionMetricsRepo

from core.tools.user_registry import UserToolRegistry
from core.tools.dispatcher import ToolDispatcher
from core.tools.dynamic_registry import DynamicToolRegistry
from core.mcp.manager import MCPManager

from prompts.soul import build_platform_preamble, build_tool_disclosure_note
from core.memory.provider import DBMemoryProvider
from core.memory.provider_hybrid import HybridMemoryProvider
from core.memory.long_term import LongTermMemoryInjector
from core.memory.engine import MemoryEngine
from core.memory.policies import policy_for
from core.memory.conversation_journal import ConversationJournal
from core.types import StatusChunk

from core.agent_engine import AgentEngine
from core.logging.message_logger import MessageLogger

from config.settings import (
    hydrate_user_session_from_db,
    get_user_session,
    DEFAULT_SESSION,
    WATCHDOG_INTERVAL_SECONDS,
    WATCHDOG_RUNNING_TIMEOUT_SECONDS,
    WATCHDOG_PENDING_TIMEOUT_SECONDS,
    WATCHDOG_APPROVAL_TIMEOUT_SECONDS,
    WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS,
    WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS,
    WORKER_HEARTBEAT_INTERVAL_SECONDS,
    WORKER_HEARTBEAT_KEY_TTL_SECONDS,
    WORKER_LIVENESS_TTL,
)
from core.execution.redis_registry import EXECUTION_KEY_PREFIX
from core.events_publisher import publish_user_event

logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# V5.0 Sprint 2 — Progressive Tool Disclosure
# ---------------------------------------------------------------------------

# Modes that receive the full tool set without category filtering.
# None means "all categories".
TOOL_CATEGORIES_BY_MODE: dict = {
    "conversational": {
        "search", "memory", "communication", "general",
        "filesystem", "git", "shell",
    },
    "agent":        None,   # all categories
    "plan_execute": None,   # all categories
}

# Modes where progressive disclosure is active (manifest + describe_tool).
# In these modes, only describe_tool + promoted tools are sent per turn.
_PROGRESSIVE_MODES = {"conversational", "plan_execute"}

# V6.3 Phase 4: PEV orchestration tools pre-seeded on turn 1 so the model can
# immediately plan/delegate without a describe_tool() round-trip first. Every
# other tool (web_search, shell, git_*, etc.) still requires promotion via
# describe_tool. Conversational mode deliberately does NOT get this pre-seed
# list -- delegate_to_agent must be discovered via describe_tool there too,
# so it doesn't sit next to web_search competing for the model's attention
# on turn 1.
_PEV_PRESEEDED_TOOLS = {"coordinate", "delegate_to_agent", "parallel_delegate", "trigger_daemon"}


@dataclass
class _SessionAndTools:
    """Bundle returned by ExecutionWorker._build_session_and_tools() --
    everything the later phases of _process_execution (system-prompt
    assembly, AgentEngine construction, post-execution gating) need from
    session hydration + tool-building, without each downstream phase
    re-deriving it. A dataclass rather than a growing kwargs dict per the
    Q-01 worker.py decomposition plan (Phase 3, Step 2) -- this is the
    highest-state-output single extraction in that plan, so the shape is
    deliberately explicit and typed instead of an ad-hoc dict."""
    session: dict
    user_tools: UserToolRegistry
    dispatcher: ToolDispatcher
    dynamic_registry: DynamicToolRegistry
    resolved_project_id: Optional[str]
    project_status: Optional[str]
    is_progressive: bool
    preamble_is_progressive: bool
    explicit_tools: list


@dataclass
class _AgentAndHistory:
    """Bundle returned by ExecutionWorker._build_agent_and_history() --
    the constructed AgentEngine, its finalizer closure, the reconstructed
    conversation history, and the mutable rollback-anchor cell the
    on_step_complete closure populates during run_execution(). Q-01 Phase 3,
    Step 4 extraction -- the second highest-state-output phase after Step
    2's session/tool bundle."""
    agent: AgentEngine
    finalizer: Callable
    messages: list
    preamble: list
    captured_context: dict
    # Mutable single-item cell (not a plain str) so the on_step_complete
    # closure captured inside _build_agent_and_history can still populate
    # it *after* this method has already returned -- run_execution() calls
    # that closure during the agent's ReAct loop, well after this bundle
    # is built. The caller's later rollback check reads this same list.
    first_tool_step_msg_id: list


# Q-01 Phase 4: moved to worker_context.py (flat sibling, matches every
# other file in core/execution/ -- see that module's own header comment
# for the full rationale). Re-exported here so every existing
# `patch("core.execution.worker._build_manifest", ...)` /
# `from core.execution.worker import _build_manifest` reference keeps
# resolving unchanged.
from core.execution.worker_context import (  # noqa: E402
    _build_manifest,
    _detect_shell,
    _build_environment_context,
)

# Q-01 Phase 4: _finalize_execution / _flush_metrics moved to
# worker_finalize.py -- see that module's own header comment for the full
# rationale. Both take an explicit `execution`/`session`/etc. parameter set
# already, so no `self.` state was lost in the move (confirmed: neither
# touched self.worker_id/_running/_active_tasks). Re-exported here so any
# existing `from core.execution.worker import _flush_metrics` import site
# keeps resolving unchanged.
from core.execution.worker_finalize import (  # noqa: E402
    _finalize_execution,
    _flush_metrics,
)


# ------------------------------------------------------------
# V6.15.2 (S-09 -- actual root cause fix)
# ------------------------------------------------------------
# runner.py / pev_runner.py each poll the broker for an external cancel
# signal, but that poll lives *inside* their own chunk/step consumption
# loop -- it only gets a turn to run between chunks the agent yields. A
# single tool call in flight yields nothing until it resolves, so the poll
# never runs while a tool call is in progress. That meant
# execution.cancel_token.cancelled -- the flag AgentEngine._execute_tool_
# cancellable() and ShellExecutor poll every ~150ms -- was never actually
# set to True until *after* the in-flight tool call finished on its own.
# The mid-flight cancellation logic was reacting correctly to a signal
# that never arrived in time.
#
# Fix: an independent background task, started once per execution and
# living for its whole lifetime, that polls the broker on its own
# schedule -- decoupled from whatever the main execution coroutine is
# doing (including sitting inside a tool call).
_CANCEL_WATCH_POLL_SECONDS = 0.25


async def _watch_for_external_cancellation(execution: "ConversationExecution") -> None:
    try:
        while True:
            await asyncio.sleep(_CANCEL_WATCH_POLL_SECONDS)
            if execution.cancel_token.cancelled:
                return  # already set via some other path -- nothing left to watch for
            try:
                client = await redis_execution_registry.connect()
                status = await client.hget(
                    f"execution:{execution.execution_id}", "status"
                )
                if status in ("cancelled", "aborted"):
                    execution.cancel_token.cancelled = True
                    logger.info(
                        "External cancel/abort detected via background watcher",
                        extra={
                            "execution_id": execution.execution_id,
                            "broker_status": status,
                        },
                    )
                    return
            except Exception as poll_exc:
                logger.debug(
                    "Cancel-watcher poll failed (will retry)",
                    extra={
                        "execution_id": execution.execution_id,
                        "error": str(poll_exc),
                    },
                )
    except asyncio.CancelledError:
        pass


class ExecutionWorker:
    """
    Headless execution worker.

    Responsibilities:
    - Consume execution jobs from the broker queue
    - Reconstruct execution context
    - Hydrate user preferences
    - Build AgentEngine
    - Execute run_execution()
    - Flush execution metrics to DB
    """

    def __init__(self):
        self._running = False
        # Maps execution_id → asyncio.Task so the watchdog can cancel a stuck
        # task directly (not just update the broker).  A task is removed from this
        # dict via a done-callback the moment it finishes, errors, or is cancelled.
        self._active_tasks: dict[str, "asyncio.Task[None]"] = {}
        # Unique identity for this worker instance.  Generated fresh on every
        # startup (uuid4 guarantees no collision with any previous run).
        #
        # Lifetime of this ID:
        #   • Written to the broker as a liveness beacon  worker:alive:{worker_id}
        #     with a 90-second TTL, refreshed every 30 s in the heartbeat.
        #   • Stamped into the execution hash (worker_id field) at dequeue time.
        #   • Read by the watchdog and orphan recovery to skip executions whose
        #     owner is still alive — preventing cross-worker abort interference.
        self.worker_id: str = str(uuid4())

    # ------------------------------------------------------------
    # Worker Main Loop
    # ------------------------------------------------------------
    async def start(self):
        self._running = True
        logger.info("Execution worker started", extra={"worker_id": self.worker_id})

        # ------------------------------------------------------------------
        # Register this worker's liveness beacon FIRST, before orphan recovery
        # runs.  The beacon is a broker key worker:alive:{worker_id} with a
        # 90-second TTL.  Registering it here means the orphan recovery scan
        # (which runs next) will correctly see this worker as alive and will
        # not accidentally abort executions that belong to us — though in
        # practice a freshly started worker has no owned executions yet.
        #
        # More importantly: in a rolling-update scenario where a NEW worker
        # starts while OLD workers are still running, the OLD workers'
        # liveness beacons will still be alive, so orphan recovery will
        # correctly skip their in-progress executions.
        # ------------------------------------------------------------------
        try:
            await redis_execution_registry.register_worker_liveness(self.worker_id)
            logger.info(
                "Worker liveness beacon registered",
                extra={"worker_id": self.worker_id},
            )
        except Exception as exc:
            logger.warning(
                "Worker liveness registration failed (non-fatal) — ownership tracking degraded",
                exc_info=exc,
            )

        # ------------------------------------------------------------------
        # Startup: recover any orphaned executions left over from a previous
        # worker or API server crash.  This marks PENDING / RUNNING /
        # AWAITING_APPROVAL records as ABORTED so their SSE streams close
        # cleanly and users are not left waiting indefinitely.
        #
        # Ownership-aware: executions owned by a live worker (another running
        # instance) are skipped — only truly orphaned ones are aborted.
        # ------------------------------------------------------------------
        try:
            recovered = await redis_execution_registry.recover_orphaned_executions()
            if recovered:
                logger.warning(
                    "Worker startup: recovered orphaned executions",
                    extra={"count": len(recovered), "execution_ids": recovered},
                )
        except Exception as exc:
            logger.error(
                "Worker startup: orphan recovery failed (non-fatal)",
                exc_info=exc,
            )

        # ------------------------------------------------------------------
        # Launch the stuck-execution watchdog as an independent background
        # task.  It periodically scans all execution hashes and ABORTs any
        # that have not made heartbeat progress within the configured timeout.
        # The first scan fires WATCHDOG_INTERVAL_SECONDS after startup so it
        # does not race with the orphan recovery that just ran above.
        # ------------------------------------------------------------------
        asyncio.create_task(self._watchdog_loop())

        # ------------------------------------------------------------------
        # Main job-dequeue loop.
        #
        # FIX (B-076): The previous backoff sleep on empty-queue polls was
        # causing jobs to be missed.  When the miss counter exceeded 3,
        # the worker slept up to 5 s BETWEEN BLPOP calls.  Jobs enqueued
        # during that sleep sat in the queue up to 10 s before being picked
        # up — and could be consumed by an unknown secondary path that marks
        # them FAILED.
        #
        # BLPOP with timeout=5 already provides efficient blocking wait.
        # A plain consecutive_misses counter still protects against a
        # tight error-spin on unexpected errors, but the backoff sleep is
        # removed so jobs are always dequeued within a few milliseconds of
        # being enqueued.
        #
        # If dequeue_job_blocking returns None repeatedly the loop continues
        # without sleeping (BLPOP provides the natural rate-limit).
        # ------------------------------------------------------------------
        consecutive_misses = 0   # empty-queue timeouts (normal idle polls)
        _last_heartbeat = 0.0

        logger.debug("Worker main loop starting — asyncio.Queue dequeue (timeout=5s)")

        while self._running:
            logger.debug("Worker queue dequeue start (timeout=5s, consecutive_misses=%d)", consecutive_misses)
            execution_id = await redis_execution_registry.dequeue_job_blocking(timeout=5)

            # Write a liveness heartbeat at most once every
            # WORKER_HEARTBEAT_INTERVAL_SECONDS seconds.
            # In LocalBroker mode this is an in-memory write (no network round-trip).
            now = time.monotonic()
            if now - _last_heartbeat >= WORKER_HEARTBEAT_INTERVAL_SECONDS:
                try:
                    hb_client = await redis_execution_registry.connect()
                    pipe = hb_client.pipeline()
                    pipe.set("worker:heartbeat", "1", ex=WORKER_HEARTBEAT_KEY_TTL_SECONDS)
                    pipe.set(
                        f"worker:alive:{self.worker_id}",
                        "1",
                        ex=WORKER_LIVENESS_TTL,
                    )
                    await pipe.execute()
                    _last_heartbeat = now
                except Exception:
                    pass

            if not execution_id:
                # None means the 5-second queue wait timed out (queue empty).
                # Track misses; log a warning every 10 consecutive misses.
                consecutive_misses += 1
                if consecutive_misses % 10 == 0:
                    logger.debug(
                        "Worker: queue empty (timeout — normal)",
                        extra={"consecutive_misses": consecutive_misses},
                    )
                continue

            # Successful dequeue — reset error counter.
            consecutive_misses = 0

            logger.debug(
                "Worker dequeued job — dispatching execution task",
                extra={"execution_id": execution_id, "worker_id": self.worker_id},
            )

            # Claim ownership: stamp the execution hash with this worker's ID
            # before any processing begins.  The watchdog and orphan recovery
            # both check this field — if the owning worker's liveness beacon is
            # still alive they will not touch the execution.
            # Non-fatal: ownership tracking is best-effort; if it fails the
            # watchdog applies standard timeout logic instead — still correct.
            await redis_execution_registry.claim_execution_ownership(
                execution_id, self.worker_id
            )

            # Dispatch each job as an independent asyncio Task so that a job
            # blocked in gate_approval() (blpop wait) does not prevent the
            # main loop from dequeuing and starting subsequent jobs.
            _task = asyncio.create_task(
                self._safe_process_execution(execution_id),
                name=f"exec-{execution_id}",
            )
            self._active_tasks[execution_id] = _task

            # Auto-remove from registry when the task finishes (any outcome).
            def _task_done_cb(t: "asyncio.Task[None]", eid: str = execution_id) -> None:
                self._active_tasks.pop(eid, None)

            _task.add_done_callback(_task_done_cb)

    async def stop(self):
        self._running = False
        logger.info("Execution worker stopped")

    # ------------------------------------------------------------
    # B-059 — Stuck-execution watchdog
    # Runs as a background asyncio.Task for the lifetime of the worker.
    # Scans all execution hashes every WATCHDOG_INTERVAL_SECONDS and
    # ABORTs any that have not progressed within their state timeout.
    # ------------------------------------------------------------
    async def _watchdog_loop(self) -> None:
        """
        Periodically scan all execution hashes and ABORT any stuck in a
        non-terminal state beyond the configured per-state timeout.

        Uses last_heartbeat_at (written by runner heartbeat every 30 s) as
        the primary staleness signal, falling back to started_at / created_at
        for states that may not have a heartbeat yet (PENDING, early RUNNING).
        """
        # Per-state timeout table keyed by raw status string value.
        _TIMEOUTS = {
            ExecutionStatus.RUNNING.value:                WATCHDOG_RUNNING_TIMEOUT_SECONDS,
            ExecutionStatus.PLANNING.value:               WATCHDOG_RUNNING_TIMEOUT_SECONDS,
            ExecutionStatus.VERIFYING.value:              WATCHDOG_RUNNING_TIMEOUT_SECONDS,
            ExecutionStatus.PENDING.value:                WATCHDOG_PENDING_TIMEOUT_SECONDS,
            ExecutionStatus.AWAITING_APPROVAL.value:      WATCHDOG_APPROVAL_TIMEOUT_SECONDS,
            ExecutionStatus.AWAITING_PLAN_APPROVAL.value: WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS,
            # V6.12 added this as a third sibling HitL gate alongside the two
            # above (see core/execution/gate_markers.py's own docstring, which
            # lists all three together) but never added it here -- a user who
            # never responds to a step-limit-extension prompt left the
            # execution stuck in this state with no watchdog coverage at all,
            # unlike its two siblings (V6.54). Its own independently-tunable
            # setting (not shared with the other two) so each gate can be
            # adjusted on its own later.
            ExecutionStatus.AWAITING_STEP_EXTENSION.value: WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS,
        }

        logger.info(
            "Execution watchdog started",
            extra={"interval_seconds": WATCHDOG_INTERVAL_SECONDS},
        )

        while self._running:
            # Sleep FIRST so the first scan fires one interval after startup,
            # avoiding a race with the orphan recovery that just completed.
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)

            try:
                client = await redis_execution_registry.connect()
                now_utc = datetime.now(timezone.utc)
                aborted: list[str] = []

                async for key in client.scan_iter(f"{EXECUTION_KEY_PREFIX}*"):
                    try:
                        if await client.type(key) != "hash":
                            continue

                        data = await client.hgetall(key)
                        status_raw = data.get("status")
                        if status_raw not in _TIMEOUTS:
                            continue  # terminal or unrecognised state — skip

                        timeout_secs = _TIMEOUTS[status_raw]

                        # Best timestamp anchor for age calculation:
                        # last_heartbeat_at is freshest (written every 30 s
                        # during active execution). Fall back to state-entry
                        # timestamps for states with no heartbeat yet.
                        ts_str = (
                            data.get("last_heartbeat_at")
                            or data.get("started_at")
                            or data.get("planning_started_at")
                            or data.get("created_at")
                        )
                        if not ts_str:
                            continue

                        try:
                            ts = datetime.fromisoformat(ts_str).replace(
                                tzinfo=timezone.utc
                            )
                        except ValueError:
                            continue

                        age_secs = (now_utc - ts).total_seconds()

                        # ── Execution ownership check ─────────────────────────
                        # Before acting on a potentially stuck execution, verify
                        # that we are the legitimate owner.
                        #
                        # Three cases:
                        #   A. No worker_id → orphaned (no owner ever claimed it,
                        #      or pre-ownership-tracking record).  Apply normal
                        #      timeout; this worker may abort it.
                        #
                        #   B. worker_id == self.worker_id → this worker owns it.
                        #      Apply normal timeout; abort our own stuck task.
                        #
                        #   C. worker_id == foreign ID → another worker instance
                        #      owns this execution.
                        #      • If that worker's liveness beacon is alive:
                        #        SKIP — do not interfere.
                        #      • If the beacon has expired (worker crashed):
                        #        apply 2× grace-period timeout before taking over,
                        #        so a transient write failure doesn't trigger
                        #        premature takeover.
                        # ──────────────────────────────────────────────────────
                        owner_worker_id = data.get("worker_id")
                        _is_takeover = False   # True when we're recovering a dead worker's job

                        if owner_worker_id and owner_worker_id != self.worker_id:
                            # Foreign execution — check if the owner is alive.
                            owner_alive = await redis_execution_registry.is_worker_alive(
                                owner_worker_id
                            )
                            if owner_alive:
                                # Owner is healthy — do NOT touch this execution.
                                continue

                            # Owner is dead.  Apply a 2× grace period to avoid
                            # races where the owner's last heartbeat was delayed
                            # by a brief network hiccup rather than a true crash.
                            doubled_timeout = timeout_secs * 2
                            if age_secs < doubled_timeout:
                                continue  # still within dead-owner grace period

                            _is_takeover = True  # flag for richer log message below

                        # ── Standard age check (and dead-owner grace check above) ─
                        if age_secs < timeout_secs:
                            continue  # healthy — not stuck

                        execution_id = key.split(EXECUTION_KEY_PREFIX, 1)[-1]
                        user_id = data.get("user_id", "")

                        if _is_takeover:
                            logger.warning(
                                "Watchdog: taking over execution from dead worker",
                                extra={
                                    "execution_id":    execution_id,
                                    "dead_worker_id":  owner_worker_id,
                                    "status":          status_raw,
                                    "age_seconds":     int(age_secs),
                                    "grace_timeout":   timeout_secs * 2,
                                },
                            )
                        else:
                            logger.warning(
                                "Watchdog: aborting stuck execution",
                                extra={
                                    "execution_id":  execution_id,
                                    "status":        status_raw,
                                    "age_seconds":   int(age_secs),
                                    "timeout_seconds": timeout_secs,
                                    "owner":         owner_worker_id or "none (orphaned)",
                                },
                            )

                        await redis_execution_registry.mark_execution_aborted(
                            execution_id,
                            reason=f"watchdog_timeout_{status_raw}",
                        )
                        aborted.append(execution_id)

                        # Cancel the asyncio Task directly so the coroutine
                        # unblocks immediately (e.g. stuck inside an LLM call).
                        # For takeovers the task belongs to a dead worker process —
                        # it won't be in self._active_tasks, so the cancel() call
                        # is a no-op (the task object no longer exists).
                        # The task's CancelledError handler in runner.py / pev_runner.py
                        # will attempt its own cleanup — that's fine because the
                        # state-machine transition is idempotent on same-state and
                        # mark_execution_aborted() is guarded by a broad except.
                        _stuck_task = self._active_tasks.get(execution_id)
                        if _stuck_task and not _stuck_task.done():
                            logger.warning(
                                "Watchdog: cancelling stuck asyncio task",
                                extra={
                                    "execution_id": execution_id,
                                    "task_name": _stuck_task.get_name(),
                                },
                            )
                            _stuck_task.cancel()

                        # B-059: mirror ABORTED status into execution_metrics so
                        # mid-run disturbances (crash, OOM) don't leave permanent
                        # "running" rows in the DB. No-op if row doesn't exist yet.
                        try:
                            async with AsyncSessionLocal() as _wd_db:
                                await ExecutionMetricsRepo.mark_aborted(
                                    _wd_db,
                                    execution_id,
                                    reason=f"watchdog_timeout_{status_raw}",
                                )
                        except Exception as _wd_exc:
                            logger.debug(
                                "Watchdog: could not update execution_metrics for %s: %s",
                                execution_id, _wd_exc,
                            )

                        # Notify the user's SSE stream so the frontend can
                        # close the loading state cleanly.
                        if user_id:
                            await publish_user_event(
                                user_id=user_id,
                                event="execution.aborted",
                                execution_id=execution_id,
                                summary=(
                                    f"Execution timed out after {int(age_secs)}s "
                                    f"stuck in {status_raw} state."
                                ),
                            )

                    except Exception as exc:
                        # Per-key errors are non-fatal — log and continue scan.
                        logger.debug(
                            "Watchdog: error processing key %s (skipping): %s",
                            key, exc,
                        )
                        continue

                if aborted:
                    logger.warning(
                        "Watchdog: scan complete — aborted stuck executions",
                        extra={"count": len(aborted), "execution_ids": aborted},
                    )

            except Exception as exc:
                # Scan-level errors are non-fatal — log and wait for next interval.
                logger.error(
                    "Watchdog: scan cycle failed (non-fatal — will retry next interval)",
                    exc_info=exc,
                )

    # ------------------------------------------------------------
    # Safe wrapper — used as the asyncio.create_task() target so
    # that exceptions from _process_execution() are logged and do
    # not silently vanish inside an unawaited Task.
    # ------------------------------------------------------------
    async def _safe_process_execution(self, execution_id: str):
        try:
            await self._process_execution(execution_id)
        except Exception as exc:
            logger.error(
                "Worker failed while processing execution",
                exc_info=exc,
                extra={"execution_id": execution_id},
            )
            # Without this, an exception from ANYWHERE inside
            # _process_execution (provider handler construction, tool
            # dispatch, a DB write, ...) leaves the execution stuck at
            # "pending" forever -- api/chat.py already returned 200 with
            # this execution_id before processing started, so there is no
            # synchronous error path left, and nothing else ever
            # transitions a crashed execution out of "pending". Applies
            # regardless of which provider/handler/model was involved --
            # this is the one place that sees every execution regardless
            # of where inside processing it failed. Reuses the exact same
            # mark_execution_failed + publish_user_event pairing already
            # used a few dozen lines below for the "no provider/model
            # configured" guard, not a new mechanism.
            try:
                client = await redis_execution_registry.connect()
                data = await client.hgetall(f"execution:{execution_id}")
                user_id = data.get("user_id") if data else None
                error_summary = f"Execution failed: {type(exc).__name__}: {exc}"
                await redis_execution_registry.mark_execution_failed(execution_id, error_summary)
                if user_id:
                    await publish_user_event(
                        user_id=user_id,
                        event="execution.failed",
                        execution_id=execution_id,
                        summary=error_summary,
                    )
            except Exception as _mark_exc:
                logger.error(
                    "Worker: could not mark execution failed after crash "
                    "(execution will remain stuck at its last known status)",
                    exc_info=_mark_exc,
                    extra={"execution_id": execution_id},
                )

    # ------------------------------------------------------------
    # Session Hydration + Tool Building
    # ------------------------------------------------------------
    async def _build_session_and_tools(
        self,
        *,
        execution_id: str,
        execution: ConversationExecution,
        conversation_id: str,
        user_id: str,
        execution_mode: str,
    ) -> Optional[_SessionAndTools]:
        """
        Hydrate the user session from DB, build the tool registries for
        this execution, and resolve project context -- the first
        substantive phase of _process_execution (Q-01 Phase 3, Step 2
        extraction).

        Returns None when the execution must stop here: either no
        provider/model is configured, or the linked project is archived.
        In both cases this method has already marked the execution failed
        and published the execution.failed SSE event itself -- the caller
        (_process_execution) just needs to return immediately on a None
        result, exactly as it did before this was a separate method.
        """
        # ------------------------------------------------------------
        # Hydrate User Preferences from DB
        # ------------------------------------------------------------
        async with AsyncSessionLocal() as db:
            prefs = await DBRepo.get_user_preferences(db, user_id)

            if prefs:
                # Single canonical mapping — see core/auth/hydration.py (4.1 fix)
                from core.auth.hydration import build_prefs_dict
                hydrate_user_session_from_db(user_id, build_prefs_dict(prefs))
            else:
                hydrate_user_session_from_db(user_id, DEFAULT_SESSION)

        session = get_user_session(user_id)

        logger.info(
            "User session hydrated",
            extra={
                "execution_id": execution_id,
                "provider": session.get("provider"),
                "model": session.get("model"),
                "execution_mode": execution_mode,
            },
        )

        # No hardcoded provider/model fallback (V6.4/2026-07-27): EVA has no
        # default model of its own. api/chat.py already rejects this
        # synchronously for the normal /chat path -- this is the defensive
        # backstop for any execution that reaches processing another way
        # (e.g. a step-extension or plan-approval continuation), mirroring
        # the C3-b archived-project hard-stop just below.
        if not session.get("provider") or not session.get("model"):
            logger.warning(
                "Execution rejected — no provider/model configured",
                extra={"execution_id": execution_id, "user_id": user_id},
            )
            try:
                await redis_execution_registry.mark_execution_failed(
                    execution_id,
                    "No provider/model configured for this session. "
                    "Configure one via POST /set-session before sending messages.",
                )
                await publish_user_event(
                    user_id=user_id,
                    event="execution.failed",
                    execution_id=execution_id,
                    summary="No provider/model configured. Execution rejected.",
                )
            except Exception as _stop_exc:
                logger.debug("Provider guard: could not write fail state: %s", _stop_exc)
            return None  # caller exits _process_execution cleanly — no metrics row

        # V6.59 Phase 5: run-time usability gate (core/providers/usability.py).
        # /chat checked this when the message arrived, but a continuation
        # (step extension, plan approval) or a key removed in between can reach
        # here with a stale target. Drop unusable candidates; if the primary is
        # unusable but a fallback is not, run on the fallback (previously the
        # handler could crash at construction, e.g. Anthropic with no key, even
        # with a good fallback configured); if nothing is usable, fail now with
        # the reason. `session` is rebound to a COPY -- the user's stored
        # session is never altered by a single run's pruning.
        from core.providers.usability import plan_run, session_for_plan
        _plan = plan_run(session["provider"], session["model"], session.get("fallback_chain"))
        if not _plan.ok:
            logger.warning(
                "Execution rejected — no usable provider/model",
                extra={"execution_id": execution_id, "user_id": user_id,
                       "reason": getattr(_plan.primary_problem, "reason", None)},
            )
            try:
                await redis_execution_registry.mark_execution_failed(
                    execution_id, _plan.failure_message(),
                )
                await publish_user_event(
                    user_id=user_id,
                    event="execution.failed",
                    execution_id=execution_id,
                    summary=_plan.failure_message(),
                )
            except Exception as _stop_exc:
                logger.debug("Usability gate: could not write fail state: %s", _stop_exc)
            return None
        if _plan.skipped:
            session = session_for_plan(session, _plan)
            if _plan.promoted:
                logger.warning(
                    "Configured provider unusable (%s) — running on fallback %s/%s",
                    _plan.primary_problem.reason, session["provider"], session["model"],
                    extra={"execution_id": execution_id},
                )
                try:
                    await publish_user_event(
                        user_id=user_id,
                        event="provider.fallback_promoted",
                        execution_id=execution_id,
                        summary=(f"{_plan.primary_problem.message} "
                                 f"Running on {session['provider']}/{session['model']} instead."),
                    )
                except Exception as _evt_exc:
                    logger.debug("Usability gate: could not publish promotion notice: %s", _evt_exc)

        if execution_mode == "plan_execute":
            logger.info(
                "PLAN_EXECUTE mode — routing to PEV loop",
                extra={"execution_id": execution_id, "execution_mode": execution_mode},
            )
        else:
            logger.debug(
                "CONVERSATIONAL mode — standard AgentEngine path",
                extra={"execution_id": execution_id},
            )

        # ------------------------------------------------------------
        # Build Tools (V5.0 Sprint 2: category filtering + progressive disclosure)
        # ------------------------------------------------------------
        _allowed_categories = TOOL_CATEGORIES_BY_MODE.get(execution_mode)
        _is_progressive = execution_mode in _PROGRESSIVE_MODES

        user_tools = UserToolRegistry(
            default_tools=True,
            enabled_tools=session.get("tools", []),
            execution_mode=execution_mode,
            progressive=_is_progressive,
            allowed_categories=_allowed_categories,   # None = no category filter
        )

        # Pre-promote all tools explicitly configured in the user's session.
        # When a caller sets session.tools = ["memory_store", "web_search", ...],
        # those tools should be available in the LLM's tool list on the FIRST turn —
        # without requiring the model to call describe_tool() first.
        # Progressive disclosure still applies to all OTHER tools (not listed here).
        _explicit_tools: list = session.get("tools", [])
        for _tool_name in _explicit_tools:
            user_tools.promote(_tool_name)

        # V6.3 Phase 4: pre-seed orchestration tools for PEV mode so the model
        # can plan/delegate on turn 1 without a describe_tool() round-trip.
        # Conversational mode intentionally does NOT pre-seed these -- see
        # _PEV_PRESEEDED_TOOLS comment above.
        if execution_mode == "plan_execute":
            for _tool_name in _PEV_PRESEEDED_TOOLS:
                user_tools.promote(_tool_name)

        # When explicit tools are pre-promoted, the progressive-disclosure
        # instructions in the platform preamble / manifest are redundant and
        # confusing — they tell the model to call describe_tool() first even
        # though the tools are already active.  Suppress them in this case.
        _preamble_is_progressive = _is_progressive and not bool(_explicit_tools)

        # Attach registry to execution so describe_tool can call promote().
        execution.user_registry = user_tools

        dispatcher = ToolDispatcher()

        # Build DynamicToolRegistry and load MCP tools for this user.
        # Per-config errors are captured and stored in the execution broker hash
        # so callers can surface them via GET /executions/{id}/trace.
        dynamic_registry = DynamicToolRegistry()

        # Wire it onto user_tools so describe_tool() can resolve MCP tools by
        # name via UserToolRegistry.promote() -- see core/tools/user_registry.py.
        user_tools.dynamic_registry = dynamic_registry

        # B-053 / B-056 / B-057: resolve project context from the conversation (if any).
        # - allowed_mcp_servers: used for MCP scoping (B-053)
        # - resolved_project_id: used to fire the artifact updater post-execution (B-056 fix)
        # - project_status: gates context injection and artifact updater (B-057)
        #   active  → full context injection + artifact updater (normal behaviour)
        #   paused  → no context injection, no artifact updater (runs as standalone)
        #   archived → same as paused (hard stop enforced at API level before reaching here)
        allowed_mcp_servers = None
        resolved_project_id = None
        project_status = None
        if conversation_id and not conversation_id.startswith("daemon:"):
            try:
                async with AsyncSessionLocal() as _proj_db:
                    from core.db.repository import ProjectRepo as _ProjRepo
                    convo = await DBRepo.get_conversation(_proj_db, conversation_id)
                    if convo and getattr(convo, "project_id", None):
                        resolved_project_id = convo.project_id
                        proj = await _ProjRepo.get(
                            _proj_db,
                            project_id=convo.project_id,
                            user_id=user_id,
                        )
                        if proj:
                            project_status = proj.status
                            # B-082: workspace comes from the conversation's own project,
                            # not from whichever project is currently "active".
                            if proj.workspace_path:
                                execution.project_workspace = proj.workspace_path
                                logger.debug(
                                    "Project workspace attached to execution",
                                    extra={
                                        "execution_id": execution_id,
                                        "project_id": resolved_project_id,
                                        "workspace_path": proj.workspace_path,
                                    },
                                )
                            if getattr(proj, "allowed_mcp_servers", None):
                                allowed_mcp_servers = proj.allowed_mcp_servers
            except Exception as _exc:
                logger.debug("B-053/B-056/B-057: could not resolve project context: %s", _exc)

        # C3-b: Hard-stop if the project is archived.
        # The API gate blocks new conversations in archived projects, but an
        # existing conversation may reach here if the project was archived
        # mid-session.  Failing hard prevents polluting the project archive
        # with new execution data.
        if resolved_project_id and project_status == "archived":
            logger.warning(
                "Execution rejected — project is archived",
                extra={
                    "execution_id": execution_id,
                    "project_id": resolved_project_id,
                    "conversation_id": conversation_id,
                },
            )
            try:
                await redis_execution_registry.mark_execution_failed(
                    execution_id,
                    "Project is archived. No new executions are allowed.",
                )
                await publish_user_event(
                    user_id=user_id,
                    event="execution.failed",
                    execution_id=execution_id,
                    summary="Project is archived. Execution rejected.",
                )
            except Exception as _stop_exc:
                logger.debug("C3-b: could not write fail state: %s", _stop_exc)
            return None  # caller exits _process_execution cleanly — no metrics row

        # G-003: Thread project_id into execution context so delegate_tool
        # can enforce project-scoped agent access at invocation time.
        execution.parent_project_id = resolved_project_id

        async with AsyncSessionLocal() as mcp_db:
            try:
                mcp_errors = await MCPManager(registry=dynamic_registry).register_tools_for_user(
                    mcp_db, user_id, allowed_mcp_servers=allowed_mcp_servers
                )
                if mcp_errors:
                    import json as _json
                    logger.warning(
                        "MCP tool registration errors (non-fatal)",
                        extra={
                            "user_id": user_id,
                            "execution_id": execution_id,
                            "errors": mcp_errors,
                        },
                    )
                    adapter = await redis_execution_registry.connect()
                    await adapter.hset(
                        f"execution:{execution_id}",
                        "mcp_errors",
                        _json.dumps(mcp_errors),
                    )
            except Exception as mcp_exc:
                logger.warning(
                    "MCP tool registration failed (non-fatal)",
                    exc_info=mcp_exc,
                    extra={"user_id": user_id, "execution_id": execution_id},
                )

        return _SessionAndTools(
            session=session,
            user_tools=user_tools,
            dispatcher=dispatcher,
            dynamic_registry=dynamic_registry,
            resolved_project_id=resolved_project_id,
            project_status=project_status,
            is_progressive=_is_progressive,
            preamble_is_progressive=_preamble_is_progressive,
            explicit_tools=_explicit_tools,
        )

    # ------------------------------------------------------------
    # System Prompt Assembly
    # ------------------------------------------------------------
    async def _assemble_system_prompt(
        self,
        *,
        execution_id: str,
        conversation_id: str,
        user_id: str,
        execution_mode: str,
        resolved_project_id: Optional[str],
        project_status: Optional[str],
        explicit_tools: list,
        session: dict,
        preamble_is_progressive: bool,
        user_tools: UserToolRegistry,
        dynamic_registry: DynamicToolRegistry,
        initial_message: str = "",
        working_folder_kind: Optional[str] = None,
    ) -> str:
        """
        Assemble the full system prompt for this execution -- Tier 1
        (platform preamble) through Tier 3 (project / long-term-memory /
        environment / skills / tool-manifest context blocks). Q-01 Phase 3,
        Step 3 extraction.
        Unlike Step 2's _build_session_and_tools (multi-field
        bundle), this phase produces exactly one value downstream code
        needs -- the assembled prompt string -- so no dataclass is needed.
        """
        # ------------------------------------------------------------
        # Build Long-Term Memory block (Scopes 1–3)
        # Augments the base system prompt with injected memory context.
        # Non-fatal: failures are logged and the base prompt is used as-is.
        # ------------------------------------------------------------
        # B-054: per-conversation system_prompt (replaces session-level)
        base_system_prompt = ""
        if conversation_id and not conversation_id.startswith("daemon:"):
            try:
                async with AsyncSessionLocal() as _sp_db:
                    _convo = await DBRepo.get_conversation(_sp_db, conversation_id)
                    if _convo:
                        base_system_prompt = getattr(_convo, "system_prompt", None) or ""
            except Exception as _sp_exc:
                logger.debug(
                    "B-054: could not resolve conversation system_prompt: %s", _sp_exc
                )
        # ── Soul Layer: Three-Tier System Prompt Assembly (V5.0 Sprint 1/2) ────────
        # Tier 1: EVA Platform Preamble — always present; defines EVA's identity,
        #         operating principles, and (in conversational mode) progressive tool
        #         disclosure instructions.
        # Tier 2: User/Agent System Prompt — conversation.system_prompt.  Extends or
        #         specialises the preamble (e.g. "You are EVA acting as a Python expert").
        #         Empty string = no customisation; preamble stands alone.
        # Tier 3: Runtime Context — project block + memory block + skills block +
        #         tool manifest (progressive mode only), injected by sections below.
        #
        # Scope: applies only to executions that go through worker.py (regular chat /
        # agent / plan_execute).  Daemons and subagents build AgentEngine directly and
        # are intentionally excluded.
        _platform_preamble = build_platform_preamble()
        # In non-progressive modes (or when explicit tools are pre-promoted)
        # the preamble's progressive tool access section does not apply —
        # append a short clarifying note instead.
        _tool_disclosure_note = build_tool_disclosure_note(progressive=preamble_is_progressive)
        if _tool_disclosure_note:
            _platform_preamble = _platform_preamble + "\n\n" + _tool_disclosure_note

        if base_system_prompt:
            augmented_system_prompt = (
                _platform_preamble + "\n\n---\n\n" + base_system_prompt
            )
        else:
            augmented_system_prompt = _platform_preamble

        # ------------------------------------------------------------
        # Inject Project context block (B-057)
        # Only when conversation belongs to an ACTIVE project.
        # Uses "focused" tier (~600 tokens) — covers synopsis, goals,
        # constraints, tasks summary, and recent knowledge entries.
        # Non-fatal: failures are logged, execution proceeds without it.
        # ------------------------------------------------------------
        if resolved_project_id and project_status == "active":
            try:
                from core.project.manager import ProjectManager as _PM  # noqa: PLC0415
                project_block = _PM.load_context(
                    user_id=user_id,
                    project_id=resolved_project_id,
                    tier="focused",
                )
                if project_block:
                    sep = "\n\n" if augmented_system_prompt else ""
                    augmented_system_prompt = augmented_system_prompt + sep + project_block
                    logger.debug(
                        "Project context injected into system prompt",
                        extra={
                            "execution_id": execution_id,
                            "project_id": resolved_project_id,
                            "context_chars": len(project_block),
                        },
                    )
            except Exception as _proj_ctx_exc:
                logger.warning(
                    "Project context injection failed (non-fatal) — skipping",
                    exc_info=_proj_ctx_exc,
                    extra={"execution_id": execution_id, "project_id": resolved_project_id},
                )

        # When memory_recall is in the explicitly configured tools, suppress
        # auto-injection of user facts into the system prompt.  Injecting facts
        # passively makes memory_recall redundant — the model sees the answer
        # in context and never calls the tool.  With suppression on, the model
        # receives a pointer hint and must call memory_recall to get the facts.
        _suppress_user_memory_inject = "memory_recall" in explicit_tools

        try:
            async with AsyncSessionLocal() as mem_db:
                injector = LongTermMemoryInjector(
                    db=mem_db,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    session=session,
                    suppress_user_facts=_suppress_user_memory_inject,
                    project_id=resolved_project_id,  # B-076: enables auto-scope
                )
                memory_block = await injector.build_memory_block()
            if memory_block:
                sep = "\n\n" if augmented_system_prompt else ""
                augmented_system_prompt = augmented_system_prompt + sep + memory_block
                logger.debug(
                    "Long-term memory injected into system prompt",
                    extra={
                        "execution_id": execution_id,
                        "memory_block_chars": len(memory_block),
                        "suppress_user_facts": _suppress_user_memory_inject,
                    },
                )
        except Exception as ltm_exc:
            logger.warning(
                "Long-term memory injection failed (non-fatal) — using base prompt",
                exc_info=ltm_exc,
                extra={"execution_id": execution_id},
            )

        # ------------------------------------------------------------
        # Inject Environment Context block (V5.5)
        # Always injected — contains current date/time (fresh each execution),
        # auto-detected OS/shell, and any user-configured location/timezone.
        # Non-fatal: failures are logged and execution proceeds without it.
        # ------------------------------------------------------------
        try:
            # working_folder_kind is computed by the caller (which owns the execution object): which
            # folder relative paths land in -- only the KIND, never a local path.
            _env_block = _build_environment_context(session, working_folder_kind)
            if _env_block:
                sep = "\n\n" if augmented_system_prompt else ""
                augmented_system_prompt = augmented_system_prompt + sep + _env_block
                logger.debug(
                    "Environment context injected into system prompt",
                    extra={"execution_id": execution_id},
                )
        except Exception as _env_exc:
            logger.warning(
                "Environment context injection failed (non-fatal) — skipping",
                exc_info=_env_exc,
                extra={"execution_id": execution_id},
            )

        # ------------------------------------------------------------
        # Inject Available Skills block (V3.1)
        # Appended after the memory block when skills_mode is "auto" or "enabled".
        # Non-fatal: failures are logged and the current prompt is kept as-is.
        # ------------------------------------------------------------
        skills_mode = session.get("skills_mode", "auto")
        if skills_mode in ("auto", "enabled"):
            try:
                from core.skills.registry import get_skill_registry  # noqa: PLC0415
                skill_registry = get_skill_registry(user_id)
                skills_block = skill_registry.build_available_skills_block(initial_message)
                if skills_block:
                    sep = "\n\n" if augmented_system_prompt else ""
                    augmented_system_prompt = augmented_system_prompt + sep + skills_block
                    logger.debug(
                        "Skills block injected into system prompt",
                        extra={
                            "execution_id": execution_id,
                            "skill_count": skill_registry.count,
                        },
                    )
            except Exception as skills_exc:
                logger.warning(
                    "Skills injection failed (non-fatal) — skipping",
                    exc_info=skills_exc,
                    extra={"execution_id": execution_id},
                )

        # ------------------------------------------------------------
        # Inject PII Redaction disclosure (S-03)
        # When pii_redaction_enabled, the user's message (and any tool
        # output passed through the "redact_pii" post-hook) may contain
        # placeholders like "<EMAIL_ADDRESS>" in place of real PII. Without
        # this notice the model may treat a placeholder as literal garbage,
        # try to guess the real value, or ask the user to "fill in" what
        # looks like missing data. Injected whenever the toggle is on for
        # the session -- not only on turns where this specific message had
        # a hit -- since an earlier turn's redacted tool output may still be
        # sitting in the conversation history the model is reading now, and
        # this block is cheap enough to never gate on that recheck.
        # Non-fatal: failures are logged and the current prompt is kept as-is.
        # ------------------------------------------------------------
        if session.get("pii_redaction_enabled"):
            try:
                _pii_notice = (
                    "Some personally identifiable information in this conversation "
                    "has been automatically redacted for privacy and replaced with "
                    "placeholders (e.g. <EMAIL_ADDRESS>, <PHONE_NUMBER>, <US_SSN>). "
                    "Treat these as intentional, not missing data -- don't ask the "
                    "user to fill them in unless the task genuinely requires the "
                    "real value, and don't guess or invent one."
                )
                sep = "\n\n" if augmented_system_prompt else ""
                augmented_system_prompt = augmented_system_prompt + sep + _pii_notice
            except Exception as pii_notice_exc:
                logger.warning(
                    "PII redaction disclosure injection failed (non-fatal) — skipping",
                    exc_info=pii_notice_exc,
                    extra={"execution_id": execution_id},
                )

        # ------------------------------------------------------------
        # Inject Tool Manifest block (V5.0 Sprint 2 — Tier 3 addition)
        # Only in progressive modes (conversational, plan_execute) AND only
        # when explicit tools haven't been pre-promoted.  When tools are
        # pre-promoted (explicit session.tools, or V6.3 Phase 4's PEV
        # orchestration pre-seed), the model already sees them in its native
        # tool list — a manifest listing them as "use describe_tool() to
        # activate" would be contradictory.  Already-active tools are
        # filtered out of the manifest listing below for exactly this reason.
        # ------------------------------------------------------------
        if preamble_is_progressive:
            try:
                _active_tool_names = set(user_tools.get_active_tool_classes().keys())
                _manifest_tools = {
                    name: cls for name, cls in user_tools.all_tools().items()
                    if name not in _active_tool_names
                }
                # Include MCP tools synced above so the model learns they
                # exist and can call describe_tool() on them -- without this
                # they're promotable (via UserToolRegistry.promote()) but
                # never discoverable in the first place.
                if dynamic_registry is not None:
                    from core.tools.tool_adapter import build_toolbase_subclass_from_manifest
                    for _mcp_manifest in dynamic_registry.list_tools():
                        if _mcp_manifest.name not in _active_tool_names:
                            _manifest_tools[_mcp_manifest.name] = build_toolbase_subclass_from_manifest(_mcp_manifest)
                _manifest_block = _build_manifest(_manifest_tools, execution_mode)
                if _manifest_block:
                    sep = "\n\n" if augmented_system_prompt else ""
                    augmented_system_prompt = augmented_system_prompt + sep + _manifest_block
                    logger.debug(
                        "Tool manifest injected into system prompt",
                        extra={
                            "execution_id": execution_id,
                            "tool_count": len(_manifest_tools),
                            "execution_mode": execution_mode,
                        },
                    )
            except Exception as _manifest_exc:
                logger.warning(
                    "Tool manifest injection failed (non-fatal) — skipping",
                    exc_info=_manifest_exc,
                    extra={"execution_id": execution_id},
                )

        return augmented_system_prompt

    # ------------------------------------------------------------
    # AgentEngine + History Construction
    # ------------------------------------------------------------
    async def _build_agent_and_history(
        self,
        *,
        db: AsyncSession,
        execution_id: str,
        execution: ConversationExecution,
        conversation_id: str,
        user_id: str,
        session_id: str,
        execution_mode: str,
        session: dict,
        user_tools: UserToolRegistry,
        dispatcher: ToolDispatcher,
        dynamic_registry: DynamicToolRegistry,
        augmented_system_prompt: str,
    ) -> _AgentAndHistory:
        """
        Build the memory provider, AgentEngine, and reconstructed
        conversation history for this execution -- Q-01 Phase 3, Step 4
        extraction.

        IMPORTANT -- `db` session lifetime contract: `db` is the caller's
        own AsyncSession, opened by _process_execution's own
        `async with AsyncSessionLocal() as db:` block. It is NOT opened or
        closed here. MessageLogger is built from `db` and is captured by
        the returned `finalizer` closure, which run_execution() /
        run_pev_execution() calls later -- potentially minutes after this
        method returns, once the agent's response is ready. The caller
        MUST keep its own `async with db:` block open for the entire
        duration of run_execution()/run_pev_execution(), exactly as
        _process_execution already does; closing it early would make
        finalizer() write through an already-closed session. This is
        pre-existing, intentional (if unusual) behavior that this
        extraction preserves exactly, not something it changes -- and is
        exactly the kind of non-obvious coupling that made this the
        highest-risk remaining Step in the Q-01 plan.
        """
        db_memory = DBMemoryProvider(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
            model_name=session["model"],
            provider=session["provider"],
            api_key=session.get("api_key"),
            system_prompt=augmented_system_prompt,
            fallback_chain=session.get("fallback_chain"),
        )

        memory_provider = HybridMemoryProvider(
            db_provider=db_memory,
            conversation_id=conversation_id,
        )

        message_logger = MessageLogger(db, conversation_id, user_id)

        # Journal: execution boundary marker (sync, never raises)
        ConversationJournal.log(user_id, conversation_id, {
            "type": "execution_start",
            "execution_id": execution_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": session.get("model"),
            "provider": session.get("provider"),
            "execution_mode": execution_mode,
        })

        # Tracks the first tool-step message written during this execution
        # so it can be used as a rollback anchor if the execution is cancelled.
        _first_tool_step_msg_id: list[str] = []  # use a list so the closure can mutate it

        async def _save_tool_step(tool_calls, tool_results):
            """
            Persist one ReAct tool-call step:
              - one assistant message with content=None and all tool_calls
              - one tool-result message per tool call
            Opens its own DB session so it's independent of the outer session lifecycle.
            """
            async with AsyncSessionLocal() as step_db:
                # Save assistant message representing the tool-call turn
                assistant_msg = await DBRepo.add_message(
                    db=step_db,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=None,
                    mtype="tool_call",
                    # Single-call case only: this column stores one set of
                    # args per row, so a step with multiple simultaneous
                    # tool calls can't be fully represented here without a
                    # bigger schema change. Known limitation, not solved now.
                    tool_args=tool_calls[0].arguments if len(tool_calls) == 1 else None,
                    # V6.14.2: the paired tool_result row already carried
                    # tool_name (below); this row never did, so any
                    # reload-path view resolving a tool_call message on its
                    # own (before its result exists, or when the call never
                    # gets a matching result -- e.g. cancelled mid-call) had
                    # nothing but "unknown" to show. Also silently affected
                    # conversation export (api/conversation_management.py's
                    # "Calling tool: {tool_name or 'unknown'}" line, which
                    # already expected this to be populated).
                    tool_name=tool_calls[0].tool_name if len(tool_calls) == 1 else None,
                )
                # Record the first tool-step message ID for cancel rollback.
                if not _first_tool_step_msg_id:
                    _first_tool_step_msg_id.append(assistant_msg.id)

                # Save each tool call record linked to the assistant message
                for tc in tool_calls:
                    await DBRepo.add_tool_call(
                        step_db,
                        assistant_msg.id,
                        tc.tool_name,
                        tc.arguments,
                    )
                # Journal: tool calls (sync, never raises)
                ConversationJournal.log(user_id, conversation_id, {
                    "type": "tool_call",
                    "execution_id": execution_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "calls": [
                        {
                            "call_id": tc.call_id,
                            "tool_name": tc.tool_name,
                            "args": tc.arguments,
                        }
                        for tc in tool_calls
                    ],
                })

                # Save each tool result as a separate "tool" role message
                for tc, result in tool_results:
                    result_content = (
                        json.dumps(result) if isinstance(result, dict) else str(result)
                    )
                    await DBRepo.add_message(
                        db=step_db,
                        conversation_id=conversation_id,
                        role="tool",
                        content=result_content,
                        mtype="tool_result",
                        tool_name=tc.tool_name,
                        tool_result=result if isinstance(result, dict) else {"output": str(result)},
                    )

                # Journal: tool results (sync, never raises)
                ConversationJournal.log(user_id, conversation_id, {
                    "type": "tool_result",
                    "execution_id": execution_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "results": [
                        {
                            "call_id": tc.call_id,
                            "tool_name": tc.tool_name,
                            "result": (
                                json.dumps(result) if isinstance(result, dict) else str(result)
                            )[:1000],  # cap at 1000 chars — full result in DB
                        }
                        for tc, result in tool_results
                    ],
                })

        # ------------------------------------------------------------
        # Build AgentEngine
        # ------------------------------------------------------------
        agent = AgentEngine(
            provider=session["provider"],
            api_key=session.get("api_key"),
            model=session["model"],
            system_prompt=augmented_system_prompt,
            reasoning=session.get("reasoning", False),
            tools=user_tools,
            dispatcher=dispatcher,
            session_id=session_id,
            memory_provider=memory_provider,
            dynamic_registry=dynamic_registry,
            execution=execution,
            fallback_chain=session.get("fallback_chain", []),
            on_step_complete=_save_tool_step,
            # Wire the shared CancellationToken so that AgentEngine's
            # _check_cancelled() can react immediately when the runner,
            # watchdog, or tool-approval gate sets cancelled=True — without
            # waiting for the next broker poll in runner.py.
            cancel_token=execution.cancel_token,
            # V5.0 Sprint 2: progressive disclosure active in conversational mode.
            progressive=execution_mode in _PROGRESSIVE_MODES,
        )

        # ----------------------------------------------------------
        # Capture usage from finalizer for metrics flush
        # ----------------------------------------------------------
        # This dict is populated inside the finalizer closure and read
        # after run_execution() returns.  It is safe because everything
        # runs in the same coroutine (no concurrency here).
        captured_context: dict = {}

        async def finalizer(final: dict):
            # Capture usage dict before saving, so metrics flush can
            # use token counts without hitting the DB again.
            # Metrics must reflect EVERY model call in this execution (each
            # ReAct step), not just the last one -- "usage" is the last call
            # only (it is what the final assistant message below records).
            captured_context["usage"]        = final.get("usage_total") or final.get("usage") or {}
            captured_context["finish_reason"] = final.get("finish_reason")

            await message_logger.save_final_message(
                role="assistant",
                content=final.get("text", ""),
                reasoning=final.get("reasoning"),
                usage=final.get("usage"),
                finish_reason=final.get("finish_reason"),
                chunked=True,
            )
            # Note: save_final_message returns None when the response is empty
            # (context overflow). No DB write occurs — intentional.

        # ------------------------------------------------------------
        # Reconstruct conversation history via MemoryEngine.
        #
        # MemoryEngine handles both concerns in one pass:
        #   1. Token-aware trimming: derives hard limits from the model's
        #      actual context_window (fetched from the capability registry).
        #      Falls back to DEFAULT_MEMORY_POLICY when unknown.
        #   2. Summarisation: when history exceeds the trigger threshold,
        #      old turns are compressed into a system-level summary block
        #      and only the recent tail is kept verbatim.
        #   3. Tool stub format (B-067): tool-call turns in the tail are
        #      represented as compact ⚙ stubs; raw tool result messages
        #      are dropped.  The active execution path (agent_engine.py)
        #      is unchanged — full results still flow in the current turn.
        # ------------------------------------------------------------

        # Look up the model's context window from the capability registry
        # to build a proportional MemoryPolicy for this specific model.
        _context_window: int | None = None
        try:
            from core.providers.capability_registry import get_model as _get_model  # noqa: PLC0415
            _cap = _get_model(session["provider"], session["model"])
            # Some providers (Groq) store models under LiteLLM routing keys
            # that include the provider prefix, e.g. "groq/openai/gpt-oss-120b".
            # Fall back to the prefixed key when the bare lookup misses.
            if _cap is None:
                _cap = _get_model(
                    session["provider"],
                    f"{session['provider']}/{session['model']}",
                )
            if _cap:
                _context_window = _cap.max_context_tokens
        except Exception:
            pass  # non-fatal — falls back to DEFAULT_MEMORY_POLICY

        _mem_policy = policy_for(_context_window)

        async with AsyncSessionLocal() as _hist_db:
            _engine = MemoryEngine(db=_hist_db, policy=_mem_policy)
            messages, _did_summarize = await _engine.build_context(
                conversation_id=conversation_id,
                system_prompt="",        # system prompt already in augmented_system_prompt
                model_name=session["model"],
                provider=session["provider"],
                api_key=session.get("api_key"),
                user_id=user_id,
            )

        # build_context prepends a system message when system_prompt is non-empty.
        # We passed "" so the list contains only user/assistant messages — the
        # system prompt is injected by AgentEngine from augmented_system_prompt.
        # Strip any accidental leading system message just in case.
        messages = [m for m in messages if m.get("role") != "system"]

        # If the memory engine ran summarisation, notify the frontend via
        # a preamble StatusChunk so it can show a "Summarising…" indicator
        # before the first AI token arrives.
        _preamble = (
            [StatusChunk(message="Summarising conversation…")]
            if _did_summarize else []
        )

        logger.debug(
            "Conversation history reconstructed via MemoryEngine",
            extra={
                "execution_id": execution_id,
                "message_count": len(messages),
                "context_window": _context_window,
                "max_context_tokens": _mem_policy.max_context_tokens,
                "summary_trigger_tokens": _mem_policy.summary_trigger_tokens,
            },
        )
        logger.info(
            "Starting run_execution",
            extra={
                "execution_id": execution_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
            },
        )

        return _AgentAndHistory(
            agent=agent,
            finalizer=finalizer,
            messages=messages,
            preamble=_preamble,
            captured_context=captured_context,
            first_tool_step_msg_id=_first_tool_step_msg_id,
        )

    # ------------------------------------------------------------
    # Process Single Execution
    # ------------------------------------------------------------
    async def _process_execution(self, execution_id: str):
        logger.info(
            "Processing execution",
            extra={"execution_id": execution_id},
        )

        client = await redis_execution_registry.connect()
        key = f"execution:{execution_id}"
        data = await client.hgetall(key)

        if not data:
            logger.error(
                "Execution metadata missing in LocalBroker",
                extra={"execution_id": execution_id},
            )
            return

        conversation_id = data.get("conversation_id")
        user_id = data.get("user_id")
        session_id = data.get("session_id")
        execution_mode = data.get("execution_mode", "conversational")

        # ------------------------------------------------------------
        # Build Execution Object
        # ------------------------------------------------------------
        execution = ConversationExecution(
            execution_id=execution_id,
            conversation_id=conversation_id,
            user_id=user_id,
            session_id=session_id,
            status=ExecutionStatus.PENDING,
            buffer=None,
        )

        # Live-found pre-existing gap (2026-08-13, during P-19 verification):
        # this object -- not whatever create_execution_record() returned to
        # the original HTTP caller -- is the one run_execution() actually
        # transitions through its whole lifecycle from here on. Attaching it
        # replaces the stale, forever-PENDING placeholder the registry has
        # held since creation time, so GET /executions/running (and
        # cancellation, which also reads this registry) see the real,
        # live-updating object instead. See core/execution/registry.py's
        # attach() docstring for the full trace of how this was found.
        from core.execution import execution_registry
        execution_registry.attach(execution)

        _bundle = await self._build_session_and_tools(
            execution_id=execution_id,
            execution=execution,
            conversation_id=conversation_id,
            user_id=user_id,
            execution_mode=execution_mode,
        )
        if _bundle is None:
            return  # exit worker cleanly — no execution, no metrics row

        session = _bundle.session
        user_tools = _bundle.user_tools
        dispatcher = _bundle.dispatcher
        dynamic_registry = _bundle.dynamic_registry
        resolved_project_id = _bundle.resolved_project_id
        project_status = _bundle.project_status
        _preamble_is_progressive = _bundle.preamble_is_progressive
        _explicit_tools = _bundle.explicit_tools

        augmented_system_prompt = await self._assemble_system_prompt(
            execution_id=execution_id,
            conversation_id=conversation_id,
            user_id=user_id,
            execution_mode=execution_mode,
            resolved_project_id=resolved_project_id,
            project_status=project_status,
            explicit_tools=_explicit_tools,
            session=session,
            preamble_is_progressive=_preamble_is_progressive,
            user_tools=user_tools,
            dynamic_registry=dynamic_registry,
            initial_message=data.get("initial_message") or "",
            # Which folder relative paths land in (the file tools resolve them the same way): the
            # project's folder when it has a workspace, else this conversation's folder.
            working_folder_kind=(
                "project" if getattr(execution, "project_workspace", None)
                else ("conversation" if conversation_id else None)
            ),
        )

        # ------------------------------------------------------------
        # Build Memory Provider, AgentEngine, and conversation history.
        #
        # `db` stays open for the whole rest of this block (including the
        # run_execution()/run_pev_execution() call below) -- see
        # _build_agent_and_history's docstring for why: its returned
        # `finalizer` closure writes through this exact session, and is
        # called by the runner potentially minutes later.
        #
        # captured_context / _first_tool_step_msg_id are seeded with their
        # empty defaults *before* the try so the outer `finally` can always
        # call _finalize_execution -- including when _build_agent_and_history
        # itself raises, before these are ever populated.
        #
        # Q-01 fix (2026-08-17): previously nothing wrapped this span, so an
        # exception raised anywhere between here and run_execution()/
        # run_pev_execution() returning (e.g. AgentEngine construction
        # failing) propagated straight to _safe_process_execution's outer
        # handler, skipping _finalize_execution -- and therefore
        # _flush_metrics -- entirely. The broker was still marked failed and
        # an SSE event still published (via _safe_process_execution), but no
        # execution_metrics row was ever written, so the run was invisible
        # to GET /executions history and cost/analytics tracking,
        # permanently. The `finally` below closes that gap: finalize now
        # always runs, then the exception (if any) continues propagating
        # unchanged, so _safe_process_execution's existing failure-marking
        # behavior is untouched. See
        # TestExceptionEscapesRunnerSkipsMetricsFlush, updated alongside
        # this fix to assert the row now exists.
        # ------------------------------------------------------------
        captured_context: dict = {}
        _first_tool_step_msg_id: list = []
        try:
            async with AsyncSessionLocal() as db:
                _ah = await self._build_agent_and_history(
                    db=db,
                    execution_id=execution_id,
                    execution=execution,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    session_id=session_id,
                    execution_mode=execution_mode,
                    session=session,
                    user_tools=user_tools,
                    dispatcher=dispatcher,
                    dynamic_registry=dynamic_registry,
                    augmented_system_prompt=augmented_system_prompt,
                )
                agent = _ah.agent
                finalizer = _ah.finalizer
                messages = _ah.messages
                _preamble = _ah.preamble
                captured_context = _ah.captured_context
                _first_tool_step_msg_id = _ah.first_tool_step_msg_id

                # ----------------------------------------------------------------
                # PEV fork: route plan_execute executions to the PEV loop;
                # all other executions use the standard conversational runner.
                #
                # V6.15.2: a background watcher polls the broker for an external
                # cancel/abort signal independently of the runner's own chunk
                # loop -- see _watch_for_external_cancellation() above for why
                # this is required (the runner's own poll can't run while a
                # tool call is in flight).
                # ----------------------------------------------------------------
                _cancel_watcher = asyncio.create_task(
                    _watch_for_external_cancellation(execution),
                    name=f"cancel-watcher-{execution_id}",
                )
                try:
                    if execution_mode == "plan_execute":
                        await run_pev_execution(
                            execution=execution,
                            agent=agent,
                            messages=messages,
                            finalizer=finalizer,
                            preamble=_preamble,
                        )
                    else:
                        await run_execution(
                            execution=execution,
                            agent=agent,
                            messages=messages,
                            finalizer=finalizer,
                            preamble=_preamble,
                        )
                finally:
                    _cancel_watcher.cancel()
                    try:
                        await _cancel_watcher
                    except asyncio.CancelledError:
                        pass
        finally:
            await _finalize_execution(
                execution_id=execution_id,
                execution=execution,
                conversation_id=conversation_id,
                user_id=user_id,
                execution_mode=execution_mode,
                session=session,
                resolved_project_id=resolved_project_id,
                project_status=project_status,
                captured_context=captured_context,
                first_tool_step_msg_id=_first_tool_step_msg_id,
            )
