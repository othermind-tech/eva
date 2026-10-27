# core/types.py

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Literal


class ChunkType(str, Enum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    USAGE = "usage"
    FINISH = "finish"                          # finalization/stop signal
    STEP_PROGRESS = "step_progress"            # PEV: structured per-step progress event
    PLAN_APPROVAL_REQUIRED = "plan_approval_required"  # PEV: plan waiting for user approval
    SUBAGENT = "subagent"                      # G-003: subagent lifecycle event
    STATUS = "status"                          # system status notification (e.g. summarising)
    PROVIDER_FALLBACK = "provider_fallback"    # V6.5 Phase 5: fallback chain switched providers


@dataclass
class TextChunk:
    type: ChunkType = ChunkType.TEXT
    content: str = ""
    role: str = "assistant"
    finish_reason: Optional[str] = None # Optional Field


@dataclass
class ErrorChunk:
    type: ChunkType = ChunkType.ERROR
    message: str = ""
    provider: Optional[str] = None
    error_type: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@dataclass
class UsageChunk:
    type: ChunkType = ChunkType.USAGE
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: Optional[int] = None
    total_tokens: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None
    # Subset of prompt_tokens served from the provider's prompt cache
    # (prompt_tokens is always the TOTAL input including this portion). None =
    # the provider did not say. See providers/usage.py.
    cached_tokens: Optional[int] = None


@dataclass
class ReasoningChunk:
    type: ChunkType = ChunkType.REASONING
    content: str = ""

@dataclass
class ToolCallChunk:
    type: ChunkType = ChunkType.TOOL_CALL
    tool_name: str = ""
    arguments: Dict[str, Any] = None
    call_id: str = ""


@dataclass
class ToolResultChunk:
    type: ChunkType = ChunkType.TOOL_RESULT
    call_id: str = ""
    tool_name: str = ""
    result: Dict[str, Any] = None



@dataclass
class StepProgressChunk:
    """
    Emitted by the PEV runner at each step transition so that frontends can
    render a structured progress indicator rather than parsing markdown text.

    V6.14: this chunk is now the single source of truth for PEV's narration.
    Previously pev_runner.py duplicated this same information as hand-authored
    markdown TextChunks mixed directly into the answer text stream -- the
    frontend had no way to tell "EVA narrating its own process" apart from
    "the actual answer" because they were the same string. That duplication
    has been removed; the fields below carry what those TextChunks used to
    render as prose.

    step_status values:
      planning        – PlannerEngine is generating (or has generated) the
                         plan; plan_steps is set once the plan is ready
      awaiting_plan_approval – plan is ready, waiting on the user's
                         approve/edit/reject decision (see also the separate
                         plan_approval_required SSE event, which carries the
                         approval token -- this status is purely informational)
      running         – the step AgentEngine call has started
      verifying       – VerifierEngine is checking the step output;
                         error_detail is set if the step itself raised
      completed       – step passed verification; verification_summary set
      failed          – step failed verification or raised an exception;
                         verification_summary / verification_issues set
      replanning       – adaptive re-planning is in progress after a failure;
                         replan_reason set on the first emission, plan_steps
                         set on a second emission once the revised plan exists
      replan_skipped   – max re-plan attempts reached; continuing with
                         remaining steps as-is instead of re-planning again;
                         replan_reason explains why
      skipped          – A-21: this step's agent was never invoked -- an
                         earlier step's verification already showed its goal
                         achieved. verification_summary carries which earlier
                         step and why. Never emitted for the plan's own final
                         step (that one always runs for real, for A-19's
                         closing synthesis).
      plan_complete    – final summary once the whole plan has finished;
                         steps_completed / steps_failed / steps_skipped /
                         replan_count set
      cancelled        – execution was cancelled (plan rejected, or an
                         external cancel signal was observed). This yield is
                         structurally required, not just informational: the
                         consumer in run_pev_execution() only checks
                         cancel_token and performs the CANCELLED FSM
                         transition inside its `async for chunk in
                         buffered.stream_and_buffer()` loop body -- a
                         generator that sets the flag and returns with no
                         further yield exits via plain StopAsyncIteration and
                         silently skips that transition, leaving the broker
                         status stuck at whatever it was (e.g.
                         awaiting_plan_approval) forever. Always yield this
                         immediately before any `return` that follows setting
                         execution.cancel_token.cancelled = True.
    """
    type: ChunkType = ChunkType.STEP_PROGRESS
    step_index: int = 0          # 0-based index in the plan
    total_steps: int = 0         # total steps in the current plan (may grow after replan)
    step_title: str = ""
    step_status: str = "running" # see docstring above for the full set of values
    goal: str = ""               # overall plan goal (for context)

    # V6.14 — structured fields replacing the old TextChunk narration.
    # All optional; only the field(s) relevant to the current step_status are set.
    plan_steps:           Optional[List[Dict[str, Any]]] = None  # [{index, title, description, tool_hint}]
    verification_summary: Optional[str] = None
    verification_issues:  Optional[str] = None
    replan_reason:         Optional[str] = None
    error_detail:          Optional[str] = None  # step-execution exception text, if any
    steps_completed:       Optional[int] = None  # set only on step_status == "plan_complete"
    steps_failed:          Optional[int] = None  # set only on step_status == "plan_complete"
    steps_skipped:         Optional[int] = None  # A-21 -- set only on step_status == "plan_complete"; never folded into steps_completed (the agent never actually ran for these)
    replan_count:          Optional[int] = None  # set only on step_status == "plan_complete"
    # A-20 — set only on step_status == "plan_complete". PlanVerifierEngine's
    # holistic verdict on the FINISHED plan against its own goal/success
    # criteria -- distinct from steps_completed/steps_failed above, which are
    # just a per-step tally. "success" | "pending" | "not_successful", or
    # None if the plan ended via a path that skips this check (e.g.
    # cancelled). goal_verification_message is set whenever the verdict
    # isn't "success" -- same text already folded into the saved answer
    # (see pev_runner.py), surfaced here too so a frontend can render it as
    # its own element instead of only reading it out of the answer prose.
    goal_verdict:             Optional[str] = None
    goal_verification_message: Optional[str] = None


@dataclass
class StatusChunk:
    """
    Emitted before the AI stream starts to notify the frontend of a
    background system event.  Currently used to surface conversation
    summarisation so the frontend can show a dismissible indicator
    (analogous to Claude's "Summarising conversation…" banner).

    The SSE endpoint emits this as:
        event: status
        data: {"type": "status", "message": "...", ...}

    Frontend should show the message until the first `event: text` arrives.
    """
    type: ChunkType = ChunkType.STATUS
    message: str = ""


@dataclass
class ProviderFallbackChunk:
    """
    V6.5 Phase 5: emitted by AgentEngine._gen_with_fallback() at the same
    moment it publishes ProviderFallbackTriggered to the event_bus (see
    core/agent_engine.py) -- that event only reaches metrics/logging, NOT
    the SSE stream (only chunks actually *yielded* by AgentEngine do, via
    core/stream_buffer.py -> core/execution/runner.py's shadow.append()).
    This chunk closes that gap: it's the live, mid-execution counterpart
    to the ProviderFallbackTriggered event, so the frontend can show a
    real-time notice when the fallback chain switches providers.

    Deliberately minimal: EVA's fallback chain exists so a provider
    hiccup is invisible to the user -- this chunk should read as routine
    "responding via X now" information, not an alarm. It carries only the
    NEW provider/model, nothing about what failed or why. That diagnostic
    detail already exists in full via GET /executions/{id}/metrics'
    fallback_details array (the post-hoc/detailed view) -- no need to duplicate it here or
    encourage a failure-focused frontend treatment of a live notice that's
    supposed to feel calm, not alarming.

    The SSE endpoint emits this as:
        event: provider_fallback
        data: {"type": "provider_fallback", "provider": "...", "model": "..."}
    """
    type: ChunkType = ChunkType.PROVIDER_FALLBACK
    provider: str = ""   # the provider now actually serving the response
    model: str = ""      # the model now actually serving the response


@dataclass
class SubagentChunk:
    """
    G-003: Emitted during subagent execution so the frontend can render a
    dedicated subagent panel alongside the orchestrator's response.

    event values:
      started    – child AgentEngine has begun
      text       – text token from the child
      tool_call  – child is calling a tool
      completed  – child finished; content holds the final answer
      failed     – child raised an error; content holds the error message
    """
    type: ChunkType = ChunkType.SUBAGENT
    event: str = ""          # started | text | tool_call | completed | failed
    agent_name: str = ""
    content: str = ""
    tool_name: Optional[str] = None


StreamChunk = Union[TextChunk, ReasoningChunk, ToolCallChunk, ToolResultChunk, ErrorChunk, UsageChunk, StepProgressChunk, SubagentChunk, StatusChunk, ProviderFallbackChunk]

# ============================================================================
# Memory & Context Types (Non-streaming, internal)
# ============================================================================

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class InferenceContext:
    """
    Immutable inference configuration derived from user session.
    Shared across AgentEngine and MemoryEngine.
    """
    provider: str
    model: str


@dataclass
class MemoryMessage:
    """
    Normalized persisted message used for memory construction.
    This is NOT a DB model and NOT streamed to UI.
    """
    message_id: str
    role: Role
    content: str
    token_estimate: int


@dataclass
class ConversationSummaryBlock:
    """
    In-memory representation of a conversation summary.
    This is injected as a system message when needed.
    """
    conversation_id: str
    user_id: str
    content: str
    last_message_id: str
    token_estimate: int


class ReActPhase(str, Enum):
    THINK = "think"
    ACT = "act"
    OBSERVE = "observe"
    FINAL = "final"
    ERROR = "error"


@dataclass
class ReActStep:
    phase: ReActPhase
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_arguments: Optional[Dict[str, Any]] = None
    tool_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

# ======================================================
# Tool Execution Semantics
# ======================================================

class ToolErrorType(str, Enum):
    VALIDATION = "validation"
    PERMISSION = "permission"
    TRANSIENT = "transient"
    EXTERNAL_DEPENDENCY = "external_dependency"
    INTERNAL_BUG = "internal_bug"

class ToolExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"

@dataclass
class ToolExecutionResult:
    status: ToolExecutionStatus

    output: Optional[Any] = None
    error_type: Optional[ToolErrorType] = None
    error_message: Optional[str] = None
    retryable: bool = False

    metadata: Optional[Dict[str, Any]] = None
    
# ======================================================
# Execution Lifecycle (V1.5)
# ======================================================

class ExecutionState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ABORTED = "ABORTED"