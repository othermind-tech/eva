# core/events_publisher.py
"""
B-051: User-facing event publisher — LocalBroker edition.

Uses local_broker.publish_event() which delivers directly into per-user
asyncio.Queue objects (zero latency).

All callers (runner.py, daemons/runner.py) are unchanged — same function
signatures, same non-fatal error handling.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from core.broker import local_broker as _broker

logger = logging.getLogger(__name__)


async def publish_user_event(
    user_id: str,
    event: str,
    execution_id: str,
    summary: str = "",
    daemon_name: Optional[str] = None,
) -> None:
    """
    Deliver a user-facing event to all open SSE connections for user_id.
    Non-fatal — any error is swallowed so the caller's hot path is never disrupted.
    """
    try:
        payload = {
            "event":        event,
            "execution_id": execution_id,
            "user_id":      user_id,
            "daemon_name":  daemon_name,
            "summary":      summary,
            "ts":           time.time(),
        }
        await _broker.publish_event(user_id, payload)
    except Exception as exc:
        logger.debug("events_publisher: publish failed (non-fatal): %s", exc)


async def publish_conversation_titled(
    user_id: str,
    conversation_id: str,
    title: str,
) -> None:
    """
    Deliver a conversation.titled event so the frontend can update the
    sidebar title without polling.
    Non-fatal.
    """
    try:
        payload = {
            "event":           "conversation.titled",
            "conversation_id": conversation_id,
            "title":           title,
            "user_id":         user_id,
            "ts":              time.time(),
        }
        await _broker.publish_event(user_id, payload)
    except Exception as exc:
        logger.debug(
            "events_publisher: publish_conversation_titled failed (non-fatal): %s", exc
        )
