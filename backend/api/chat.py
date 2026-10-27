import datetime
from fastapi import APIRouter, Depends, HTTPException
from logging import getLogger

from api.schemas.requests import ChatRequest
from api.schemas.responses import ChatAcceptedResponse
from core.memory.conversation_journal import ConversationJournal

from core.execution import execution_registry
from core.execution.models import ExecutionStatus
from core.execution.redis_registry import redis_execution_registry
from core.execution.intent_router import intent_router

# DB
from core.db.session import AsyncSessionLocal
from core.db import repository as DBRepoModule
from core.db.repository import ProjectRepo
from api.deps import get_db

from core.auth.dependencies import get_current_auth
from core.hooks import HookRunner
from config.settings import get_user_session
from utils.workspace import get_user_workspace

# Statuses that block a new chat message from being accepted for an
# existing conversation.  Allowing a new message while an execution is
# already in one of these states would create two concurrent executions
# writing to the same message history.
_ACTIVE_STATUSES = {
    ExecutionStatus.PENDING.value,
    ExecutionStatus.RUNNING.value,
    ExecutionStatus.AWAITING_APPROVAL.value,
}

logger = getLogger(__name__)
router = APIRouter()


# get_db lives in api/deps.py (imported above) — no local definition needed


@router.post("/chat", response_model=ChatAcceptedResponse)
async def chat_endpoint(
    request: ChatRequest,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    """
    Submit-only chat endpoint.

    Responsibilities:
    - Validate conversation
    - Persist user message
    - Create execution record
    - Enqueue job
    - Return 202

    No execution logic here.
    """

    user_id = auth["user_id"]
    session_id = auth["session_id"]

    logger.info(
        "Chat request received",
        extra={
            "user_id": user_id,
            "session_id": session_id,
            "conversation_id": request.conversation_id,
            "message_length": len(request.message),
        },
    )

    # No hardcoded provider/model fallback (V6.4/2026-07-27): EVA has no
    # default model of its own -- an unconfigured session is a real 424,
    # not a guess. Checked here, synchronously, before any work is done,
    # so the caller gets an immediate, structured rejection instead of
    # discovering the failure later via polling/SSE. error_code is stable
    # across endpoints for the frontend to key off; message is for humans.
    _session = get_user_session(user_id)
    if not _session.get("provider") or not _session.get("model"):
        raise HTTPException(
            status_code=424,
            detail={
                "error_code": "provider_not_configured",
                "message": (
                    "No provider/model configured for this session. "
                    "Configure one via POST /set-session before sending messages."
                ),
            },
        )

    # V6.59 Phase 5: the saved provider/model can have gone stale since
    # /set-session (key removed, model unusable). Reject synchronously with the
    # reason, rather than accepting the message and failing in the background.
    # A usable fallback keeps the request alive: the worker promotes it.
    from core.providers.usability import plan_run
    _plan = plan_run(_session["provider"], _session["model"], _session.get("fallback_chain"))
    if not _plan.ok:
        _problem = _plan.primary_problem
        raise HTTPException(
            status_code=_problem.status_code,
            detail={
                "error_code": "provider_not_usable",
                "reason": _problem.reason,
                "provider": _problem.provider,
                "model": _problem.model,
                "message": _plan.failure_message(),
            },
        )

    # ------------------------------------------------------------
    # 1. Validate or Create Conversation
    # ------------------------------------------------------------
    if request.conversation_id:
        logger.debug(
            "Retrieving existing conversation",
            extra={"conversation_id": request.conversation_id},
        )

        conv = await DBRepoModule.DBRepo.get_conversation(
            db, request.conversation_id
        )

        if conv is None or conv.user_id != user_id:
            logger.warning(
                "Invalid conversation access attempt",
                extra={
                    "conversation_id": request.conversation_id,
                    "user_id": user_id,
                },
            )
            raise HTTPException(
                status_code=400,
                detail="Invalid conversation_id",
            )
    else:
        logger.debug(
            "Creating new conversation",
            extra={"user_id": user_id},
        )

        # Guard: reject if the target project is paused or archived.
        # active  → allow (full context injection + artifact updater)
        # paused  → block (linking temporarily disabled while project is on hold)
        # archived → block (project is closed; no new items allowed)
        if request.project_id:
            proj = await ProjectRepo.get(db, project_id=request.project_id, user_id=user_id)
            if proj is None:
                raise HTTPException(status_code=404, detail="Project not found.")
            if proj.status == "archived":
                raise HTTPException(
                    status_code=403,
                    detail="Cannot start a conversation in an archived project.",
                )
            if proj.status == "paused":
                raise HTTPException(
                    status_code=403,
                    detail="Project is paused. Resume it (set status=active) before linking new conversations.",
                )

        conv = await DBRepoModule.DBRepo.create_conversation(
            db,
            user_id=user_id,
            session_id=session_id,
            project_id=request.project_id or None,
        )

    conversation_id = conv.id

    logger.info(
        "Conversation ready",
        extra={
            "conversation_id": conversation_id,
            "user_id": user_id,
        },
    )

    # ------------------------------------------------------------
    # 1b. Guard: reject if the conversation's project is paused or archived.
    # The new-conversation guard above (1a) only runs for new conversations.
    # This guard handles existing conversations — if the project was paused or
    # archived after the conversation was created, no new messages are allowed.
    # ------------------------------------------------------------
    if request.conversation_id and getattr(conv, "project_id", None):
        _proj_chat = await ProjectRepo.get(
            db, project_id=conv.project_id, user_id=user_id
        )
        if _proj_chat is not None:
            if _proj_chat.status == "archived":
                raise HTTPException(
                    status_code=403,
                    detail="This project is archived. No new messages are allowed.",
                )
            if _proj_chat.status == "paused":
                raise HTTPException(
                    status_code=403,
                    detail="This project is paused. Resume it (set status=active) before sending new messages.",
                )

    # ------------------------------------------------------------
    # 1c. Guard: reject if an execution is already active for this
    #     conversation.  Allowing a second message while the first is
    #     PENDING / RUNNING / AWAITING_APPROVAL would create two
    #     concurrent executions writing to the same history.
    # ------------------------------------------------------------
    if request.conversation_id:
        # Only existing conversations can have an active execution.
        try:
            broker_client = await redis_execution_registry.connect()
            active_execution_id = await broker_client.get(
                f"conversation:{conversation_id}"
            )
            if active_execution_id:
                status_raw = await broker_client.hget(
                    f"execution:{active_execution_id}", "status"
                )
                if status_raw in _ACTIVE_STATUSES:
                    logger.warning(
                        "Chat rejected: execution already active for conversation",
                        extra={
                            "conversation_id": conversation_id,
                            "active_execution_id": active_execution_id,
                            "status": status_raw,
                            "user_id": user_id,
                        },
                    )
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"A response is already in progress for this conversation "
                            f"(status: {status_raw}). "
                            "Please wait for it to complete or cancel it before sending "
                            "a new message."
                        ),
                    )
        except HTTPException:
            raise  # re-raise 409 as-is
        except Exception as exc:
            # Non-fatal — log and allow the request through.
            logger.warning(
                "Could not check active execution status — allowing chat through",
                exc_info=exc,
                extra={"conversation_id": conversation_id},
            )

    # ------------------------------------------------------------
    # 1c. UserPromptSubmit hooks (B-064) — blocking, can deny/modify prompt
    # ------------------------------------------------------------
    _user_session = get_user_session(user_id)
    _hook_defs = _user_session.get("tool_hooks") or []
    if _hook_defs:
        _hook_resp = await HookRunner.fire_prompt_submit(
            prompt=request.message,
            hook_definitions=_hook_defs,
            user_id=user_id,
            session_id=session_id,
            workspace_root=get_user_workspace(user_id),
        )
        if _hook_resp.blocked:
            logger.warning(
                "UserPromptSubmit hook blocked message",
                extra={"user_id": user_id, "reason": _hook_resp.reason},
            )
            raise HTTPException(
                status_code=403,
                detail=_hook_resp.reason or "Message blocked by hook policy.",
            )

    # ------------------------------------------------------------
    # 1d. PII Redaction (S-03) — opt-in. Applied after the block/allow
    # decision above (a policy hook should see what the user actually
    # typed) but before persistence, so a redacted message is what's
    # stored, journaled, routed, and sent to the LLM -- nothing sensitive
    # touches disk or a log file, not just "what leaves for the LLM
    # provider". Off by default; unaffected callers pay zero cost.
    # ------------------------------------------------------------
    if _user_session.get("pii_redaction_enabled"):
        from core.security.pii_redactor import redact_pii  # noqa: PLC0415
        _redacted_message, _pii_count = redact_pii(request.message)
        if _pii_count > 0:
            logger.info(
                "PII redacted from user message",
                extra={"user_id": user_id, "conversation_id": conversation_id,
                       "count": _pii_count},
            )
            request.message = _redacted_message

    # ------------------------------------------------------------
    # 2. Persist User Message Immediately
    # ------------------------------------------------------------
    try:
        await DBRepoModule.DBRepo.add_message(
            db=db,
            conversation_id=conversation_id,
            role="user",
            content=request.message,
            chunked=False,
        )

        logger.info(
            "User message persisted",
            extra={
                "conversation_id": conversation_id,
                "user_id": user_id,
            },
        )

        # Journal: user message (sync, never raises)
        ConversationJournal.log(user_id, conversation_id, {
            "type": "user_message",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "content": request.message,
        })

    except Exception as exc:
        logger.error(
            "Failed to persist user message",
            exc_info=exc,
            extra={
                "conversation_id": conversation_id,
                "user_id": user_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to persist user message",
        )

    # ------------------------------------------------------------
    # 3. Route Intent (3-layer: session override → heuristics → LLM)
    # ------------------------------------------------------------
    execution_mode = (await intent_router.route(
        message=request.message,
        user_id=user_id,
    )).value   # "conversational" | "plan_execute"

    logger.info(
        "Intent routing complete",
        extra={
            "conversation_id": conversation_id,
            "user_id": user_id,
            "execution_mode": execution_mode,
            "message_length": len(request.message),
        },
    )

    # ------------------------------------------------------------
    # Snapshot the current conversation→execution mapping in the broker
    # BEFORE create_execution_record overwrites it.
    # ------------------------------------------------------------
    try:
        _diag_rc = await redis_execution_registry.connect()
        _prev_exec_id = await _diag_rc.get(f"conversation:{conversation_id}")
        _prev_status = None
        if _prev_exec_id:
            _prev_status = await _diag_rc.hget(f"execution:{_prev_exec_id}", "status")
        logger.debug(
            "chat pre-create — conversation→execution broker snapshot",
            extra={
                "conversation_id": conversation_id,
                "prev_execution_id": _prev_exec_id,
                "prev_execution_status": _prev_status,
            },
        )
    except Exception as _snap_exc:
        logger.debug("chat pre-create broker snapshot failed (non-fatal): %s", _snap_exc)

    # ------------------------------------------------------------
    # 4. Create Execution Record (No Execution Start)
    # ------------------------------------------------------------
    execution = await execution_registry.create_execution_record(
        conversation_id=conversation_id,
        user_id=user_id,
        session_id=session_id,
        execution_mode=execution_mode,
        initial_message=request.message,
    )

    logger.info(
        "Execution record created",
        extra={
            "execution_id": execution.execution_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "execution_mode": execution_mode,
        },
    )

    # ------------------------------------------------------------
    # 5. Enqueue Job for Worker
    # ------------------------------------------------------------
    await redis_execution_registry.enqueue_job(
        execution.execution_id
    )

    logger.info(
        "Execution job enqueued",
        extra={
            "execution_id": execution.execution_id,
            "conversation_id": conversation_id,
        },
    )

    # ------------------------------------------------------------
    # Confirm the execution_id being returned in the 202.
    # The SSE will open with conversation_id; the broker must map it to THIS id.
    # ------------------------------------------------------------
    logger.debug(
        "chat 202 — execution_id dispatched to client",
        extra={
            "conversation_id": conversation_id,
            "new_execution_id": execution.execution_id,
        },
    )

    # ------------------------------------------------------------
    # 6. Immediate 202 Response
    # ------------------------------------------------------------
    return {
        "conversation_id": conversation_id,
        "execution_id": execution.execution_id,
        "status": "accepted",
        "execution_mode": execution_mode,
    }
