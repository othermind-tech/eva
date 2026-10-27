# api/executions.py
#
# Read-only Execution APIs.
#   GET /executions/{execution_id}/trace   — step-by-step execution trace
#   GET /executions/{execution_id}/metrics — aggregated execution metrics
#
# Reconstructs an ordered, step-by-step trace of an execution from two
# existing data sources — no new tables are created or modified.
#
# Data source strategy
# --------------------
# PENDING   → trace is empty; execution hasn't started yet.
# RUNNING   → snapshot the live LocalShadowBuffer (in-memory, zero-copy).
#             Only TextChunk / ReasoningChunk types appear there because
#             stream_and_buffer() only yields those two to the runner.
# terminal  → read the persisted DB messages for the conversation,
#  (COMPLETED / FAILED / ABORTED / CANCELLED)
#             ordered by created_at.  Each message can produce up to two
#             steps: one for its text content and one for its reasoning.
#
# Ownership enforcement
# ---------------------
# The broker hash execution:{id} stores the user_id that created the job.
# We compare it against the authenticated caller before returning anything,
# mirroring the pattern used in api/eva_response.py.
#
# Endpoint
# --------
#   GET /executions/{execution_id}/trace

import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.auth.dependencies import get_current_auth
from api.tools import TOOL_AUTHORITY_MAP
from core.db.repository import DBRepo, ExecutionMetricsRepo, PlanRepo
from core.db.session import AsyncSessionLocal
from core.execution.redis_registry import redis_execution_registry
from core.metrics.collector import metrics_collector

router = APIRouter(prefix="/executions", tags=["Executions"])

# ---------------------------------------------------------------------------
# Terminal states — these are served from the DB rather than the shadow buffer
# ---------------------------------------------------------------------------
_TERMINAL_STATUSES = {"completed", "failed", "aborted", "cancelled"}


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ExecutionSummary(BaseModel):
    """Lightweight summary of a single execution — used by GET /executions list."""
    execution_id: str
    conversation_id: str
    status: str
    triggered_by: Optional[str] = None   # "conversation" | "daemon"
    daemon_name: Optional[str] = None
    project_id: Optional[str] = None
    model_used: Optional[str] = None
    provider_used: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: Optional[str] = None   # ISO-8601
    completed_at: Optional[str] = None # ISO-8601


class RunningExecutionSummary(BaseModel):
    """
    One live (non-terminal) execution for GET /executions/running.

    Source is the in-memory ExecutionRegistry, not the DB -- the DB's
    execution_metrics row for a still-running execution doesn't exist yet
    (only written at terminal state), and this is also the one place a
    live, cancellable cancel_token actually lives. Used by the app-close
    flow to decide whether to warn the user before quitting, and to know
    which conversation_ids to cancel if they choose to stop and quit.
    """
    conversation_id: str
    execution_id: str
    execution_mode: str   # "conversational" | "plan_execute" | "daemon" | "subagent_background" | "background_process" (Phase 3)
    status: str            # ...includes "awaiting_wakeup" (P-19) alongside the usual ExecutionStatus values
    started_at: Optional[str] = None   # ISO-8601; None if not yet set
    # Phase 3 (background-process-supervision plan) -- only set for
    # execution_mode="background_process" rows. None for every other mode.
    pid: Optional[int] = None
    log_path: Optional[str] = None
    # Frontend-requested addition (2026-09-17): the Activity panel had nothing
    # but the bare PID to show for a background-process row. This is the
    # process's own argv (BackgroundProcess.command, captured at start time),
    # joined into a single display string -- not re-parseable, not truncated
    # server-side (frontend's choice, to keep any future "see full command"
    # affordance UI-only). Only set for execution_mode="background_process".
    command: Optional[str] = None
    # Session-scoped background processes (2026-09-29) -- only set for
    # execution_mode="background_process" rows. False (default): stopped
    # automatically when EVA quits. True: the user (or the agent at start
    # time) opted this specific process out of that cleanup. Drives the
    # Activity panel's "keep running after EVA closes" toggle state.
    keep_alive_on_app_shutdown: Optional[bool] = None


class TraceStep(BaseModel):
    step_index: int
    type: str                      # text | reasoning | tool_call | tool_result | error | step_progress
    content: Any                   # str for text/reasoning/error; dict for tool args/result/step_progress
    tool_name: Optional[str] = None
    authority: Optional[str] = None  # read-only | mutating | destructive | unclassified
    timestamp: Optional[str] = None  # ISO-8601 from DB; None for live shadow-buffer steps
    duration_ms: Optional[int] = None


class ExecutionTraceResponse(BaseModel):
    execution_id: str
    conversation_id: str
    status: str          # pending | running | awaiting_approval | completed | failed | aborted | cancelled
    is_partial: bool     # True when the execution is still RUNNING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    trace: List[TraceStep]
    # Set only when status == "awaiting_approval"; contains the UUID token
    # the client must pass to POST /executions/{id}/approve or /reject.
    pending_approval_token: Optional[str] = None
    # Set only when status == "awaiting_plan_approval"; contains the UUID token
    # the client must pass to POST /executions/{id}/plan-decision
    # (decision="approve" | "edit" | "reject" — UX-01).
    pending_plan_approval_token: Optional[str] = None
    # Non-empty when one or more MCP server connections failed during tool
    # registration at execution start.  Keys are MCP config names; values are
    # the error messages.  Always None (not present) for executions where all
    # MCP servers registered successfully.
    mcp_registration_errors: Optional[dict] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _authority(tool_name: Optional[str]) -> Optional[str]:
    """Return authority level for a tool name, or None if tool_name is absent."""
    if not tool_name:
        return None
    return TOOL_AUTHORITY_MAP.get(tool_name, "unclassified")


async def _trace_from_shadow_buffer(execution_id: str) -> List[TraceStep]:
    """
    Snapshot the LocalShadowBuffer for a running or recently completed execution.

    Non-blocking: returns all chunks written so far via buf.chunks_from(0).
    The buffer persists for ~1 hour after execution (LocalBroker cleanup TTL).
    """
    from core.broker import local_broker as _broker
    buf = _broker.get_shadow_buffer(execution_id)
    raw_items = buf.chunks_from(0)  # snapshot — no network, no blocking

    steps: List[TraceStep] = []
    for idx, item in enumerate(raw_items):
        # Items are already dicts (stored as dict in LocalShadowBuffer)
        if not isinstance(item, dict):
            try:
                item = json.loads(item)
            except (json.JSONDecodeError, TypeError):
                continue

        chunk_type = item.get("type", "")

        if chunk_type == "text":
            content = item.get("content", "")
            if not content:           # skip empty finish-reason sentinels
                continue
            steps.append(TraceStep(
                step_index=idx,
                type="text",
                content=content,
            ))

        elif chunk_type == "reasoning":
            content = item.get("content", "")
            if not content:
                continue
            steps.append(TraceStep(
                step_index=idx,
                type="reasoning",
                content=content,
            ))

        elif chunk_type == "tool_call":
            tool_name = item.get("tool_name")
            steps.append(TraceStep(
                step_index=idx,
                type="tool_call",
                content=item.get("arguments") or {},
                tool_name=tool_name,
                authority=_authority(tool_name),
            ))

        elif chunk_type == "tool_result":
            tool_name = item.get("tool_name")
            steps.append(TraceStep(
                step_index=idx,
                type="tool_result",
                content=item.get("result") or {},
                tool_name=tool_name,
                authority=_authority(tool_name),
            ))

        elif chunk_type == "error":
            steps.append(TraceStep(
                step_index=idx,
                type="error",
                content=item.get("message", "unknown error"),
            ))

        elif chunk_type == "step_progress":
            # V6.14: PEV's structured phase-transition events (planning,
            # running, verifying, completed, failed, replanning,
            # replan_skipped, plan_complete) -- see StepProgressChunk in
            # core/types.py. Surfacing these here is what makes them visible
            # via GET /trace (not just the live SSE stream): without this
            # branch they were silently dropped from the trace entirely, and
            # a reloaded/re-polled conversation would have no record of the
            # plan or its verification results once the shadow buffer (~1hr
            # TTL) expired past the live stream.
            steps.append(TraceStep(
                step_index=idx,
                type="step_progress",
                content={
                    "step_index":            item.get("step_index"),
                    "total_steps":           item.get("total_steps"),
                    "step_title":            item.get("step_title"),
                    "step_status":           item.get("step_status"),
                    "goal":                  item.get("goal"),
                    "plan_steps":            item.get("plan_steps"),
                    "verification_summary":  item.get("verification_summary"),
                    "verification_issues":   item.get("verification_issues"),
                    "replan_reason":         item.get("replan_reason"),
                    "error_detail":          item.get("error_detail"),
                    "steps_completed":       item.get("steps_completed"),
                    "steps_failed":          item.get("steps_failed"),
                    "replan_count":          item.get("replan_count"),
                },
            ))

    # Re-index sequentially (some items may have been skipped)
    for i, step in enumerate(steps):
        step.step_index = i

    return steps


async def _trace_from_db(conversation_id: str) -> List[TraceStep]:
    """
    Build the trace from persisted DB messages for a terminal execution.

    Each Message row can produce up to two steps:
      1. A "text" step for its main content.
      2. A "reasoning" step if the message has a reasoning field.

    Steps are ordered by message.created_at.  step_index is assigned
    sequentially after flattening.

    Note: tool_call / tool_result steps are not currently populated
    because the worker finalizer passes tool_call=None.  The schema and
    authority lookup are already in place for when that changes.
    """
    async with AsyncSessionLocal() as db:
        messages = await DBRepo.list_messages_for_conversation(db, conversation_id)

    # Ensure deterministic ordering (list_messages_for_conversation may not
    # guarantee order depending on DB driver).
    messages = sorted(messages, key=lambda m: m.created_at)

    raw_steps: List[TraceStep] = []

    for msg in messages:
        if msg.content is None:
            continue

        ts = msg.created_at.isoformat() if msg.created_at else None

        # --- Main content step ---
        raw_steps.append(TraceStep(
            step_index=0,           # assigned below
            type="text",
            content=msg.content,
            timestamp=ts,
        ))

        # --- Reasoning step (if present) ---
        if msg.reasoning:
            raw_steps.append(TraceStep(
                step_index=0,
                type="reasoning",
                content=msg.reasoning,
                timestamp=ts,
            ))

    # Assign final sequential indices
    for i, step in enumerate(raw_steps):
        step.step_index = i

    return raw_steps


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# B-021 — List executions for the authenticated user.
# Must be registered BEFORE the /{execution_id}/... routes to prevent
# FastAPI from treating the literal string "list" as an execution_id.
@router.get(
    "",
    response_model=List[ExecutionSummary],
    summary="List executions for the authenticated user",
)
async def list_executions(
    conversation_id: Optional[str] = None,
    execution_status: Optional[str] = None,
    triggered_by: Optional[str] = None,
    daemon_name: Optional[str] = None,
    limit: int = 20,
    auth=Depends(get_current_auth),
):
    """
    Returns a paginated list of executions for the authenticated user, newest first.

    **Query parameters:**
    - `conversation_id` — filter to executions belonging to a specific conversation
    - `execution_status` — filter by status (`completed`, `failed`, `cancelled`, etc.)
    - `triggered_by` — filter by trigger source: `conversation` or `daemon`
    - `daemon_name` — filter to executions from a specific daemon
    - `limit` — max number of results to return (default 20, max 100)
    """
    user_id = auth["user_id"]
    limit = min(max(1, limit), 100)

    async with AsyncSessionLocal() as db:
        rows = await ExecutionMetricsRepo.list_for_user(
            db,
            user_id,
            conversation_id=conversation_id,
            status=execution_status,
            triggered_by=triggered_by,
            daemon_name=daemon_name,
            limit=limit,
        )

    return [
        ExecutionSummary(
            execution_id=row.execution_id,
            conversation_id=row.conversation_id,
            status=row.status or "unknown",
            triggered_by=getattr(row, "triggered_by", None),
            daemon_name=getattr(row, "daemon_name", None),
            project_id=getattr(row, "project_id", None),
            model_used=row.model_used,
            provider_used=row.provider_used,
            duration_ms=row.duration_ms,
            created_at=row.created_at.isoformat() if row.created_at else None,
            completed_at=row.completed_at.isoformat() if row.completed_at else None,
        )
        for row in rows
    ]


@router.get(
    "/running",
    response_model=List[RunningExecutionSummary],
    summary="List all live (non-terminal) executions for the authenticated user",
)
async def list_running_executions(auth=Depends(get_current_auth)):
    """
    Returns every currently non-terminal execution for the authenticated
    user, across every conversation and daemon -- including ones with no
    open SSE connection (e.g. the user navigated away, or it's a daemon
    that fired on its own schedule).

    Backed by the in-memory ExecutionRegistry, not the DB: execution_metrics
    rows for a still-running execution don't exist until it finishes, and
    this is the one place a live, actually-cancellable cancel_token lives.

    Used by the app-close flow (Electron) to decide whether to warn the
    user before quitting, and which conversation_ids to POST /cancel if
    they choose to stop and quit rather than abandon the work.
    """
    from datetime import datetime, timezone as _tz
    from core.execution import execution_registry
    from core.db.session import AsyncSessionLocal as _ASL
    from core.db.repository import ProcessRepo

    user_id = auth["user_id"]
    running = execution_registry.list_running_for_user(user_id)

    summaries = [
        RunningExecutionSummary(
            conversation_id=e.conversation_id,
            execution_id=e.execution_id,
            execution_mode=e.execution_mode,
            status=e.status.value,
            started_at=(
                datetime.fromtimestamp(e.started_at, tz=_tz.utc).isoformat()
                if e.started_at else None
            ),
        )
        for e in running
    ]

    # Phase 3 (background-process supervision): append DB-tracked background processes
    # (started via the background_process tool / core/shell/executor.py) as
    # synthesized rows. These are real OS processes, not ConversationExecution
    # objects, so they come from ProcessRepo, not the in-memory
    # execution_registry above -- a separate query, appended to the same list
    # so the frontend's existing running-executions view shows both without
    # a second panel.
    async with _ASL() as db:
        bg_processes = await ProcessRepo.list_running_for_user(db, user_id)

    summaries.extend(
        RunningExecutionSummary(
            # BackgroundProcess.conversation_id/.execution_id are both
            # nullable (a process can be started standalone, not from
            # within a tracked conversation) but this schema's fields are
            # required strings -- conversation_id falls back to "" and
            # execution_id uses the process's own id (never null), which
            # doubles as the identifier process.stopped/.crashed events
            # carry, so the frontend can match an incoming event to the
            # row it should remove/update in this same list.
            conversation_id=proc.conversation_id or "",
            execution_id=proc.id,
            execution_mode="background_process",
            status=proc.status,
            started_at=proc.started_at.isoformat() if proc.started_at else None,
            pid=proc.pid,
            log_path=proc.log_path,
            command=" ".join(proc.command) if proc.command else None,
            keep_alive_on_app_shutdown=proc.keep_alive_on_app_shutdown,
        )
        for proc in bg_processes
    )

    return summaries


# Must be registered BEFORE the /{execution_id}/... routes below -- same
# reasoning as list_executions() above ("running" would otherwise be
# swallowed as a literal execution_id path parameter).
@router.get(
    "/{execution_id}/trace",
    response_model=ExecutionTraceResponse,
    summary="Get the execution trace for a given execution",
)
async def get_execution_trace(
    execution_id: str,
    auth=Depends(get_current_auth),
):
    """
    Returns an ordered list of trace steps for the given execution.

    **Ownership:** Only the authenticated user who submitted the original
    chat request (and therefore owns the execution) may access its trace.
    Any other caller receives `403 Forbidden`.

    **Live executions:** When the execution is still `running`, the trace
    is sourced from the live shadow buffer and `is_partial` is set to
    `true`.  Re-poll to see additional steps as they arrive.

    **Completed executions:** Trace is reconstructed from the persisted
    DB messages for the conversation, ordered by timestamp.

    **Step types currently emitted:**

    | type       | when                          | content shape  |
    |------------|-------------------------------|----------------|
    | text       | assistant text response       | string         |
    | reasoning  | hidden chain-of-thought       | string         |
    | tool_call  | future (not yet persisted)    | dict of args   |
    | tool_result| future (not yet persisted)    | dict of result |
    | error      | provider / tool error         | string         |
    """
    user_id = auth["user_id"]

    # ------------------------------------------------------------------
    # 1. Resolve execution metadata from the broker
    # ------------------------------------------------------------------
    client = await redis_execution_registry.connect()
    data = await client.hgetall(f"execution:{execution_id}")

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    # ------------------------------------------------------------------
    # 2. Enforce ownership
    # ------------------------------------------------------------------
    if data.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    conversation_id              = data.get("conversation_id", "")
    exec_status                  = data.get("status", "unknown")
    started_at                   = data.get("started_at")
    completed_at                 = data.get("completed_at")
    pending_approval_token       = data.get("pending_approval_token")       # set by gate_approval()
    pending_plan_approval_token  = data.get("pending_plan_approval_token")  # set by _gate_plan_approval()

    # MCP registration errors stored by the worker at execution start.
    _mcp_errors_raw = data.get("mcp_errors")
    mcp_registration_errors: Optional[dict] = None
    if _mcp_errors_raw:
        try:
            mcp_registration_errors = json.loads(_mcp_errors_raw)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 3. Build trace based on execution state
    # ------------------------------------------------------------------
    if exec_status == "pending":
        # Execution is queued but the worker hasn't started it yet
        trace: List[TraceStep] = []
        is_partial = False

    elif exec_status == "running":
        # Live snapshot from the shadow buffer
        trace = await _trace_from_shadow_buffer(execution_id)
        is_partial = True

    elif exec_status == "awaiting_approval":
        # Worker is paused at the Human-in-the-Loop gate.  Return whatever
        # stream content has been written so far plus the approval token so
        # the client can call /approve or /reject without needing the SSE stream.
        trace = await _trace_from_shadow_buffer(execution_id)
        is_partial = True

    elif exec_status == "awaiting_plan_approval":
        # Worker is paused at the Plan Approval gate.  Return stream content
        # plus the plan approval token so the client can approve or reject.
        trace = await _trace_from_shadow_buffer(execution_id)
        is_partial = True

    elif exec_status in _TERMINAL_STATUSES:
        # Prefer the LocalShadowBuffer (persists ~1 hour after completion) because it
        # captures ToolCallChunk / ToolResultChunk steps that the DB finalizer does not
        # persist (tool_call=None placeholder).  Fall back to DB-only trace when
        # the buffer has been evicted by the broker cleanup task.
        buf_trace = await _trace_from_shadow_buffer(execution_id)
        trace = buf_trace if buf_trace else await _trace_from_db(conversation_id)
        is_partial = False
        # Tokens are no longer valid once the execution reaches a terminal state.
        pending_approval_token      = None
        pending_plan_approval_token = None

    else:
        # Unknown / unexpected status — return empty trace gracefully
        trace = []
        is_partial = False

    return ExecutionTraceResponse(
        execution_id=execution_id,
        conversation_id=conversation_id,
        status=exec_status,
        is_partial=is_partial,
        started_at=started_at,
        completed_at=completed_at,
        trace=trace,
        pending_approval_token=pending_approval_token,
        pending_plan_approval_token=pending_plan_approval_token,
        mcp_registration_errors=mcp_registration_errors,
    )


# ===========================================================================
# Execution Metrics
# ===========================================================================
#
# Data sourcing strategy
# ─────────────────────
# terminal (COMPLETED / FAILED / ABORTED / CANCELLED)
#   → read the persisted ExecutionMetrics DB row (written by worker).
#
# RUNNING
#   → DB row may not exist yet; return a *partial* snapshot from the
#     in-memory MetricsCollector (populated in real-time by the event bus).
#
# PENDING
#   → execution hasn't started; return a zero-filled partial response.
#
# Ownership
# ─────────
# Same pattern as /trace: execution:{id} broker hash stores user_id.
# We compare it against the authenticated caller before returning anything.


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ToolCallMetric(BaseModel):
    """Per-tool aggregated stats for one execution."""
    tool_name:           str
    call_count:          int            = Field(description="Number of distinct first-attempt invocations")
    avg_latency_ms:      int            = Field(description="Average wall-clock latency across all attempts")
    failure_count:       int            = Field(description="Invocations that ended in any error")
    success_count:       int            = Field(description="call_count - failure_count (clamped ≥ 0)")
    last_denial_reason:  Optional[str]  = None  # Gap 9: most recent ToolPermissionDenied.error


class ExecutionMetricsResponse(BaseModel):
    """
    Aggregated metrics for a single execution.

    Fields
    ──────
    execution_id        : EVA execution identifier (UUID)
    conversation_id     : parent conversation
    status              : pending | running | completed | failed | aborted | cancelled
    is_partial          : True when the execution is still RUNNING or PENDING
                          (data reflects only what has been collected so far)

    duration_ms         : wall-clock duration from RUNNING transition to terminal state.
                          None while execution is still in progress.
    started_at          : ISO-8601 timestamp from broker metadata
    completed_at        : ISO-8601 timestamp from broker metadata (None if still running)

    model_used          : model string (e.g. "llama-3.3-70b-versatile")
    provider_used       : provider string (e.g. "groq")

    prompt_tokens       : tokens in all input messages for this execution
    completion_tokens   : tokens in the generated output
    reasoning_tokens    : tokens spent on chain-of-thought (provider-specific)
    cached_tokens       : the part of prompt_tokens served from the provider's
                          prompt cache (billed at a discount)
    total_tokens        : prompt + completion (reasoning is a subset of completion)

    total_steps         : number of distinct tool invocations (first-attempt ToolCalled
                          events).  0 for a direct single-response execution.
                          NOTE: does not include the final text-generation step until
                          AgentEngine publishes StepCompleted events.
    fallbacks_triggered : number of times the agent terminated early (max_steps /
                          error / cancelled paths).  Currently populated only after
                          AgentEngine is migrated to publish FallbackTriggered events.
    termination_reason  : "success" | error_type (e.g. "RuntimeError") |
                          "user" | "task_cancelled" | None while running.

    tool_calls          : per-tool breakdown sorted by call_count descending.
    """
    execution_id:        str
    conversation_id:     str
    status:              str
    is_partial:          bool = False

    # Timing
    duration_ms:    Optional[int] = None
    started_at:     Optional[str] = None
    completed_at:   Optional[str] = None

    # Model
    model_used:    Optional[str] = None
    provider_used: Optional[str] = None

    # Tokens
    prompt_tokens:     int = 0
    completion_tokens: int = 0
    reasoning_tokens:  int = 0   # subset of completion_tokens
    cached_tokens:     int = 0   # subset of prompt_tokens
    total_tokens:      int = 0   # derived: prompt + completion

    # ReAct
    total_steps:         int = 0
    fallbacks_triggered: int = 0
    fallback_details:    List[dict] = []        # Gap 8: per-fallback provider/model/reason
    termination_reason:  Optional[str] = None
    error_message:       Optional[str] = None   # full exception text; None on success/cancel

    # Subagent stats (Gap 10)
    subagent_calls:    int = 0
    subagent_failures: int = 0

    # Tool breakdown
    tool_calls: List[ToolCallMetric] = []

    # Cost (raw USD value from the metrics row)
    computed_cost_usd: Optional[float] = None

    # Lineage — set when this execution is a child of another (B-073 Gap 2)
    # parent_execution_id is always None in current builds because child
    # AgentEngine runs share the parent execution context; tracked inline.
    parent_execution_id: Optional[str] = None
    subagent_name:       Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _metrics_from_db_row(
    row,                     # ExecutionMetrics ORM instance
    exec_data: dict,         # execution:{id} broker hash
) -> ExecutionMetricsResponse:
    """Build a complete (non-partial) response from a persisted DB row."""
    tool_metrics_raw: List[dict] = row.tool_metrics or []
    tool_calls = [
        ToolCallMetric(
            tool_name=t.get("tool_name", ""),
            call_count=t.get("call_count", 0),
            avg_latency_ms=t.get("avg_latency_ms", 0),
            failure_count=t.get("failure_count", 0),
            success_count=t.get("success_count", 0),
            last_denial_reason=t.get("last_denial_reason"),  # Gap 9
        )
        for t in tool_metrics_raw
    ]
    # Sort most-used tools first
    tool_calls.sort(key=lambda tc: tc.call_count, reverse=True)

    pt  = row.prompt_tokens     or 0
    ct  = row.completion_tokens or 0
    rt  = row.reasoning_tokens  or 0

    return ExecutionMetricsResponse(
        execution_id=row.execution_id,
        conversation_id=row.conversation_id,
        status=row.status,
        is_partial=False,
        duration_ms=row.duration_ms,
        # Gap 11: prefer persisted DB value; fall back to broker for pre-migration rows
        started_at=(
            row.started_at.isoformat()
            if getattr(row, "started_at", None)
            else exec_data.get("started_at")
        ),
        completed_at=(
            row.completed_at.isoformat()
            if row.completed_at
            else exec_data.get("completed_at")
        ),
        model_used=row.model_used,
        provider_used=row.provider_used,
        prompt_tokens=pt,
        completion_tokens=ct,
        reasoning_tokens=rt,
        cached_tokens=getattr(row, "cached_tokens", 0) or 0,
        total_tokens=pt + ct,
        total_steps=row.total_steps or 0,
        fallbacks_triggered=row.fallbacks_triggered or 0,
        fallback_details=getattr(row, "fallback_details", None) or [],   # Gap 8
        termination_reason=row.termination_reason,
        error_message=getattr(row, "error_message", None),
        subagent_calls=getattr(row, "subagent_calls", 0) or 0,           # Gap 10
        subagent_failures=getattr(row, "subagent_failures", 0) or 0,     # Gap 10
        tool_calls=tool_calls,
        computed_cost_usd=row.computed_cost_usd,
        parent_execution_id=row.parent_execution_id,
        subagent_name=row.subagent_name,
    )


def _metrics_from_snapshot(
    execution_id: str,
    conversation_id: str,
    exec_status: str,
    exec_data: dict,
    snapshot,               # ExecutionMetricsSnapshot | None
) -> ExecutionMetricsResponse:
    """
    Build a *partial* response from the in-memory MetricsCollector snapshot.
    Used for RUNNING and PENDING executions where the DB row doesn't exist yet.
    """
    tool_calls: List[ToolCallMetric] = []
    if snapshot and snapshot.tool_stats:
        tool_calls = [
            ToolCallMetric(
                tool_name=t.tool_name,
                call_count=t.call_count,
                avg_latency_ms=t.avg_latency_ms,
                failure_count=t.failure_count,
                success_count=t.success_count,
                last_denial_reason=t.last_denial_reason,  # Gap 9
            )
            for t in snapshot.tool_stats.values()
        ]
        tool_calls.sort(key=lambda tc: tc.call_count, reverse=True)

    return ExecutionMetricsResponse(
        execution_id=execution_id,
        conversation_id=conversation_id,
        status=exec_status,
        is_partial=True,
        duration_ms=snapshot.duration_ms if snapshot else None,
        started_at=exec_data.get("started_at"),
        completed_at=None,
        model_used=None,
        provider_used=None,
        prompt_tokens=0,
        completion_tokens=0,
        reasoning_tokens=0,
        total_tokens=0,
        total_steps=snapshot.total_steps if snapshot else 0,
        fallbacks_triggered=snapshot.fallbacks_triggered if snapshot else 0,
        fallback_details=snapshot.fallback_details if snapshot else [],      # Gap 8
        termination_reason=snapshot.termination_reason if snapshot else None,
        error_message=snapshot.error_message if snapshot else None,
        subagent_calls=snapshot.subagent_calls if snapshot else 0,           # Gap 10
        subagent_failures=snapshot.subagent_failures if snapshot else 0,     # Gap 10
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/{execution_id}/metrics",
    response_model=ExecutionMetricsResponse,
    summary="Get aggregated execution metrics",
)
async def get_execution_metrics(
    execution_id: str,
    auth=Depends(get_current_auth),
):
    """
    Returns aggregated metrics for the given execution.

    **Ownership:** Only the authenticated user who submitted the original
    chat request may access its metrics.  Any other caller receives
    `403 Forbidden`.

    **Terminal executions** (`completed` / `failed` / `aborted` / `cancelled`):
    Returns persisted data from the `execution_metrics` DB table.  All fields
    are fully populated.  `is_partial` is `false`.

    **Running executions:** Returns a partial real-time snapshot from the
    in-memory `MetricsCollector` (populated live by the event bus).
    Token counts and model info are `0` / `null` until the execution
    finishes.  `is_partial` is `true`.  Re-poll to see updated tool stats.

    **Pending executions:** Returns a zero-filled partial response.
    `is_partial` is `true`.

    **`total_steps`** counts distinct tool invocations (first-attempt
    `ToolCalled` events).  It does not currently include the final
    text-generation step (pending `AgentEngine` migration).

    **`fallbacks_triggered`** is populated after `AgentEngine` publishes
    `FallbackTriggered` events (migration pending; currently always 0).
    """
    user_id = auth["user_id"]

    # ------------------------------------------------------------------
    # 1. Resolve execution metadata from the broker + enforce ownership
    # ------------------------------------------------------------------
    client = await redis_execution_registry.connect()
    exec_data = await client.hgetall(f"execution:{execution_id}")

    if not exec_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    if exec_data.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    conversation_id = exec_data.get("conversation_id", "")
    exec_status     = exec_data.get("status", "unknown")

    # ------------------------------------------------------------------
    # 2. Terminal state → read from DB (authoritative, fully populated)
    # ------------------------------------------------------------------
    if exec_status in _TERMINAL_STATUSES:
        async with AsyncSessionLocal() as db:
            row = await ExecutionMetricsRepo.get(db, execution_id)

        if row:
            return _metrics_from_db_row(row, exec_data)

        # Rare race: execution just finished but worker hasn't flushed yet.
        # Fall through to partial snapshot path.

    # ------------------------------------------------------------------
    # 3. Running / pending / race → partial snapshot from in-memory collector
    # ------------------------------------------------------------------
    snapshot = metrics_collector.get_snapshot(execution_id)

    return _metrics_from_snapshot(
        execution_id=execution_id,
        conversation_id=conversation_id,
        exec_status=exec_status,
        exec_data=exec_data,
        snapshot=snapshot,
    )


# ===========================================================================
# Execution Plan — GET /executions/{id}/plan
# ===========================================================================
#
# Returns the structured execution plan generated by the PlannerEngine for a
# plan_execute execution.  For conversational executions the endpoint returns
# 404 (no plan was generated).
#
# Ownership: same pattern as /trace and /metrics.

class PlanStepResponse(BaseModel):
    index: int
    title: str
    description: str
    tool_hint: Optional[str] = None
    status: str                          # pending | running | completed | failed | skipped | cancelled
    result_summary: Optional[str] = None
    verified: bool = False
    verification_notes: Optional[str] = None


class ExecutionPlanResponse(BaseModel):
    execution_id: str
    conversation_id: str
    goal: str
    status: str          # pending | executing | completed | failed | cancelled
    current_step_index: int
    steps: List[PlanStepResponse]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@router.get(
    "/{execution_id}/plan",
    response_model=ExecutionPlanResponse,
    summary="Get the execution plan for a plan_execute execution",
)
async def get_execution_plan(
    execution_id: str,
    auth=Depends(get_current_auth),
):
    """
    Returns the structured execution plan generated by the PlannerEngine.

    Only available for executions routed to `plan_execute` mode.  Returns
    `404 Not Found` for conversational executions (no plan was generated).

    The `steps[*].status` field reflects live plan progress:
    - `pending`   — step has not started yet
    - `running`   — step is currently executing
    - `completed` — step finished and passed verification
    - `failed`    — step failed or did not pass verification
    - `skipped`   — step was bypassed (future)
    """
    user_id = auth["user_id"]

    # ------------------------------------------------------------------
    # 1. Enforce ownership via broker execution metadata
    # ------------------------------------------------------------------
    client = await redis_execution_registry.connect()
    exec_data = await client.hgetall(f"execution:{execution_id}")
    if not exec_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    if exec_data.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    # ------------------------------------------------------------------
    # 2. Fetch plan from DB
    # ------------------------------------------------------------------
    async with AsyncSessionLocal() as db:
        plan = await PlanRepo.get_by_execution(db, execution_id)

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No execution plan found for this execution. "
                   "Plans are only generated for plan_execute mode executions.",
        )

    # ------------------------------------------------------------------
    # 3. Build response
    # ------------------------------------------------------------------
    steps = [
        PlanStepResponse(
            index=s.get("index", i),
            title=s.get("title", ""),
            description=s.get("description", ""),
            tool_hint=s.get("tool_hint"),
            status=s.get("status", "pending"),
            result_summary=s.get("result_summary"),
            verified=s.get("verified", False),
            verification_notes=s.get("verification_notes"),
        )
        for i, s in enumerate(plan.steps or [])
    ]

    return ExecutionPlanResponse(
        execution_id=execution_id,
        conversation_id=plan.conversation_id,
        goal=plan.goal,
        status=plan.status,
        current_step_index=plan.current_step_index,
        steps=steps,
        created_at=plan.created_at.isoformat() if plan.created_at else None,
        updated_at=plan.updated_at.isoformat() if plan.updated_at else None,
    )


# ===========================================================================
# Execution Status — GET /executions/{id}/status  (B-009)
# ===========================================================================
#
# Lightweight alternative to /trace for clients that only need to know if an
# execution is still running, completed, or failed (e.g. after a page refresh
# when the SSE connection was lost).
#
# Data source: broker execution hash (primary) → same key as /trace and /metrics.
# Ownership:   same pattern — broker user_id must match authenticated caller.

class ExecutionStatusResponse(BaseModel):
    """Lightweight execution status. Only the essential fields."""
    execution_id: str
    status: str          # pending | running | awaiting_approval | awaiting_plan_approval
                         # | completed | failed | aborted | cancelled
    conversation_id: Optional[str] = None


@router.get(
    "/{execution_id}/status",
    response_model=ExecutionStatusResponse,
    summary="Get lightweight execution status",
)
async def get_execution_status(
    execution_id: str,
    auth=Depends(get_current_auth),
):
    """
    Returns just the status of an execution — no trace content.

    Use this after a page refresh when the SSE connection was lost to decide
    whether to re-open the stream (`running`) or fetch the persisted trace
    (`completed` / `failed`).

    **Ownership:** Only the authenticated owner of the execution may read it.

    **Restart note:** this checks the live in-memory registry first (fast
    path — the common case, still-running or just-finished executions in
    this server session). If the process has restarted since the execution
    ran, that registry is empty (in-memory only), so this falls back to the
    durable execution_metrics record instead of 404ing on every execution
    that predates the current process — including ones a boot-time
    reconciliation pass (B2-1.7/4.3) marked `aborted` after a crash.
    """
    user_id = auth["user_id"]

    client = await redis_execution_registry.connect()
    data = await client.hgetall(f"execution:{execution_id}")

    if data:
        if data.get("user_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden",
            )
        return ExecutionStatusResponse(
            execution_id=execution_id,
            status=data.get("status", "unknown"),
            conversation_id=data.get("conversation_id"),
        )

    # Not in the live registry -- either it never existed, or the process
    # has restarted since it ran. Fall back to durable history.
    async with AsyncSessionLocal() as db:
        row = await ExecutionMetricsRepo.get(db, execution_id)

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    if row.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    return ExecutionStatusResponse(
        execution_id=execution_id,
        status=row.status,
        conversation_id=row.conversation_id,
    )


# ---------------------------------------------------------------------------
# Execution Retry (Wave 2.4)
# ---------------------------------------------------------------------------

# States that can be retried — terminal states where the execution did NOT
# succeed.  COMPLETED is intentionally excluded: a successful execution needs
# no retry.  PENDING / RUNNING / AWAITING_* are excluded because the
# execution is still active.
_RETRYABLE_STATUSES = {"failed", "aborted", "cancelled"}


class ExecutionRetryResponse(BaseModel):
    new_execution_id: str
    original_execution_id: str
    conversation_id: str
    execution_mode: str
    status: str = "queued"


@router.post("/{execution_id}/retry", response_model=ExecutionRetryResponse)
async def retry_execution(
    execution_id: str,
    auth=Depends(get_current_auth),
):
    """
    Re-queue a failed, aborted, or cancelled execution.

    Creates a **new** execution that runs against the same conversation —
    the conversation history already contains the original user message, so
    the agent will pick it up naturally.

    **Retryable states:** ``failed``, ``aborted``, ``cancelled``

    **Not retryable:** ``completed`` (409), ``pending`` / ``running`` /
    ``awaiting_*`` (409 — still active).

    Returns the new ``execution_id`` and ``status: "queued"``.
    """
    user_id = auth["user_id"]

    client = await redis_execution_registry.connect()

    # ── Fetch original execution metadata ────────────────────────────────────
    data = await client.hgetall(f"execution:{execution_id}")

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    # ── Ownership check ──────────────────────────────────────────────────────
    if data.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    # ── State check ──────────────────────────────────────────────────────────
    original_status = data.get("status", "")
    if original_status not in _RETRYABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Execution is in state '{original_status}' and cannot be retried. "
                f"Only {sorted(_RETRYABLE_STATUSES)} executions are retryable."
            ),
        )

    conversation_id  = data.get("conversation_id", "")
    session_id       = data.get("session_id", "")
    execution_mode   = data.get("execution_mode", "conversational")

    # ── Create & enqueue new execution ───────────────────────────────────────
    new_execution_id = str(uuid4())

    await redis_execution_registry.create_execution(
        execution_id=new_execution_id,
        convo_id=conversation_id,
        session_id=session_id,
        user_id=user_id,
        execution_mode=execution_mode,
    )
    await redis_execution_registry.enqueue_job(new_execution_id)

    return ExecutionRetryResponse(
        new_execution_id=new_execution_id,
        original_execution_id=execution_id,
        conversation_id=conversation_id,
        execution_mode=execution_mode,
    )


# ---------------------------------------------------------------------------
# Execution Cancel by execution_id (B-057)
# ---------------------------------------------------------------------------

class ExecutionCancelResponse(BaseModel):
    execution_id: str
    status: str          # "cancelled" or existing terminal status
    conversation_id: Optional[str] = None


@router.post("/{execution_id}/cancel", response_model=ExecutionCancelResponse)
async def cancel_execution_by_id(
    execution_id: str,
    auth=Depends(get_current_auth),
):
    """
    Cancel an execution by its execution_id.

    - **404** if the execution does not exist.
    - **403** if the caller does not own the execution.
    - **409** if the execution is already in a terminal state
      (completed / failed / aborted / cancelled).
    - **200** on successful cancellation request.

    The cancellation is best-effort: if the worker is actively running
    the execution it will detect the cancelled flag on its next cycle.
    """
    user_id = auth["user_id"]

    client = await redis_execution_registry.connect()
    data = await client.hgetall(f"execution:{execution_id}")

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    if data.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    current_status = data.get("status", "")
    conversation_id = data.get("conversation_id")

    if current_status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Execution is already in terminal state '{current_status}' "
                f"and cannot be cancelled."
            ),
        )

    # Mark cancelled in the broker — the worker detects this on its next cycle
    await redis_execution_registry.mark_execution_cancelled(
        execution_id, reason="user"
    )

    # Best-effort: also signal the in-memory cancel token if the execution
    # is tracked by the local execution registry (same process / worker).
    try:
        from core.execution import execution_registry as _mem_registry
        _mem_exec = _mem_registry.get_latest(conversation_id) if conversation_id else None
        if (
            _mem_exec
            and _mem_exec.execution_id == execution_id
            and hasattr(_mem_exec, "cancel_token")
        ):
            _mem_exec.cancel_token.cancelled = True
    except Exception:
        pass  # Non-fatal — broker mark is authoritative

    return ExecutionCancelResponse(
        execution_id=execution_id,
        status="cancelled",
        conversation_id=conversation_id,
    )
