from typing import Dict, Set
from core.execution.models import ExecutionStatus

TERMINAL_STATES: Set[ExecutionStatus] = {
    ExecutionStatus.COMPLETED,
    ExecutionStatus.FAILED,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.ABORTED,
}

ALLOWED_TRANSITIONS: Dict[ExecutionStatus, Set[ExecutionStatus]] = {
    ExecutionStatus.PENDING: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.PLANNING,   # PEV: plan_execute mode starts here
        ExecutionStatus.ABORTED,
        ExecutionStatus.CANCELLED,  # user-initiated cancel before worker picks up
    },
    # PEV planning phase: PlannerEngine is generating the plan.
    # On success (no approval needed) → RUNNING  (execution of first step begins)
    # On success (approval needed)    → AWAITING_PLAN_APPROVAL
    # On failure  → FAILED
    # On cancel   → CANCELLED
    ExecutionStatus.PLANNING: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.AWAITING_PLAN_APPROVAL,  # plan approval gate
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.ABORTED,
    },
    # Plan approval gate: plan has been shown to user; waiting for approve/reject.
    # Approve  → RUNNING  (execution of first step begins)
    # Reject   → CANCELLED (reason: user_rejected_plan)
    # Timeout  → CANCELLED (reason: plan_approval_timeout)
    # Watchdog → ABORTED  (reason: watchdog_timeout — gate itself got stuck)
    ExecutionStatus.AWAITING_PLAN_APPROVAL: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.ABORTED,
    },
    ExecutionStatus.RUNNING: {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.ABORTED,
        ExecutionStatus.AWAITING_APPROVAL,       # tool approval gate
        ExecutionStatus.AWAITING_PLAN_APPROVAL,  # PEV: plan approval gate (inline pause)
        ExecutionStatus.AWAITING_STEP_EXTENSION, # V6.12: ReAct step-limit gate
        ExecutionStatus.AWAITING_WAKEUP,         # P-19: agent scheduled a wakeup and paused
        ExecutionStatus.VERIFYING,               # PEV: post-step verification
    },
    # PEV verification phase: VerifierEngine is checking a completed step.
    # Pass + more steps → RUNNING  (next step)
    # Pass + last step  → COMPLETED
    # Fail              → RUNNING  (retry / continue at discretion of PEV runner)
    # On cancel         → CANCELLED
    ExecutionStatus.VERIFYING: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.ABORTED,
    },
    # Human-in-the-Loop gate: execution is paused waiting for a user decision.
    # Approve  → RUNNING  (tool executes normally, execution continues)
    # Reject   → CANCELLED (reason: user_rejected_tool)
    # Timeout  → CANCELLED (reason: approval_timeout)
    # Watchdog → ABORTED  (reason: watchdog_timeout — gate itself got stuck)
    ExecutionStatus.AWAITING_APPROVAL: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.ABORTED,
    },
    # V6.12: ReAct step-limit gate — execution paused after MAX_REACT_STEPS
    # (and after the V6.11 forced-best-effort-answer attempt has already
    # run) waiting for a user decision to extend or stop.
    # Continue → RUNNING (react.tool_steps reset, extensions_used incremented)
    # Stop / decline / extensions exhausted / timeout → COMPLETED
    #   (deliberately NOT CANCELLED — stopping here means "keep the work
    #   already done," a natural completion, not a rejection; matches the
    #   gap tracker's DoneChunk(status="completed_at_limit") design, not
    #   the plan/tool-approval gates' reject-is-CANCELLED precedent)
    # Watchdog → ABORTED (gate itself got stuck — same failsafe as the
    #   other two approval gates above)
    ExecutionStatus.AWAITING_STEP_EXTENSION: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.COMPLETED,
        ExecutionStatus.ABORTED,
    },
    # P-19 Phase 1: nothing blocks in this coroutine waiting for a decision
    # (unlike the other AWAITING_* gates above) -- the execution just ends
    # here. WakeupScheduler's poll loop is the only thing that ever moves
    # it out of this state, always to COMPLETED (its lifecycle is over; the
    # continuation is a brand new execution in the same conversation, not
    # this one resuming). CANCELLED/ABORTED cover an explicit user cancel
    # or a watchdog cleanup finding a row whose conversation was deleted.
    ExecutionStatus.AWAITING_WAKEUP: {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.ABORTED,
    },
}
