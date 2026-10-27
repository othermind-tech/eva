"""
core/metrics/service.py

MetricsService — aggregation layer for execution metrics (B-073).

Provides session-level and user-level aggregations over the
execution_metrics table. All public methods are coroutines that
open their own DB session — callers do not need to manage sessions.

Response shape (returned as plain dict — API layer converts to Pydantic):
    {
        "execution_count":    int,
        "prompt_tokens":      int,
        "completion_tokens":  int,
        "reasoning_tokens":   int,
        "total_tokens":       int,
        "total_cost_usd":     float | None,   # None when zero executions or all unknown
        "cost_is_partial":    bool,            # True when ≥1 execution had null cost
        "tool_calls_count":   int,             # total_steps (tool invocations) across executions
        "duration_total_ms":  int | None,      # None when all duration_ms are null
        "provider_breakdown": [
            {
                "provider":        str,
                "execution_count": int,
                "total_tokens":    int,
                "total_cost_usd":  float | None,
            },
            ...
        ],
    }

Notes on accuracy for subagent / daemon executions
───────────────────────────────────────────────────
Child AgentEngine runs (via delegate_tool / parallel_delegate_tool) share the
parent execution context — their token usage flows into the parent's
execution_metrics row.  Daemon executions get their own rows (triggered_by
= "daemon").  Aggregating by conversation_id therefore captures:
  - Parent execution tokens (including inlined subagent turns) ≈ 90%+ accuracy
  - Daemon executions if they are associated with the same conversation
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.db.repository import ExecutionMetricsRepo
from core.db.session import AsyncSessionLocal


class MetricsService:
    """Aggregation service over ExecutionMetricsRepo.  All methods are static."""

    @staticmethod
    async def for_conversation(conversation_id: str) -> dict:
        """
        Aggregate all execution_metrics rows for *conversation_id*.

        Returns the standard metrics aggregate dict.
        Raises no exceptions — returns zero counts if no rows exist.
        """
        async with AsyncSessionLocal() as db:
            return await ExecutionMetricsRepo.aggregate_for_conversation(
                db, conversation_id
            )

    @staticmethod
    async def for_user(
        user_id: str,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> dict:
        """
        Aggregate all execution_metrics rows for *user_id*, with optional
        time-range filter (inclusive on both ends).

        *start* and *end* should be timezone-aware UTC datetimes when provided.
        Omit both for an all-time summary.
        """
        async with AsyncSessionLocal() as db:
            return await ExecutionMetricsRepo.aggregate_for_user(
                db, user_id, start=start, end=end
            )

    @staticmethod
    async def for_project(project_id: str) -> dict:
        """
        Aggregate all execution_metrics rows for *project_id*.

        Returns the extended project metrics dict (includes trigger split,
        per-provider+model breakdown, last_active_at).
        Raises no exceptions — returns zero counts if no rows exist.
        """
        async with AsyncSessionLocal() as db:
            return await ExecutionMetricsRepo.aggregate_for_project(db, project_id)
