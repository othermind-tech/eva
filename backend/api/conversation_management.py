import asyncio
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from logging import getLogger
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from sqlalchemy import select
from api.schemas.responses import (
    ConversationResponse,
    MessageResponse,
    PaginatedMessagesResponse,
    DeleteConversationResponse,
    DeleteAllConversationsResponse,
)

from core.db.session import AsyncSessionLocal
from core.db.models import Conversation, Message, ConversationSummary, AgentMemoryEntry
from core.db import repository as DBRepoModule
from core.db.repository import AttachmentRepo, ConversationAttachmentRepo, ExecutionMetricsRepo
from core.auth.dependencies import get_current_auth
from core.files.parser import INLINE_TOKEN_THRESHOLD
from utils.blob_store import read_file_blob
from utils.workspace import (
    get_conversation_dir,
    get_project_dir,
    get_user_root,
    move_conversation_workspace_to_project,
)

router = APIRouter()
logger = getLogger(__name__)


class TruncateMessagesResponse(BaseModel):
    deleted: int


class PromoteConversationResponse(BaseModel):
    conversation_id: str
    project_id: str
    scenario: str  # "attach" | "files"
    # "attach" scenario fields
    already_attached: Optional[bool] = None
    workspace_moved: Optional[bool] = None
    files_moved: Optional[int] = None
    # "files" scenario fields
    promoted: Optional[List[str]] = None
    errors: Optional[List[Dict[str, str]]] = None


class UnlinkAttachmentResponse(BaseModel):
    status: str
    unlinked_ca_id: str


# ---------------------------------------------------------------------------
# Export schemas (Wave 2.2)
# ---------------------------------------------------------------------------

class ConversationExportMessage(BaseModel):
    id: str
    role: str                          # user | assistant | tool | system
    type: Optional[str] = None         # text | tool_call | tool_result | error | …
    content: Optional[str] = None      # None for tool_call turns
    tool_name: Optional[str] = None    # set for role=tool or type=tool_call
    reasoning: Optional[str] = None
    created_at: Optional[str] = None


class ConversationExportResponse(BaseModel):
    conversation_id: str
    title: Optional[str] = None
    created_at: Optional[str] = None
    exported_at: str
    message_count: int
    messages: List[ConversationExportMessage]


class ConversationUpdate(BaseModel):
    # B-015: title update (kept optional so system_prompt can be patched alone)
    title: Optional[str] = None
    # B-054: per-conversation system prompt
    system_prompt: Optional[str] = None

# Keep old name as alias for backward compat
ConversationTitleUpdate = ConversationUpdate

# ----------------------------
# Async DB dependency
# ----------------------------
async def get_db():
    async with AsyncSessionLocal() as db:
        yield db

# ============================
# Update Conversation (B-015 / B-054)
# ============================
@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    user_id = auth["user_id"]
    logger.info(f"Updating conversation={conversation_id} user={user_id}")

    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    updated = await DBRepoModule.DBRepo.update_conversation(db, conversation_id, body)
    return updated

# ============================
# List Conversations
# ============================
@router.get("/conversations", response_model=List[ConversationResponse])
async def list_conversations(
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    user_id = auth["user_id"]
    logger.info(f"Fetching conversations for user={user_id}")
    convos = await DBRepoModule.DBRepo.list_conversations_for_user(db, user_id)
    return convos

# ============================
# Get Specific Conversation
# ============================
@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    user_id = auth["user_id"]
    logger.info(f"Fetching conversation {conversation_id} for user={user_id}")

    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        logger.warning(f"Conversation not found or unauthorized: {conversation_id} user={user_id}")
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo

# ============================
# List Messages for a Conversation (B-013: cursor-based pagination)
# ============================
@router.get("/conversations/{conversation_id}/messages", response_model=PaginatedMessagesResponse)
async def list_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    before: Optional[str] = Query(default=None, description="Message ID cursor — return messages older than this"),
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    user_id = auth["user_id"]
    logger.info(f"Fetching messages for conversation={conversation_id} user={user_id} limit={limit} before={before}")

    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        logger.warning(f"Unauthorized messages list: convo={conversation_id} user={user_id}")
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs, has_more, next_cursor = await DBRepoModule.DBRepo.list_messages_paginated(
        db, conversation_id, limit=limit, before_id=before
    )
    return PaginatedMessagesResponse(messages=msgs, has_more=has_more, next_cursor=next_cursor)


# ============================
# Truncate Messages From a Point (edit / retry)
# ============================
@router.delete("/conversations/{conversation_id}/messages", response_model=TruncateMessagesResponse)
async def truncate_messages(
    conversation_id: str,
    from_message_id: str = Query(..., description="Delete this message and all that follow it"),
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    """Delete from_message_id and every subsequent message in the conversation.
    Used by the frontend edit/retry flow to keep the DB in sync with the UI truncation."""
    user_id = auth["user_id"]
    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    count = await DBRepoModule.DBRepo.delete_messages_from(db, conversation_id, from_message_id)
    logger.info(f"Truncated {count} messages from convo={conversation_id} at msg={from_message_id}")
    return {"deleted": count}


# ============================
# Get a Specific Message
# ============================
@router.get("/conversations/{conversation_id}/messages/{message_id}", response_model=MessageResponse)
async def get_message(
    conversation_id: str,
    message_id: str,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    user_id = auth["user_id"]
    logger.info(f"Fetching message={message_id} for convo={conversation_id} user={user_id}")

    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        logger.warning(f"Unauthorized get_message: convo={conversation_id} user={user_id}")
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg = await DBRepoModule.DBRepo.get_message(db, message_id)
    if not msg:
        logger.warning(f"Message not found: {message_id}")
        raise HTTPException(status_code=404, detail="Message not found")

    return msg

# ============================
# Export Conversation (Wave 2.2)
# ============================

# Role → display label for Markdown / text formats
_ROLE_LABEL: Dict[str, str] = {
    "user":      "🧑 User",
    "assistant": "🤖 Assistant",
    "tool":      "🔧 Tool Result",
    "system":    "⚙️  System",
}


@router.get("/conversations/{conversation_id}/export", response_model=ConversationExportResponse)
async def export_conversation(
    conversation_id: str,
    format: str = Query(default="json", pattern="^(json|markdown|text)$"),
    include_tool_calls: bool = Query(
        default=True,
        description="When True, include tool-call and tool-result turns in the export",
    ),
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    """
    Export all messages in a conversation.

    - ``?format=json``      (default) — structured JSON with full metadata including
                            tool calls, tool results, and reasoning turns
    - ``?format=markdown``  — human-readable Markdown; role headers distinguish
                            user / assistant / tool / system turns
    - ``?format=text``      — plain UTF-8 text, one turn per block; easiest to
                            copy-paste into other tools

    ``?include_tool_calls=false`` strips tool_call / tool_result turns from all
    formats, producing a cleaner human-readable transcript.

    Ownership is enforced: only the conversation owner may export.
    """
    user_id = auth["user_id"]
    logger.info(
        f"Exporting conversation={conversation_id} format={format} "
        f"include_tool_calls={include_tool_calls} user={user_id}"
    )

    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Fetch ALL messages — full export, no pagination cap.
    messages = await DBRepoModule.DBRepo.list_messages_for_conversation(db, conversation_id)

    # Stable ordering: sequence (preferred) → created_at (fallback for old rows)
    messages = sorted(
        messages,
        key=lambda m: (
            m.sequence is None,   # None → True → sorts last
            m.sequence if m.sequence is not None else 0,
            m.created_at,
        ),
    )

    # Optionally strip tool-call / tool-result turns
    if not include_tool_calls:
        messages = [
            m for m in messages
            if m.type not in ("tool_call", "tool_result") and m.role != "tool"
        ]

    exported_at = datetime.now(timezone.utc).isoformat()
    title = getattr(convo, "title", None) or conversation_id

    # ── JSON ──────────────────────────────────────────────────────────────────
    if format == "json":
        export_msgs = [
            ConversationExportMessage(
                id=str(m.id),
                role=m.role,
                type=m.type,
                # tool_call assistant turns have content=None — surface that cleanly
                content=m.content,
                tool_name=m.tool_name,
                reasoning=m.reasoning or None,
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
            for m in messages
        ]
        return ConversationExportResponse(
            conversation_id=conversation_id,
            title=title if title != conversation_id else None,
            created_at=convo.created_at.isoformat() if convo.created_at else None,
            exported_at=exported_at,
            message_count=len(export_msgs),
            messages=export_msgs,
        )

    # ── Markdown ──────────────────────────────────────────────────────────────
    if format == "markdown":
        lines: List[str] = [
            f"# Conversation: {title}",
            "",
            f"**ID:** `{conversation_id}`  ",
            f"**Exported:** {exported_at}  ",
            f"**Turns:** {len(messages)}",
            "",
            "---",
            "",
        ]
        for msg in messages:
            role_label = _ROLE_LABEL.get(msg.role, f"### {msg.role.title()}")
            lines.append(f"### {role_label}")

            if msg.created_at:
                lines.append(f"*{msg.created_at.isoformat()}*")
            lines.append("")

            if msg.type == "tool_call" and not msg.content:
                # Assistant decided to call a tool — show the intent clearly
                lines.append(f"*[Calling tool: **{msg.tool_name or 'unknown'}**]*")
            elif msg.role == "tool":
                # Tool result — show tool name and content
                if msg.tool_name:
                    lines.append(f"*Result from **{msg.tool_name}**:*")
                    lines.append("")
                lines.append(f"```\n{(msg.content or '').strip()}\n```")
            else:
                lines.append((msg.content or "").strip())

            if msg.reasoning:
                lines.append("")
                lines.append(f"> 💭 **Reasoning:** {msg.reasoning.strip()}")

            lines.append("")
            lines.append("---")
            lines.append("")

        md_text = "\n".join(lines)
        safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:40]
        filename = f"conversation-{safe_title}.md"
        return Response(
            content=md_text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── Plain text ────────────────────────────────────────────────────────────
    # format == "text"
    text_lines: List[str] = [
        f"CONVERSATION EXPORT",
        f"ID:       {conversation_id}",
        f"Title:    {title}",
        f"Exported: {exported_at}",
        f"Turns:    {len(messages)}",
        "",
        "=" * 60,
        "",
    ]
    for msg in messages:
        role_label = _ROLE_LABEL.get(msg.role, msg.role.upper()).replace("🧑 ", "").replace("🤖 ", "").replace("🔧 ", "").replace("⚙️  ", "")
        text_lines.append(f"[{role_label.upper()}]")
        if msg.type == "tool_call" and not msg.content:
            text_lines.append(f"(Calling tool: {msg.tool_name or 'unknown'})")
        else:
            text_lines.append((msg.content or "").strip())
        if msg.reasoning:
            text_lines.append(f"\n  Reasoning: {msg.reasoning.strip()}")
        text_lines.append("")
        text_lines.append("-" * 40)
        text_lines.append("")

    txt = "\n".join(text_lines)
    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:40]
    filename = f"conversation-{safe_title}.txt"
    return Response(
        content=txt,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================
# Delete all Conversations for a User
# ============================
@router.delete("/conversations", response_model=DeleteAllConversationsResponse)
async def delete_all_conversations(
    auth=Depends(get_current_auth),
    db=Depends(get_db),
    response_model=DeleteAllConversationsResponse,
):
    user_id = auth["user_id"]
    logger.info(f"Deleting all conversations for user={user_id}")

    deleted_count = await DBRepoModule.DBRepo.delete_all_conversations_for_user(db, user_id)
    logger.info(f"Deleted {deleted_count} conversations for user={user_id}")

    # Remove all conversation scratch dirs in one shot (B-075)
    conv_root = get_user_root(user_id) / "conversations"
    if conv_root.exists():
        try:
            shutil.rmtree(conv_root, ignore_errors=True)
            logger.info(f"Deleted all conversation workspace dirs for user={user_id}")
        except Exception as _e:
            logger.warning(
                f"Failed to delete conversation workspace dirs for user={user_id}: {_e}"
            )

    return {"deleted_conversations": deleted_count}

# ============================
# Delete a Specific Conversation
# ============================
@router.delete("/conversations/{conversation_id}", response_model=DeleteConversationResponse)
async def delete_conversation(
    conversation_id: str,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
    response_model=DeleteConversationResponse,
):
    user_id = auth["user_id"]
    logger.info(f"Deleting conversation={conversation_id} for user={user_id}")

    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        logger.warning(f"Conversation not found or unauthorized delete attempt: {conversation_id} user={user_id}")
        raise HTTPException(status_code=404, detail="Conversation not found")

    await DBRepoModule.DBRepo.delete_conversation_and_children(db, conversation_id)
    logger.info(f"Deleted conversation={conversation_id} user={user_id}")

    # Remove the conversation's scratch workspace dir (B-075)
    conv_dir = get_conversation_dir(user_id, conversation_id)
    if conv_dir.exists():
        try:
            shutil.rmtree(conv_dir, ignore_errors=True)
            logger.info(
                f"Deleted conversation workspace dir: conv={conversation_id} user={user_id}"
            )
        except Exception as _e:
            logger.warning(
                f"Failed to delete workspace dir for conversation={conversation_id}: {_e}"
            )

    return {"deleted_conversation_id": conversation_id}


# ============================================================
# Promote Conversation → Project Workspace (B-075, redesigned)
# ============================================================
#
# Two scenarios handled by the same endpoint, selected by the ``files`` field:
#
#   Scenario 1 — Conversation attachment (files is empty or omitted):
#     • Verifies the target project exists and belongs to the user.
#     • Sets ``project_id`` on the conversation DB row.
#     • Physically moves the conversation scratch directory from
#         ~/eva_workspace/{user_id}/conversations/{conv_id}/
#       to
#         ~/eva_workspace/{user_id}/projects/{project_id}/conversations/{conv_id}/
#     • Leaves a ``.project_link`` redirect at the old location so that
#       ``get_conversation_dir()`` transparently resolves the new path for all
#       callers (journal, shell executor, git tool) without any further changes.
#
#   Scenario 2 — File promotion (files is non-empty):
#     • Copies the listed files from the conversation scratch dir to the project
#       workspace root (~/eva_workspace/{user_id}/projects/{project_id}/).
#     • The conversation's project_id is NOT changed by this operation.
#     • Path traversal is rejected.
#     • Originals are NOT deleted — this is a copy, not a move.
# ============================================================

class PromoteFilesRequest(BaseModel):
    """
    Body for POST /conversations/{id}/promote.

    ``project_id``  — target project (required for both scenarios).
    ``files``       — list of relative paths within the conversation scratch dir.
                      When empty (default) the entire conversation is attached to
                      the project (Scenario 1).  When non-empty, only the listed
                      files are copied to the project workspace (Scenario 2).
    """
    project_id: str
    files: List[str] = []   # empty → Scenario 1 (attach conversation); non-empty → Scenario 2 (file copy)


@router.post("/conversations/{conversation_id}/promote", response_model=PromoteConversationResponse)
async def promote_conversation_files(
    conversation_id: str,
    body: PromoteFilesRequest,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    """
    Promote a conversation to a project workspace.

    **Scenario 1 — Attach conversation to project** (``files`` is empty):

    Sets the conversation's ``project_id`` in the database and moves its scratch
    workspace directory under the project tree.  After this call the conversation
    appears under the project in all listings and its filesystem artifacts live
    alongside other project-scoped conversations.

    **Scenario 2 — Copy specific files** (``files`` is non-empty):

    Copies the listed files from the conversation's scratch directory to the
    project workspace root.  The conversation's ``project_id`` is unchanged.
    Use this to rescue individual output files that were written to a
    conversation workspace but belong to a project.

    Path traversal attempts (``../../evil.txt``) are rejected in both scenarios.
    """
    user_id = auth["user_id"]

    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Verify the target project exists and belongs to this user
    from core.db.repository import ProjectRepo as _ProjectRepo  # noqa: PLC0415
    project = await _ProjectRepo.get(db, project_id=body.project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # ── Scenario 1: Attach conversation to project ────────────────────────────
    if not body.files:
        if convo.project_id == body.project_id:
            # Already attached to this project — idempotent, return current state
            return {
                "conversation_id": conversation_id,
                "project_id": body.project_id,
                "scenario": "attach",
                "already_attached": True,
                "workspace_moved": False,
                "files_moved": 0,
            }

        # Set project_id on the DB row
        try:
            async with AsyncSessionLocal() as write_db:
                await DBRepoModule.DBRepo.set_conversation_project_id(
                    write_db, conversation_id, body.project_id
                )
        except Exception as _db_exc:
            logger.error(
                "Promote Scenario 1: DB update failed conv=%s project=%s err=%s",
                conversation_id, body.project_id, _db_exc, exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update conversation project link: {_db_exc}",
            )

        # Move the workspace directory (blocking I/O — run in thread pool)
        try:
            move_result = await asyncio.to_thread(
                move_conversation_workspace_to_project,
                user_id,
                conversation_id,
                body.project_id,
            )
        except Exception as _fs_exc:
            logger.error(
                "Promote Scenario 1: workspace move failed conv=%s project=%s err=%s",
                conversation_id, body.project_id, _fs_exc, exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to move conversation workspace: {_fs_exc}",
            )

        logger.info(
            "Conversation promoted to project: conv=%s project=%s files_moved=%d user=%s",
            conversation_id, body.project_id, move_result["files_moved"], user_id,
        )
        return {
            "conversation_id": conversation_id,
            "project_id": body.project_id,
            "scenario": "attach",
            "already_attached": False,
            "workspace_moved": True,
            "files_moved": move_result["files_moved"],
        }

    # ── Scenario 2: Copy specific files to project workspace ─────────────────
    src_dir = get_conversation_dir(user_id, conversation_id)
    dst_dir = get_project_dir(user_id, body.project_id)
    src_root = src_dir.resolve()

    promoted: List[str] = []
    errors: List[Dict[str, str]] = []

    for rel_path in body.files:
        # Guard against path traversal
        try:
            target = (src_dir / rel_path).resolve()
        except Exception:
            errors.append({"file": rel_path, "error": "Invalid path"})
            continue
        if not str(target).startswith(str(src_root)):
            errors.append({"file": rel_path, "error": "Path traversal not allowed"})
            continue
        if not target.exists() or not target.is_file():
            errors.append({"file": rel_path, "error": "File not found in conversation workspace"})
            continue
        dst = dst_dir / rel_path
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dst)
            promoted.append(rel_path)
        except Exception as exc:
            errors.append({"file": rel_path, "error": str(exc)})

    logger.info(
        "Promoted %d file(s) from conv=%s to project=%s (errors=%d) user=%s",
        len(promoted), conversation_id, body.project_id, len(errors), user_id,
    )
    return {
        "conversation_id": conversation_id,
        "project_id": body.project_id,
        "scenario": "files",
        "promoted": promoted,
        "errors": errors,
    }


# ============================================================
# Conversation Attachments (B-066)
# ============================================================
# POST   /conversations/{id}/attachments   — link an attachment
# GET    /conversations/{id}/attachments   — list linked attachments
# DELETE /conversations/{id}/attachments/{ca_id}  — unlink
# ============================================================


class ConversationAttachmentLinkRequest(BaseModel):
    """
    Body for POST /conversations/{id}/attachments.

    Exactly one of the three fields must be set:
      attachment_id       — link a file/image from the attachment library
      conversation_ref_id — reference another conversation (summary injected)
      execution_ref_id    — reference a past execution (metrics injected)
    """
    attachment_id: Optional[str] = None
    conversation_ref_id: Optional[str] = None
    execution_ref_id: Optional[str] = None


class ConversationAttachmentResponse(BaseModel):
    conversation_attachment_id: str
    conversation_id: str
    attachment_type: str
    attachment_id: Optional[str] = None
    ref_conversation_id: Optional[str] = None
    ref_execution_id: Optional[str] = None
    message_id: Optional[str] = None
    rag_indexed: bool
    rag_job_id: Optional[str] = None
    created_at: Optional[str] = None


def _ca_to_response(ca) -> ConversationAttachmentResponse:
    return ConversationAttachmentResponse(
        conversation_attachment_id=ca.id,
        conversation_id=ca.conversation_id,
        attachment_type=ca.attachment_type,
        attachment_id=ca.attachment_id,
        ref_conversation_id=ca.ref_conversation_id,
        ref_execution_id=ca.ref_execution_id,
        message_id=ca.message_id,
        rag_indexed=ca.rag_indexed,
        rag_job_id=ca.rag_job_id,
        created_at=ca.created_at.isoformat() if ca.created_at else None,
    )


async def _inject_context_message(db, conversation_id: str, content: str) -> str:
    """Insert an attachment context message and return its message_id.

    Uses role="user" (not "system") so that MemoryEngine.build_context()
    includes it in the LLM message list.  role="system" messages are
    silently stripped by both _append_message_with_tools (no handler) and
    the worker.py post-build filter.  mtype="attachment_context" distinguishes
    these injected blocks from real user messages in any DB queries.
    """
    msg = await DBRepoModule.DBRepo.add_message(
        db,
        conversation_id=conversation_id,
        role="user",
        content=content,
        mtype="attachment_context",
    )
    return msg.id


@router.post("/conversations/{conversation_id}/attachments",
             response_model=ConversationAttachmentResponse)
async def link_attachment_to_conversation(
    conversation_id: str,
    body: ConversationAttachmentLinkRequest,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    """
    Link an attachment (file/image/conversation-ref/execution-ref) to a conversation.

    **File / Image**
    The raw file is read from the blob store and a context summary is injected
    as a system message into the conversation history.  If the file's token
    estimate exceeds the inline threshold (32 000 tokens by default), the file
    is pushed to RAG instead and a notice message is injected.

    For **images**, the selected model must support vision (supports_vision=True).
    If it does not, a 422 is returned immediately.

    **conversation_ref**
    A summary of the referenced conversation (last 20 messages) is injected.

    **execution_ref**
    Key execution metrics and the outcome are injected as context.
    """
    from config.settings import get_user_session
    user_id = auth["user_id"]
    # get_user_session() always returns a dict (never falsy) -- its own
    # blank template already represents "unconfigured" honestly. The old
    # "or DEFAULT_SESSION" fallback here was unreachable dead code
    # (2026-07-27 cleanup).
    session = get_user_session(user_id)

    # Validate exactly one source
    sources = [body.attachment_id, body.conversation_ref_id, body.execution_ref_id]
    filled = [s for s in sources if s]
    if len(filled) != 1:
        raise HTTPException(
            status_code=400,
            detail="Exactly one of attachment_id, conversation_ref_id, or execution_ref_id must be provided.",
        )

    # Verify conversation ownership
    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # ── File / Image attachment ────────────────────────────────────────────────
    if body.attachment_id:
        att = await AttachmentRepo.get(db, body.attachment_id)
        if not att or att.user_id != user_id:
            raise HTTPException(status_code=404, detail="Attachment not found.")

        # Prevent duplicate linking
        existing_link = await ConversationAttachmentRepo.get_for_conversation_and_attachment(
            db, conversation_id, body.attachment_id
        )
        if existing_link:
            raise HTTPException(
                status_code=409,
                detail="This attachment is already linked to the conversation.",
            )

        # Image vision check
        if att.attachment_type == "image":
            prefs = await DBRepoModule.DBRepo.get_user_preferences(db, user_id=user_id)
            vision_ok = False
            if prefs:
                try:
                    from core.providers import capability_registry  # noqa: PLC0415
                    model_info = capability_registry.get_model(prefs.provider, prefs.model)
                    if model_info:
                        vision_ok = model_info.supports_vision
                except Exception:
                    pass
            if not vision_ok:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "The currently selected model does not support vision / image input. "
                        "Switch to a vision-capable model before attaching images."
                    ),
                )

        rag_indexed = False
        rag_job_id = None
        message_id = None

        if att.attachment_type == "image":
            # Images are always inline. This message is placeholder text; the real base64 is
            # read from Attachment.blob_path and forwarded at inference time via
            # ConversationAttachment.message_id (M-07: MemoryEngine._image_attachments_for_vision()
            # in core/memory/engine.py), but ONLY for a confirmed vision-capable model -- for any
            # other model this placeholder is genuinely all the agent sees, by design.
            context_text = (
                f"[IMAGE ATTACHMENT]\n"
                f"Filename: {att.filename or 'image'}\n"
                f"Type: {att.mime_type or 'image'}\n"
                f"Size: {att.size_bytes or 0:,} bytes\n\n"
                f"An image has been attached to this conversation. "
                f"Refer to it when answering the user's next message."
            )
            async with AsyncSessionLocal() as write_db:
                message_id = await _inject_context_message(write_db, conversation_id, context_text)

        else:
            # Text/document file
            token_est = att.token_estimate or 0
            if token_est >= INLINE_TOKEN_THRESHOLD:
                # RAG path — ingest the file asynchronously
                from api.rag import _run_ingestion_job  # noqa: PLC0415
                if att.blob_path:
                    raw_bytes = await asyncio.to_thread(
                        read_file_blob, user_id, att.blob_path, att.project_id
                    )
                    if raw_bytes:
                        rag_job_id = str(__import__("uuid").uuid4())
                        asyncio.create_task(
                            _run_ingestion_job(
                                rag_job_id,
                                user_id,
                                att.filename or att.blob_path,
                                raw_bytes,
                                att.blob_path,
                                convo.project_id,
                            )
                        )
                        rag_indexed = True

                context_text = (
                    f"[FILE ATTACHMENT — RAG INDEXED]\n"
                    f"Filename: {att.filename or 'file'}\n"
                    f"Type: {att.mime_type or 'document'}\n"
                    f"Size: {att.size_bytes or 0:,} bytes  |  ~{token_est:,} tokens\n\n"
                    f"This file has been indexed in your knowledge base. "
                    f"Use rag_retrieve to search its content when answering questions about it."
                )
            else:
                # Inline path — read file content and inject as context
                raw_bytes = await asyncio.to_thread(
                    read_file_blob, user_id, att.blob_path or "", att.project_id
                ) if att.blob_path else None

                if raw_bytes:
                    from core.files.parser import parse_file  # noqa: PLC0415
                    try:
                        parsed = await asyncio.to_thread(parse_file, att.filename or "file", raw_bytes)
                        file_text = parsed.get("text") or ""
                    except Exception:
                        file_text = "[Could not extract text from file]"
                else:
                    file_text = "[File content unavailable]"

                context_text = (
                    f"[FILE ATTACHMENT — INLINE]\n"
                    f"Filename: {att.filename or 'file'}\n"
                    f"Type: {att.mime_type or 'document'}\n"
                    f"Size: {att.size_bytes or 0:,} bytes  |  ~{token_est:,} tokens\n\n"
                    f"--- FILE CONTENT START ---\n"
                    f"{file_text}\n"
                    f"--- FILE CONTENT END ---"
                )

            async with AsyncSessionLocal() as write_db:
                message_id = await _inject_context_message(write_db, conversation_id, context_text)

        # Record the link
        async with AsyncSessionLocal() as write_db:
            ca = await ConversationAttachmentRepo.create(
                write_db,
                conversation_id=conversation_id,
                user_id=user_id,
                attachment_type=att.attachment_type,
                attachment_id=att.id,
                message_id=message_id,
                rag_indexed=rag_indexed,
                rag_job_id=rag_job_id,
            )

        logger.info(
            "Attachment linked to conversation",
            extra={
                "user_id": user_id,
                "conversation_id": conversation_id,
                "attachment_id": att.id,
                "rag_indexed": rag_indexed,
            },
        )
        return _ca_to_response(ca)

    # ── Conversation reference ────────────────────────────────────────────────
    if body.conversation_ref_id:
        import json as _json
        from utils.workspace import get_conversation_dir
        from prompts.memory import CONVERSATION_EXPORT_PROMPT
        from core.providers.fallback_call import resolve_and_call_once
        from core.tools.user_registry import UserToolRegistry

        ref_convo = await DBRepoModule.DBRepo.get_conversation(db, body.conversation_ref_id)
        if not ref_convo or ref_convo.user_id != user_id:
            raise HTTPException(status_code=404, detail="Referenced conversation not found.")

        title = ref_convo.title or "Untitled"
        conv_dir = get_conversation_dir(user_id, body.conversation_ref_id)
        jsonl_path = conv_dir / "conversation.jsonl"
        summary_path = conv_dir / "summary.md"

        # ── Determine if summary.md needs (re)generation ─────────────────────
        # Cache by mtime: regenerate only if conversation.jsonl is newer than summary.md
        need_summary = True
        if summary_path.exists() and jsonl_path.exists():
            if summary_path.stat().st_mtime >= jsonl_path.stat().st_mtime:
                need_summary = False

        if need_summary:
            if jsonl_path.exists():
                # Read all events from the real-time JSONL journal
                events: list[dict] = []
                with open(jsonl_path, encoding="utf-8") as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if _line:
                            try:
                                events.append(_json.loads(_line))
                            except Exception:
                                pass

                # Genuine events: user messages, assistant messages, tool calls, tool results
                genuine_events = [
                    e for e in events
                    if e.get("type") in ("user_message", "assistant_message", "tool_call", "tool_result")
                ]

                date_str = (
                    ref_convo.created_at.strftime("%Y-%m-%d")
                    if ref_convo.created_at else "unknown"
                )
                model_used = next(
                    (e.get("model", "unknown") for e in events if e.get("type") == "execution_start"),
                    "unknown",
                )

                if len(genuine_events) <= 15:
                    # ── Pure-code summary (fast, free) ────────────────────────
                    md_lines = [
                        f"# {title}",
                        f"**Date:** {date_str}  **Events:** {len(genuine_events)}  **Model:** {model_used}",
                        "",
                        "## Transcript",
                        "",
                    ]
                    for e in events:
                        etype = e.get("type")
                        if etype == "user_message":
                            md_lines.append(f"**User:** {(e.get('content') or '').strip()}")
                            md_lines.append("")
                        elif etype == "assistant_message":
                            content_val = (e.get("content") or "").strip()
                            if content_val:
                                md_lines.append(f"**Assistant:** {content_val}")
                                md_lines.append("")
                        elif etype == "tool_call":
                            for call in e.get("calls", []):
                                args_str = _json.dumps(call.get("args", {}), ensure_ascii=False)[:300]
                                md_lines.append(f"**Tool call:** `{call.get('tool_name')}({args_str})`")
                        elif etype == "tool_result":
                            for res in e.get("results", []):
                                result_str = str(res.get("result", ""))[:400]
                                md_lines.append(f"**Tool result** `{res.get('tool_name')}`: {result_str}")
                                md_lines.append("")
                    summary_text = "\n".join(md_lines)

                else:
                    # ── LLM summary (> 15 genuine events) ────────────────────
                    transcript_lines: list[str] = []
                    for e in events:
                        etype = e.get("type")
                        if etype == "user_message":
                            transcript_lines.append(f'USER: {e.get("content", "")}')
                        elif etype == "assistant_message":
                            if e.get("content"):
                                transcript_lines.append(f'ASSISTANT: {e.get("content", "")}')
                        elif etype == "tool_call":
                            for call in e.get("calls", []):
                                transcript_lines.append(
                                    f'TOOL_CALL: {call.get("tool_name")}({_json.dumps(call.get("args", {}))[:300]})'
                                )
                        elif etype == "tool_result":
                            for res in e.get("results", []):
                                transcript_lines.append(
                                    f'TOOL_RESULT ({res.get("tool_name")}): {str(res.get("result", ""))[:500]}'
                                )

                    export_prompt = CONVERSATION_EXPORT_PROMPT.format(
                        title=title,
                        date=date_str,
                        message_count=len(genuine_events),
                        model=model_used,
                    )
                    # Resolve-Attempt-Record tier (V6.8): the user is
                    # waiting on this synchronous request and sees any
                    # failure immediately, so a manual retry (re-issuing
                    # this same POST) is the natural recovery path -- and
                    # it benefits from whatever this attempt just recorded
                    # into the shared cooldown store. Also fixes a real
                    # pre-existing bug: this call had no try/except at all,
                    # so a failure fell through to a raw, unstructured 500
                    # instead of EVA's normal {"code": ..., "detail": ...}
                    # error envelope (nothing here was persisted yet at
                    # that point, so retrying is always safe).
                    try:
                        summary_text = await resolve_and_call_once(
                            session=session,
                            system_prompt=export_prompt,
                            messages=[{"role": "user", "content": "\n".join(transcript_lines)}],
                            user_id=user_id,
                            tools=UserToolRegistry(default_tools=False, enabled_tools=[]),
                            caller_tag="conversation_ref.summarize",
                        )
                    except Exception as exc:
                        logger.warning(
                            "Conversation-ref summary generation failed",
                            exc_info=exc,
                            extra={"user_id": user_id, "conversation_ref_id": body.conversation_ref_id},
                        )
                        raise HTTPException(
                            status_code=502,
                            detail=f"Failed to summarize referenced conversation: {exc}",
                        ) from exc

            else:
                # ── Fallback: JSONL not yet available (pre-journal conversation) ─
                # Use filtered DB transcript — same as the previous implementation.
                all_msgs = await DBRepoModule.DBRepo.list_messages_for_conversation(
                    db, body.conversation_ref_id
                )
                genuine_db = sorted(
                    [
                        m for m in all_msgs
                        if m.role in ("user", "assistant")
                        and m.content
                        and getattr(m, "type", None) != "attachment_context"
                    ],
                    key=lambda m: (m.sequence is None, m.sequence or 0, m.created_at),
                )
                fb_lines = [f"# {title}", "", "## Transcript (DB fallback — journal not available)", ""]
                for m in genuine_db[:20]:
                    label = "User" if m.role == "user" else "Assistant"
                    fb_lines.append(f"**{label}:** {(m.content or '').strip()[:600]}")
                    fb_lines.append("")
                summary_text = "\n".join(fb_lines)

            # Append workspace-relative read_file reference (Electron-safe: no absolute paths)
            rel_jsonl = f"conversations/{body.conversation_ref_id}/conversation.jsonl"
            summary_text += (
                f"\n\n---\n"
                f"*Full transcript: `{rel_jsonl}` — use the read_file tool for exact details.*"
            )
            summary_path.write_text(summary_text, encoding="utf-8")

        # Read summary.md for inline injection
        summary_content = (
            summary_path.read_text(encoding="utf-8")
            if summary_path.exists()
            else "(Summary unavailable)"
        )

        context_text = (
            f"[CONVERSATION REFERENCE]\n"
            f"ID: {body.conversation_ref_id}\n"
            f"Title: {title}\n\n"
            f"{summary_content}"
        )

        async with AsyncSessionLocal() as write_db:
            message_id = await _inject_context_message(write_db, conversation_id, context_text)

        async with AsyncSessionLocal() as write_db:
            ca = await ConversationAttachmentRepo.create(
                write_db,
                conversation_id=conversation_id,
                user_id=user_id,
                attachment_type="conversation_ref",
                ref_conversation_id=body.conversation_ref_id,
                message_id=message_id,
            )
        return _ca_to_response(ca)

    # ── Execution reference ───────────────────────────────────────────────────
    if body.execution_ref_id:
        async with AsyncSessionLocal() as read_db:
            exec_row = await ExecutionMetricsRepo.get(read_db, body.execution_ref_id)
        if not exec_row or exec_row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Execution not found.")

        duration_s = (exec_row.duration_ms or 0) / 1000
        lines: List[str] = [
            f"[EXECUTION REFERENCE]",
            f"Execution ID: {exec_row.execution_id}",
            f"Status: {exec_row.status}",
            f"Model: {exec_row.model_used or 'unknown'}  Provider: {exec_row.provider_used or 'unknown'}",
            f"Duration: {duration_s:.1f}s   Steps: {exec_row.total_steps}",
            f"Tokens: {exec_row.prompt_tokens} prompt + {exec_row.completion_tokens} completion",
            f"Termination: {exec_row.termination_reason or 'n/a'}",
        ]

        # ── Agent memory entries produced during this execution ────────────────
        # AgentMemoryEntry rows tagged with source_execution_id capture what the
        # agent learned: errors hit, decisions made, state at completion, artifacts
        # produced. This is the actual substance of the execution — far more useful
        # than metrics alone. Sensitive entries are excluded (placeholder only).
        agent_entries_stmt = (
            select(AgentMemoryEntry)
            .where(AgentMemoryEntry.source_execution_id == body.execution_ref_id)
            .order_by(AgentMemoryEntry.created_at.asc())
        )
        agent_entries_result = await db.execute(agent_entries_stmt)
        agent_entries = list(agent_entries_result.scalars().all())

        if agent_entries:
            type_labels = {
                "error":    "⚠️ Errors Encountered",
                "success":  "✅ Approaches That Worked",
                "decision": "📌 Decisions Made",
                "state":    "📍 State at Completion",
                "artifact": "📎 Artifacts Produced",
                "note":     "📝 Notes",
            }
            grouped: Dict[str, List[str]] = {}
            for e in agent_entries:
                if e.sensitive:
                    grouped.setdefault(e.entry_type, []).append("[sensitive — content hidden]")
                else:
                    grouped.setdefault(e.entry_type, []).append(e.content.strip())

            lines.append("\n--- AGENT MEMORY ENTRIES ---")
            for etype, items in grouped.items():
                label = type_labels.get(etype, etype.title())
                lines.append(f"\n{label}")
                for item in items:
                    lines.append(f"- {item}")
            lines.append("--- END AGENT MEMORY ---")

        lines.append("\nThis execution is provided as context for the current conversation.")
        context_text = "\n".join(lines)

        async with AsyncSessionLocal() as write_db:
            message_id = await _inject_context_message(write_db, conversation_id, context_text)

        async with AsyncSessionLocal() as write_db:
            ca = await ConversationAttachmentRepo.create(
                write_db,
                conversation_id=conversation_id,
                user_id=user_id,
                attachment_type="execution_ref",
                ref_execution_id=body.execution_ref_id,
                message_id=message_id,
            )
        return _ca_to_response(ca)

    # Should never reach here due to validation above
    raise HTTPException(status_code=400, detail="No attachment source specified.")


@router.get("/conversations/{conversation_id}/attachments",
            response_model=List[ConversationAttachmentResponse])
async def list_conversation_attachments(
    conversation_id: str,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    """
    List all attachments linked to a conversation, in the order they were added.
    """
    user_id = auth["user_id"]
    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    items = await ConversationAttachmentRepo.list_for_conversation(db, conversation_id)
    return [_ca_to_response(ca) for ca in items]


@router.delete("/conversations/{conversation_id}/attachments/{ca_id}", response_model=UnlinkAttachmentResponse)
async def unlink_conversation_attachment(
    conversation_id: str,
    ca_id: str,
    auth=Depends(get_current_auth),
    db=Depends(get_db),
):
    """
    Unlink an attachment from a conversation.

    This removes the ConversationAttachment record only.  The injected context
    message remains in the conversation history (it's already part of the thread).
    The file in the attachment library is NOT deleted — use DELETE /attachments/{id}
    for that separately.
    """
    user_id = auth["user_id"]
    convo = await DBRepoModule.DBRepo.get_conversation(db, conversation_id)
    if not convo or convo.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    ca = await ConversationAttachmentRepo.get(db, ca_id)
    if not ca or ca.conversation_id != conversation_id or ca.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation attachment not found.")

    async with AsyncSessionLocal() as write_db:
        await ConversationAttachmentRepo.delete(write_db, ca_id)

    logger.info(
        "Conversation attachment unlinked",
        extra={"user_id": user_id, "conversation_id": conversation_id, "ca_id": ca_id},
    )
    return {"status": "ok", "unlinked_ca_id": ca_id}
