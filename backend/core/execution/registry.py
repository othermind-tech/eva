import asyncio
import uuid
from logging import getLogger
from typing import Dict, List, Optional

from core.execution.redis_registry import redis_execution_registry
from core.execution.models import ConversationExecution, ExecutionStatus
from core.execution.state_machine import ExecutionStateMachine
from core.execution.errors import IllegalStateTransition

logger = getLogger(__name__)

# Statuses that mean "this execution is genuinely done" -- everything else
# (including the PEV wait states) is live work that would be silently
# abandoned by an unguarded app quit. Mirrors the broker-side terminal set
# in api/executions.py / core/execution/registry.py's cancel_latest(), kept
# separate because this one filters ExecutionStatus enum members (in-memory
# representation) rather than the broker's lowercase string representation.
_TERMINAL_EXECUTION_STATUSES = frozenset({
    ExecutionStatus.COMPLETED,
    ExecutionStatus.FAILED,
    ExecutionStatus.ABORTED,
    ExecutionStatus.CANCELLED,
})

# Per-conversation execution history is retained in memory for the life of
# the process (needed so get_latest()/cancel_latest() always see the most
# recent run). Daemons can re-append to the same conversation_id (daemon.id)
# every run_interval_seconds indefinitely, which would grow this list
# without bound for a long-lived, frequently-firing daemon. Trim old
# *terminal* entries past this cap; never trims a live (non-terminal) entry.
_MAX_RETAINED_EXECUTIONS_PER_CONVERSATION = 50


class ExecutionRegistry:
    """
    In-memory execution reference manager.

    Responsibilities:
    - Create execution objects
    - Maintain in-process execution references (for SSE + cancel)
    - Validate state transitions
    - Delegate durable state to the broker execution registry

    NOTE:
    This class no longer starts run_execution().
    Worker process is responsible for execution.
    """

    def __init__(self):
        self._executions: Dict[str, List[ConversationExecution]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------
    # Internal state transition helper
    # ------------------------------------------------------------
    def _transition(self, execution: ConversationExecution, new_status: ExecutionStatus):
        ExecutionStateMachine.assert_transition_allowed(
            execution.execution_id,
            execution.status,
            new_status,
        )
        execution.status = new_status

        logger.debug(
            "Execution state transition",
            extra={
                "execution_id": execution.execution_id,
                "conversation_id": execution.conversation_id,
                "new_status": new_status.value,
            },
        )

    # ------------------------------------------------------------
    # Create Execution Record (NO TASK START HERE)
    # ------------------------------------------------------------
    async def create_execution_record(
        self,
        *,
        conversation_id: str,
        user_id: str,
        session_id: str,
        execution_mode: str = "conversational",
        execution_id: Optional[str] = None,
        initial_message: Optional[str] = None,
    ) -> ConversationExecution:
        """
        Creates execution metadata in memory + the broker.
        Does NOT start execution.
        Worker is responsible for calling run_execution().

        `execution_id` is optional: pass a pre-generated one when a caller
        (e.g. the daemon trigger endpoint) already handed that ID to the
        client before this record exists; omitted, one is generated here as
        before.
        """

        async with self._lock:
            if execution_id is None:
                execution_id = str(uuid.uuid4())

            execution = ConversationExecution(
                execution_id=execution_id,
                conversation_id=conversation_id,
                user_id=user_id,
                session_id=session_id,
                status=ExecutionStatus.PENDING,
                buffer=None,
                execution_mode=execution_mode,
            )

            logger.info(
                "Creating execution record",
                extra={
                    "execution_id": execution_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "execution_mode": execution_mode,
                },
            )

            # Persist durable metadata in the broker
            await redis_execution_registry.create_execution(
                execution_id=execution_id,
                convo_id=conversation_id,
                session_id=session_id,
                user_id=user_id,
                initial_message=initial_message,
                execution_mode=execution_mode,
            )

            # Track locally (for SSE + cancellation)
            bucket = self._executions.setdefault(conversation_id, [])
            bucket.append(execution)
            self._trim_terminal_entries(bucket)

            return execution

    # ------------------------------------------------------------
    # Bound per-conversation history (Option A hardening, 2026-07-21):
    # daemons can append to the same conversation_id every
    # run_interval_seconds indefinitely. Only terminal (done) entries are
    # ever dropped -- a live entry is never trimmed regardless of list size.
    # ------------------------------------------------------------
    def _trim_terminal_entries(self, bucket: List[ConversationExecution]) -> None:
        if len(bucket) <= _MAX_RETAINED_EXECUTIONS_PER_CONVERSATION:
            return
        overflow = len(bucket) - _MAX_RETAINED_EXECUTIONS_PER_CONVERSATION
        kept: List[ConversationExecution] = []
        dropped = 0
        for exec_obj in bucket:
            if dropped < overflow and exec_obj.status in _TERMINAL_EXECUTION_STATUSES:
                dropped += 1
                continue
            kept.append(exec_obj)
        bucket[:] = kept

    # ------------------------------------------------------------
    # Pre-existing gap, found live during P-19 verification (2026-08-13):
    # core/execution/worker.py's _process_execution() only ever receives an
    # execution_id string from the job queue (not the ConversationExecution
    # object create_execution_record() returned to the HTTP caller), so it
    # builds a SEPARATE, fresh ConversationExecution(status=PENDING, ...) to
    # actually run the work. That fresh object is exactly what
    # core/execution/runner.py's run_execution() correctly transitions
    # through its whole lifecycle (RUNNING -> ... -> COMPLETED/FAILED/etc.)
    # -- but it is never registered back into this registry, so
    # list_running_for_user() (GET /executions/running) only ever sees the
    # original placeholder from creation time, forever stuck at PENDING,
    # with its own separate, never-triggered cancel_token. Confirmed via a
    # live control test with a plain chat message, no wakeup involvement --
    # this predates and is unrelated to P-19, matches the exact gap
    # cancel_latest()'s own comment above already alludes to ("the worker
    # updates the broker directly but NOT the server's in-memory execution
    # object"), just not until now generalized as a fix.
    #
    # attach() lets the worker hand its live, correctly-maintained object
    # to the registry, replacing the stale placeholder in place (same list
    # position) so every subsequent status transition on it -- including
    # cancellation -- is visible here without any other call site changing.
    # ------------------------------------------------------------
    def attach(self, execution: ConversationExecution) -> None:
        bucket = self._executions.setdefault(execution.conversation_id, [])
        for idx, exec_obj in enumerate(bucket):
            if exec_obj.execution_id == execution.execution_id:
                bucket[idx] = execution
                return
        bucket.append(execution)
        self._trim_terminal_entries(bucket)

    # ------------------------------------------------------------
    # List all non-terminal executions for a user, across every
    # conversation/daemon -- used by GET /executions/running (app-close
    # safety). Deliberately reads this in-memory registry, not the DB:
    # this is the one place a live cancel_token actually lives.
    # ------------------------------------------------------------
    def list_running_for_user(self, user_id: str) -> List[ConversationExecution]:
        result: List[ConversationExecution] = []
        for bucket in self._executions.values():
            for exec_obj in bucket:
                if exec_obj.user_id == user_id and exec_obj.status not in _TERMINAL_EXECUTION_STATUSES:
                    result.append(exec_obj)
        return result

    # ------------------------------------------------------------
    # P-19 Phase 1: resolve a paused (AWAITING_WAKEUP) execution once
    # WakeupScheduler has started its continuation. Mirrors cancel_latest()'s
    # "also reflect this locally, not just in the broker" pattern -- without
    # it, GET /executions/running (which reads this in-memory registry, not
    # the broker) would keep reporting the old execution as still waiting
    # forever, since a poll-based resolution never resumes the coroutine
    # that would otherwise update this object itself.
    # ------------------------------------------------------------
    def _finalize_wakeup(self, execution_id: str, target_status: ExecutionStatus) -> bool:
        for bucket in self._executions.values():
            for exec_obj in bucket:
                if exec_obj.execution_id == execution_id:
                    if exec_obj.status not in _TERMINAL_EXECUTION_STATUSES:
                        try:
                            self._transition(exec_obj, target_status)
                        except IllegalStateTransition:
                            logger.warning(
                                "_finalize_wakeup: could not locally transition to "
                                "%s (unexpected local status)",
                                target_status.value,
                                extra={
                                    "execution_id": execution_id,
                                    "status": exec_obj.status.value,
                                },
                            )
                            return False
                    return True
        return False

    def resolve_wakeup(self, execution_id: str) -> bool:
        """WakeupScheduler's own resolution path -- the wakeup fired
        (naturally or forced via POST /wakeups/{id}/resume) and a
        continuation execution was created."""
        return self._finalize_wakeup(execution_id, ExecutionStatus.COMPLETED)

    def cancel_wakeup(self, execution_id: str) -> bool:
        """P-19 Tier A: POST /wakeups/{id}/cancel -- the user decided not to
        pursue this paused execution. No continuation is created. Only the
        paused execution itself transitions here -- never whatever it may
        have been watching via watch_execution_id, which is an independent
        execution with its own lifecycle and may have value on its own."""
        return self._finalize_wakeup(execution_id, ExecutionStatus.CANCELLED)

    # ------------------------------------------------------------
    # Get Latest Execution
    # ------------------------------------------------------------
    def get_latest(self, conversation_id: str) -> Optional[ConversationExecution]:
        """
        Returns the most relevant execution for a conversation.
        Prefers RUNNING over COMPLETED.
        """

        executions = self._executions.get(conversation_id)
        if not executions:
            return None

        # Prefer RUNNING execution
        for exec_obj in reversed(executions):
            if exec_obj.status == ExecutionStatus.RUNNING:
                return exec_obj

        return executions[-1]

    # ------------------------------------------------------------
    # Cancel Latest Execution
    # ------------------------------------------------------------
    async def cancel_latest(self, conversation_id: str) -> bool:
        """
        Marks the latest execution as cancelled.
        Worker loop will detect cancellation via cancel_token.
        """

        executions = self._executions.get(conversation_id)
        if not executions:
            logger.debug(
                "No executions found for cancellation",
                extra={"conversation_id": conversation_id},
            )
            return False

        # Prefer RUNNING execution
        for exec_obj in reversed(executions):
            if exec_obj.status == ExecutionStatus.RUNNING:
                logger.info(
                    "Cancelling running execution",
                    extra={
                        "execution_id": exec_obj.execution_id,
                        "conversation_id": conversation_id,
                    },
                )

                exec_obj.cancel_token.cancelled = True
                self._transition(exec_obj, ExecutionStatus.CANCELLED)

                if exec_obj.buffer:
                    exec_obj.buffer.clear()

                await redis_execution_registry.mark_execution_cancelled(
                    exec_obj.execution_id,
                    reason="user",
                )

                return True

        # No RUNNING execution found in the server's in-memory view.
        # The worker updates the broker directly but NOT the server's in-memory
        # execution object, so we query the broker for the actual current status
        # before attempting a state transition.
        latest = executions[-1]

        _TERMINAL_STATUSES = {"completed", "failed", "cancelled", "aborted"}
        try:
            _client = await redis_execution_registry.connect()
            broker_status = await _client.hget(
                f"execution:{latest.execution_id}", "status"
            )
            if broker_status in _TERMINAL_STATUSES:
                logger.debug(
                    "Execution already in terminal state in broker — nothing to cancel",
                    extra={
                        "execution_id": latest.execution_id,
                        "conversation_id": conversation_id,
                        "broker_status": broker_status,
                    },
                )
                return False
        except Exception as broker_err:
            logger.warning(
                "Failed to check broker status before cancel attempt (will proceed anyway)",
                extra={
                    "execution_id": latest.execution_id,
                    "error": str(broker_err),
                },
            )

        # Execution is PENDING or RUNNING in the broker — safe to request cancel
        logger.debug(
            "Marking latest execution cancelled in broker (not yet running locally)",
            extra={
                "execution_id": latest.execution_id,
                "conversation_id": conversation_id,
                "status": latest.status.value,
            },
        )

        await redis_execution_registry.mark_execution_cancelled(
            latest.execution_id,
            reason="user",
        )

        # Also reflect the cancellation on this server's own in-memory object --
        # otherwise list_running_for_user() (GET /executions/running) keeps
        # reporting it as still live forever, since that reads this same
        # local .status field, not the broker. Found live by the frontend
        # team (2026-07-21): an execution cancelled before the worker had
        # locally marked it RUNNING stayed stuck in the running list across
        # repeated polls with no convergence. Mirrors what the RUNNING branch
        # above already does correctly.
        latest.cancel_token.cancelled = True
        try:
            self._transition(latest, ExecutionStatus.CANCELLED)
        except IllegalStateTransition:
            logger.warning(
                "cancel_latest: could not locally transition to CANCELLED "
                "(unexpected local status) -- broker is authoritative and "
                "was already updated above",
                extra={
                    "execution_id": latest.execution_id,
                    "conversation_id": conversation_id,
                    "status": latest.status.value,
                },
            )

        return True
