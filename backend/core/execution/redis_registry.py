# core/execution/redis_registry.py
"""
SHIM — delegates all calls to core.broker.local_broker.

This file previously contained the full RedisExecutionRegistry (819 lines).
All callers import redis_execution_registry from here unchanged; internally
everything is routed to the in-process LocalBroker singleton.

The EXECUTION_KEY_PREFIX constant is kept for the watchdog in worker.py which
uses it for key-pattern filtering.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Any

from core.broker import local_broker as _broker

logger = logging.getLogger(__name__)

# Kept for backwards-compat (worker.py watchdog uses this as a key prefix string)
EXECUTION_KEY_PREFIX = "execution:"
WORKER_LIVENESS_PREFIX = "worker:alive:"


class _LocalRegistryShim:
    """
    Drop-in replacement for RedisExecutionRegistry.
    Every method delegates to LocalBroker; the public interface is identical.
    """

    # ── Connection ───────────────────────────────────────────────────────────

    async def connect(self) -> Any:
        """Return the LocalRedisAdapter (duck-typed Redis client)."""
        return await _broker.connect()

    @property
    def client(self) -> Any:
        """Synchronous accessor for callers that check `if client:`."""
        return _broker.client

    # ── Execution CRUD ───────────────────────────────────────────────────────

    async def create_execution(
        self,
        execution_id: str,
        convo_id: str,
        session_id: str,
        user_id: str,
        initial_message: Optional[str] = None,
        execution_mode: str = "conversational",
    ) -> None:
        await _broker.create_execution(
            execution_id=execution_id,
            convo_id=convo_id,
            session_id=session_id,
            user_id=user_id,
            initial_message=initial_message,
            execution_mode=execution_mode,
        )

    async def update_execution(self, execution_id: str, **fields: Any) -> None:
        await _broker.update_execution(execution_id, **fields)

    async def heartbeat(self, execution_id: str) -> None:
        await _broker.heartbeat(execution_id)

    # ── Status transitions ───────────────────────────────────────────────────

    async def mark_execution_running(self, execution_id: str) -> None:
        await _broker.mark_execution_running(execution_id)

    async def mark_execution_planning(self, execution_id: str) -> None:
        await _broker.mark_execution_planning(execution_id)

    async def mark_execution_verifying(self, execution_id: str, step_index: int) -> None:
        await _broker.mark_execution_verifying(execution_id, step_index)

    async def mark_execution_awaiting_plan_approval(self, execution_id: str) -> None:
        await _broker.mark_execution_awaiting_plan_approval(execution_id)

    async def mark_execution_awaiting_approval(self, execution_id: str) -> None:
        await _broker.mark_execution_awaiting_approval(execution_id)

    async def mark_execution_awaiting_step_extension(self, execution_id: str) -> None:
        await _broker.mark_execution_awaiting_step_extension(execution_id)

    async def mark_execution_awaiting_wakeup(self, execution_id: str) -> None:
        await _broker.mark_execution_awaiting_wakeup(execution_id)

    async def mark_execution_resumed(self, execution_id: str) -> None:
        await _broker.mark_execution_resumed(execution_id)

    async def mark_execution_finished(self, execution_id: str) -> None:
        await _broker.mark_execution_finished(execution_id)

    async def mark_execution_cancelled(self, execution_id: str, reason: str = "") -> None:
        await _broker.mark_execution_cancelled(execution_id, reason)

    async def mark_execution_aborted(self, execution_id: str, reason: str = "") -> None:
        await _broker.mark_execution_aborted(execution_id, reason)

    async def mark_execution_failed(self, execution_id: str, error: str) -> None:
        await _broker.mark_execution_failed(execution_id, error)

    # ── Worker liveness ──────────────────────────────────────────────────────

    async def register_worker_liveness(self, worker_id: str) -> None:
        await _broker.register_worker_liveness(worker_id)

    async def claim_execution_ownership(
        self, execution_id: str, worker_id: str
    ) -> None:
        await _broker.claim_execution_ownership(execution_id, worker_id)

    async def is_worker_alive(self, worker_id: str) -> bool:
        return await _broker.is_worker_alive(worker_id)

    # ── Job queue ────────────────────────────────────────────────────────────

    async def enqueue_job(self, execution_id: str) -> None:
        await _broker.enqueue_job(execution_id)

    async def dequeue_job_blocking(self, timeout: int = 0) -> Optional[str]:
        return await _broker.dequeue_job_blocking(timeout)

    # ── Recovery ────────────────────────────────────────────────────────────

    async def recover_orphaned_executions(self) -> List[str]:
        return await _broker.recover_orphaned_executions()


# Singleton — same name as before; all importers work unchanged.
redis_execution_registry = _LocalRegistryShim()
