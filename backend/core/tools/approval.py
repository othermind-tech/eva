# core/tools/approval.py
"""
Human-in-the-Loop Approval Gate for EVA.

Architecture
------------
When the dispatcher decides that a tool call requires human approval it calls
``gate_approval()``.  That function:

  1. Generates a unique ``approval_token`` (UUID).
  2. Registers a broker key ``approval_pending:{token}`` so that the
     approve/reject API endpoints can validate the token.
  3. Transitions the execution status to AWAITING_APPROVAL (memory + broker).
  4. Appends an ``approval_required`` chunk directly to the execution's shadow
     buffer so the SSE consumer forwards it to the client.
  5. Blocks via ``client.blpop("approval_decision:{token}", timeout)`` until
     the API server (or timeout) pushes a decision — while a background
     heartbeat task extends the execution key TTL so it cannot expire during
     the wait.
  6. Reads the decision and transitions back to RUNNING (approve) or
     CANCELLED (reject / timeout).
  7. Returns ``True`` (proceed) or ``False`` (abort).

Cross-process signalling
------------------------
In LocalBroker mode the worker runs in-process with the FastAPI server, but
the blpop/rpush mailbox pattern is preserved via the LocalRedisAdapter so the
approval flow works identically:

  gate_approval()     →  blpop("approval_decision:{token}", timeout=N)
  resolve_approval()  →  rpush("approval_decision:{token}", "1"|"0")

Per-Conversation Auto-Approval (ToolApprovalMode.PER_CONVERSATION)
-------------------------------------------------------------------
Conversation approval state is stored in the broker under the key
``conv_approved:{conversation_id}`` with a 7-day TTL.  This survives worker
restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional
from uuid import uuid4

from config.settings import (
    TOOL_APPROVAL_TIMEOUT_SECONDS,
    APPROVAL_HEARTBEAT_INTERVAL_SECONDS,
    PER_CONV_APPROVAL_TTL_SECONDS,
    APPROVAL_ARGS_PREVIEW_MAX_LEN,
)
from core.event_bus import event_bus
from core.events import ToolApprovalRequested, ToolApprovalResolved
from core.execution.gate_markers import GATE_KIND_APPROVAL, delete_gate_marker, write_gate_marker
from core.execution.models import ExecutionStatus
from core.execution.state_machine import ExecutionStateMachine
from core.execution.redis_registry import redis_execution_registry
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-conversation approval tracking — broker-backed
# ---------------------------------------------------------------------------
# TTL for the conv_approved key — see config.settings.PER_CONV_APPROVAL_TTL_SECONDS
_CONV_APPROVED_TTL = PER_CONV_APPROVAL_TTL_SECONDS


async def is_conversation_approved(conversation_id: str) -> bool:
    """
    Return True if the user already approved a tool call in this conversation.

    State is stored in the broker so it survives worker restarts.
    """
    client = await redis_execution_registry.connect()
    return bool(await client.exists(f"conv_approved:{conversation_id}"))


async def mark_conversation_approved(conversation_id: str) -> None:
    """
    Record that the user approved a tool call in this conversation.

    Stored in the broker with a 7-day TTL so it survives worker restarts.
    """
    client = await redis_execution_registry.connect()
    await client.setex(f"conv_approved:{conversation_id}", _CONV_APPROVED_TTL, "1")
    logger.debug(
        "Conversation marked as approved for tool auto-execution",
        extra={"conversation_id": conversation_id},
    )


# ---------------------------------------------------------------------------
# Broker key helpers
# ---------------------------------------------------------------------------

def _pending_key(token: str) -> str:
    """Key that signals the token is still awaiting a decision."""
    return f"approval_pending:{token}"


def _decision_key(token: str) -> str:
    """List key: gate_approval() blpops this; resolve_approval() rpushes to it."""
    return f"approval_decision:{token}"


# ---------------------------------------------------------------------------
# Resolve (called by approve / reject API endpoints — API server process)
# ---------------------------------------------------------------------------

async def resolve_approval(token: str, approved: bool) -> bool:
    """
    Signal the waiting ``gate_approval()`` coroutine (in the worker process)
    via the broker.

    Parameters
    ----------
    token    : The ``approval_token`` from the SSE event.
    approved : True to allow the tool to run; False to cancel.

    Returns
    -------
    True if the token was found and resolved, False if unknown (already
    timed-out or never issued).
    """
    client = await redis_execution_registry.connect()

    # Validate token is still pending (not timed out / already resolved).
    exists = await client.exists(_pending_key(token))
    if not exists:
        logger.warning(
            "resolve_approval: unknown or expired token",
            extra={"token": token},
        )
        return False

    # Push decision to the mailbox list that gate_approval() is blpop-ing.
    decision = "1" if approved else "0"
    await client.rpush(_decision_key(token), decision)
    # Give the decision key a short TTL so it self-cleans if blpop already
    # timed out before we pushed.
    await client.expire(_decision_key(token), 120)

    # Remove the pending marker so duplicate resolve calls are rejected.
    await client.delete(_pending_key(token))

    logger.info(
        "Approval resolved",
        extra={"token": token, "approved": approved},
    )
    return True


# ---------------------------------------------------------------------------
# Args preview sanitiser
# ---------------------------------------------------------------------------

# Keys whose values should be redacted (case-insensitive substring match).
_SENSITIVE_PATTERNS = re.compile(
    r"password|token|secret|api[_\-]?key|auth|credential|bearer",
    re.IGNORECASE,
)
_MAX_VALUE_LEN = APPROVAL_ARGS_PREVIEW_MAX_LEN


def _sanitise_value(key: str, value: Any) -> Any:
    """Redact sensitive values; truncate long strings."""
    if _SENSITIVE_PATTERNS.search(key):
        return "<redacted>"
    if isinstance(value, str) and len(value) > _MAX_VALUE_LEN:
        return value[:_MAX_VALUE_LEN] + "…<truncated>"
    return value


def build_args_preview(arguments: Dict[str, Any]) -> str:
    """
    Return a compact, human-readable JSON summary of ``arguments`` suitable
    for display in the approval UI.  Sensitive keys are redacted and long
    strings are truncated.
    """
    sanitised = {k: _sanitise_value(k, v) for k, v in arguments.items()}
    try:
        return json.dumps(sanitised, ensure_ascii=False)
    except Exception:
        return str(sanitised)


# ---------------------------------------------------------------------------
# State-transition helpers
# ---------------------------------------------------------------------------

def _transition_execution(execution, new_status: ExecutionStatus) -> None:
    """Update the in-memory execution status via the state machine."""
    ExecutionStateMachine.assert_transition_allowed(
        execution.execution_id,
        execution.status,
        new_status,
    )
    execution.status = new_status
    logger.debug(
        "Approval gate: execution status transition",
        extra={
            "execution_id": execution.execution_id,
            "new_status": new_status.value,
        },
    )


async def _push_approval_event_to_stream(
    execution_id: str,
    tool_name: str,
    authority: str,
    args_preview: str,
    approval_token: str,
) -> None:
    """
    Write an ``approval_required`` chunk directly into the execution's shadow
    buffer so the SSE consumer forwards it to the client.

    The chunk includes ``execution_id`` so that the client can immediately
    call POST /executions/{execution_id}/approve|reject without a separate
    lookup step.
    """
    from core.broker import local_broker as _broker
    buf = _broker.get_shadow_buffer(execution_id)
    await buf.append({
        "type": "approval_required",
        "execution_id": execution_id,   # client needs this for approve/reject URLs
        "tool_name": tool_name,
        "authority": authority,
        "args_preview": args_preview,
        "approval_token": approval_token,
    })
    logger.debug(
        "Approval gate: approval_required event pushed to SSE stream",
        extra={"execution_id": execution_id, "approval_token": approval_token},
    )


# ---------------------------------------------------------------------------
# Heartbeat task (keeps execution key alive during long approval waits)
# ---------------------------------------------------------------------------

# How often (in seconds) the heartbeat refreshes broker key TTLs while waiting.
_APPROVAL_HEARTBEAT_INTERVAL = APPROVAL_HEARTBEAT_INTERVAL_SECONDS


async def _approval_heartbeat(
    execution_id: str,
    pending_key: str,
    extended_ttl: int,
) -> None:
    """
    Background task that extends the execution and pending-token key TTLs
    every ``_APPROVAL_HEARTBEAT_INTERVAL`` seconds.

    Prevents the execution hash from expiring while the gate is blocking on
    blpop (which can wait up to TOOL_APPROVAL_TIMEOUT_SECONDS).
    Cancelled by gate_approval() via the finally block that wraps the blpop.
    """
    client = await redis_execution_registry.connect()
    while True:
        await asyncio.sleep(_APPROVAL_HEARTBEAT_INTERVAL)
        try:
            await client.expire(f"execution:{execution_id}", extended_ttl)
            await client.expire(pending_key, extended_ttl)
            logger.debug(
                "Approval gate heartbeat: extended key TTLs",
                extra={"execution_id": execution_id},
            )
        except Exception as exc:
            logger.warning(
                "Approval gate heartbeat: failed to extend key TTLs",
                extra={"execution_id": execution_id, "error": str(exc)},
            )


# ---------------------------------------------------------------------------
# Main gate function (called by dispatcher — worker process)
# ---------------------------------------------------------------------------

async def gate_approval(
    *,
    execution,
    call_id: str,
    tool_name: str,
    authority: str,
    arguments: Dict[str, Any],
    timeout_seconds: int = TOOL_APPROVAL_TIMEOUT_SECONDS,
) -> bool:
    """
    Pause execution until the user approves or rejects the pending tool call.

    Uses broker blpop/rpush for cross-process signalling (works whether the
    worker runs in-process or as a separate process via LocalRedisAdapter).
    A background heartbeat task extends the execution key TTL every 60 s
    so that long waits (e.g. user away for 30 minutes) do not cause the
    execution hash to expire mid-wait.

    Parameters
    ----------
    execution        : ConversationExecution object.
    call_id          : Tool call ID (for event correlation).
    tool_name        : Name of the tool awaiting approval.
    authority        : "mutating" or "destructive".
    arguments        : Raw tool arguments (will be sanitised for preview).
    timeout_seconds  : Seconds to wait before auto-cancelling.
                       Defaults to TOOL_APPROVAL_TIMEOUT_SECONDS from
                       config/settings.py (env: TOOL_APPROVAL_TIMEOUT_SECONDS,
                       default 3600 s / 1 hour).

    Returns
    -------
    True  – user approved; dispatcher should proceed to execute the tool.
    False – user rejected or timeout; dispatcher should return a PERMISSION
            failure result (execution is already CANCELLED at this point).
    """
    execution_id    = execution.execution_id
    conversation_id = execution.conversation_id
    approval_token  = str(uuid4())
    args_preview    = build_args_preview(arguments)

    logger.info(
        "Approval gate: requesting approval",
        extra={
            "execution_id": execution_id,
            "tool_name": tool_name,
            "authority": authority,
            "approval_token": approval_token,
        },
    )

    # Acquire broker client (required for blpop wait).
    client = await redis_execution_registry.connect()
    pending_key = _pending_key(approval_token)
    extended_ttl = timeout_seconds + 120   # keys self-clean after timeout + buffer

    # 1. Register token in the broker so resolve_approval() can validate it.
    await client.setex(pending_key, extended_ttl, "1")

    # 2. Transition execution → AWAITING_APPROVAL.
    _transition_execution(execution, ExecutionStatus.AWAITING_APPROVAL)
    await redis_execution_registry.mark_execution_awaiting_approval(execution_id)

    # 2b. Store the approval token in the execution hash so the /trace
    #     endpoint can return it to the client for approve/reject calls.
    #     This allows polling clients that miss the SSE stream to still
    #     discover and act on the pending approval.
    try:
        await client.hset(
            f"execution:{execution_id}", "pending_approval_token", approval_token
        )
    except Exception as _hset_exc:
        logger.warning(
            "Approval gate: failed to write pending_approval_token to execution hash",
            extra={"execution_id": execution_id, "error": str(_hset_exc)},
        )

    # 2c. File-based liveness marker (B2-1.7/4.3) -- survives a real process
    #     restart (the broker keys above and the execution's own coroutine
    #     do not), letting boot-time reconciliation detect and cleanly fail
    #     this execution if the process dies while we're waiting below.
    write_gate_marker(
        execution_id=execution_id,
        conversation_id=conversation_id,
        user_id=execution.user_id,
        gate_kind=GATE_KIND_APPROVAL,
        token=approval_token,
        tool_name=tool_name,
        authority=authority,
    )

    # 3. Push SSE event so the client sees it.
    await _push_approval_event_to_stream(
        execution_id=execution_id,
        tool_name=tool_name,
        authority=authority,
        args_preview=args_preview,
        approval_token=approval_token,
    )

    # 4. Publish internal observability event.
    event_bus.publish(
        ToolApprovalRequested(
            execution_id=execution_id,
            call_id=call_id,
            tool_name=tool_name,
            authority=authority,
            args_preview=args_preview,
            approval_token=approval_token,
        )
    )

    # 5. Block (cross-process) until the API server pushes a decision or
    #    timeout fires.  blpop returns None on timeout, else [key, value].
    #    A background heartbeat task keeps the execution + pending key TTLs
    #    refreshed so they cannot expire while we wait.
    timed_out = False
    approved  = False

    heartbeat_task = asyncio.create_task(
        _approval_heartbeat(execution_id, pending_key, extended_ttl)
    )

    try:
        result = await client.blpop(
            _decision_key(approval_token),
            timeout=timeout_seconds,
        )
        if result is None:
            timed_out = True
        else:
            # result is a list/tuple: [key_bytes, value_bytes]
            decision_bytes = result[1] if isinstance(result, (list, tuple)) else result
            approved = (decision_bytes == b"1" or decision_bytes == "1")
    except Exception as exc:
        logger.error(
            "Approval gate: error during blpop wait — auto-rejecting",
            exc_info=exc,
            extra={"execution_id": execution_id},
        )
        timed_out = True
    finally:
        # Always cancel the heartbeat task when the gate resolves.
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    # 6. Clean up broker keys (best-effort).
    try:
        await client.delete(pending_key)
        await client.delete(_decision_key(approval_token))
        # Clear the pending_approval_token field from the execution hash so
        # subsequent /trace calls no longer surface a stale token.
        await client.hdel(f"execution:{execution_id}", "pending_approval_token")
    except Exception:
        pass
    delete_gate_marker(execution_id)

    if timed_out:
        approved = False
        logger.warning(
            "Approval gate: timed out waiting for user decision",
            extra={
                "execution_id": execution_id,
                "approval_token": approval_token,
                "timeout_seconds": timeout_seconds,
            },
        )

    # 7. Publish resolution event.
    event_bus.publish(
        ToolApprovalResolved(
            execution_id=execution_id,
            call_id=call_id,
            tool_name=tool_name,
            approved=approved,
            timeout=timed_out,
        )
    )

    # 8. Transition execution status based on decision.
    if approved:
        # Mark conversation as approved for PER_CONVERSATION mode.
        await mark_conversation_approved(conversation_id)

        _transition_execution(execution, ExecutionStatus.RUNNING)
        await redis_execution_registry.mark_execution_resumed(execution_id)

        logger.info(
            "Approval gate: approved — resuming execution",
            extra={"execution_id": execution_id, "tool_name": tool_name},
        )
    else:
        cancel_reason = "approval_timeout" if timed_out else "user_rejected_tool"

        _transition_execution(execution, ExecutionStatus.CANCELLED)

        # Set the in-process cancellation token immediately so that
        # AgentEngine._check_cancelled() terminates the ReAct loop on
        # the very next iteration — before it makes another LLM round-trip
        # to "explain" the rejection.  Without this the agent engine would
        # only stop when runner.py's 2-second broker poll fires.
        if hasattr(execution, "cancel_token") and execution.cancel_token is not None:
            execution.cancel_token.cancelled = True

        await redis_execution_registry.mark_execution_cancelled(
            execution_id,
            reason=cancel_reason,
        )

        logger.info(
            "Approval gate: rejected — execution cancelled",
            extra={
                "execution_id": execution_id,
                "tool_name": tool_name,
                "reason": cancel_reason,
            },
        )

    return approved
