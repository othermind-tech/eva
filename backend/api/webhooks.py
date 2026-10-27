# api/webhooks.py
#
# G-009: Daemon Webhook trigger endpoint
#
# Public (no JWT) endpoint that external systems call to trigger a daemon run.
# Auth is via a secret_token embedded in the URL path.
#
# Routes:
#   POST /webhooks/{token}   — trigger the daemon associated with this token

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config.settings import WEBHOOK_RATE_LIMIT_WINDOW_SECONDS, WEBHOOK_RATE_LIMIT_MAX_CALLS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# S-3 — sliding-window rate limiter for webhook triggers, using the shared
# core.rate_limit implementation (extracted here originally; S-02 reuses
# the same mechanism for the auth endpoints). All state is in-process.
# Limits are tunable via config.settings.
from core.rate_limit import SlidingWindowRateLimiter

_webhook_rate_limiter = SlidingWindowRateLimiter(
    window_seconds=WEBHOOK_RATE_LIMIT_WINDOW_SECONDS,
    max_calls=WEBHOOK_RATE_LIMIT_MAX_CALLS,
    message=(
        f"Webhook rate limit exceeded. "
        f"Max {WEBHOOK_RATE_LIMIT_MAX_CALLS} calls per {WEBHOOK_RATE_LIMIT_WINDOW_SECONDS}s window."
    ),
)


class WebhookTriggerResponse(BaseModel):
    triggered:    bool
    daemon_name:  str
    execution_id: Optional[str] = None
    note:         Optional[str] = None


@router.post("/webhooks/{token}", response_model=WebhookTriggerResponse)
async def webhook_trigger(token: str, request: Request):
    """
    Trigger a daemon run via a webhook.

    This endpoint is public — no JWT required.  Authentication is via the
    ``secret_token`` embedded in the URL path.  Keep the URL secret.

    An optional JSON body is forwarded to the daemon as additional context
    in its activation prompt.  Non-JSON bodies are forwarded as plain text.
    """
    # ------------------------------------------------------------------
    # 1. Look up webhook record by secret_token
    # ------------------------------------------------------------------
    from sqlalchemy import select
    from core.db.session import AsyncSessionLocal
    from core.db.models import DaemonWebhook

    # Hash the received token and look up by hash.
    # The DB stores SHA-256(token) — never the plain token.
    # hmac.compare_digest prevents timing side-channel attacks.
    received_hash = hashlib.sha256(token.encode()).hexdigest()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DaemonWebhook).where(DaemonWebhook.secret_token == received_hash)
        )
        webhook = result.scalar_one_or_none()

    if webhook is None or not hmac.compare_digest(webhook.secret_token, received_hash):
        # Return 404 rather than 401 — don't reveal whether the token exists
        raise HTTPException(status_code=404, detail="Webhook not found.")

    # S-3: enforce rate limit per token (only for valid tokens)
    await _webhook_rate_limiter.check_and_record(token)

    user_id     = webhook.user_id
    daemon_name = webhook.daemon_name

    # ------------------------------------------------------------------
    # 2. Look up daemon config
    # ------------------------------------------------------------------
    from core.daemons.registry import daemon_registry
    daemon = await daemon_registry.get(user_id, daemon_name)
    if daemon is None:
        raise HTTPException(
            status_code=404,
            detail=f"Daemon '{daemon_name}' not found (webhook is stale).",
        )

    # ------------------------------------------------------------------
    # 3. Parse request body (best-effort)
    # ------------------------------------------------------------------
    payload_str = ""
    try:
        raw = await request.body()
        if raw:
            import json as _json
            try:
                payload_obj = _json.loads(raw)
                payload_str = _json.dumps(payload_obj, indent=2)
            except Exception:
                payload_str = raw.decode("utf-8", errors="replace")
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 4. Build override trigger
    # ------------------------------------------------------------------
    if payload_str:
        override_trigger = (
            f"You have been triggered by an external webhook.\n"
            f"Webhook payload:\n{payload_str}"
        )
    else:
        override_trigger = "You have been triggered by an external webhook."

    # ------------------------------------------------------------------
    # 5. Pre-generate execution_id and mark daemon as running
    # ------------------------------------------------------------------
    execution_id = str(uuid.uuid4())
    try:
        await daemon_registry.set_lifecycle_status(
            user_id, daemon_name, "running", execution_id=execution_id
        )
    except Exception as exc:
        logger.warning("webhook_trigger: lifecycle update failed: %s", exc)

    # ------------------------------------------------------------------
    # 6. Fire background task
    # ------------------------------------------------------------------
    from core.daemons.runner import run_daemon

    async def _run_and_record() -> None:
        result = await run_daemon(
            daemon=daemon,
            execution_id=execution_id,
            override_trigger=override_trigger,
        )
        success   = result.get("success", False)
        error_msg = result.get("error", "")
        try:
            if success:
                await daemon_registry.record_success(user_id=daemon.user_id, name=daemon.name)
            else:
                await daemon_registry.record_failure(
                    user_id=daemon.user_id, name=daemon.name, error=error_msg
                )
        except Exception as exc:
            logger.error("webhook_trigger: lifecycle update failed: %s", exc)

    asyncio.create_task(_run_and_record(), name=f"webhook-daemon-{daemon_name}")

    # ------------------------------------------------------------------
    # 8. Update webhook stats (fire-and-forget)
    # ------------------------------------------------------------------
    async def _update_webhook_stats() -> None:
        from datetime import datetime, timezone
        async with AsyncSessionLocal() as db:
            result2 = await db.execute(
                select(DaemonWebhook).where(DaemonWebhook.secret_token == received_hash)
            )
            wh = result2.scalar_one_or_none()
            if wh is not None:
                wh.last_triggered_at = datetime.now(timezone.utc)
                wh.trigger_count     = (wh.trigger_count or 0) + 1
                await db.commit()

    asyncio.create_task(_update_webhook_stats(), name=f"webhook-stats-{daemon_name}")

    logger.info(
        "Webhook triggered",
        extra={"daemon_name": daemon_name, "execution_id": execution_id},
    )

    return WebhookTriggerResponse(
        triggered=True,
        daemon_name=daemon_name,
        execution_id=execution_id,
        note=(
            f"Daemon '{daemon_name}' triggered via webhook. "
            f"Poll GET /executions/{execution_id}/status for the outcome."
        ),
    )
