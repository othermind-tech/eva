"""
api/attachments.py — B-066: Conversation Attachment & File Library

Endpoints
---------
POST   /attachments              Upload a file/image → parse, store, record in library
GET    /attachments              List user's file library
DELETE /attachments/{id}         Remove a file from the library (and blob store)

These endpoints manage the user's file library only.  Linking a file to a
specific conversation is done via:
  POST   /conversations/{id}/attachments   (in conversation_management.py)
  GET    /conversations/{id}/attachments
  DELETE /conversations/{id}/attachments/{ca_id}
"""

import asyncio
import uuid as _uuid
from logging import getLogger
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.auth.dependencies import get_current_auth
from core.db.session import AsyncSessionLocal
from core.db.repository import AttachmentRepo
from core.files.parser import parse_file
from utils.blob_store import save_file_blob, delete_file_blob
from api.schemas.responses import DeleteAttachmentResponse

logger = getLogger(__name__)

router = APIRouter(prefix="/attachments", tags=["Attachments"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class AttachmentResponse(BaseModel):
    attachment_id: str
    attachment_type: str      # "file" | "image"
    filename: Optional[str]
    mime_type: Optional[str]
    token_estimate: Optional[int]
    size_bytes: Optional[int]
    project_id: Optional[str]
    created_at: Optional[str]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _att_to_response(att) -> AttachmentResponse:
    return AttachmentResponse(
        attachment_id=att.id,
        attachment_type=att.attachment_type,
        filename=att.filename,
        mime_type=att.mime_type,
        token_estimate=att.token_estimate,
        size_bytes=att.size_bytes,
        project_id=att.project_id,
        created_at=att.created_at.isoformat() if att.created_at else None,
    )


# ---------------------------------------------------------------------------
# POST /attachments
# ---------------------------------------------------------------------------

@router.post("", response_model=AttachmentResponse)
async def upload_attachment(
    file: UploadFile,
    project_id: Optional[str] = Form(default=None),
    current_auth=Depends(get_current_auth),
):
    """
    Upload a file or image to the user's attachment library.

    - Parses the file (PDF, DOCX, TXT, code, CSV/XLSX, or image).
    - For **images**: validates that the caller intends vision usage.
      The attachment is stored; the conversation link step (POST /conversations/{id}/attachments)
      will enforce model.supports_vision at link time.
    - Stores the raw file in the files/ blob directory (project-scoped when project_id is set).
    - Records the attachment in the DB and returns an attachment_id.

    Only **one file per request** is accepted.
    """
    user_id = current_auth["user_id"]
    filename = file.filename or f"upload_{_uuid.uuid4().hex[:8]}"

    logger.info(
        "Attachment upload started",
        extra={"user_id": user_id, "file_name": filename, "project_id": project_id},
    )

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Parse
    try:
        parsed = await asyncio.to_thread(parse_file, filename, raw_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Save to blob store
    # Use a unique filename to avoid collisions (prefix with a UUID fragment).
    unique_prefix = _uuid.uuid4().hex[:8]
    stored_filename = f"{unique_prefix}_{filename}"
    await asyncio.to_thread(
        save_file_blob,
        user_id,
        stored_filename,
        raw_bytes,
        project_id,
    )

    # Record in DB
    async with AsyncSessionLocal() as db:
        att = await AttachmentRepo.create(
            db,
            user_id=user_id,
            attachment_type=parsed["attachment_type"],
            filename=filename,
            mime_type=parsed["mime_type"],
            blob_path=stored_filename,
            token_estimate=parsed["token_estimate"],
            size_bytes=parsed["size_bytes"],
            project_id=project_id,
        )

    logger.info(
        "Attachment uploaded",
        extra={
            "user_id": user_id,
            "attachment_id": att.id,
            "attachment_type": att.attachment_type,
            "token_estimate": att.token_estimate,
        },
    )

    return _att_to_response(att)


# ---------------------------------------------------------------------------
# GET /attachments
# ---------------------------------------------------------------------------

@router.get("", response_model=List[AttachmentResponse])
async def list_attachments(
    project_id: Optional[str] = None,
    current_auth=Depends(get_current_auth),
):
    """
    List the user's file library.

    Pass ?project_id=<id> to filter to a specific project's files only.
    Without the filter all user files (global + all projects) are returned.
    """
    user_id = current_auth["user_id"]
    async with AsyncSessionLocal() as db:
        items = await AttachmentRepo.list_for_user(db, user_id=user_id, project_id=project_id)
    return [_att_to_response(a) for a in items]


# ---------------------------------------------------------------------------
# DELETE /attachments/{attachment_id}
# ---------------------------------------------------------------------------

@router.delete("/{attachment_id}", response_model=DeleteAttachmentResponse)
async def delete_attachment(
    attachment_id: str,
    current_auth=Depends(get_current_auth),
):
    """
    Delete a file from the user's library.

    This removes the DB record and the raw blob from the files/ directory.
    Any ConversationAttachment rows that reference this attachment are NOT
    automatically cleaned up — the conversation context message remains in
    history (the file content was already injected or RAG-indexed when linked).
    """
    user_id = current_auth["user_id"]

    async with AsyncSessionLocal() as db:
        att = await AttachmentRepo.get(db, attachment_id)

    if not att or att.user_id != user_id:
        raise HTTPException(status_code=404, detail="Attachment not found.")

    # Delete blob
    if att.blob_path:
        deleted_blob = await asyncio.to_thread(
            delete_file_blob,
            user_id,
            att.blob_path,
            att.project_id,
        )
        logger.debug(
            "Attachment blob deletion",
            extra={"deleted": deleted_blob, "blob_path": att.blob_path},
        )

    # Delete DB record
    async with AsyncSessionLocal() as db:
        await AttachmentRepo.delete(db, attachment_id)

    logger.info(
        "Attachment deleted",
        extra={"user_id": user_id, "attachment_id": attachment_id},
    )

    return {"status": "ok", "deleted_attachment_id": attachment_id}
