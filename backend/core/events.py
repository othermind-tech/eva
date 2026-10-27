# core/events.py
#
# Typed internal event catalog for EVA's event bus.
#
# Design rules:
#   - Every event is a plain dataclass (no Pydantic, no inheritance).
#   - Every event carries `execution_id` and `timestamp`.
#   - Fields are immutable-by-convention (no mutation after publish).
#   - Adding new events never breaks existing subscribers.
#
# Lifecycle flow:
#
#   runner.py publishes:
#     ExecutionStarted → ExecutionCompleted
#                      → ExecutionFailed
#                      → ExecutionCancelled
#
#   dispatcher.py publishes (per tool invocation):
#     ToolCalled  →  ToolResult          (success or failure)
#               ↘  ToolPermissionDenied  (PermissionError path)
#
#   agent_engine.py publishes (via legacy observability.emit — not yet migrated):
#     StepCompleted / FallbackTriggered   ← defined here, reserved for future.

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Execution lifecycle
# ---------------------------------------------------------------------------

@dataclass
class ExecutionStarted:
    """Fired as soon as the execution is transitioned to RUNNING."""
    execution_id:    str
    conversation_id: str
    user_id:         str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ExecutionCompleted:
    """Fired when the execution stream finishes normally."""
    execution_id: str
    duration_ms:  int
    # None = not linked to a project. Consumers that fan out to other
    # owned resources (e.g. event-triggered daemons, main.py) must treat
    # this as a scoping key, not just informational -- see P-05b.
    project_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ExecutionFailed:
    """Fired when an unhandled exception terminates the execution."""
    execution_id:  str
    duration_ms:   int
    error_type:    str   # type(exc).__name__
    error_message: str = ""  # str(exc) — full exception message text
    # None = not linked to a project. Same scoping contract as
    # ExecutionCompleted.project_id above.
    project_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ExecutionCancelled:
    """Fired when the execution is cancelled (user request or asyncio task cancel)."""
    execution_id:  str
    duration_ms:   int
    cancel_reason: str  # "user" | "task_cancelled"
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------

@dataclass
class ToolCalled:
    """
    Fired at the start of each tool execution attempt (including retries).
    `attempt` is 1-based; `retry_count` is attempt - 1.
    """
    execution_id: str
    call_id:      str
    tool_name:    str
    attempt:      int
    retry_count:  int
    timestamp: float = field(default_factory=time.time)


@dataclass
class ToolResult:
    """
    Fired after a tool attempt resolves (success or failure).
    On success: error_type is None.
    On failure: error_type is one of "validation" | "timeout" | "internal" | "permission".
    """
    execution_id: str
    call_id:      str
    tool_name:    str
    success:      bool
    latency_ms:   int
    retry_count:  int
    error_type:   Optional[str] = None   # None on success
    timestamp: float = field(default_factory=time.time)


@dataclass
class ToolPermissionDenied:
    """Fired when a tool raises PermissionError (non-retryable, distinct from generic failure)."""
    execution_id: str
    call_id:      str
    tool_name:    str
    error:        str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ToolApprovalRequested:
    """
    Fired when the dispatcher pauses execution to await Human-in-the-Loop approval.

    The approval_token is the key used to look up the waiting asyncio.Event in
    the ApprovalGate.  Clients receive this via an SSE ``approval_required``
    event and must POST to /executions/{id}/approve or /executions/{id}/reject
    with this token.
    """
    execution_id:   str
    call_id:        str
    tool_name:      str
    authority:      str   # "mutating" | "destructive"
    args_preview:   str   # sanitised args summary (no secrets, truncated)
    approval_token: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ToolApprovalResolved:
    """
    Fired once the user approves or rejects a tool call (or the gate times out).

    approved=True  → execution resumes; tool runs normally.
    approved=False → execution is cancelled with reason user_rejected_tool.
    timeout=True   → no user response received within the timeout window.
    """
    execution_id:   str
    call_id:        str
    tool_name:      str
    approved:       bool
    timeout:        bool = False
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Agent ReAct loop (reserved — published by AgentEngine in future migration)
# ---------------------------------------------------------------------------

@dataclass
class StepCompleted:
    """
    Fired after each ReAct step completes (think → act → observe cycle).
    Currently emitted via legacy observability.emit() in AgentEngine;
    reserved here for future migration.
    """
    execution_id: str
    session_id:   str
    step_index:   int
    timestamp: float = field(default_factory=time.time)


@dataclass
class FallbackTriggered:
    """
    Fired when the agent terminates early due to cancellation, error, or step limit.
    reason: "cancelled" | "max_steps" | "error" | "fatal"
    """
    execution_id: str
    session_id:   str
    step_index:   int
    reason:       str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProviderFallbackTriggered:
    """
    Fired when AgentEngine falls back to a different provider/model because the
    active provider responded with a fallback-eligible error
    (rate_limit | provider_down | invalid_model).

    Fields
    ------
    failed_provider  : provider string that raised the error
    failed_model     : model string that raised the error
    failure_reason   : ErrorType.value string (e.g. "rate_limit")
    fallback_provider: provider string being tried next
    fallback_model   : model string being tried next
    fallback_index   : 1-based position in the user's fallback_chain list
    """
    execution_id:      str
    session_id:        str
    failed_provider:   str
    failed_model:      str
    failure_reason:    str
    fallback_provider: str
    fallback_model:    str
    fallback_index:    int   # 1-based
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Subagent lifecycle (G-003)
# ---------------------------------------------------------------------------

@dataclass
class SubagentStarted:
    """
    Fired when delegate_to_agent begins running a child AgentEngine.
    parent_call_id is the tool call_id in the orchestrator's ReAct loop.
    """
    execution_id:   str
    agent_name:     str
    parent_call_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class SubagentCompleted:
    """
    Fired when a child AgentEngine finishes (success or failure).
    success=False means the child raised an error or returned an ErrorChunk.
    """
    execution_id:   str
    agent_name:     str
    parent_call_id: str
    success:        bool
    duration_ms:    int
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Convenience: all concrete event types (useful for wildcard subscription)
# ---------------------------------------------------------------------------

ALL_EVENT_TYPES = (
    ExecutionStarted,
    ExecutionCompleted,
    ExecutionFailed,
    ExecutionCancelled,
    ToolCalled,
    ToolResult,
    ToolPermissionDenied,
    ToolApprovalRequested,
    ToolApprovalResolved,
    StepCompleted,
    FallbackTriggered,
    ProviderFallbackTriggered,
    SubagentStarted,
    SubagentCompleted,
)
