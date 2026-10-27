# core/execution/pev_runner.py
#
# Plan → Execute → Verify (PEV) Runner  —  V2.9, narration restructured V6.14
#
# Orchestrates the full PEV loop for executions routed to plan_execute mode.
#
# V6.14 note: every phase transition below is carried by a structured
# StepProgressChunk (or, for genuine error conditions, an ErrorChunk) --
# never by hand-authored markdown mixed into the answer's TextChunk stream.
# Previously this file yielded both: a StepProgressChunk AND a duplicate
# narrative TextChunk at nearly every transition ("## Planning...",
# "_Verifying step..._", "_Adaptive Re-planning..._", etc.), so the
# frontend's visible answer text was polluted with EVA's own process
# narration with no way to tell it apart from the model's actual response.
# The fix removes all narrative
# TextChunk yields from this file; TextChunk here now only ever carries
# genuine model-generated tokens forwarded from `agent.call()`.
#
# Flow
# ----
# 1. PLANNING  — PlannerEngine generates a structured JSON plan.
#                Emits StepProgressChunk(step_status="planning") twice: once
#                to signal planning has started, once with plan_steps set.
#
# 1a. PLAN APPROVAL (optional) — If the user session has require_plan_approval=True
#                the runner pauses (AWAITING_PLAN_APPROVAL state) and waits for
#                the user to call POST /executions/{id}/plan-decision with
#                decision="approve" | "edit" | "reject" (UX-01). A "edit"
#                decision replaces the plan that executes with the
#                user-submitted one (validated via planner.validate_plan_shape()).
#                A plan_approval_required SSE chunk carries the plan_approval_token
#                -- this is what ApprovalGate.tsx renders; no duplicate text notice.
#
# 2. RUNNING   — For each step in the plan:
#                a. Emit a StepProgressChunk (step_status="running") -- this
#                   is also the step announcement (step_title).
#                b. Build a focused message list for the step.
#                c. Run the AgentEngine; stream all chunks.
#                d. Collect the step's aggregated text output.
#
# 3. VERIFYING — After each step:
#                a. Emit StepProgressChunk (step_status="verifying"); carries
#                   error_detail if the step's AgentEngine call raised.
#                b. Call VerifierEngine.
#                c. Emit StepProgressChunk (step_status="completed" or
#                   "failed"); carries verification_summary/verification_issues.
#                d. If failed AND steps remain → ADAPTIVE RE-PLANNING:
#                   i.  Emit StepProgressChunk (step_status="replanning",
#                       replan_reason set).
#                   ii. Call planner_engine.replan() with context.
#                   iii. Emit StepProgressChunk (step_status="replanning",
#                        plan_steps set to the revised steps).
#                   iv. Continue loop with new steps.
#                e. If the max re-plan attempt cap is hit, emit
#                   StepProgressChunk (step_status="replan_skipped",
#                   replan_reason set) instead of re-planning again.
#
# A final StepProgressChunk (step_status="plan_complete") carries
# steps_completed/steps_failed/steps_skipped/replan_count once the whole plan finishes.
#
# Cancellation
# ------------
# Cancel checks happen at the same points as in runner.py.
#
# Error resilience
# ----------------
# PlannerEngine, VerifierEngine and replan() all fall back to safe defaults
# on error so the PEV loop always makes forward progress even if LLM calls fail.

import asyncio
import copy
import json
import time
import logging
from typing import Any, AsyncGenerator, Callable, Dict, List, NamedTuple, Optional
from uuid import uuid4

from core.stream_buffer import BufferedStream
from core.execution.gate_markers import (
    GATE_KIND_PLAN_APPROVAL, delete_gate_marker, write_gate_marker,
)
from core.execution.models import ConversationExecution, ExecutionStatus
from core.execution.redis_registry import redis_execution_registry
from core.execution.state_machine import ExecutionStateMachine
from core.execution.planner import (
    planner_engine,
    validate_plan_shape,
    PlanValidationError,
)
from core.execution.verifier import verifier_engine
from core.execution.plan_verifier import plan_verifier_engine
from core.types import (
    TextChunk, ErrorChunk, UsageChunk, StepProgressChunk, ToolCallChunk, ToolResultChunk,
)
from core.execution.step_record import (
    StepRecord, StepToolLedger, active_tool_names, build_active_tools_line,
    build_prior_steps_block, make_record,
)
from core.event_bus import event_bus
from core.events import (
    ExecutionStarted,
    ExecutionCompleted,
    ExecutionFailed,
    ExecutionCancelled,
)
from config.settings import (
    EXECUTION_HEARTBEAT_SECONDS,
    PLAN_APPROVAL_TIMEOUT_SECONDS,
    PLAN_APPROVAL_HEARTBEAT_SECONDS,
    MAX_REPLAN_ATTEMPTS,
    PLAN_REVISION_MAX_ROUNDS,
    PEV_PLAN_VERIFIER_TRAIL_MAX_CHARS,
    PEV_LOOKAHEAD_MAX_SKIP,
    get_user_session,
)
from core.db.session import AsyncSessionLocal
from core.db.repository import PlanRepo

logger = logging.getLogger(__name__)

# Local aliases for readability inside this module
_PLAN_APPROVAL_TIMEOUT   = PLAN_APPROVAL_TIMEOUT_SECONDS
_PLAN_APPROVAL_HEARTBEAT = PLAN_APPROVAL_HEARTBEAT_SECONDS
_MAX_REPLAN_ATTEMPTS     = MAX_REPLAN_ATTEMPTS


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_last_user_message(messages: List[Dict[str, Any]]) -> str:
    """Return the content of the last user-role message, or empty string."""
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            return str(msg["content"])
    return ""


# A-19: on the final step, the closing "summarise what you did" instruction is
# replaced with one that also asks for the user-facing closing message --
# see _build_step_messages()'s is_last_step branch. Kept as a template
# constant (not inlined) so its exact wording is easy to find and tune
# without hunting through the f-string assembly logic around it.
_FINAL_STEP_CLOSING_INSTRUCTION = (
    "This is the FINAL step of the plan. In addition to completing this step's "
    "own work, end your response with a closing summary addressed directly to "
    "the user -- not a report to the system, not a step-by-step transcript.\n\n"
    "Overall goal of this plan: {goal}\n"
    "Success criteria for this plan:\n{criteria_list}\n\n"
    "Your closing summary must:\n"
    "- State plainly what was accomplished, in terms of the goal above -- not a "
    "restatement of each step.\n"
    "- Reference concrete results (what was built, what a test or command "
    "actually showed, what value was found) only if you or an earlier step "
    "actually observed it via a tool result or the earlier-steps record. Do "
    "not state a result, number, or file content you did not see.\n"
    "- If something in the success criteria was not fully achieved, say so "
    "plainly and explain what's missing -- never present an unfinished or "
    "uncertain result as complete.\n"
    "- Use clear formatting (short paragraphs, a bullet list for multiple "
    "results) -- this is the part of your response the user reads by default.\n"
    "- This plan may be one part of a larger request. Describe what THIS "
    "plan's goal achieved, not the user's entire original message if it asked "
    "for more than this plan covers."
)


def _build_step_messages(
    base_messages: List[Dict[str, Any]],
    plan: Dict[str, Any],
    step: Dict[str, Any],
    prior_steps_block: str = "",
    step_number: Optional[int] = None,
    active_tools_line: str = "",
    is_last_step: bool = False,
) -> List[Dict[str, Any]]:
    """
    Build the message list the AgentEngine should use when executing a step.

    prior_steps_block (A-14): what the earlier steps did -- see core/execution/step_record.py.
    Without it a step starts blind: it redoes earlier work, and a closing "summarise" step has
    nothing real to summarise and invents details.

    is_last_step (A-19): the closing instruction changes from "summarise what
    you did" (step-scoped, unchanged for every other step) to one that also
    asks for the plan's user-facing closing message. Bundled into this same
    call rather than a separate synthesis pass -- this step already has the
    full prior_steps_block, so nothing extra needs fetching, and it's the
    only step whose own text is what actually reaches the user (see
    core/execution/pev_runner.py's final-answer assembly at the end of
    _pev_stream, which no longer concatenates every step's text -- only this
    one's, plus the closing verifier's message on a non-"success" verdict).
    """
    prior = f"{prior_steps_block}\n\n" if prior_steps_block else ""
    if active_tools_line:
        prior += f"{active_tools_line}\n\n"
    if is_last_step:
        criteria = plan.get("success_criteria") or [plan.get("goal", "")]
        criteria_list = "\n".join(f"- {c}" for c in criteria)
        closing_instruction = _FINAL_STEP_CLOSING_INSTRUCTION.format(
            goal=plan.get("goal", ""), criteria_list=criteria_list,
        )
    else:
        closing_instruction = (
            "Focus exclusively on completing this step. "
            "Use tools as needed. When the step is complete, summarise what you did. "
            "In that summary state only what this step did and what tool results you actually "
            "received. Do not state an error message, file content, id or number that you did not "
            "see in a tool result or in the earlier-steps record above; if you do not have it, say so."
        )
    step_instruction = (
        f"You are executing step {step_number or step['index'] + 1} of a multi-step plan.\n\n"
        f"**Overall goal:** {plan.get('goal', '')}\n\n"
        f"{prior}"
        f"**This step:** {step['title']}\n"
        f"{step['description']}\n\n"
        f"{closing_instruction}"
    )
    return list(base_messages) + [{"role": "user", "content": step_instruction}]


def _plan_steps_payload(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extract the display-relevant fields from a plan's steps for the
    StepProgressChunk.plan_steps payload -- deliberately excludes runtime
    bookkeeping fields (status, result_summary, verified, verification_notes)
    that belong to the persisted ExecutionPlan row, not the live progress event.
    """
    return [
        {
            "index":       s.get("index"),
            "title":       s.get("title", ""),
            "description": s.get("description", ""),
            "tool_hint":   s.get("tool_hint"),
        }
        for s in steps
    ]


# ---------------------------------------------------------------------------
# Plan Approval Gate helpers
# ---------------------------------------------------------------------------

def _plan_pending_key(token: str) -> str:
    return f"plan_approval_pending:{token}"


def _plan_decision_key(token: str) -> str:
    return f"plan_approval_decision:{token}"


async def _push_plan_approval_event(
    execution_id: str,
    plan: Dict[str, Any],
    plan_approval_token: str,
) -> None:
    """Write a plan_approval_required chunk to the shadow buffer."""
    from core.broker import local_broker as _broker
    buf = _broker.get_shadow_buffer(execution_id)
    await buf.append({
        "type": "plan_approval_required",
        "execution_id": execution_id,
        "plan_approval_token": plan_approval_token,
        "plan": plan,
    })
    logger.debug(
        "[PEV] plan_approval_required event pushed to SSE stream",
        extra={"execution_id": execution_id, "token": plan_approval_token},
    )


async def _plan_approval_heartbeat(
    execution_id: str,
    pending_key: str,
    extended_ttl: int,
) -> None:
    """Extend execution and pending-token key TTLs every interval."""
    client = await redis_execution_registry.connect()
    while True:
        await asyncio.sleep(_PLAN_APPROVAL_HEARTBEAT)
        try:
            await client.expire(f"execution:{execution_id}", extended_ttl)
            await client.expire(pending_key, extended_ttl)
        except Exception:
            pass


class PlanApprovalResult(NamedTuple):
    """
    Outcome of _gate_plan_approval().

    decision: "approve" | "edit" | "reject"
      - "approve" — user approved the plan as originally generated;
                    ``plan`` is None, caller keeps using the plan it passed in.
      - "edit"    — user submitted a replacement plan; ``plan`` is that
                    submitted dict (already parsed from the wire payload —
                    NOT yet re-validated; the caller must still call
                    validate_plan_shape() on it before using it, since the
                    payload travels through Redis as opaque JSON).
      - "reject"  — user rejected the plan, or the gate timed out / errored;
                    ``plan`` is None.

    reason: optional free-text feedback submitted alongside a "reject"
      decision (UX-01 Phase 2 — multi-round plan revision). None on
      timeout/error, and always None for "approve"/"edit" decisions.
    """
    decision: str
    plan: Optional[Dict[str, Any]]
    reason: Optional[str] = None


async def _gate_plan_approval(
    execution,
    plan: Dict[str, Any],
    timeout_seconds: int = _PLAN_APPROVAL_TIMEOUT,
) -> PlanApprovalResult:
    """
    Pause PEV execution until the user approves, edits, or rejects the
    generated plan.

    Returns a PlanApprovalResult (decision="approve"|"edit"|"reject", plan=...).
    On timeout or exception, resolves as decision="reject" — identical to
    the previous bool-returning behaviour, just re-shaped.
    Mirrors the pattern in core/tools/approval.py gate_approval().
    """
    execution_id   = execution.execution_id
    plan_token     = str(uuid4())
    extended_ttl   = timeout_seconds + 120

    client = await redis_execution_registry.connect()
    pending_key = _plan_pending_key(plan_token)

    # Register token in the broker so the API can validate it.
    await client.setex(pending_key, extended_ttl, "1")

    # Store token in execution hash so /trace can surface it.
    try:
        await client.hset(
            f"execution:{execution_id}", "pending_plan_approval_token", plan_token
        )
    except Exception:
        pass

    # Transition execution → AWAITING_PLAN_APPROVAL.
    ExecutionStateMachine.assert_transition_allowed(
        execution_id, execution.status, ExecutionStatus.AWAITING_PLAN_APPROVAL
    )
    execution.status = ExecutionStatus.AWAITING_PLAN_APPROVAL
    await redis_execution_registry.mark_execution_awaiting_plan_approval(execution_id)

    # File-based liveness marker (B2-1.7/4.3) -- see core/execution/gate_markers.py.
    write_gate_marker(
        execution_id=execution_id,
        conversation_id=execution.conversation_id,
        user_id=execution.user_id,
        gate_kind=GATE_KIND_PLAN_APPROVAL,
        token=plan_token,
    )

    # Push SSE event so client can show an approval UI.
    await _push_plan_approval_event(execution_id, plan, plan_token)

    timed_out     = False
    decision      = "reject"
    modified_plan = None
    reject_reason = None

    heartbeat_task = asyncio.create_task(
        _plan_approval_heartbeat(execution_id, pending_key, extended_ttl)
    )

    try:
        result = await client.blpop(
            _plan_decision_key(plan_token),
            timeout=timeout_seconds,
        )
        if result is None:
            timed_out = True
        else:
            raw_value = result[1] if isinstance(result, (list, tuple)) else result
            if isinstance(raw_value, bytes):
                raw_value = raw_value.decode("utf-8", errors="replace")

            # Wire format (written by resolve_plan_approval): a JSON string
            #   {"decision": "approve"|"edit"|"reject", "plan": <dict>|null, "reason": <str>|null}
            # Legacy sentinel strings ("1"/"0") are still accepted as a
            # defensive fallback so this doesn't hard-crash if anything ever
            # writes the old format — not expected in normal operation since
            # resolve_plan_approval() only ever writes the JSON form now.
            try:
                payload = json.loads(raw_value)
                decision = payload.get("decision", "reject")
                modified_plan = payload.get("plan")
                reject_reason = payload.get("reason")
            except (json.JSONDecodeError, TypeError):
                decision = "approve" if raw_value in ("1", b"1") else "reject"
                modified_plan = None
                reject_reason = None

            if decision not in ("approve", "edit", "reject"):
                decision = "reject"
    except Exception as exc:
        logger.error(
            "[PEV] Plan approval: error during blpop wait — auto-rejecting",
            exc_info=exc,
            extra={"execution_id": execution_id},
        )
        timed_out = True
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    # Clean up broker keys.
    try:
        await client.delete(pending_key)
        await client.delete(_plan_decision_key(plan_token))
        await client.hdel(f"execution:{execution_id}", "pending_plan_approval_token")
    except Exception:
        pass
    delete_gate_marker(execution_id)

    if timed_out:
        decision = "reject"
        modified_plan = None
        reject_reason = None
        logger.warning(
            "[PEV] Plan approval timed out",
            extra={"execution_id": execution_id, "timeout_seconds": timeout_seconds},
        )

    return PlanApprovalResult(decision=decision, plan=modified_plan, reason=reject_reason)


# ---------------------------------------------------------------------------
# Public resolve helper (called by the plan-decision API — api/approval.py)
# ---------------------------------------------------------------------------

async def resolve_plan_approval(
    token: str,
    decision: str,
    modified_plan: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> bool:
    """
    Signal the waiting _gate_plan_approval() coroutine via the broker.

    ``decision`` is one of "approve", "edit", "reject". When "edit",
    ``modified_plan`` must be the full replacement plan dict (the API layer
    is responsible for validating its shape via planner.validate_plan_shape()
    *before* calling this — this function does not re-validate). ``reason``
    is optional free-text feedback, only meaningful when decision=="reject"
    (UX-01 Phase 2 — drives whether the PEV runner revises the plan instead
    of cancelling outright; ignored for "approve"/"edit").

    Wire format: this pushes a JSON string through the same blpop/rpush
    decision-key mechanism _gate_plan_approval() already waits on —
        {"decision": "approve"|"edit"|"reject", "plan": <dict>|null, "reason": <str>|null}
    replacing the previous plain "1"/"0" sentinel strings. The underlying
    Redis mechanism itself (pending-key registration, decision-key blpop/
    rpush, TTLs, cleanup) is unchanged — only the string payload shape is.

    Returns True if the token was found and resolved, False if unknown.
    """
    if decision not in ("approve", "edit", "reject"):
        logger.error(
            "[PEV] resolve_plan_approval: invalid decision value",
            extra={"token": token, "decision": decision},
        )
        return False

    client = await redis_execution_registry.connect()
    exists = await client.exists(_plan_pending_key(token))
    if not exists:
        logger.warning(
            "[PEV] resolve_plan_approval: unknown or expired token",
            extra={"token": token},
        )
        return False

    payload = json.dumps({
        "decision": decision,
        "plan": modified_plan if decision == "edit" else None,
        "reason": reason if decision == "reject" else None,
    })
    await client.rpush(_plan_decision_key(token), payload)
    await client.expire(_plan_decision_key(token), 120)
    await client.delete(_plan_pending_key(token))

    logger.info(
        "[PEV] Plan approval resolved",
        extra={"token": token, "decision": decision},
    )
    return True


# ---------------------------------------------------------------------------
# Composite stream generator
# ---------------------------------------------------------------------------

async def _pev_stream(
    *,
    execution: ConversationExecution,
    agent,
    base_messages: List[Dict[str, Any]],
    user_id: str,
    require_plan_approval: bool,
    lookahead_skip_enabled: bool = True,
) -> AsyncGenerator[Any, None]:
    """
    Composite async generator that drives the full PEV loop.

    Yields StreamChunk objects for all phases (Planning, Executing, Verifying).
    The caller wraps this in a BufferedStream identical to the conversational runner.

    lookahead_skip_enabled (A-21): the user's own pev_lookahead_skip_enabled
    session preference, read once by run_pev_execution() the same way
    require_plan_approval is, and passed through here. When False, the
    per-step verifier is never given remaining_steps, so also_satisfies_count
    is always 0 and no step is ever skipped -- same behaviour as before A-21.
    """
    execution_id = execution.execution_id

    # ------------------------------------------------------------------
    # Phase 1: PLANNING
    # ------------------------------------------------------------------
    # --- StepProgressChunk: planning started (no plan yet) ---
    yield StepProgressChunk(step_status="planning", goal="")

    user_message = _extract_last_user_message(base_messages)

    plan = await planner_engine.generate(
        user_message=user_message,
        user_id=user_id,
    )

    # --- StepProgressChunk: planning complete, plan_steps populated ---
    yield StepProgressChunk(
        total_steps=len(plan["steps"]),
        step_status="planning",
        goal=plan.get("goal", ""),
        plan_steps=_plan_steps_payload(plan["steps"]),
    )

    # Persist plan to DB
    conversation_id = execution.conversation_id
    steps = copy.deepcopy(plan["steps"])

    async with AsyncSessionLocal() as db:
        await PlanRepo.create(
            db,
            execution_id=execution_id,
            conversation_id=conversation_id,
            user_id=user_id,
            goal=plan["goal"],
            success_criteria=plan.get("success_criteria"),
            steps=steps,
        )

    logger.info(
        "[PEV] Plan persisted",
        extra={
            "execution_id": execution_id,
            "goal":         plan["goal"],
            "step_count":   len(steps),
        },
    )

    # ------------------------------------------------------------------
    # Phase 1a: PLAN APPROVAL (optional)
    # ------------------------------------------------------------------
    if require_plan_approval:
        # UX-01 Phase 2: a reject carrying non-empty feedback triggers a
        # bounded number of revise-and-re-gate rounds (PLAN_REVISION_MAX_ROUNDS)
        # instead of cancelling outright. A bare reject (no reason), or a
        # reject once rounds are exhausted, still cancels exactly as before --
        # see the "reject" branch below.
        revision_rounds = 0

        while True:
            # No narration chunk here: the plan_approval_required SSE event
            # (pushed by _gate_plan_approval() below, via _push_plan_approval_event())
            # already carries the plan + token and is what ApprovalGate.tsx renders --
            # a duplicate text notice would just be the same information twice.
            gate_result = await _gate_plan_approval(execution, plan)

            if gate_result.decision == "reject":
                if gate_result.reason and revision_rounds < PLAN_REVISION_MAX_ROUNDS:
                    revision_rounds += 1
                    logger.info(
                        "[PEV] Plan rejected with feedback — revising "
                        "(round %d/%d)",
                        revision_rounds, PLAN_REVISION_MAX_ROUNDS,
                        extra={
                            "execution_id": execution_id,
                            "reason":       gate_result.reason,
                        },
                    )
                    plan = await planner_engine.revise(
                        goal=plan["goal"],
                        rejected_steps=plan["steps"],
                        feedback=gate_result.reason,
                        user_id=user_id,
                    )
                    steps = copy.deepcopy(plan["steps"])
                    # Same row, same helper the "edit" branch below uses --
                    # a revision is not a new plan lineage, just the latest
                    # state of the one plan attached to this execution.
                    async with AsyncSessionLocal() as db:
                        await PlanRepo.update_steps(
                            db,
                            execution_id=execution_id,
                            steps=steps,
                            current_step_index=0,
                            status="pending",
                        )
                    # --- StepProgressChunk: the revised plan, so the
                    # frontend's plan_trace block reflects it even before
                    # the next plan_approval_required event re-opens the
                    # gate (_gate_plan_approval() pushes that on the next
                    # loop iteration). ---
                    yield StepProgressChunk(
                        total_steps=len(steps),
                        step_status="planning",
                        goal=plan.get("goal", ""),
                        plan_steps=_plan_steps_payload(steps),
                    )
                    continue

                # No reason given, or revision rounds exhausted -- cancel,
                # identical to the pre-Phase-2 behaviour.
                # Signal the outer run_pev_execution() loop to transition → CANCELLED
                # (from AWAITING_PLAN_APPROVAL) rather than → COMPLETED.
                execution.cancel_token.cancelled = True
                # B-099: this DB write MUST happen before the yield below, not
                # after. run_pev_execution()'s consumer loop returns immediately
                # once it sees a "cancelled" chunk -- it never calls __anext__()
                # on this generator again, so any code placed after that yield
                # is dead: it would only run if the generator were resumed,
                # which never happens on this exit path. (The V6.15 comment
                # this replaced described the right intent -- persist the
                # execution_plans row so GET /executions/{id}/plan doesn't
                # stay stuck at "pending"/"executing" forever -- but placed
                # the write on the unreachable side of the yield, so it was
                # dead code from the moment it landed. Confirmed live: the
                # row was still "pending" after a real reject, for both this
                # revision-cap-exhausted path and the plain bare-reject path
                # below, proving neither the multi-round-revision feature nor
                # anything else introduced it -- it predates both.)
                async with AsyncSessionLocal() as db:
                    await PlanRepo.update_status(
                        db, execution_id=execution_id, status="cancelled",
                    )
                # This yield is structurally required, not just informational:
                # run_pev_execution()'s consumer loop (`async for chunk in
                # buffered.stream_and_buffer()`) only checks cancel_token and
                # performs the CANCELLED transition *inside* that loop body --
                # returning here with no yield lets the loop exit via normal
                # StopAsyncIteration and silently skips the transition entirely,
                # leaving the broker status stuck at awaiting_plan_approval
                # forever. (V6.14 regression, caught live: the old narrative
                # TextChunk yielded here was unintentionally serving this same
                # pumping role alongside its text.)
                yield StepProgressChunk(step_status="cancelled", goal=plan.get("goal", ""))
                return

            if gate_result.decision == "edit":
                # The API layer (api/approval.py) already validated the submitted
                # plan via validate_plan_shape() before calling resolve_plan_approval(),
                # but we re-validate here too (belt-and-suspenders — the payload
                # travelled through Redis as opaque JSON, and this gate is the
                # last line of defence before it drives the step loop below).
                try:
                    plan = validate_plan_shape(gate_result.plan)
                except PlanValidationError as exc:
                    logger.error(
                        "[PEV] Edited plan failed shape validation at the gate — "
                        "cancelling execution rather than running a malformed plan",
                        extra={"execution_id": execution_id, "error": str(exc)},
                    )
                    execution.cancel_token.cancelled = True
                    yield ErrorChunk(
                        message=f"Edited plan was invalid: {exc}",
                        error_type="plan_validation_failed",
                    )
                    return

                # The edited plan replaces the original for everything downstream:
                # the step loop below reads from `steps`/`plan`, so both must be
                # reassigned. Persist the new steps to the same ExecutionPlan row
                # created above (via PlanRepo.create) using the same update_steps()
                # helper the adaptive re-planning path already uses. goal/
                # success_criteria are ALSO passed here (unlike every other
                # update_steps() call site) -- editing is the one path where
                # the in-memory plan can hold a different goal/success_criteria
                # than what was originally persisted; every other caller only
                # ever changes steps.
                steps = copy.deepcopy(plan["steps"])
                async with AsyncSessionLocal() as db:
                    await PlanRepo.update_steps(
                        db,
                        execution_id=execution_id,
                        steps=steps,
                        current_step_index=0,
                        status="pending",
                        goal=plan.get("goal"),
                        success_criteria=plan.get("success_criteria"),
                    )

                logger.info(
                    "[PEV] Plan edited by user before execution",
                    extra={
                        "execution_id": execution_id,
                        "goal":         plan["goal"],
                        "step_count":   len(steps),
                    },
                )

                # --- StepProgressChunk: confirm the (possibly re-normalised) plan
                # that will actually execute, now that the user's edit has been
                # validated and persisted. ---
                yield StepProgressChunk(
                    total_steps=len(steps),
                    step_status="planning",
                    goal=plan.get("goal", ""),
                    plan_steps=_plan_steps_payload(steps),
                )

            # Approved (as-is or edited) — exit the revision loop.
            break

        # Approved (as-is or edited) — transition execution back to RUNNING so
        # the step loop runs under the correct FSM state
        # (AWAITING_PLAN_APPROVAL → RUNNING).
        # No narration chunk for "approved": the frontend already knows this
        # from the user's own approve action, and the first "running"
        # StepProgressChunk below signals execution starting anyway.
        ExecutionStateMachine.assert_transition_allowed(
            execution_id, execution.status, ExecutionStatus.RUNNING
        )
        execution.status = ExecutionStatus.RUNNING
        await redis_execution_registry.mark_execution_running(execution_id)

    # ------------------------------------------------------------------
    # Phase 2 + 3: EXECUTE each step, then VERIFY (with adaptive replanning)
    # ------------------------------------------------------------------
    completed_steps: List[Dict[str, Any]] = []   # steps that passed (for replan context)
    replanned_count = 0
    # A-08 item 4: list-position of every step that failed verification but was
    # then superseded by a successful adaptive replan (as opposed to a failure
    # that reached the max-replan-attempts limit, or a failure on the final
    # step, which have no later steps to fix things up). The step itself stays
    # in `steps` with status="failed" -- that's the accurate per-step history
    # -- but the plan-level final tally below should not count it as an
    # overall plan failure once the replan actually resolved it.
    superseded_failed_indices: set = set()
    # A-14: what each finished step did, carried into the prompts of the steps after it.
    step_records: List[StepRecord] = []
    # Tools active before any step ran (session-configured, orchestration pre-seed): the "already
    # active" line names only what the run itself activates on top of these.
    _baseline_active_tools = active_tool_names(getattr(execution, "user_registry", None))

    # A-22: bound before the loop. It is only (re)assigned once a step has produced
    # output, and the closing-synthesis block after the loop reads it; a plan that
    # never enters the loop used to crash there with UnboundLocalError. The planner now
    # never returns an empty plan (planner._ensure_executable_plan), this is the
    # backstop for any other path that reaches the loop with nothing to run.
    step_output = ""

    step_idx = 0
    while step_idx < len(steps):
        step = steps[step_idx]
        total_steps = len(steps)
        # A-19/A-20: computed once per iteration and reused both before
        # execution (which step gets the closing-synthesis instruction) and
        # after verification (the existing replan-gating check below) --
        # steps/step_idx don't change in between, so both reads agree.
        is_last_step = (step_idx == len(steps) - 1)

        # --- Cancel check between steps ---
        # The yield here is structurally required (not just informational) --
        # see the identical comment on the plan-rejection cancel path above
        # for why returning with no yield silently breaks the outer
        # cancellation transition.
        if execution.cancel_token.cancelled:
            # B-099: write before the yield -- see the identical comment on
            # the plan-rejection cancel path above for why. No step is
            # "running" at this exact check (it fires before the next step
            # starts, and the previous one already persisted its own
            # terminal status via the normal per-step flow) -- only the
            # overall plan status needs backfilling here.
            async with AsyncSessionLocal() as db:
                await PlanRepo.update_status(
                    db, execution_id=execution_id, status="cancelled",
                )
            yield StepProgressChunk(
                step_index=step_idx, total_steps=total_steps,
                step_status="cancelled", goal=plan.get("goal", ""),
            )
            return

        # --- StepProgressChunk: running (this is also the step announcement --
        # step_title carries what the old "### Step N/total: {title}" text
        # header used to render, so no separate text chunk is needed) ---
        yield StepProgressChunk(
            step_index=step_idx,
            total_steps=total_steps,
            step_title=step["title"],
            step_status="running",
            goal=plan.get("goal", ""),
        )

        # Update step status in our local copy
        steps[step_idx]["status"] = "running"
        async with AsyncSessionLocal() as db:
            await PlanRepo.update_steps(
                db,
                execution_id=execution_id,
                steps=steps,
                current_step_index=step_idx,
                status="executing",
            )

        # Build focused messages for this step
        # A-16: the same block is reused for the verifier below, so it and the
        # step's own execution prompt never disagree about what earlier steps did.
        prior_steps_block = build_prior_steps_block(step_records)
        step_messages = _build_step_messages(
            base_messages, plan, step, prior_steps_block,
            step_number=step_idx + 1,
            active_tools_line=build_active_tools_line(
                active_tool_names(getattr(execution, "user_registry", None)),
                _baseline_active_tools,
            ),
            is_last_step=is_last_step,
        )

        # --- Run AgentEngine for this step ---
        step_output_parts: List[str] = []
        # A-13: which tools this step actually ran (one short line per call), shown to the verifier
        # and carried forward in step_records.
        step_ledger = StepToolLedger()

        step_exception_detail: Optional[str] = None

        try:
            async for chunk in agent.call(step_messages, stream=True):
                # Yield required here too -- see the identical comment on the
                # plan-rejection cancel path above.
                if execution.cancel_token.cancelled:
                    # B-099: write before the yield -- see the identical
                    # comment on the plan-rejection cancel path above for
                    # why. This step was marked "running" and persisted
                    # before agent.call() started (see the
                    # PlanRepo.update_steps(..., status="executing") call
                    # above) -- without this write actually landing, it
                    # would stay "running" in the DB forever.
                    steps[step_idx]["status"] = "cancelled"
                    async with AsyncSessionLocal() as db:
                        await PlanRepo.update_steps(
                            db, execution_id=execution_id, steps=steps,
                            current_step_index=step_idx, status="cancelled",
                        )
                    yield StepProgressChunk(
                        step_index=step_idx, total_steps=total_steps,
                        step_title=step["title"], step_status="cancelled",
                        goal=plan.get("goal", ""),
                    )
                    return

                if isinstance(chunk, TextChunk) and chunk.content:
                    step_output_parts.append(chunk.content)
                elif isinstance(chunk, ToolCallChunk):
                    step_ledger.add_call(chunk.tool_name, chunk.arguments, chunk.call_id)
                elif isinstance(chunk, ToolResultChunk):
                    step_ledger.add_result(chunk.tool_name, chunk.result, chunk.call_id)

                yield chunk

        except Exception as exc:
            logger.error(
                "[PEV] Agent step raised an exception",
                exc_info=exc,
                extra={"execution_id": execution_id, "step_index": step_idx},
            )
            # Carried on the upcoming "verifying" StepProgressChunk below
            # instead of a separate text notice -- the step continues to
            # verification normally (see comment below), so there's no
            # standalone moment to attach a dedicated error chunk to.
            step_exception_detail = f"Step encountered an error: {exc}"
            # Capture the error as the step output so Phase 3 (verification) can
            # run normally. The verifier will see the error and return passed=False,
            # which triggers adaptive replanning exactly as intended.
            # Do NOT continue/skip here — Phase 3 is the correct handler for failures.
            step_output_parts.append(
                f"[Step execution failed with exception: {type(exc).__name__}: {exc}]"
            )

        step_output = "".join(step_output_parts)

        # P-19 Phase 1: agent.call() just ended cleanly (not an exception)
        # because the step called schedule_wakeup, which transitions
        # execution.status to AWAITING_WAKEUP and returns from the ReAct
        # loop immediately (see core/agent_engine.py's tool-result hook).
        # Without this check, the plan loop below has no way to tell that
        # apart from an ordinary step completion -- it would run
        # verification, and (if verification "fails" because there's
        # nothing left to verify) potentially replan or advance to a next
        # step, calling agent.call() again for an execution that has
        # already ended. Live-found: a second agent.call() invocation on an
        # AWAITING_WAKEUP execution can itself call schedule_wakeup again,
        # which is correctly rejected by the tool's one-row-per-execution
        # constraint, but as an unhandled IntegrityError rather than a
        # clean stop -- wasteful and needlessly noisy. Stop the whole plan
        # here instead, the same way the cancel checks above short-circuit,
        # not just this one step.
        if execution.status == ExecutionStatus.AWAITING_WAKEUP:
            steps[step_idx]["status"] = "completed"
            steps[step_idx]["result_summary"] = "Execution paused via schedule_wakeup."
            async with AsyncSessionLocal() as db:
                await PlanRepo.update_steps(
                    db, execution_id=execution_id, steps=steps,
                    current_step_index=step_idx, status="completed",
                )
            yield StepProgressChunk(
                step_index=step_idx, total_steps=total_steps,
                step_title=step["title"], step_status="completed",
                goal=plan.get("goal", ""),
                verification_summary="Execution paused via schedule_wakeup.",
            )
            # P-19 Phase 3: the "execution.awaiting_wakeup" user event is
            # published from run_pev_execution()'s own "all steps streamed"
            # finalize path, not here -- this generator ending (via this
            # very `return`) is exactly what makes that outer async-for loop
            # exit normally and reach that path, so publishing here too
            # would double-fire the same event for the same pause (live-
            # found during Phase 3 verification: both fired, ~80ms apart).
            # One choke point, not two.
            return

        # ------------------------------------------------------------------
        # Phase 3 (per-step): VERIFYING
        # ------------------------------------------------------------------
        # --- StepProgressChunk: verifying ---
        yield StepProgressChunk(
            step_index=step_idx,
            total_steps=total_steps,
            step_title=step["title"],
            step_status="verifying",
            goal=plan.get("goal", ""),
            error_detail=step_exception_detail,
        )

        # A-21: never offered for the true last step (nothing after it to
        # skip) or for the plan's second-to-last step onward -- the range
        # excludes the plan's own final step, which must always run for real
        # so A-19's closing synthesis has something to run on (see the
        # look-ahead application below for the same exclusion applied when
        # actually marking steps skipped).
        lookahead_candidates = (
            steps[step_idx + 1: len(steps) - 1]
            if lookahead_skip_enabled and not is_last_step
            else []
        )

        verification = await verifier_engine.verify(
            step=step,
            execution_output=step_output,
            user_id=user_id,
            tool_activity=step_ledger.render(),
            prior_steps_block=prior_steps_block,
            remaining_steps=lookahead_candidates or None,
        )

        # is_last_step computed once near the top of this iteration (A-19/A-20).

        # A-14: remember what this step did for the steps after it.
        step_records.append(make_record(
            step,
            number=step_idx + 1,
            output_text=step_output,
            ledger=step_ledger,
            verdict=(
                "unverified" if verification.infra_error
                else ("passed" if verification.passed else "failed")
            ),
            issues=None if verification.passed else verification.issues,
        ))

        # A-08 item 3: a verifier-infra failure (LLM error, timeout, unparseable
        # response -- see VerificationResult.infra_error) is not evidence the
        # step itself is bad, so it shouldn't cost a replan attempt. Surface it
        # as a visible warning and continue, the same as a normal pass --
        # without ever marking the step "completed" as if it had been
        # positively verified-good (V4.41's actual concern was silent masking,
        # not this).
        if verification.infra_error:
            steps[step_idx]["status"] = "completed"
            steps[step_idx]["result_summary"] = verification.summary
            steps[step_idx]["verified"] = False
            steps[step_idx]["verification_notes"] = verification.issues

            yield StepProgressChunk(
                step_index=step_idx,
                total_steps=total_steps,
                step_title=step["title"],
                step_status="completed",
                goal=plan.get("goal", ""),
                verification_summary=verification.summary,
                verification_issues=verification.issues,
                replan_reason=(
                    "Verification could not run for this step (verifier "
                    "infrastructure error) — continuing without verification "
                    "instead of spending a replan attempt on it."
                ),
            )

            completed_steps.append(steps[step_idx])
            plan_status = "completed" if is_last_step else "executing"

            async with AsyncSessionLocal() as db:
                await PlanRepo.update_steps(
                    db, execution_id=execution_id, steps=steps,
                    current_step_index=step_idx + 1, status=plan_status,
                )
            step_idx += 1
            continue

        # Update step in local copy
        steps[step_idx]["status"] = "completed" if verification.passed else "failed"
        steps[step_idx]["result_summary"] = verification.summary
        steps[step_idx]["verified"] = True
        steps[step_idx]["verification_notes"] = verification.issues

        # --- StepProgressChunk: completed or failed -- carries the
        # verification result that used to render as a standalone text
        # notice ("_Verification: ..._" / "_Issues: ..._") ---
        yield StepProgressChunk(
            step_index=step_idx,
            total_steps=total_steps,
            step_title=step["title"],
            step_status="completed" if verification.passed else "failed",
            goal=plan.get("goal", ""),
            verification_summary=verification.summary,
            verification_issues=verification.issues,
        )

        # Defaults; the look-ahead skip below is the only thing that ever
        # changes next_step_idx away from a plain +1 advance.
        next_step_idx = step_idx + 1

        if verification.passed:
            completed_steps.append(steps[step_idx])
            plan_status = "completed" if is_last_step else "executing"

            # A-21: apply whatever look-ahead skip the verifier's own call
            # (above) reported, clamped by the hard ceiling regardless of
            # what it claimed. lookahead_candidates is already the same
            # last-step-excluding slice passed into that call, so indices
            # line up directly -- no re-derivation, no risk of the two
            # disagreeing about what "remaining" meant.
            skip_count = min(verification.also_satisfies_count, len(lookahead_candidates), PEV_LOOKAHEAD_MAX_SKIP)
            if skip_count > 0:
                for offset in range(skip_count):
                    skip_idx = step_idx + 1 + offset
                    skip_step = steps[skip_idx]
                    skip_step["status"] = "skipped"
                    skip_step["result_summary"] = (
                        f"Skipped — already satisfied by step {step_idx + 1}'s work."
                    )
                    skip_step["verified"] = True
                    skip_step["verification_notes"] = verification.also_satisfies_reason

                    step_records.append(make_record(
                        skip_step,
                        number=skip_idx + 1,
                        output_text=(
                            f"(Skipped -- step {step_idx + 1} already accomplished this. "
                            f"{verification.also_satisfies_reason or ''}"
                        ).strip(),
                        ledger=StepToolLedger(),
                        verdict="passed",
                    ))

                    yield StepProgressChunk(
                        step_index=skip_idx,
                        total_steps=total_steps,
                        step_title=skip_step["title"],
                        step_status="skipped",
                        goal=plan.get("goal", ""),
                        verification_summary=skip_step["result_summary"],
                    )

                next_step_idx = step_idx + 1 + skip_count

                logger.info(
                    "[PEV] Adaptive skip applied (A-21)",
                    extra={
                        "execution_id": execution_id,
                        "step_index":   step_idx,
                        "skip_count":   skip_count,
                        "reason":       verification.also_satisfies_reason,
                    },
                )
        else:
            # ------------------------------------------------------------------
            # Adaptive Re-planning (only when steps remain)
            # ------------------------------------------------------------------
            if not is_last_step:
                # Guard against infinite re-planning: if we've already hit the
                # max, skip re-planning and move on to the next step as-is.
                if replanned_count >= _MAX_REPLAN_ATTEMPTS:
                    yield StepProgressChunk(
                        step_index=step_idx,
                        total_steps=total_steps,
                        step_title=step["title"],
                        step_status="replan_skipped",
                        goal=plan.get("goal", ""),
                        replan_reason=(
                            f"Max re-plan attempts reached ({_MAX_REPLAN_ATTEMPTS}). "
                            "Continuing with remaining steps as-is."
                        ),
                    )
                    plan_status = "executing"
                    async with AsyncSessionLocal() as db:
                        await PlanRepo.update_steps(
                            db,
                            execution_id=execution_id,
                            steps=steps,
                            current_step_index=step_idx + 1,
                            status=plan_status,
                        )
                    step_idx += 1
                    continue

                replanned_count += 1
                # A-08 item 4: this step's "failed" status is about to be
                # patched over by a replan -- exclude it from the final
                # plan-level failure tally below (it stays "failed" in the
                # step's own history, which is correct; it just shouldn't
                # read as an unresolved overall-plan failure once the replan
                # actually carries the goal forward).
                superseded_failed_indices.add(step_idx)

                # --- StepProgressChunk: replanning started ---
                yield StepProgressChunk(
                    step_index=step_idx,
                    total_steps=total_steps,
                    step_title=step["title"],
                    step_status="replanning",
                    goal=plan.get("goal", ""),
                    replan_reason=(
                        f"Step failed — generating revised steps (attempt {replanned_count})."
                    ),
                )

                original_remaining = steps[step_idx + 1:]

                new_steps = await planner_engine.replan(
                    goal=plan.get("goal", ""),
                    completed_steps=completed_steps,
                    failed_step=steps[step_idx],
                    remaining_steps=original_remaining,
                    user_id=user_id,
                )

                # Replace the failed step + all remaining steps with the new plan
                steps = steps[: step_idx + 1] + new_steps
                plan["steps"] = steps

                # --- StepProgressChunk: replanning complete, revised plan_steps ---
                yield StepProgressChunk(
                    step_index=step_idx,
                    total_steps=len(steps),
                    step_title=step["title"],
                    step_status="replanning",
                    goal=plan.get("goal", ""),
                    plan_steps=_plan_steps_payload(new_steps),
                )

                # Persist updated plan to DB
                async with AsyncSessionLocal() as db:
                    await PlanRepo.update_steps(
                        db,
                        execution_id=execution_id,
                        steps=steps,
                        current_step_index=step_idx + 1,
                        status="executing",
                    )

                logger.info(
                    "[PEV] Adaptive re-plan applied",
                    extra={
                        "execution_id":   execution_id,
                        "step_index":     step_idx,
                        "new_step_count": len(new_steps),
                        "replanned_count": replanned_count,
                    },
                )

            plan_status = "executing"

        async with AsyncSessionLocal() as db:
            await PlanRepo.update_steps(
                db,
                execution_id=execution_id,
                steps=steps,
                current_step_index=next_step_idx,
                status=plan_status,
            )

        logger.info(
            "[PEV] Step verified",
            extra={
                "execution_id": execution_id,
                "step_index":   step_idx,
                "passed":       verification.passed,
                "is_last_step": is_last_step,
                "skipped_ahead_to": next_step_idx if next_step_idx != step_idx + 1 else None,
            },
        )

        step_idx = next_step_idx

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    completed = sum(1 for s in steps if s.get("status") == "completed")
    # A-08 item 4: a step that failed verification but was then successfully
    # superseded by an adaptive replan (superseded_failed_indices, set above)
    # is not counted here -- it's resolved history, not an unresolved
    # overall-plan failure. This is also what execution_plans.status (below)
    # is computed from, so the two no longer disagree the way they used to
    # (metrics/answer reading as success while status/scorecard still counted
    # an already-fixed failure).
    failed = sum(
        1 for i, s in enumerate(steps)
        if s.get("status") == "failed" and i not in superseded_failed_indices
    )
    # A-21: reported separately from `completed` above, not folded into it --
    # a skipped step never ran the agent for real, so counting it as plainly
    # "completed" would overstate what actually happened. It's also never
    # counted in `failed` (its status is "skipped", not "failed"), so
    # final_plan_status below is unaffected either way.
    skipped = sum(1 for s in steps if s.get("status") == "skipped")

    # A-19/A-20: the saved final answer is no longer the naive concatenation
    # of every step's TextChunk (see BufferedStream.aggregated() -- still
    # used, unchanged, for the plain ReAct path). It's the LAST step's own
    # closing synthesis (that step's prompt asked for one -- see
    # _build_step_messages()'s is_last_step branch), plus an independent
    # holistic check of the whole plan against its own goal/success criteria,
    # appended only when that check disagrees with a plain "it worked".
    #
    # `step_output` here is the last completed while-loop iteration's local
    # variable -- Python retains a loop-local binding after the loop exits,
    # and every early-return path above (cancelled, plan rejected, awaiting
    # wakeup) returns before this point, so reaching here always means the
    # loop ran to completion and step_output holds the last step's real text.
    # (A-22: not quite -- a plan with no steps never enters the loop; step_output is
    # now initialised to "" before it, and the planner no longer returns empty plans.)
    success_criteria = plan.get("success_criteria") or [plan.get("goal", "")]
    full_step_trail = build_prior_steps_block(step_records, max_chars=PEV_PLAN_VERIFIER_TRAIL_MAX_CHARS)
    plan_verification = await plan_verifier_engine.verify_plan(
        goal=plan.get("goal", ""),
        success_criteria=success_criteria,
        step_record_trail=full_step_trail,
        draft_closing_summary=step_output,
        user_id=user_id,
    )
    final_answer_text = step_output
    if plan_verification.verdict != "success" and plan_verification.message:
        final_answer_text = f"{step_output}\n\n---\n\n{plan_verification.message}"
    execution.final_answer_override = final_answer_text

    # A-20: persist the verdict itself, not just its text effect on the
    # saved answer above -- see PlanRepo.update_goal_verification()'s own
    # docstring for why this needs a durable home (the SSE-only
    # StepProgressChunk below does not survive past the shadow buffer's
    # eviction window).
    async with AsyncSessionLocal() as db:
        await PlanRepo.update_goal_verification(
            db,
            execution_id=execution_id,
            goal_verdict=plan_verification.verdict,
            goal_verification_message=plan_verification.message or None,
            goal_criteria_results=plan_verification.criteria_results or None,
        )

    logger.info(
        "[PEV] Plan outcome verified",
        extra={
            "execution_id": execution_id,
            "verdict":      plan_verification.verdict,
            "infra_error":  plan_verification.infra_error,
        },
    )

    # V6.14: this used to render as a hand-authored "## Execution Complete /
    # Steps: X/Y completed, Z failed" TextChunk mixed directly into the answer
    # text -- which is also what caused the misleading-scorecard problem
    # (a step failing verification purely on a technicality, or the verifier
    # itself erroring, could tack "0/5 completed, 5 failed" onto an otherwise
    # correct final answer). Structured data lets the frontend decide how to
    # present a partial/technical failure without that risk.
    yield StepProgressChunk(
        total_steps=len(steps),
        step_status="plan_complete",
        goal=plan.get("goal", ""),
        steps_completed=completed,
        steps_failed=failed,
        steps_skipped=skipped,
        replan_count=replanned_count,
        goal_verdict=plan_verification.verdict,
        goal_verification_message=plan_verification.message or None,
    )

    # Mark plan as completed/failed in DB
    final_plan_status = "completed" if failed == 0 else "failed"
    async with AsyncSessionLocal() as db:
        await PlanRepo.update_status(
            db,
            execution_id=execution_id,
            status=final_plan_status,
        )


# ---------------------------------------------------------------------------
# Public entry-point: run_pev_execution
# ---------------------------------------------------------------------------

async def run_pev_execution(
    *,
    execution: ConversationExecution,
    agent,
    messages: List[Dict[str, Any]],
    finalizer: Callable,
    preamble: list = None,
):
    """
    Top-level PEV execution runner.  Mirrors the signature of run_execution()
    in runner.py so the worker can call either interchangeably.

    FSM path (no approval):
      PENDING → PLANNING → RUNNING → (VERIFYING → RUNNING)* → COMPLETED

    FSM path (with plan approval):
      PENDING → PLANNING → AWAITING_PLAN_APPROVAL → RUNNING → (VERIFYING → RUNNING)* → COMPLETED
    """

    def transition(new_status: ExecutionStatus):
        ExecutionStateMachine.assert_transition_allowed(
            execution.execution_id,
            execution.status,
            new_status,
        )
        execution.status = new_status
        logger.debug(
            "[PEV] Execution status transition",
            extra={
                "execution_id": execution.execution_id,
                "new_status":   new_status,
            },
        )

    start_monotonic = time.monotonic()
    execution.started_at = time.time()

    # Read require_plan_approval / pev_lookahead_skip_enabled from user session
    try:
        session = get_user_session(execution.user_id)
        require_plan_approval = bool(session.get("require_plan_approval", False))
        lookahead_skip_enabled = bool(session.get("pev_lookahead_skip_enabled", True))
    except Exception:
        require_plan_approval = False
        lookahead_skip_enabled = True

    logger.info(
        "[PEV] PEV execution starting",
        extra={
            "execution_id":          execution.execution_id,
            "conversation_id":       execution.conversation_id,
            "user_id":               execution.user_id,
            "require_plan_approval": require_plan_approval,
        },
    )

    # ------------------------------------------------------------------
    # Transition → PLANNING
    # ------------------------------------------------------------------
    transition(ExecutionStatus.PLANNING)
    await redis_execution_registry.mark_execution_planning(execution.execution_id)
    await redis_execution_registry.update_execution(
        execution.execution_id,
        started_at=str(execution.started_at),
    )

    event_bus.publish(
        ExecutionStarted(
            execution_id=execution.execution_id,
            conversation_id=execution.conversation_id,
            user_id=execution.user_id,
        )
    )

    # ------------------------------------------------------------------
    # Transition → RUNNING (before streaming begins)
    # The _pev_stream generator may internally gate on AWAITING_PLAN_APPROVAL
    # and transition back to RUNNING once approved; the outer FSM stays in
    # RUNNING wrapping the whole execution window from this point forward.
    # ------------------------------------------------------------------
    transition(ExecutionStatus.RUNNING)
    await redis_execution_registry.mark_execution_running(execution.execution_id)

    from core.broker import local_broker as _broker
    shadow = _broker.get_shadow_buffer(execution.execution_id)
    last_heartbeat     = time.monotonic()
    last_cancel_check  = time.monotonic()
    _CANCEL_CHECK_SECS = 2

    try:
        # Wrap the composite PEV generator in a BufferedStream
        raw_stream = _pev_stream(
            execution=execution,
            agent=agent,
            base_messages=messages,
            user_id=execution.user_id,
            require_plan_approval=require_plan_approval,
            lookahead_skip_enabled=lookahead_skip_enabled,
        )
        buffered = BufferedStream(raw_stream)
        execution.buffer = buffered

        # Eager clear: wipe any leftover data from a previous run with the
        # same execution_id before writing preamble chunks.
        await shadow.clear()
        for pc in (preamble or []):
            await shadow.append(pc.__dict__)

        async def _finalize_as_cancelled() -> None:
            """
            Perform the CANCELLED FSM transition + broker/event cleanup.

            Factored out so it can run both from inside the consumer loop
            (the normal, immediate-response path) AND once more right after
            the loop exits (see the defensive check below) -- a backstop
            against any future _pev_stream() code path that sets
            cancel_token.cancelled and returns without a further yield,
            which would otherwise let the `async for` loop exit via plain
            StopAsyncIteration and silently skip this transition entirely.
            This exact bug shipped once already (V6.14's narration removal
            accidentally dropped the TextChunk yields that had been
            incidentally pumping this loop one more time after every
            cancellation) -- see StepProgressChunk's "cancelled" docstring
            entry in core/types.py for the fix at the yield sites themselves.
            """
            transition(ExecutionStatus.CANCELLED)
            execution.completed_at = time.time()

            # V6.15.3: recover partial usage before buffered.clear() wipes it
            # -- see the matching comment in core/execution/runner.py's
            # cancellation branch for the full rationale. Persisted to the
            # broker rather than appended to `shadow` because shadow.clear()
            # right below would silently drop it (the SSE endpoint's
            # per-connection chunk index doesn't reset when the underlying
            # list is cleared).
            _partial_final = await buffered.aggregated(require_ended=False)
            await finalizer(_partial_final)
            _partial_usage = _partial_final.get("usage")
            if _partial_usage:
                try:
                    await redis_execution_registry.update_execution(
                        execution.execution_id,
                        usage_json=json.dumps(_partial_usage),
                    )
                except Exception as _usage_persist_exc:
                    logger.debug(
                        "Failed to persist partial usage on cancel (non-fatal): %s",
                        _usage_persist_exc,
                    )

            buffered.clear()
            await shadow.clear()
            await redis_execution_registry.mark_execution_cancelled(
                execution.execution_id, reason="user"
            )
            event_bus.publish(
                ExecutionCancelled(
                    execution_id=execution.execution_id,
                    duration_ms=int((time.monotonic() - start_monotonic) * 1000),
                    cancel_reason="user",
                )
            )

        async for chunk in buffered.stream_and_buffer():
            await shadow.append(chunk.__dict__)

            # Heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= EXECUTION_HEARTBEAT_SECONDS:
                await redis_execution_registry.heartbeat(execution.execution_id)
                last_heartbeat = now

            # Cross-process cancel check
            if now - last_cancel_check >= _CANCEL_CHECK_SECS:
                last_cancel_check = now
                try:
                    _client = await redis_execution_registry.connect()
                    _broker_status = await _client.hget(
                        f"execution:{execution.execution_id}", "status"
                    )
                    if _broker_status in ("cancelled", "aborted"):
                        execution.cancel_token.cancelled = True
                        logger.info(
                            "[PEV] External cancel/abort detected via broker — stopping execution",
                            extra={
                                "execution_id": execution.execution_id,
                                "broker_status": _broker_status,
                            },
                        )
                except Exception:
                    pass

            if execution.cancel_token.cancelled:
                await _finalize_as_cancelled()
                return

            await asyncio.sleep(0)

        # ------------------------------------------------------------------
        # Defensive backstop: the loop above exited normally (generator
        # returned/exhausted) rather than via the `return` inside it, but
        # cancel_token may still be set if some _pev_stream() path set it
        # and returned without a final yield. See _finalize_as_cancelled()'s
        # docstring for why this matters.
        # ------------------------------------------------------------------
        if execution.cancel_token.cancelled:
            await _finalize_as_cancelled()
            return

        # ------------------------------------------------------------------
        # All steps streamed — aggregate and finalise
        # ------------------------------------------------------------------
        final = await buffered.aggregated()
        # A-19/A-20: execution.final_answer_override is set by _pev_stream()
        # only when the loop ran to a normal finish (see its own comment at
        # the point it's set) -- None for every path that returns early
        # (cancelled, awaiting_plan_approval rejected, AWAITING_WAKEUP),
        # which all correctly keep aggregated()'s own "text" (the naive
        # per-step concatenation is still the right answer for a run that
        # never reached its own closing step). getattr(), not direct
        # attribute access, matching this file's own existing convention for
        # optional execution fields (e.g. `user_registry` above) -- an
        # execution-like test double that predates this field must not
        # AttributeError here.
        if getattr(execution, "final_answer_override", None) is not None:
            final["text"] = execution.final_answer_override
        await finalizer(final)

        # P-19 Phase 1: a step ending because it called schedule_wakeup
        # leaves execution.status at AWAITING_WAKEUP and the generator
        # exhausts normally (not via cancel) -- reaching this same "all
        # steps streamed" path a genuine plan completion does. Without this
        # check, the unconditional transition below overwrites
        # AWAITING_WAKEUP with COMPLETED within milliseconds of it being
        # set (COMPLETED is a legal target from AWAITING_WAKEUP -- it has
        # to be, for WakeupScheduler's own later resolution -- so this
        # doesn't raise, it just silently defeats the entire point of a
        # distinct paused state: the execution would show "completed"
        # immediately instead of "awaiting_wakeup" for the actual wait
        # duration). Leave both the local status and the broker alone here
        # -- WakeupScheduler._resolve() is what correctly transitions both
        # once the wakeup genuinely resolves, at core/execution/wakeup_scheduler.py.
        if execution.status == ExecutionStatus.AWAITING_WAKEUP:
            # P-19 Phase 3: let the frontend's live task-tray know this
            # execution just paused, without it having to poll
            # GET /executions/running to notice.
            try:
                from core.events_publisher import publish_user_event
                await publish_user_event(
                    user_id=execution.user_id,
                    event="execution.awaiting_wakeup",
                    execution_id=execution.execution_id,
                    summary="Execution paused, waiting to resume.",
                )
            except Exception:
                pass
            return

        # A provider/fallback failure AgentEngine has nothing left to retry or fail
        # over into is *yielded* as an ErrorChunk, not raised (so the SSE stream can
        # finish cleanly). core/execution/runner.py turns a stream that ends with an
        # error and nothing else into FAILED; this path used to mark it COMPLETED,
        # so e.g. an exhausted API credit balance showed up as a successful run with
        # a generic "I wasn't able to generate a response" message. Same rule here:
        # an error and no text at all means the run produced nothing but the error.
        final_error = final.get("error")
        if final_error and not (final.get("text") or "").strip():
            error_message = final_error.get("message") or "Execution ended in an unrecoverable error."
            logger.warning(
                "[PEV] Stream ended in an unrecoverable ErrorChunk with no output, marking FAILED",
                extra={
                    "execution_id": execution.execution_id,
                    "error_type": final_error.get("error_type"),
                    "provider": final_error.get("provider"),
                },
            )
            transition(ExecutionStatus.FAILED)
            execution.error = {"message": error_message}
            execution.completed_at = time.time()
            # Buffer/shadow are deliberately left in place: the ErrorChunk is what
            # the SSE endpoint's drain-then-finish sequence still has to deliver.
            await redis_execution_registry.mark_execution_failed(
                execution.execution_id, error=error_message,
            )
            event_bus.publish(
                ExecutionFailed(
                    execution_id=execution.execution_id,
                    duration_ms=int((time.monotonic() - start_monotonic) * 1000),
                    error_type=final_error.get("error_type") or "unknown",
                    error_message=error_message,
                    project_id=execution.parent_project_id,
                )
            )
            try:
                from core.events_publisher import publish_user_event
                await publish_user_event(
                    user_id=execution.user_id,
                    event="execution.failed",
                    execution_id=execution.execution_id,
                    summary=f"Execution failed: {final_error.get('error_type') or 'unknown'}",
                )
            except Exception:
                pass
            return

        transition(ExecutionStatus.COMPLETED)
        execution.completed_at = time.time()

        await redis_execution_registry.mark_execution_finished(execution.execution_id)

        event_bus.publish(
            ExecutionCompleted(
                execution_id=execution.execution_id,
                duration_ms=int((time.monotonic() - start_monotonic) * 1000),
                project_id=execution.parent_project_id,
            )
        )

    except asyncio.CancelledError:
        # Guard: the watchdog may have already set status→ABORTED in the broker AND
        # called mark_execution_aborted().  The in-process transition() will
        # raise IllegalStateTransition if the execution is already terminal.
        # We catch that here so cleanup still runs cleanly.
        try:
            transition(ExecutionStatus.ABORTED)
        except Exception:
            pass  # already in a terminal state — safe to continue cleanup
        execution.completed_at = time.time()
        if execution.buffer:
            execution.buffer.clear()
        await shadow.clear()
        # mark_execution_aborted is idempotent — safe to call even if the
        # watchdog already set ABORTED in the broker.
        await redis_execution_registry.mark_execution_aborted(
            execution.execution_id, reason="task_cancelled"
        )
        event_bus.publish(
            ExecutionCancelled(
                execution_id=execution.execution_id,
                duration_ms=int((time.monotonic() - start_monotonic) * 1000),
                cancel_reason="task_cancelled",
            )
        )

    except Exception as exc:
        transition(ExecutionStatus.FAILED)
        execution.error = {"message": str(exc)}
        execution.completed_at = time.time()
        if execution.buffer:
            execution.buffer.clear()
        await shadow.clear()
        await redis_execution_registry.mark_execution_failed(
            execution.execution_id, error=str(exc)
        )
        event_bus.publish(
            ExecutionFailed(
                execution_id=execution.execution_id,
                duration_ms=int((time.monotonic() - start_monotonic) * 1000),
                error_type=type(exc).__name__,
                error_message=str(exc),
                project_id=execution.parent_project_id,
            )
        )
