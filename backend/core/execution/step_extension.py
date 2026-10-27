# core/execution/step_extension.py
#
# V6.12 — ReAct step-limit "ask user to continue" gate.
#
# When AgentEngine's ReAct loop hits MAX_REACT_STEPS -- after V6.9/V6.11's
# forced-best-effort-answer attempt has already run and resolved either
# way -- this module pauses the execution and asks the user whether to
# extend the step budget, instead of hard-erroring immediately.
#
# Mirrors core/execution/pev_runner.py's _gate_plan_approval() /
# resolve_plan_approval() pattern closely (same blpop/rpush mailbox via
# the broker, same heartbeat-TTL-refresh approach, same
# ExecutionStateMachine-gated status transition) -- deliberately not a new
# idiom, this codebase already has one proven mechanism for "pause
# execution, wait for a user decision, resume or terminate cleanly."
#
# Cross-process signalling
# -------------------------
#   gate_step_extension()    -> blpop("step_extension_decision:{token}", timeout=N)
#   resolve_step_extension() -> rpush("step_extension_decision:{token}", "1"|"0")

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.execution.gate_markers import (
    GATE_KIND_STEP_EXTENSION, delete_gate_marker, write_gate_marker,
)
from core.execution.models import ExecutionStatus
from core.execution.redis_registry import redis_execution_registry
from core.execution.state_machine import ExecutionStateMachine
from config.settings import (
    STEP_EXTENSION_TIMEOUT_SECONDS,
    STEP_EXTENSION_HEARTBEAT_SECONDS,
)

logger = logging.getLogger(__name__)

_STEP_EXTENSION_TIMEOUT   = STEP_EXTENSION_TIMEOUT_SECONDS
_STEP_EXTENSION_HEARTBEAT = STEP_EXTENSION_HEARTBEAT_SECONDS


def _step_extension_pending_key(token: str) -> str:
    return f"step_extension_pending:{token}"


def _step_extension_decision_key(token: str) -> str:
    return f"step_extension_decision:{token}"


async def _push_step_limit_reached_event(
    execution_id: str,
    token: str,
    steps_used: int,
    max_steps: int,
    extensions_used: int,
    extensions_max: int,
) -> None:
    """Write a step_limit_reached chunk to the shadow buffer (SSE)."""
    from core.broker import local_broker as _broker
    buf = _broker.get_shadow_buffer(execution_id)
    await buf.append({
        "type": "step_limit_reached",
        "execution_id": execution_id,
        "step_extension_token": token,
        "steps_used": steps_used,
        "max_steps": max_steps,
        "extensions_used": extensions_used,
        "extensions_max": extensions_max,
    })
    logger.debug(
        "[StepExtension] step_limit_reached event pushed to SSE stream",
        extra={"execution_id": execution_id, "token": token},
    )


async def _step_extension_heartbeat(
    execution_id: str,
    pending_key: str,
    extended_ttl: int,
) -> None:
    """Extend execution and pending-token key TTLs every interval."""
    client = await redis_execution_registry.connect()
    while True:
        await asyncio.sleep(_STEP_EXTENSION_HEARTBEAT)
        try:
            await client.expire(f"execution:{execution_id}", extended_ttl)
            await client.expire(pending_key, extended_ttl)
        except Exception:
            pass


async def gate_step_extension(
    execution: Any,
    *,
    steps_used: int,
    max_steps: int,
    extensions_used: int,
    extensions_max: int,
    timeout_seconds: int = _STEP_EXTENSION_TIMEOUT,
) -> bool:
    """
    Pause execution until the user decides to continue or stop at the
    ReAct step limit.

    Returns True (continue -- caller resets react.tool_steps and resumes)
    or False (stop -- caller ends cleanly, NOT as an error; see
    core/execution/transitions.py for why AWAITING_STEP_EXTENSION's "stop"
    transition targets COMPLETED, not CANCELLED).

    Caller (core/agent_engine.py) is responsible for checking
    extensions_used < extensions_max BEFORE calling this -- this function
    does not itself enforce the cap, it only carries out one round of
    "ask and wait" once the caller has already decided a round is allowed.
    """
    execution_id = getattr(execution, "execution_id", "") or ""
    token        = str(uuid4())
    extended_ttl = timeout_seconds + 120

    client = await redis_execution_registry.connect()
    pending_key = _step_extension_pending_key(token)

    # Register token in the broker so the API can validate it.
    await client.setex(pending_key, extended_ttl, "1")

    # Store token in execution hash so /trace can surface it.
    try:
        await client.hset(
            f"execution:{execution_id}", "pending_step_extension_token", token
        )
    except Exception:
        pass

    # Transition execution -> AWAITING_STEP_EXTENSION.
    ExecutionStateMachine.assert_transition_allowed(
        execution_id, execution.status, ExecutionStatus.AWAITING_STEP_EXTENSION
    )
    execution.status = ExecutionStatus.AWAITING_STEP_EXTENSION
    await redis_execution_registry.mark_execution_awaiting_step_extension(execution_id)

    # File-based liveness marker (B2-1.7/4.3) -- see core/execution/gate_markers.py.
    write_gate_marker(
        execution_id=execution_id,
        conversation_id=execution.conversation_id,
        user_id=execution.user_id,
        gate_kind=GATE_KIND_STEP_EXTENSION,
        token=token,
    )

    # Push SSE event so client can show the continue/stop UI.
    await _push_step_limit_reached_event(
        execution_id, token, steps_used, max_steps, extensions_used, extensions_max
    )

    timed_out = False
    continue_ = False

    heartbeat_task = asyncio.create_task(
        _step_extension_heartbeat(execution_id, pending_key, extended_ttl)
    )

    try:
        result = await client.blpop(
            _step_extension_decision_key(token),
            timeout=timeout_seconds,
        )
        if result is None:
            timed_out = True
        else:
            decision_bytes = result[1] if isinstance(result, (list, tuple)) else result
            continue_ = (decision_bytes == b"1" or decision_bytes == "1")
    except Exception as exc:
        logger.error(
            "[StepExtension] error during blpop wait — auto-stopping",
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
        await client.delete(_step_extension_decision_key(token))
        await client.hdel(f"execution:{execution_id}", "pending_step_extension_token")
    except Exception:
        pass
    delete_gate_marker(execution_id)

    if timed_out:
        continue_ = False
        logger.warning(
            "[StepExtension] decision timed out — auto-stopping (clean completion)",
            extra={"execution_id": execution_id, "timeout_seconds": timeout_seconds},
        )

    if continue_:
        # Put the broker's status back to RUNNING, as the approval gate does after an
        # approve (core/tools/approval.py -> mark_execution_resumed). The caller
        # (agent_engine.py) resets only the in-memory status; without this the broker
        # -- what GET /executions/{id}/status and the SSE/UI read -- kept reporting
        # "awaiting_step_extension" for the rest of the run, even while the agent was
        # working (found live: a script polling /status saw it stuck for 10 minutes).
        try:
            await redis_execution_registry.mark_execution_resumed(execution_id)
        except Exception as exc:
            logger.warning(
                "[StepExtension] could not restore RUNNING in the broker after continue (non-fatal)",
                exc_info=exc,
                extra={"execution_id": execution_id},
            )

    return continue_


async def resolve_step_extension(token: str, continue_: bool) -> bool:
    """
    Signal the waiting gate_step_extension() coroutine via the broker.

    Returns True if the token was found and resolved, False if unknown/expired.
    """
    client = await redis_execution_registry.connect()
    exists = await client.exists(_step_extension_pending_key(token))
    if not exists:
        logger.warning(
            "[StepExtension] resolve: unknown or expired token",
            extra={"token": token},
        )
        return False

    decision = "1" if continue_ else "0"
    await client.rpush(_step_extension_decision_key(token), decision)
    await client.expire(_step_extension_decision_key(token), 120)
    await client.delete(_step_extension_pending_key(token))

    logger.info(
        "[StepExtension] decision resolved",
        extra={"token": token, "continue": continue_},
    )
    return True
