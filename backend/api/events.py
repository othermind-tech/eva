# api/events.py
"""
B-051: User-Facing Event SSE Feed — LocalBroker edition.

GET /events/stream
    Opens a Server-Sent Events stream for the authenticated user.

    Receives events from LocalBroker via a per-connection asyncio.Queue —
    delivery is instantaneous (no poll delay).

Authentication (same as /executions/{id}/stream):
    1. ?sse_token=<uuid>           browser EventSource (can't set headers)
    2. Authorization: Bearer <jwt> standard clients

Event data format (JSON string in each SSE "data:" line, tagged with a
matching "event: <name>" line so EventSource.addEventListener(name, ...)
fires correctly):
    {
        "event":        str,    # "execution.completed" | "execution.failed" |
                                # "execution.cancelled" | "execution.aborted" |
                                # "execution.awaiting_wakeup" | "wakeup.resolved" |
                                # (P-19 Phase 3) "daemon.completed" |
                                # "daemon.failed" | "conversation.titled" |
                                # (background-process-supervision Phase 3)
                                # "process.stopped" | "process.crashed"
        "execution_id": str,
        "user_id":      str,
        "daemon_name":  str | null,
        "summary":      str,
        "ts":           float
    }

Heartbeat:
    A comment line (": heartbeat") is sent every 30 s when no events arrive,
    keeping the connection alive through proxies and load balancers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from api.eva_response import _resolve_sse_auth
from config.settings import SSE_HEARTBEAT_SECONDS
from core.broker import local_broker as _broker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["Events"])

_HEARTBEAT_SECONDS = SSE_HEARTBEAT_SECONDS


@router.get(
    "/stream",
    summary="Stream user-facing events as SSE (B-051)",
    response_class=StreamingResponse,
)
async def event_stream(
    request: Request,
    sse_token: Optional[str] = Query(default=None),
):
    """
    Open a long-lived Server-Sent Events connection.

    Stays open until the client disconnects.  Each event published by the
    worker (execution completed/failed, daemon completed/failed, conversation
    titled) is forwarded as an ``event: <name>`` / ``data: <json>`` SSE pair —
    the name is the payload's own "event" field, so a client using
    ``EventSource.addEventListener(name, ...)`` receives it as that named
    type rather than the generic "message" type.

    Heartbeat comment lines are sent every 30 s to prevent proxy timeouts.
    """
    user_id = await _resolve_sse_auth(request, sse_token)

    async def _generator():
        sub = _broker.subscribe_events(user_id)
        last_heartbeat = time.monotonic()

        try:
            while True:
                # Check for client disconnect first.
                if await request.is_disconnected():
                    break

                remaining_hb = _HEARTBEAT_SECONDS - (time.monotonic() - last_heartbeat)
                wait_seconds = max(0.1, min(remaining_hb, 5.0))

                try:
                    payload = await asyncio.wait_for(
                        sub.queue.get(), timeout=wait_seconds
                    )
                    event_name = payload.get("event", "message")
                    yield f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
                    last_heartbeat = time.monotonic()
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("SSE events generator error: %s", exc)
        finally:
            _broker.unsubscribe_events(user_id, sub)
            logger.debug("SSE events: unsubscribed user=%s", user_id)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
