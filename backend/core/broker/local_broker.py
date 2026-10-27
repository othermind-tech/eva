# core/broker/local_broker.py
"""
LocalBroker — single-process, Redis-free replacement for all six Redis roles
that EVA previously used:

  Role 1  Job queue              asyncio.Queue
  Role 2  Execution state store  LocalRedisAdapter (in-memory dict)
  Role 3  SSE shadow buffer      LocalShadowBuffer (list + asyncio.Event)
  Role 4  Approval gate          asyncio.Event per token
  Role 5  SSE auth tokens        in-memory dict with TTL
  Role 6  User event pub/sub     per-user asyncio.Queue list

The public API mirrors RedisExecutionRegistry exactly so that
core/execution/redis_registry.py can become a thin shim with zero
changes to any caller.

connect() returns a LocalRedisAdapter — an in-memory object that
duck-types the redis.asyncio.Redis interface for the small subset of
Redis commands used by ExecutionBlackboard, approval.py, and runner.py.
This means those files also need zero changes.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.broker.shadow_buffer import LocalShadowBuffer
from core.broker.exceptions import ApprovalGateNotFound, ApprovalGateExpired

logger = logging.getLogger(__name__)

# How long to keep completed/failed/aborted execution records in memory
# before the cleanup task evicts them. 1 hour is generous — SSE clients
# should reconnect well within this window.
_COMPLETED_RECORD_TTL_SECONDS = 3600

# How often the cleanup coroutine runs (seconds).
_CLEANUP_INTERVAL_SECONDS = 60


# ---------------------------------------------------------------------------
# LocalPipeline — queues adapter operations for batch execution
# ---------------------------------------------------------------------------

class LocalPipeline:
    """Mimics redis.asyncio.client.Pipeline for the operations EVA uses."""

    def __init__(self, adapter: "LocalRedisAdapter") -> None:
        self._adapter = adapter
        self._ops: list[tuple] = []

    def set(self, key: str, value: str, ex: Optional[int] = None) -> "LocalPipeline":
        self._ops.append(("set", key, value, ex))
        return self

    def hset(
        self,
        key: str,
        field: Optional[str] = None,
        value: Optional[str] = None,
        mapping: Optional[dict] = None,
    ) -> "LocalPipeline":
        self._ops.append(("hset", key, field, value, mapping))
        return self

    def expire(self, key: str, seconds: int) -> "LocalPipeline":
        self._ops.append(("expire", key, seconds))
        return self

    async def execute(self) -> list:
        results = []
        for op in self._ops:
            if op[0] == "set":
                results.append(await self._adapter.set(op[1], op[2], ex=op[3]))
            elif op[0] == "hset":
                results.append(
                    await self._adapter.hset(op[1], op[2], op[3], mapping=op[4])
                )
            elif op[0] == "expire":
                results.append(await self._adapter.expire(op[1], op[2]))
        return results


# ---------------------------------------------------------------------------
# LocalRedisAdapter — in-memory, duck-typed Redis client
# ---------------------------------------------------------------------------

class LocalRedisAdapter:
    """
    In-memory implementation of the redis.asyncio.Redis interface subset
    used across EVA (blackboard, approval, runner cancel-check, heartbeat).

    All methods are async to match the Redis asyncio interface exactly.
    Key expiry is tracked and checked lazily on read (no background reaper).
    The cleanup task in LocalBroker periodically evicts stale keys.

    blpop() is special: rather than busy-polling it uses per-key asyncio.Event
    objects so that rpush() wakes the waiting blpop() with zero delay.
    """

    def __init__(self) -> None:
        self._hashes:  Dict[str, Dict[str, str]] = {}
        self._lists:   Dict[str, List[str]]       = {}
        self._strings: Dict[str, str]             = {}
        self._expiry:  Dict[str, float]           = {}
        # Per list-key event: signalled when rpush() adds items.
        self._list_events: Dict[str, asyncio.Event] = {}

    # ── TTL helpers ─────────────────────────────────────────────────────────

    def _is_expired(self, key: str) -> bool:
        exp = self._expiry.get(key)
        return exp is not None and time.monotonic() > exp

    def _evict_if_expired(self, key: str) -> bool:
        """Evict key if expired. Returns True if evicted."""
        if self._is_expired(key):
            self._hashes.pop(key, None)
            self._lists.pop(key, None)
            self._strings.pop(key, None)
            self._expiry.pop(key, None)
            return True
        return False

    # ── String ops ──────────────────────────────────────────────────────────

    async def set(
        self,
        key: str,
        value: str,
        ex: Optional[int] = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self._strings and not self._is_expired(key):
            return False
        self._strings[key] = str(value)
        if ex is not None:
            self._expiry[key] = time.monotonic() + ex
        return True

    async def setex(self, key: str, seconds: int, value: str) -> None:
        self._strings[key] = str(value)
        self._expiry[key] = time.monotonic() + seconds

    async def get(self, key: str) -> Optional[str]:
        if self._evict_if_expired(key):
            return None
        return self._strings.get(key)

    # ── Hash ops ────────────────────────────────────────────────────────────

    async def hset(
        self,
        key: str,
        field: Optional[str] = None,
        value: Optional[str] = None,
        mapping: Optional[dict] = None,
    ) -> int:
        if self._evict_if_expired(key):
            pass   # proceed to create fresh hash
        bucket = self._hashes.setdefault(key, {})
        if mapping:
            bucket.update({str(k): str(v) for k, v in mapping.items()})
            return len(mapping)
        elif field is not None:
            bucket[str(field)] = str(value) if value is not None else ""
            return 1
        return 0

    async def hget(self, key: str, field: str) -> Optional[str]:
        if self._evict_if_expired(key):
            return None
        return self._hashes.get(key, {}).get(str(field))

    async def hgetall(self, key: str) -> Dict[str, str]:
        if self._evict_if_expired(key):
            return {}
        return dict(self._hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        bucket = self._hashes.get(key, {})
        removed = 0
        for f in fields:
            if str(f) in bucket:
                del bucket[str(f)]
                removed += 1
        return removed

    # ── List ops ────────────────────────────────────────────────────────────

    async def rpush(self, key: str, *values: str) -> int:
        if self._evict_if_expired(key):
            pass
        lst = self._lists.setdefault(key, [])
        for v in values:
            lst.append(str(v))
        # Wake any waiting blpop
        ev = self._list_events.get(key)
        if ev is not None:
            ev.set()
        return len(lst)

    async def lrange(self, key: str, start: int, stop: int) -> List[str]:
        if self._evict_if_expired(key):
            return []
        lst = self._lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]

    async def llen(self, key: str) -> int:
        if self._evict_if_expired(key):
            return 0
        return len(self._lists.get(key, []))

    async def blpop(
        self, key: str, timeout: int = 0
    ) -> Optional[Tuple[str, str]]:
        """
        Block until an item is available in the list or timeout expires.

        Uses asyncio.Event (set by rpush) for zero-latency wakeup —
        no polling sleep loop.
        """
        deadline = time.monotonic() + timeout if timeout else float("inf")
        event = self._list_events.setdefault(key, asyncio.Event())

        while True:
            lst = self._lists.get(key)
            if lst:
                val = lst.pop(0)
                return (key, val)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            # Wait for rpush to signal, or for the timeout to fire.
            event.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(event.wait()),
                    timeout=min(remaining, 5.0),  # re-check deadline every 5 s
                )
            except asyncio.TimeoutError:
                if time.monotonic() >= deadline:
                    return None

    # ── Key ops ─────────────────────────────────────────────────────────────

    async def exists(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if self._evict_if_expired(key):
                continue
            if (
                key in self._hashes
                or key in self._strings
                or key in self._lists
            ):
                count += 1
        return count

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            gone = False
            if key in self._hashes:
                del self._hashes[key]
                gone = True
            if key in self._lists:
                del self._lists[key]
                gone = True
            if key in self._strings:
                del self._strings[key]
                gone = True
            self._expiry.pop(key, None)
            if gone:
                removed += 1
        return removed

    async def expire(self, key: str, seconds: int) -> int:
        """Set TTL on an existing key. Returns 1 if key exists, 0 otherwise."""
        if (
            key in self._hashes
            or key in self._strings
            or key in self._lists
        ):
            self._expiry[key] = time.monotonic() + seconds
            return 1
        return 0

    async def type(self, key: str) -> str:
        if self._evict_if_expired(key):
            return "none"
        if key in self._hashes:
            return "hash"
        if key in self._lists:
            return "list"
        if key in self._strings:
            return "string"
        return "none"

    # ── Scan ────────────────────────────────────────────────────────────────

    async def scan(
        self,
        cursor: int,
        match: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Tuple[int, List[str]]:
        """Single-pass scan (cursor always returns 0 — no pagination needed)."""
        all_keys: set[str] = set(self._hashes) | set(self._lists) | set(self._strings)
        if match:
            matching = [k for k in all_keys if fnmatch.fnmatch(k, match)]
        else:
            matching = list(all_keys)
        return (0, matching)

    async def scan_iter(self, pattern: Optional[str] = None):  # type: ignore[return]
        """Async generator that yields all matching keys."""
        all_keys: set[str] = set(self._hashes) | set(self._lists) | set(self._strings)
        for key in list(all_keys):   # snapshot to avoid mutation during iteration
            if not pattern or fnmatch.fnmatch(key, pattern):
                yield key

    # ── Pub/sub stub (unused after rewrite — kept for interface completeness) ─

    async def publish(self, channel: str, message: str) -> int:
        return 0

    def pubsub(self) -> "LocalPubSub":
        return LocalPubSub()

    # ── Pipeline ────────────────────────────────────────────────────────────

    def pipeline(self) -> LocalPipeline:
        return LocalPipeline(self)

    # ── Health ──────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        return True

    # ── Cleanup (called by LocalBroker cleanup task) ─────────────────────────

    def evict_expired_keys(self) -> int:
        """Scan all keys and remove expired ones. Called by the cleanup task."""
        now = time.monotonic()
        to_remove = [k for k, exp in self._expiry.items() if now > exp]
        for key in to_remove:
            self._hashes.pop(key, None)
            self._lists.pop(key, None)
            self._strings.pop(key, None)
            del self._expiry[key]
        return len(to_remove)


class LocalPubSub:
    """Stub pub/sub object — api/events.py is rewritten to not use pubsub."""

    async def subscribe(self, *channels: str) -> None:
        pass

    async def unsubscribe(self, *channels: str) -> None:
        pass

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 0.0):
        await asyncio.sleep(timeout)
        return None

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# ApprovalGate — per-token asyncio.Event for the approval decision
# ---------------------------------------------------------------------------

@dataclass
class ApprovalGate:
    token:    str
    decision: Optional[str] = None   # "approved" | "rejected" | None (pending)
    resolved: bool = False


# ---------------------------------------------------------------------------
# UserEventSubscription — per-SSE-connection queue
# ---------------------------------------------------------------------------

@dataclass
class UserEventSubscription:
    user_id: str
    queue:   asyncio.Queue = field(default_factory=asyncio.Queue)


# ---------------------------------------------------------------------------
# LocalBroker — singleton
# ---------------------------------------------------------------------------

class LocalBroker:
    """
    Single-process, Redis-free broker for EVA execution infrastructure.

    All state lives in Python objects inside this singleton.
    Crash recovery queries the SQLite execution_metrics table on startup.
    """

    def __init__(self) -> None:
        # Job queue (Role 1)
        self._job_queue: asyncio.Queue[str] = asyncio.Queue()

        # Key-value + hash + list store (Roles 2, 4, 5 raw storage)
        self._adapter = LocalRedisAdapter()

        # Shadow buffers keyed by execution_id (Role 3)
        self._shadow_buffers: Dict[str, LocalShadowBuffer] = {}

        # Approval gates keyed by token string (Role 4 — asyncio.Event wait)
        self._approval_gates: Dict[str, ApprovalGate] = {}

        # User event subscribers (Role 6)
        self._event_subscribers: Dict[str, List[UserEventSubscription]] = {}

        # Worker identity (single in-process worker)
        self._worker_id: Optional[str] = None
        self._worker_alive: bool = False
        self._worker_last_heartbeat: float = 0.0

        # Background cleanup task handle
        self._cleanup_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

        self._started: bool = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise broker and start background cleanup task. Call once from lifespan."""
        if self._started:
            return
        self._started = True
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="local-broker-cleanup"
        )
        logger.info("[LocalBroker] started")

    async def stop(self) -> None:
        """Cancel background tasks. Call from lifespan shutdown."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        # Close all shadow buffers so SSE generators can exit cleanly.
        for buf in self._shadow_buffers.values():
            buf.close()
        self._started = False
        logger.info("[LocalBroker] stopped")

    async def connect(self) -> "LocalRedisAdapter":
        """
        Return the LocalRedisAdapter (duck-typed Redis client).
        Called by shims that previously called redis.connect().
        Always succeeds — no network, no errors.
        """
        return self._adapter

    @property
    def client(self) -> "LocalRedisAdapter":
        """Synchronous access to the adapter (mirrors redis_registry.client)."""
        return self._adapter

    # ── Execution state (Role 2) ─────────────────────────────────────────────

    async def create_execution(
        self,
        execution_id: str,
        convo_id: str,
        session_id: str,
        user_id: str,
        initial_message: Optional[str] = None,
        execution_mode: str = "conversational",
    ) -> None:
        from core.execution.models import ExecutionStatus
        now = datetime.now(timezone.utc).isoformat()
        data: Dict[str, str] = {
            "conversation_id": convo_id,
            "session_id":      session_id,
            "user_id":         user_id,
            "status":          ExecutionStatus.PENDING.value,
            "created_at":      now,
            "execution_mode":  execution_mode,
        }
        if initial_message:
            data["initial_message"] = initial_message

        key = f"execution:{execution_id}"
        await self._adapter.hset(key, mapping=data)

        # Conversation → execution lookup
        old_exec = await self._adapter.get(f"conversation:{convo_id}")
        if old_exec:
            old_status = await self._adapter.hget(f"execution:{old_exec}", "status")
            logger.debug(
                "[LocalBroker] create_execution — overwriting conversation lookup",
                extra={
                    "convo_id": convo_id,
                    "old_execution_id": old_exec,
                    "old_status": old_status,
                    "new_execution_id": execution_id,
                },
            )
        await self._adapter.set(f"conversation:{convo_id}", execution_id)

        logger.info(
            "[LocalBroker] execution created",
            extra={"execution_id": execution_id, "conversation_id": convo_id, "user_id": user_id},
        )

    async def update_execution(self, execution_id: str, **fields: Any) -> None:
        if "status" in fields:
            logger.error(
                "[LocalBroker] direct status update blocked",
                extra={"execution_id": execution_id},
            )
            return
        key = f"execution:{execution_id}"
        await self._adapter.hset(key, mapping={k: str(v) for k, v in fields.items()})

    async def heartbeat(self, execution_id: str) -> None:
        key = f"execution:{execution_id}"
        await self._adapter.hset(
            key, "last_heartbeat_at", datetime.now(timezone.utc).isoformat()
        )

    # ── Status transitions ───────────────────────────────────────────────────

    async def _set_status(
        self,
        execution_id: str,
        status: Any,  # ExecutionStatus
        extra: Optional[Dict[str, str]] = None,
    ) -> None:
        from core.execution.models import ExecutionStatus
        from core.execution.state_machine import ExecutionStateMachine

        key = f"execution:{execution_id}"
        current_raw = await self._adapter.hget(key, "status")
        if current_raw:
            try:
                current = ExecutionStatus(current_raw)
                ExecutionStateMachine.assert_transition_allowed(
                    execution_id, current, status
                )
            except Exception:
                # State machine rejected this transition (e.g. CANCELLED → RUNNING).
                # Do NOT overwrite — the existing terminal status is authoritative.
                # This prevents a race where the worker marks an execution RUNNING
                # after the cancel endpoint has already written CANCELLED.
                return

        mapping: Dict[str, str] = {"status": status.value}
        if extra:
            mapping.update(extra)
        await self._adapter.hset(key, mapping=mapping)

        logger.info(
            "[LocalBroker] execution status → %s", status.value,
            extra={"execution_id": execution_id},
        )

    async def mark_execution_running(self, execution_id: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_status(
            execution_id,
            ExecutionStatus.RUNNING,
            {"started_at": datetime.now(timezone.utc).isoformat()},
        )

    async def mark_execution_planning(self, execution_id: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_status(
            execution_id,
            ExecutionStatus.PLANNING,
            {"planning_started_at": datetime.now(timezone.utc).isoformat()},
        )

    async def mark_execution_verifying(self, execution_id: str, step_index: int) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_status(
            execution_id,
            ExecutionStatus.VERIFYING,
            {
                "verifying_step": str(step_index),
                "verifying_started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def mark_execution_awaiting_plan_approval(self, execution_id: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_status(
            execution_id,
            ExecutionStatus.AWAITING_PLAN_APPROVAL,
            {"awaiting_plan_approval_at": datetime.now(timezone.utc).isoformat()},
        )

    async def mark_execution_awaiting_approval(self, execution_id: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_status(
            execution_id,
            ExecutionStatus.AWAITING_APPROVAL,
            {"awaiting_approval_at": datetime.now(timezone.utc).isoformat()},
        )

    async def mark_execution_awaiting_step_extension(self, execution_id: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_status(
            execution_id,
            ExecutionStatus.AWAITING_STEP_EXTENSION,
            {"awaiting_step_extension_at": datetime.now(timezone.utc).isoformat()},
        )

    async def mark_execution_awaiting_wakeup(self, execution_id: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_status(
            execution_id,
            ExecutionStatus.AWAITING_WAKEUP,
            {"awaiting_wakeup_at": datetime.now(timezone.utc).isoformat()},
        )

    async def mark_execution_resumed(self, execution_id: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_status(
            execution_id,
            ExecutionStatus.RUNNING,
            {"resumed_at": datetime.now(timezone.utc).isoformat()},
        )

    async def mark_execution_finished(self, execution_id: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_terminal(execution_id, ExecutionStatus.COMPLETED)

    async def mark_execution_cancelled(self, execution_id: str, reason: str = "") -> None:
        from core.execution.models import ExecutionStatus
        extra: Dict[str, str] = {}
        if reason:
            extra["cancel_reason"] = reason
        await self._set_terminal(execution_id, ExecutionStatus.CANCELLED, extra)

    async def mark_execution_aborted(self, execution_id: str, reason: str = "") -> None:
        from core.execution.models import ExecutionStatus
        extra: Dict[str, str] = {}
        if reason:
            extra["cancel_reason"] = reason
        await self._set_terminal(execution_id, ExecutionStatus.ABORTED, extra)

    async def mark_execution_failed(self, execution_id: str, error: str) -> None:
        from core.execution.models import ExecutionStatus
        await self._set_terminal(
            execution_id, ExecutionStatus.FAILED, {"error": error}
        )

    async def _set_terminal(
        self,
        execution_id: str,
        status: Any,
        extra: Optional[Dict[str, str]] = None,
    ) -> None:
        mapping: Dict[str, str] = {
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            mapping.update(extra)
        await self._set_status(execution_id, status, mapping)

        # Close this execution's shadow buffer so SSE consumers exit cleanly.
        buf = self._shadow_buffers.get(execution_id)
        if buf:
            buf.close()

    # ── Worker liveness (single in-process worker — always alive) ────────────

    async def register_worker_liveness(self, worker_id: str) -> None:
        self._worker_id = worker_id
        self._worker_alive = True
        self._worker_last_heartbeat = time.monotonic()
        # Also write into adapter so /health/ready heartbeat check works
        await self._adapter.set("worker:heartbeat", "1")
        await self._adapter.set(f"worker:alive:{worker_id}", "1")
        logger.debug("[LocalBroker] worker liveness registered", extra={"worker_id": worker_id})

    async def claim_execution_ownership(self, execution_id: str, worker_id: str) -> None:
        await self._adapter.hset(f"execution:{execution_id}", "worker_id", worker_id)

    async def is_worker_alive(self, worker_id: str) -> bool:
        """In single-process mode, only one worker exists — always the current one."""
        return worker_id == self._worker_id and self._worker_alive

    # ── Job queue (Role 1) ───────────────────────────────────────────────────

    async def enqueue_job(self, execution_id: str) -> None:
        logger.info("[LocalBroker] job enqueued", extra={"execution_id": execution_id})
        await self._job_queue.put(execution_id)

    async def dequeue_job_blocking(self, timeout: int = 0) -> Optional[str]:
        """
        Dequeue the next execution_id.

        timeout=0 means wait indefinitely (mirrors Redis BLPOP semantics).
        Returns None on timeout (for compatibility with the shim's loop).
        """
        try:
            if timeout:
                return await asyncio.wait_for(self._job_queue.get(), timeout=float(timeout))
            else:
                return await self._job_queue.get()
        except asyncio.TimeoutError:
            return None

    # ── Shadow buffers (Role 3) ──────────────────────────────────────────────

    def get_shadow_buffer(self, execution_id: str) -> LocalShadowBuffer:
        """Return (creating if needed) the shadow buffer for this execution."""
        if execution_id not in self._shadow_buffers:
            self._shadow_buffers[execution_id] = LocalShadowBuffer()
        return self._shadow_buffers[execution_id]

    # ── User event pub/sub (Role 6) ──────────────────────────────────────────

    async def publish_event(self, user_id: str, payload: dict) -> None:
        """Deliver payload to all open SSE connections for this user."""
        subs = self._event_subscribers.get(user_id, [])
        dead: List[UserEventSubscription] = []
        for sub in subs:
            try:
                sub.queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(sub)
        for d in dead:
            self._event_subscribers.get(user_id, []).remove(d)

    def subscribe_events(self, user_id: str) -> UserEventSubscription:
        """Open a new event subscription for user_id. Returns the subscription object."""
        sub = UserEventSubscription(user_id=user_id)
        self._event_subscribers.setdefault(user_id, []).append(sub)
        return sub

    def unsubscribe_events(self, user_id: str, sub: UserEventSubscription) -> None:
        """Remove a subscription (called when SSE client disconnects)."""
        subs = self._event_subscribers.get(user_id, [])
        try:
            subs.remove(sub)
        except ValueError:
            pass

    # ── Crash recovery ───────────────────────────────────────────────────────

    async def recover_orphaned_executions(self) -> List[str]:
        """
        Query SQLite for PENDING/RUNNING/AWAITING_* rows older than 5 minutes
        and mark them ABORTED.  Also marks any in-memory records (from a
        within-process reset) as ABORTED.

        Called at startup (from main.py lifespan AND from worker.start()).
        The second call is a no-op because SQLite rows are already updated.
        """
        from core.execution.models import ExecutionStatus
        from core.execution.state_machine import ExecutionStateMachine

        recovered: List[str] = []

        # ── In-memory scan (handles same-process restarts) ────────────────
        recoverable_statuses = {
            ExecutionStatus.PENDING.value,
            ExecutionStatus.RUNNING.value,
            ExecutionStatus.PLANNING.value,
            ExecutionStatus.VERIFYING.value,
            ExecutionStatus.AWAITING_APPROVAL.value,
            ExecutionStatus.AWAITING_PLAN_APPROVAL.value,
        }
        all_exec_keys = [k for k in self._adapter._hashes if k.startswith("execution:")]
        for key in all_exec_keys:
            data = dict(self._adapter._hashes.get(key, {}))
            status_raw = data.get("status")
            if status_raw not in recoverable_statuses:
                continue
            execution_id = key[len("execution:"):]
            try:
                from_status = ExecutionStatus(status_raw)
                ExecutionStateMachine.assert_transition_allowed(
                    execution_id, from_status, ExecutionStatus.ABORTED
                )
            except Exception:
                continue
            await self._adapter.hset(
                key,
                mapping={
                    "status":       ExecutionStatus.ABORTED.value,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "cancel_reason": "process_restart",
                },
            )
            buf = self._shadow_buffers.get(execution_id)
            if buf:
                buf.close()
            recovered.append(execution_id)

        # ── SQLite scan (handles cross-process restart, previous crashes) ─
        try:
            from core.db.session import AsyncSessionLocal
            from core.db.repository import ExecutionMetricsRepo
            async with AsyncSessionLocal() as db:
                db_recovered = await ExecutionMetricsRepo.recover_orphaned(db)
            if db_recovered:
                logger.warning(
                    "[LocalBroker] SQLite orphan recovery: marked %d executions ABORTED",
                    len(db_recovered),
                    extra={"execution_ids": db_recovered},
                )
                for eid in db_recovered:
                    if eid not in recovered:
                        recovered.append(eid)
        except Exception as exc:
            logger.warning(
                "[LocalBroker] SQLite orphan recovery failed (non-fatal): %s", exc
            )

        # ── File-based marker scan (B2-1.7/4.3) — the real cross-process
        #    restart case for the approval/plan-approval/step-extension
        #    gates. See core/execution/gate_markers.py's module docstring
        #    for why this is file-based rather than an early ExecutionMetrics
        #    row (SQLite here only ever records completed executions).
        try:
            marker_recovered = await self._recover_from_gate_markers()
            for eid in marker_recovered:
                if eid not in recovered:
                    recovered.append(eid)
        except Exception as exc:
            logger.warning(
                "[LocalBroker] gate-marker orphan recovery failed (non-fatal): %s", exc
            )

        if recovered:
            logger.warning(
                "[LocalBroker] recovered %d orphaned executions", len(recovered),
                extra={"execution_ids": recovered},
            )
        return recovered

    async def _recover_from_gate_markers(self) -> List[str]:
        """
        B2-1.7/4.3 — scan core/execution/gate_markers.py's on-disk markers
        for executions that were paused (awaiting approval/plan-approval/
        step-extension) when this process last died.

        The waiting coroutine is unrecoverably gone (a suspended Python
        `await` cannot survive a process restart), so this does not attempt
        to resume anything — it writes exactly one terminal ExecutionMetrics
        row per orphaned execution (status="aborted", matching the
        in-memory scan branch above) so GET /executions/{id} and friends
        show a clean, visible failure instead of a permanently stuck
        "awaiting_*" state that can never actually be resolved (the token
        a user might still try to approve has no live coroutine behind it
        any more), then deletes the marker.
        """
        from core.execution.gate_markers import scan_orphaned_markers, delete_gate_marker
        from core.db.session import AsyncSessionLocal
        from core.db.repository import ExecutionMetricsRepo

        markers = scan_orphaned_markers()
        recovered: List[str] = []

        for marker in markers:
            execution_id = marker.get("execution_id")
            conversation_id = marker.get("conversation_id")
            user_id = marker.get("user_id")
            gate_kind = marker.get("gate_kind", "unknown")

            if not execution_id or not conversation_id or not user_id:
                logger.warning(
                    "[LocalBroker] skipping malformed gate marker (missing required field)",
                    extra={"marker": marker},
                )
                if execution_id:
                    delete_gate_marker(execution_id)
                continue

            tool_name = marker.get("tool_name")
            error_message = f"Execution was interrupted by a server restart while paused ({gate_kind}"
            if tool_name:
                error_message += f", tool='{tool_name}'"
            error_message += ")."

            try:
                async with AsyncSessionLocal() as db:
                    await ExecutionMetricsRepo.upsert(db, {
                        "execution_id": execution_id,
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "status": "aborted",
                        "termination_reason": "process_restart",
                        "error_message": error_message,
                    })
                recovered.append(execution_id)
            except Exception as exc:
                logger.warning(
                    "[LocalBroker] failed to write recovery metrics row for orphaned gate marker (non-fatal): %s",
                    exc,
                    extra={"execution_id": execution_id},
                )
                continue
            finally:
                delete_gate_marker(execution_id)

        return recovered

    # ── Watchdog (embedded — runs every WATCHDOG_INTERVAL_SECONDS) ───────────

    async def _watchdog_loop(self) -> None:
        """
        NOT used: the watchdog lives in ExecutionWorker._watchdog_loop() and
        is re-used as-is (it scans via redis_registry which shims to this
        broker's adapter).  This method is a no-op placeholder kept for
        potential future use.
        """
        pass

    # ── Background cleanup ───────────────────────────────────────────────────

    async def _cleanup_loop(self) -> None:
        """
        Runs every _CLEANUP_INTERVAL_SECONDS.
        - Evicts expired keys from the adapter.
        - Removes shadow buffers for completed executions older than 1 hour.
        - Prunes empty subscriber lists.
        """
        while True:
            try:
                await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
                self._run_cleanup()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("[LocalBroker] cleanup error (non-fatal): %s", exc)

    def _run_cleanup(self) -> None:
        now = time.monotonic()

        # Evict expired keys from adapter
        evicted = self._adapter.evict_expired_keys()
        if evicted:
            logger.debug("[LocalBroker] evicted %d expired keys", evicted)

        # Prune shadow buffers for terminal executions
        to_remove: List[str] = []
        for exec_id, buf in list(self._shadow_buffers.items()):
            if not buf._closed:
                continue
            # Check if the execution record is in a terminal state
            key = f"execution:{exec_id}"
            status = self._adapter._hashes.get(key, {}).get("status", "")
            terminal = {"completed", "failed", "cancelled", "aborted"}
            if status in terminal:
                to_remove.append(exec_id)
        for exec_id in to_remove:
            del self._shadow_buffers[exec_id]

        # Prune empty subscriber lists
        empty_users = [uid for uid, subs in self._event_subscribers.items() if not subs]
        for uid in empty_users:
            del self._event_subscribers[uid]

        # Prune resolved approval gates
        resolved = [t for t, g in self._approval_gates.items() if g.resolved]
        for t in resolved:
            del self._approval_gates[t]

        if to_remove or empty_users or resolved:
            logger.debug(
                "[LocalBroker] cleanup: removed %d shadow buffers, %d subscriber lists, %d gates",
                len(to_remove), len(empty_users), len(resolved),
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

local_broker = LocalBroker()
