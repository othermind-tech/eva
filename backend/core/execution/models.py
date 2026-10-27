from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional
from core.stream_buffer import BufferedStream
import asyncio
import time
from uuid import uuid4


class ExecutionStatus(str, Enum):
    PENDING           = "pending"
    # PEV-specific phases (plan_execute mode only)
    PLANNING          = "planning"              # PlannerEngine generating the execution plan
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"  # Plan shown; waiting for user to approve
    VERIFYING         = "verifying"             # VerifierEngine checking a completed step
    # Normal execution states
    RUNNING           = "running"
    AWAITING_APPROVAL = "awaiting_approval"     # HitL gate for tool calls
    # V6.12: ReAct step-limit reached; waiting for user decision to extend or stop.
    AWAITING_STEP_EXTENSION = "awaiting_step_extension"
    # P-19 Phase 1: agent called schedule_wakeup and paused itself -- unlike
    # the other AWAITING_* gates, nothing blocks waiting for this; the
    # execution ends cleanly here and a WakeupScheduler poll loop resolves
    # it later by transitioning to COMPLETED and starting a new, separate
    # execution that continues the conversation (checkpoint model, not a
    # resumed live object).
    AWAITING_WAKEUP   = "awaiting_wakeup"
    COMPLETED         = "completed"
    FAILED            = "failed"
    ABORTED           = "aborted"
    CANCELLED         = "cancelled"


@dataclass
class CancellationToken:
    cancelled: bool = False

@dataclass
class ConversationExecution:
    execution_id: str
    conversation_id: str
    user_id: str
    session_id: str

    status: ExecutionStatus
    buffer: BufferedStream

    task: asyncio.Task | None = None
    cancel_token: CancellationToken = field(default_factory=CancellationToken)

    started_at: float | None = None
    completed_at: float | None = None

    error: dict | None = None

    # SSE (V1) single-consumer enforcement
    active_consumer: bool = False

    # G-003: Shadow buffer reference — set by runner.py after the buffer
    # is created so that tools (e.g. delegate_to_agent) can emit subagent
    # chunks into the parent's live SSE stream without extra wiring.
    shadow: Optional[Any] = None

    # G-004: Execution-scoped blackboard for multi-agent coordination.
    # Set by runner.py alongside shadow.
    blackboard: Optional[Any] = None

    # B-051: Active project workspace root — set by worker at execution start.
    # When set, tools use this as their default CWD instead of the conversation dir.
    project_workspace: Optional[str] = None

    # Project ID for this execution — set by worker and daemon runner so that
    # project-scoped MCPs and context are available during execution.
    parent_project_id: Optional[str] = None

    # G-004: Name of the agent currently writing in this context.
    # "orchestrator" for the main agent; temporarily overridden to the
    # subagent's name by delegate_to_agent before running the child agent,
    # and restored in finally.  Used by blackboard_write to enforce slot
    # namespace ownership (L2) without exposing the ID to the LLM.
    caller_agent_name: str = "orchestrator"

    # V5.0 Sprint 2: Progressive Tool Disclosure.
    # Set by worker.py after UserToolRegistry is created so that
    # describe_tool (and the hallucination gate) can call promote() without
    # a separate registry lookup.  None when progressive disclosure is off.
    user_registry: Optional[Any] = None

    # A-19/A-20: set by pev_runner.py's _pev_stream() right before it
    # finishes, to the properly-assembled closing answer (the last step's
    # own synthesis, plus the plan verifier's message on a non-"success"
    # verdict) -- NOT the naive concatenation of every step's TextChunk that
    # BufferedStream.aggregated() would otherwise produce. run_pev_execution()
    # reads this after the stream is fully consumed and uses it in place of
    # aggregated()'s own "text" field before calling finalizer(). None for
    # every other execution mode (conversational/daemon), which keep using
    # aggregated()'s text unchanged -- this only ever applies to plan_execute.
    final_answer_override: Optional[str] = None

    # "conversational" | "plan_execute" | "daemon" — set at creation via
    # ExecutionRegistry.create_execution_record(). Used by GET /executions/running
    # (app-close safety) to distinguish daemon runs from chat turns without a
    # second DB round-trip.
    execution_mode: str = "conversational"