# core/metrics/collector.py
#
# In-memory per-execution metrics accumulator.
#
# Design:
#   - Subscribes to the typed event bus (synchronous handlers).
#   - Accumulates data in a dict keyed by execution_id.
#   - pop_snapshot(execution_id) → snapshot + clears the accum → caller
#     persists to DB asynchronously.
#   - Thread-safe: Lock guards the shared _store dict, because the asyncio
#     event loop may run on a thread-pool executor in some configurations.
#
# What is tracked
# ───────────────
# ToolCalled (attempt == 1)  → increments call_count + total_steps per tool
# ToolCalled (attempt > 1)   → retry; does NOT increment call_count/steps
# ToolResult (success)       → accumulates latency_ms
# ToolResult (failure)       → accumulates latency_ms + failure_count
# ToolPermissionDenied       → increments failure_count (no latency captured)
# FallbackTriggered          → increments fallbacks_triggered
# StepCompleted              → reserved; no-op until AgentEngine is migrated
# ExecutionCompleted/Failed/Cancelled → records duration_ms + termination_reason
#
# total_steps = number of distinct tool invocations (attempt == 1).
# For a direct single-response execution (no tools): total_steps = 0.

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.event_bus import event_bus
from core.events import (
    ExecutionCancelled,
    ExecutionCompleted,
    ExecutionFailed,
    FallbackTriggered,
    ProviderFallbackTriggered,
    StepCompleted,
    SubagentCompleted,
    ToolCalled,
    ToolPermissionDenied,
    ToolResult,
)


# ---------------------------------------------------------------------------
# Internal accumulator (mutable, not exposed outside)
# ---------------------------------------------------------------------------

class _ToolAccum:
    """Per-tool mutable accumulator."""
    __slots__ = ("call_count", "total_latency_ms", "failure_count", "last_denial_reason")

    def __init__(self) -> None:
        self.call_count:          int = 0
        self.total_latency_ms:    int = 0
        self.failure_count:       int = 0
        self.last_denial_reason:  Optional[str] = None  # Gap 9


class _ExecAccum:
    """Per-execution mutable accumulator."""
    __slots__ = (
        "tools", "fallbacks", "fallback_details",
        "total_steps", "termination_reason", "error_message", "duration_ms",
        "subagent_calls", "subagent_failures",
    )

    def __init__(self) -> None:
        self.tools:               Dict[str, _ToolAccum] = {}
        self.fallbacks:           int = 0
        self.fallback_details:    List[dict] = []       # Gap 8: per-fallback detail
        self.total_steps:         int = 0               # distinct first-attempt tool calls
        self.termination_reason:  Optional[str] = None
        self.error_message:       Optional[str] = None  # str(exc) on failure
        self.duration_ms:         Optional[int] = None
        self.subagent_calls:      int = 0               # Gap 10
        self.subagent_failures:   int = 0               # Gap 10

    def tool(self, name: str) -> _ToolAccum:
        if name not in self.tools:
            self.tools[name] = _ToolAccum()
        return self.tools[name]


# ---------------------------------------------------------------------------
# Public snapshot types (immutable, safe to pass around)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolMetricSnapshot:
    tool_name:           str
    call_count:          int
    avg_latency_ms:      int            # rounded to nearest ms; 0 if no successful calls yet
    failure_count:       int
    success_count:       int            # call_count - failure_count (clamped to 0)
    last_denial_reason:  Optional[str] = None  # Gap 9: last ToolPermissionDenied.error


@dataclass(frozen=True)
class ExecutionMetricsSnapshot:
    execution_id:        str
    total_steps:         int
    fallbacks_triggered: int
    fallback_details:    List[dict]      # Gap 8: per-fallback provider detail
    termination_reason:  Optional[str]
    error_message:       Optional[str]   # full exception text; None on success/cancel
    duration_ms:         Optional[int]
    subagent_calls:      int             # Gap 10: SubagentCompleted count
    subagent_failures:   int             # Gap 10: SubagentCompleted where success=False
    tool_stats:          Dict[str, ToolMetricSnapshot]  # keyed by tool_name


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class MetricsCollector:
    """
    Thread-safe in-process metrics accumulator.

    Registered with the global event_bus singleton via `register()`.
    Call `pop_snapshot(execution_id)` to retrieve and clear the accumulated
    data for an execution (typically done in worker.py after run_execution).

    Call `get_snapshot(execution_id)` for a non-destructive read (e.g. for
    a running-execution partial response from the metrics endpoint).
    """

    def __init__(self) -> None:
        self._store: Dict[str, _ExecAccum] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create(self, execution_id: str) -> _ExecAccum:
        """Must be called under self._lock."""
        if execution_id not in self._store:
            self._store[execution_id] = _ExecAccum()
        return self._store[execution_id]

    def _build_snapshot(self, execution_id: str, accum: _ExecAccum) -> ExecutionMetricsSnapshot:
        """Must be called under self._lock."""
        tool_stats: Dict[str, ToolMetricSnapshot] = {}
        for name, t in accum.tools.items():
            call_count  = t.call_count
            failures    = t.failure_count
            successes   = max(0, call_count - failures)
            # avg latency: uses total latency over ALL calls (including failed)
            avg_ms = (t.total_latency_ms // call_count) if call_count > 0 else 0
            tool_stats[name] = ToolMetricSnapshot(
                tool_name=name,
                call_count=call_count,
                avg_latency_ms=avg_ms,
                failure_count=failures,
                success_count=successes,
                last_denial_reason=t.last_denial_reason,
            )
        return ExecutionMetricsSnapshot(
            execution_id=execution_id,
            total_steps=accum.total_steps,
            # Derived from fallback_details' length rather than the
            # separately-tracked accum.fallbacks counter: that counter only
            # increments on the legacy FallbackTriggered event, which
            # nothing in the codebase publishes anymore (V6.5's
            # _gen_with_fallback publishes the richer ProviderFallbackTriggered
            # instead, which populates fallback_details below) -- using the
            # same source for both keeps them impossible to drift apart.
            fallbacks_triggered=len(accum.fallback_details),
            fallback_details=list(accum.fallback_details),  # copy to keep frozen
            termination_reason=accum.termination_reason,
            error_message=accum.error_message,
            duration_ms=accum.duration_ms,
            subagent_calls=accum.subagent_calls,
            subagent_failures=accum.subagent_failures,
            tool_stats=tool_stats,
        )

    # ------------------------------------------------------------------
    # Event handlers (synchronous — called by event_bus.publish)
    # ------------------------------------------------------------------

    def on_tool_called(self, e: ToolCalled) -> None:
        if not e.execution_id:
            return
        with self._lock:
            accum = self._get_or_create(e.execution_id)
            tool  = accum.tool(e.tool_name)
            if e.attempt == 1:
                # First attempt = new distinct tool invocation = new step
                tool.call_count += 1
                accum.total_steps += 1
            # Retries (attempt > 1) do not create new steps or call_counts

    def on_tool_result(self, e: ToolResult) -> None:
        if not e.execution_id:
            return
        with self._lock:
            accum = self._get_or_create(e.execution_id)
            tool  = accum.tool(e.tool_name)
            tool.total_latency_ms += e.latency_ms
            if not e.success:
                tool.failure_count += 1

    def on_tool_permission_denied(self, e: ToolPermissionDenied) -> None:
        if not e.execution_id:
            return
        with self._lock:
            accum = self._get_or_create(e.execution_id)
            tool  = accum.tool(e.tool_name)
            tool.failure_count += 1
            tool.last_denial_reason = e.error  # Gap 9: preserve denial message

    def on_fallback_triggered(self, e: FallbackTriggered) -> None:
        if not e.execution_id:
            return
        with self._lock:
            self._get_or_create(e.execution_id).fallbacks += 1

    def on_provider_fallback_triggered(self, e: ProviderFallbackTriggered) -> None:
        """Gap 8: capture per-fallback provider/model/reason detail."""
        if not e.execution_id:
            return
        with self._lock:
            accum = self._get_or_create(e.execution_id)
            accum.fallback_details.append({
                "failed_provider":   e.failed_provider,
                "failed_model":      e.failed_model,
                "failure_reason":    e.failure_reason,
                "fallback_provider": e.fallback_provider,
                "fallback_model":    e.fallback_model,
                "fallback_index":    e.fallback_index,
            })

    def on_subagent_completed(self, e: SubagentCompleted) -> None:
        """Gap 10: track subagent call counts within this execution."""
        if not e.execution_id:
            return
        with self._lock:
            accum = self._get_or_create(e.execution_id)
            accum.subagent_calls += 1
            if not e.success:
                accum.subagent_failures += 1

    def on_step_completed(self, e: StepCompleted) -> None:
        # Reserved: no-op until AgentEngine publishes StepCompleted events.
        pass

    def on_execution_completed(self, e: ExecutionCompleted) -> None:
        with self._lock:
            accum = self._get_or_create(e.execution_id)
            accum.duration_ms = e.duration_ms
            accum.termination_reason = "success"

    def on_execution_failed(self, e: ExecutionFailed) -> None:
        with self._lock:
            accum = self._get_or_create(e.execution_id)
            accum.duration_ms = e.duration_ms
            accum.termination_reason = e.error_type or "error"
            accum.error_message = e.error_message or None

    def on_execution_cancelled(self, e: ExecutionCancelled) -> None:
        with self._lock:
            accum = self._get_or_create(e.execution_id)
            accum.duration_ms = e.duration_ms
            accum.termination_reason = e.cancel_reason or "cancelled"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_snapshot(self, execution_id: str) -> Optional[ExecutionMetricsSnapshot]:
        """
        Non-destructive read. Returns the current snapshot without clearing.
        Returns None if no data is present for this execution_id.
        Safe to call at any time (including during a running execution).
        """
        with self._lock:
            accum = self._store.get(execution_id)
            if not accum:
                return None
            return self._build_snapshot(execution_id, accum)

    def pop_snapshot(self, execution_id: str) -> Optional[ExecutionMetricsSnapshot]:
        """
        Destructive read. Returns the snapshot AND clears the accumulator.
        Call exactly once per execution, after run_execution() returns,
        to get the final data before persisting to DB.
        Returns None if no data is present (e.g. execution had no events).
        """
        with self._lock:
            accum = self._store.pop(execution_id, None)
            if not accum:
                return None
            return self._build_snapshot(execution_id, accum)

    def clear(self, execution_id: str) -> None:
        """Discard all accumulated data for execution_id (e.g. after a failed flush)."""
        with self._lock:
            self._store.pop(execution_id, None)

    def active_execution_ids(self) -> List[str]:
        """Return all execution_ids currently being tracked (for diagnostics)."""
        with self._lock:
            return list(self._store.keys())

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, bus=event_bus) -> None:
        """Subscribe all handlers to the given event bus."""
        bus.subscribe(ToolCalled,                  self.on_tool_called)
        bus.subscribe(ToolResult,                  self.on_tool_result)
        bus.subscribe(ToolPermissionDenied,        self.on_tool_permission_denied)
        bus.subscribe(FallbackTriggered,           self.on_fallback_triggered)
        bus.subscribe(ProviderFallbackTriggered,   self.on_provider_fallback_triggered)  # Gap 8
        bus.subscribe(StepCompleted,               self.on_step_completed)
        bus.subscribe(ExecutionCompleted,          self.on_execution_completed)
        bus.subscribe(ExecutionFailed,             self.on_execution_failed)
        bus.subscribe(ExecutionCancelled,          self.on_execution_cancelled)
        bus.subscribe(SubagentCompleted,           self.on_subagent_completed)           # Gap 10


# ---------------------------------------------------------------------------
# Module-level singleton — import and use this everywhere
# ---------------------------------------------------------------------------

metrics_collector = MetricsCollector()
metrics_collector.register()
