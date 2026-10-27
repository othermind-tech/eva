import json
import time
import asyncio
from datetime import datetime, timezone
from logging import getLogger
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from config.settings import SSE_STALE_POLL_SECONDS
from core.execution.redis_registry import redis_execution_registry
from core.execution.models import ExecutionStatus

# Maximum wall-clock seconds the SSE generator will wait for an execution
# whose metadata has disappeared from the broker (e.g. it never started).
_MAX_STALE_POLL_SECONDS = SSE_STALE_POLL_SECONDS

logger = getLogger(__name__)
router = APIRouter()


# ── B-008: SSE-specific authentication helper ─────────────────────────────────

async def _resolve_sse_auth(request: Request, sse_token: Optional[str]) -> str:
    """
    Authenticate an SSE connection.  Two paths are accepted:

    1. ?sse_token=<uuid>  — short-lived (30 s), single-use token obtained via
       POST /auth/sse-token.  Preferred for browser EventSource which cannot
       send custom headers.  The token is deleted on first use so even if it
       appears in logs it cannot be reused.  (B-008)

    2. Authorization: Bearer <jwt>  — standard long-lived JWT.  Kept for
       backward compatibility with non-browser clients (curl, API tests)
       that CAN set the Authorization header.

    Returns the authenticated user_id string.
    Raises HTTPException(401) if neither path succeeds.
    """
    from core.broker import local_broker as _broker
    adapter = _broker.client

    # ── Path 1: Short-lived SSE token ────────────────────────────────────────
    if sse_token:
        key = f"sse_token:{sse_token}"
        user_id = await adapter.get(key)

        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired SSE token — call POST /auth/sse-token to get a new one",
            )

        # Single-use: delete immediately after validation
        await adapter.delete(key)
        logger.debug(
            "SSE auth via sse_token",
            extra={"user_id": user_id, "token_prefix": sse_token[:8]},
        )
        return user_id

    # ── Path 2: JWT via Authorization header ─────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    jwt_token: Optional[str] = None

    if auth_header.startswith("Bearer "):
        jwt_token = auth_header[7:]

    if not jwt_token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required — provide ?sse_token= or Authorization: Bearer <token>",
        )

    # Validate JWT
    from jose import JWTError
    from core.auth.security import decode_access_token, session_matches_user
    from core.db.session import AsyncSessionLocal
    from core.db.models import AuthSession

    try:
        payload = decode_access_token(jwt_token)
        user_id = payload.get("sub")
        session_id = payload.get("sid")
        if not user_id or not session_id:
            raise HTTPException(status_code=401, detail="Invalid token claims")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    async with AsyncSessionLocal() as db:
        auth_session = await db.get(AuthSession, session_id)

    # The session must exist, belong to the token's user, and not be revoked.
    if not session_matches_user(auth_session, user_id) or auth_session.revoked:
        raise HTTPException(status_code=401, detail="Session expired or revoked")

    expires_at = auth_session.expires_at
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Session expired")

    logger.debug("SSE auth via JWT", extra={"user_id": user_id})
    return user_id


# ── SSE endpoint ─────────────────────────────────────────────────────────────

@router.get("/eva_response", response_class=StreamingResponse)
async def eva_response_endpoint(
    request: Request,
    conversation_id: str,
    sse_token: Optional[str] = None,   # B-008: short-lived token for browser EventSource
):
    """
    SSE endpoint to stream responses for an execution.

    Authentication (B-008):
      Option A (browser EventSource): pass ?sse_token=<uuid> obtained from
        POST /auth/sse-token.  The token expires in 30 s and is single-use.
      Option B (API clients): include Authorization: Bearer <jwt> header.

    LocalBroker-backed streaming:
    - Zero-copy: chunks are delivered directly from LocalShadowBuffer
    - Real-time follow via asyncio.Event (no polling sleep)
    - Replay after completion: buffer persists until cleanup task evicts it
    - Cancel / fail detection via execution hash in LocalRedisAdapter
    """
    user_id = await _resolve_sse_auth(request, sse_token)

    # LocalBroker always returns an adapter — connect() never fails
    client = await redis_execution_registry.connect()

    # ------------------------------------------------------------
    # Find execution by conversation lookup
    # ------------------------------------------------------------
    execution_id = await client.get(f"conversation:{conversation_id}")

    logger.debug(
        "SSE open — conversation→execution lookup",
        extra={
            "conversation_id": conversation_id,
            "found_execution_id": execution_id,
            "has_execution": bool(execution_id),
        },
    )

    if not execution_id:
        raise HTTPException(
            status_code=404,
            detail=f"No execution found for conversation_id={conversation_id}",
        )

    execution_data = await client.hgetall(f"execution:{execution_id}")

    logger.debug(
        "SSE open — execution hash at open time",
        extra={
            "execution_id": execution_id,
            "conversation_id": conversation_id,
            "status_at_open": execution_data.get("status") if execution_data else None,
            "created_at": execution_data.get("created_at") if execution_data else None,
            "execution_mode": execution_data.get("execution_mode") if execution_data else None,
            "hash_empty": not bool(execution_data),
        },
    )

    if not execution_data:
        raise HTTPException(
            status_code=404,
            detail=f"Execution metadata not found for conversation_id={conversation_id}",
        )

    if execution_data.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    logger.info(
        "SSE connection opened",
        extra={
            "execution_id": execution_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
        },
    )

    def _normalize_event_name(raw) -> str:
        """
        Coerce a payload 'type' value to a plain string.

        Chunks stored in LocalShadowBuffer carry their 'type' field as the
        original Python value set by the runner — which may be a ChunkType
        enum instance (a str subclass).  In Python < 3.12 f-string formatting
        of a str-enum produces "ChunkType.STEP_PROGRESS" rather than the
        member value "step_progress".  Calling .value forces the primitive
        string regardless of Python version.
        """
        if hasattr(raw, "value"):
            return str(raw.value)
        return str(raw) if raw is not None else "text"

    def _serialize_payload(payload: dict) -> str:
        """json.dumps with a fallback encoder for any surviving enum objects."""
        def _default(obj):
            if hasattr(obj, "value"):
                return obj.value
            return str(obj)
        return json.dumps(payload, default=_default)

    async def event_generator():
        from core.broker import local_broker as _broker
        buf = _broker.get_shadow_buffer(execution_id)

        last_ping = time.time()
        stale_since: Optional[float] = None
        index = 0
        _last_polled_status: Optional[str] = None
        _open_time: float = time.time()

        try:
            # Initial handshake
            yield (
                "event: init\n"
                f"data: {json.dumps({'conversation_id': conversation_id})}\n\n"
            )

            while True:
                # ── Client-disconnect check ──────────────────────────────
                if await request.is_disconnected():
                    logger.info(
                        "SSE client disconnected — closing generator",
                        extra={"execution_id": execution_id},
                    )
                    return

                now = time.time()

                # Heartbeat ping
                if now - last_ping > 10:
                    yield "event: ping\ndata: {}\n\n"
                    last_ping = now

                # ── Drain available chunks from LocalShadowBuffer ────────
                new_chunks = buf.chunks_from(index)
                if new_chunks:
                    for payload in new_chunks:
                        payload = dict(payload)
                        payload["timestamp"] = now
                        payload["conversation_id"] = conversation_id
                        event_name = _normalize_event_name(payload.get("type", "text"))
                        payload["type"] = event_name  # normalise in-place before serialisation
                        yield (
                            f"event: {event_name}\n"
                            f"data: {_serialize_payload(payload)}\n\n"
                        )
                    index += len(new_chunks)

                # ── Check execution status AFTER replay ──────────────────
                status_raw = await client.hget(
                    f"execution:{execution_id}", "status"
                )

                # log every status transition
                if status_raw != _last_polled_status:
                    logger.debug(
                        "SSE status transition",
                        extra={
                            "execution_id": execution_id,
                            "conversation_id": conversation_id,
                            "prev_status": _last_polled_status,
                            "new_status": status_raw,
                            "elapsed_ms": int((time.time() - _open_time) * 1000),
                            "chunks_replayed": index,
                        },
                    )
                    _last_polled_status = status_raw

                # ── Stale-execution guard ────────────────────────────────
                # If the execution hash is gone from the broker (e.g. it
                # never started) we give up after _MAX_STALE_POLL_SECONDS.
                if status_raw is None:
                    if stale_since is None:
                        stale_since = now
                    elif now - stale_since > _MAX_STALE_POLL_SECONDS:
                        logger.warning(
                            "Execution key gone from broker — closing SSE stream",
                            extra={"execution_id": execution_id},
                        )
                        yield (
                            "event: error\n"
                            f"data: {json.dumps({'message': 'Execution record expired'})}\n\n"
                        )
                        yield "event: done\ndata: {}\n\n"
                        return
                else:
                    stale_since = None  # reset on any valid status

                # ── Terminal-state handling ──────────────────────────────
                if status_raw in (
                    ExecutionStatus.COMPLETED.value,
                    ExecutionStatus.FAILED.value,
                    ExecutionStatus.CANCELLED.value,
                    ExecutionStatus.ABORTED.value,
                ):
                    # Drain any chunks that arrived since last check
                    remaining = buf.chunks_from(index)
                    for payload in remaining:
                        payload = dict(payload)
                        payload["timestamp"] = now
                        payload["conversation_id"] = conversation_id
                        event_name = _normalize_event_name(payload.get("type", "text"))
                        payload["type"] = event_name  # normalise in-place before serialisation
                        yield (
                            f"event: {event_name}\n"
                            f"data: {_serialize_payload(payload)}\n\n"
                        )
                    index += len(remaining)

                    logger.info(
                        "Execution terminal state detected in SSE",
                        extra={
                            "execution_id": execution_id,
                            "status": status_raw,
                        },
                    )

                    if status_raw == ExecutionStatus.CANCELLED.value:
                        # V6.15.3: the runner persists whatever token usage
                        # was accumulated before cancellation landed to this
                        # broker field (see core/execution/runner.py's and
                        # pev_runner.py's cancellation paths) -- it can't
                        # travel through the normal shadow-buffer chunk
                        # replay because shadow.clear() (called right before
                        # the terminal status is set) would silently drop it.
                        # Emit it here as a real `event: usage` frame so the
                        # frontend's existing usage handler picks it up
                        # exactly like a normal completion would.
                        _usage_json = await client.hget(
                            f"execution:{execution_id}", "usage_json"
                        )
                        if _usage_json:
                            try:
                                _usage_payload = json.loads(_usage_json)
                                _usage_payload["timestamp"] = now
                                _usage_payload["conversation_id"] = conversation_id
                                yield (
                                    "event: usage\n"
                                    f"data: {_serialize_payload(_usage_payload)}\n\n"
                                )
                            except (ValueError, TypeError) as _usage_parse_exc:
                                logger.debug(
                                    "Failed to parse persisted usage_json (non-fatal)",
                                    extra={
                                        "execution_id": execution_id,
                                        "error": str(_usage_parse_exc),
                                    },
                                )

                        yield (
                            "event: cancelled\n"
                            f"data: {json.dumps({'reason': 'user'})}\n\n"
                        )
                    elif status_raw == ExecutionStatus.FAILED.value:
                        yield (
                            "event: error\n"
                            f"data: {json.dumps({'message': 'Execution failed'})}\n\n"
                        )

                    yield (
                        "event: finish\n"
                        f"data: {json.dumps({'status': status_raw.lower()})}\n\n"
                    )
                    yield "event: done\ndata: {}\n\n"
                    return

                # ── Wait efficiently for the next chunk ──────────────────
                # wait_for_more() uses asyncio.Event so we wake the instant
                # the runner appends a chunk — no busy-polling.  The 5-second
                # timeout keeps heartbeat and disconnect checks alive.
                await buf.wait_for_more(min_count=index, timeout=5.0)

        except Exception as exc:
            logger.error(
                "SSE streaming error",
                exc_info=exc,
                extra={"execution_id": execution_id},
            )
            yield (
                "event: error\n"
                f"data: {json.dumps({'message': 'Streaming failure'})}\n\n"
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
