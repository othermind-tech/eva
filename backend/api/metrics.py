"""
api/metrics.py

Usage Metrics API (B-073) — session aggregation and user-level summaries.

Endpoints
─────────
GET /metrics/session?conversation_id=<id>
    Aggregate all execution_metrics rows for one conversation.
    Captures parent + inlined subagent tokens (~90%+ of true spend).

GET /metrics/usage/summary
    Aggregate all-time metrics for the authenticated user across every
    conversation and daemon execution.

GET /metrics/usage?start=<iso>&end=<iso>
    Same as /metrics/usage/summary but scoped to the given UTC time window.
    Both *start* and *end* are optional; omit either to make the window open-ended.

All endpoints require JWT authentication.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.auth.dependencies import get_current_auth
from core.metrics.service import MetricsService

router = APIRouter(prefix="/metrics", tags=["metrics"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class ProviderBreakdownItem(BaseModel):
    """Token and cost breakdown for a single provider."""
    provider:        str
    execution_count: int
    total_tokens:    int
    total_cost_usd:  Optional[float] = None


class MetricsAggregateResponse(BaseModel):
    """
    Aggregated execution metrics over a set of executions.

    Fields
    ──────
    execution_count     : number of execution rows included in the aggregate
    prompt_tokens       : sum of all prompt (input) tokens
    completion_tokens   : sum of all completion (output) tokens
    reasoning_tokens    : sum of all reasoning/chain-of-thought tokens (a subset
                          of completion_tokens)
    cached_tokens       : sum of prompt tokens served from the provider's prompt
                          cache (a subset of prompt_tokens)
    total_tokens        : prompt + completion
    total_cost_usd      : sum of computed_cost_usd for executions that have
                          cost data; None when execution_count == 0
    cost_is_partial     : True when ≥1 execution had null cost (e.g. Ollama Cloud
                          subscription, local Ollama inference, or metrics gap)
    tool_calls_count    : total distinct tool invocations across all executions
                          (includes child-agent tool calls that ran under the
                          parent execution context)
    duration_total_ms   : sum of wall-clock durations; None when none are recorded
    provider_breakdown  : per-provider token and cost totals, sorted by
                          execution_count descending
    """
    execution_count:   int
    prompt_tokens:     int
    completion_tokens: int
    reasoning_tokens:  int
    cached_tokens:     int = 0
    total_tokens:      int
    total_cost_usd:    Optional[float] = None
    cost_is_partial:   bool = False
    tool_calls_count:  int
    duration_total_ms: Optional[int] = None
    provider_breakdown: List[ProviderBreakdownItem] = []


def _build_response(data: dict) -> MetricsAggregateResponse:
    """Convert the repo aggregate dict to a Pydantic response model."""
    breakdown = [
        ProviderBreakdownItem(
            provider=p["provider"],
            execution_count=p["execution_count"],
            total_tokens=p["total_tokens"],
            total_cost_usd=p["total_cost_usd"],
        )
        for p in data.get("provider_breakdown", [])
    ]
    return MetricsAggregateResponse(
        execution_count=data["execution_count"],
        prompt_tokens=data["prompt_tokens"],
        completion_tokens=data["completion_tokens"],
        reasoning_tokens=data["reasoning_tokens"],
        cached_tokens=data.get("cached_tokens", 0),
        total_tokens=data["total_tokens"],
        total_cost_usd=data["total_cost_usd"],
        cost_is_partial=data["cost_is_partial"],
        tool_calls_count=data["tool_calls_count"],
        duration_total_ms=data["duration_total_ms"],
        provider_breakdown=breakdown,
    )


def _parse_iso(value: Optional[str], param_name: str) -> Optional[datetime]:
    """
    Parse an ISO-8601 datetime string into a timezone-aware UTC datetime.
    Raises HTTP 422 on invalid format.
    """
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Attach UTC if no timezone was given
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid datetime format for '{param_name}'. "
                   f"Expected ISO-8601 (e.g. '2026-05-01T00:00:00Z').",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/session",
    response_model=MetricsAggregateResponse,
    summary="Aggregate metrics for a single conversation",
)
async def get_session_metrics(
    conversation_id: str = Query(..., description="Conversation ID to aggregate"),
    auth=Depends(get_current_auth),
) -> MetricsAggregateResponse:
    """
    Return aggregated execution metrics for *conversation_id*.

    Includes all executions (completed, failed, cancelled, aborted) that are
    associated with this conversation. Subagent token usage is captured within
    the parent execution's row (~90%+ accuracy).
    """
    data = await MetricsService.for_conversation(conversation_id)
    return _build_response(data)


@router.get(
    "/usage/summary",
    response_model=MetricsAggregateResponse,
    summary="All-time usage summary for the authenticated user",
)
async def get_usage_summary(
    auth=Depends(get_current_auth),
) -> MetricsAggregateResponse:
    """
    Return all-time aggregated metrics for the authenticated user across
    every conversation and daemon execution — no time filter applied.
    """
    user_id: str = auth["user_id"]
    data = await MetricsService.for_user(user_id)
    return _build_response(data)


@router.get(
    "/usage",
    response_model=MetricsAggregateResponse,
    summary="Time-range usage summary for the authenticated user",
)
async def get_usage_by_range(
    start: Optional[str] = Query(
        None,
        description="Start of time range (ISO-8601 UTC, inclusive). "
                    "Omit for open-ended start.",
    ),
    end: Optional[str] = Query(
        None,
        description="End of time range (ISO-8601 UTC, inclusive). "
                    "Omit for open-ended end.",
    ),
    auth=Depends(get_current_auth),
) -> MetricsAggregateResponse:
    """
    Return aggregated metrics for the authenticated user within the given
    UTC time window. Both *start* and *end* are optional; omit either to
    make the window open-ended on that side.

    Typical use: day/week/month billing summaries.
    """
    user_id: str = auth["user_id"]
    start_dt = _parse_iso(start, "start")
    end_dt   = _parse_iso(end,   "end")

    if start_dt and end_dt and start_dt > end_dt:
        raise HTTPException(
            status_code=422,
            detail="'start' must be before 'end'.",
        )

    data = await MetricsService.for_user(user_id, start=start_dt, end=end_dt)
    return _build_response(data)
