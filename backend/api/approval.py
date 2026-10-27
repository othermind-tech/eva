# api/approval.py
"""
Human-in-the-Loop tool approval endpoints.

POST /executions/{execution_id}/approve
    Signal that the user allows the pending tool to execute.
    Body: { "approval_token": "<uuid>" }

POST /executions/{execution_id}/reject
    Signal that the user denies the pending tool execution.
    Body: { "approval_token": "<uuid>" }

POST /executions/{execution_id}/plan-decision   (UX-01)
    Resolve a pending execution plan (PEV loop): approve it as-is, submit an
    edited replacement plan (which becomes what actually executes), or
    reject it.
    Body: {
        "decision": "approve" | "edit" | "reject",
        "plan_approval_token": "<uuid>",
        "plan": { "goal": "...", "steps": [...] } | null   # required iff decision == "edit"
    }
    See PlanApprovalRequest below for the full schema and validation rules.

POST /executions/{execution_id}/extend   (V6.12)
    Resolve a pending step-limit gate (ReAct loop reached MAX_REACT_STEPS):
    continue with a fresh step budget, or stop and keep the work done so far.
    Body: { "decision": "continue" | "stop", "step_extension_token": "<uuid>" }

All endpoints are authenticated.  The caller must be the owner of the
execution (user_id must match the execution's user_id recorded in the broker).
"""

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator
from logging import getLogger

from core.auth.dependencies import get_current_auth
from core.tools.approval import resolve_approval
from core.execution.pev_runner import resolve_plan_approval
from core.execution.planner import validate_plan_shape, PlanValidationError
from core.execution.redis_registry import redis_execution_registry
from core.execution.step_extension import resolve_step_extension

logger = getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request body schema
# ---------------------------------------------------------------------------

class ApprovalRequest(BaseModel):
    approval_token: str


# ---------------------------------------------------------------------------
# Ownership helper
# ---------------------------------------------------------------------------

async def _verify_execution_owner(execution_id: str, user_id: str) -> None:
    """
    Verify that ``user_id`` owns ``execution_id`` via broker metadata.
    Raises HTTP 403 if not, HTTP 404 if execution not found.
    """
    client = await redis_execution_registry.connect()
    key = f"execution:{execution_id}"
    data = await client.hgetall(key)

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution '{execution_id}' not found.",
        )

    execution_user = data.get("user_id", "")
    if execution_user and execution_user != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to approve/reject this execution.",
        )


# ---------------------------------------------------------------------------
# Approve endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/executions/{execution_id}/approve",
    summary="Approve a pending tool call",
    response_model=dict,
)
async def approve_tool_call(
    execution_id: str,
    body: ApprovalRequest,
    auth=Depends(get_current_auth),
):
    """
    Allow the execution to proceed with the pending mutating/destructive tool call.

    The ``approval_token`` must match the token sent in the SSE
    ``approval_required`` event.  Once signalled, the blocked dispatcher
    will transition the execution back to RUNNING and execute the tool.
    """
    user_id = auth["user_id"]

    logger.info(
        "Tool call approval requested",
        extra={
            "execution_id": execution_id,
            "user_id": user_id,
            "approval_token": body.approval_token,
        },
    )

    await _verify_execution_owner(execution_id, user_id)

    resolved = await resolve_approval(body.approval_token, approved=True)
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Approval token not found or already resolved.  "
                "The request may have timed out."
            ),
        )

    logger.info(
        "Tool call approved",
        extra={"execution_id": execution_id, "approval_token": body.approval_token},
    )

    return {
        "status": "approved",
        "execution_id": execution_id,
        "approval_token": body.approval_token,
    }


# ---------------------------------------------------------------------------
# Reject endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/executions/{execution_id}/reject",
    summary="Reject a pending tool call",
    response_model=dict,
)
async def reject_tool_call(
    execution_id: str,
    body: ApprovalRequest,
    auth=Depends(get_current_auth),
):
    """
    Deny the pending tool call.

    The execution will be transitioned to CANCELLED with reason
    ``user_rejected_tool``.  The ``approval_token`` must match the token
    sent in the SSE ``approval_required`` event.
    """
    user_id = auth["user_id"]

    logger.info(
        "Tool call rejection requested",
        extra={
            "execution_id": execution_id,
            "user_id": user_id,
            "approval_token": body.approval_token,
        },
    )

    await _verify_execution_owner(execution_id, user_id)

    resolved = await resolve_approval(body.approval_token, approved=False)
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Approval token not found or already resolved.  "
                "The request may have timed out."
            ),
        )

    logger.info(
        "Tool call rejected",
        extra={"execution_id": execution_id, "approval_token": body.approval_token},
    )

    return {
        "status": "rejected",
        "execution_id": execution_id,
        "approval_token": body.approval_token,
    }


# ---------------------------------------------------------------------------
# Plan Approval endpoint (PEV loop) — UX-01: approve / edit / reject
# ---------------------------------------------------------------------------
#
# REST shape decision: a single unified endpoint replaces the previous pair
# of approve-plan/reject-plan endpoints. Once the request body needs a
# `decision` field anyway (to carry the "edit" case's replacement plan),
# keeping two-plus separate URL verbs that must agree with the body becomes
# redundant and a source of drift. One endpoint + one discriminated body is
# also the cleanest mapping onto the three-button UX (Approve as-is /
# Save edits + Approve / Reject) the frontend needs to build here.
#
# BREAKING CHANGE FOR FRONTEND: this replaces POST .../approve-plan and
# POST .../reject-plan, which no longer exist. Any caller of those two
# routes must switch to POST .../plan-decision with an explicit "decision".

class PlanApprovalRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    plan_approval_token: str
    # Required (non-empty: a non-empty 'goal' string + at least one step)
    # iff decision == "edit". Omit or send null otherwise. Full shape
    # validated server-side against core.execution.planner.validate_plan_shape().
    plan: Optional[Dict[str, Any]] = None
    # Optional free-text reason. Only meaningful when decision == "reject"
    # (surfaced in the UX spec's "reason input on reject"); logged for
    # audit purposes but does not otherwise change behaviour.
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _plan_required_iff_edit(self):
        # Deliberately a model_validator (not a field_validator on 'plan'):
        # pydantic v2 field_validators don't run when a field is left at its
        # default (unprovided), so a field_validator alone would silently
        # let `{"decision": "edit", "plan_approval_token": "..."}` (no
        # 'plan' key at all) through unvalidated. A model_validator runs
        # after every field is set regardless of whether it came from the
        # request or from its default.
        if self.decision == "edit" and not self.plan:
            raise ValueError(
                "'plan' is required and must be a non-empty object when decision == 'edit'."
            )
        return self


@router.post(
    "/executions/{execution_id}/plan-decision",
    summary="Resolve a pending execution plan: approve, edit, or reject (PEV loop, UX-01)",
    response_model=dict,
)
async def resolve_plan_decision(
    execution_id: str,
    body: PlanApprovalRequest,
    auth=Depends(get_current_auth),
):
    """
    Resolve the AWAITING_PLAN_APPROVAL gate for a plan_execute execution.

    - decision="approve": the PEV runner proceeds with the plan exactly as
      shown in the ``plan_approval_required`` SSE event.
    - decision="edit": ``body.plan`` becomes what actually executes,
      replacing the original LLM-generated plan. Validated here against the
      same shape PlannerEngine.generate() produces — see
      core/execution/planner.py's validate_plan_shape() — malformed plans
      are rejected with 422 before ever reaching the PEV runner.
    - decision="reject": if ``body.reason`` is non-empty AND the execution
      has not yet used up its plan-revision rounds (UX-01 Phase 2 — see
      PLAN_REVISION_MAX_ROUNDS), the PEV runner revises the plan with that
      feedback and re-emits a fresh ``plan_approval_required`` event instead
      of cancelling. A reject with no reason, or once revision rounds are
      exhausted, transitions the execution to CANCELLED
      (reason: user_rejected_plan) exactly as before.

    The ``plan_approval_token`` must match the token sent in the
    ``plan_approval_required`` SSE event — each revision round issues a new
    token, so a stale token from an earlier round is correctly rejected.
    """
    user_id = auth["user_id"]

    logger.info(
        "Plan decision requested",
        extra={
            "execution_id":        execution_id,
            "user_id":             user_id,
            "plan_approval_token": body.plan_approval_token,
            "decision":            body.decision,
        },
    )

    await _verify_execution_owner(execution_id, user_id)

    validated_plan: Optional[Dict[str, Any]] = None
    if body.decision == "edit":
        try:
            validated_plan = validate_plan_shape(body.plan)
        except PlanValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid edited plan: {exc}",
            )

    resolved = await resolve_plan_approval(
        body.plan_approval_token,
        decision=body.decision,
        modified_plan=validated_plan,
        reason=body.reason,
    )
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Plan approval token not found or already resolved.  "
                "The request may have timed out."
            ),
        )

    if body.decision == "reject" and body.reason:
        logger.info(
            "Plan rejected with reason — PEV runner will attempt a revision "
            "if revision rounds remain (UX-01 Phase 2)",
            extra={
                "execution_id":        execution_id,
                "plan_approval_token": body.plan_approval_token,
                "reason":              body.reason,
            },
        )

    status_label = {"approve": "approved", "edit": "edited", "reject": "rejected"}[body.decision]

    logger.info(
        "Plan %s" % status_label,
        extra={
            "execution_id":        execution_id,
            "plan_approval_token": body.plan_approval_token,
        },
    )

    return {
        "status": status_label,
        "execution_id": execution_id,
        "plan_approval_token": body.plan_approval_token,
    }


# ---------------------------------------------------------------------------
# Step-extension endpoint (ReAct loop) — V6.12
# ---------------------------------------------------------------------------

class StepExtensionRequest(BaseModel):
    decision: Literal["continue", "stop"]
    step_extension_token: str


@router.post(
    "/executions/{execution_id}/extend",
    summary="Resolve a pending step-limit gate: continue or stop (ReAct loop, V6.12)",
    response_model=dict,
)
async def resolve_step_extension_decision(
    execution_id: str,
    body: StepExtensionRequest,
    auth=Depends(get_current_auth),
):
    """
    Resolve the AWAITING_STEP_EXTENSION gate for an execution that reached
    its ReAct step limit.

    - decision="continue": AgentEngine resets its step counter and resumes
      the ReAct loop with a fresh budget. Only honoured while the
      execution's ``max_step_extensions`` session limit hasn't already
      been exhausted -- see core/agent_engine.py, which checks the cap
      before ever opening this gate.
    - decision="stop": the execution transitions to COMPLETED (not an
      error, not CANCELLED) -- whatever work was already done is kept.

    The ``step_extension_token`` must match the token sent in the
    ``step_limit_reached`` SSE event. Returns 404 if the token is unknown,
    already resolved, or expired (the gate auto-stops on timeout, same as
    the plan-approval and tool-approval gates).
    """
    user_id = auth["user_id"]

    logger.info(
        "Step-extension decision requested",
        extra={
            "execution_id":         execution_id,
            "user_id":              user_id,
            "step_extension_token": body.step_extension_token,
            "decision":             body.decision,
        },
    )

    await _verify_execution_owner(execution_id, user_id)

    resolved = await resolve_step_extension(
        body.step_extension_token,
        continue_=(body.decision == "continue"),
    )
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Step-extension token not found or already resolved.  "
                "The request may have timed out."
            ),
        )

    status_label = "continuing" if body.decision == "continue" else "stopped"

    logger.info(
        "Step-extension %s" % status_label,
        extra={
            "execution_id":         execution_id,
            "step_extension_token": body.step_extension_token,
        },
    )

    return {
        "status": status_label,
        "execution_id": execution_id,
        "step_extension_token": body.step_extension_token,
    }
