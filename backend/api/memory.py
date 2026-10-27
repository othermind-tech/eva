# api/memory.py
"""
Long-Term Memory Management API (V3.0)

Endpoints for inspecting and managing all memory scopes:
  Chat summaries  : GET /memory/chats (read-only — in-conversation compression records)
  Scope 2 — User  : GET/POST/PUT/DELETE /memory/user-facts
  Scope 3 — Agent : GET/POST/PUT/DELETE /memory/agent/{scope_key}
  Diagnostic      : GET /memory/summary
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config.settings import MEMORY_SENSITIVE_GRANT_TTL_SECONDS
from core.auth.dependencies import get_current_auth
from core.db.session import AsyncSessionLocal
from core.db.repository import MemoryRepo
from core.memory.fact_summary import schedule_summary_rebuild, force_rebuild_summary

router = APIRouter(prefix="/memory", tags=["memory"])

_SENSITIVE_GRANT_TTL = MEMORY_SENSITIVE_GRANT_TTL_SECONDS

def _grant_key(user_id: str, entry_id: str) -> str:
    """Build the broker key for a sensitive-memory access grant."""
    return f"sensitive_grant:{user_id}:{entry_id}"


# ── Request / Response schemas ────────────────────────────────────────────────

class UserFactRequest(BaseModel):
    fact: str
    category: Optional[str] = None
    slot_key: Optional[str] = None
    confidence: Optional[float] = None


class UserFactUpdateRequest(BaseModel):
    fact: Optional[str] = None
    category: Optional[str] = None
    slot_key: Optional[str] = None
    confidence: Optional[float] = None


class AgentEntryRequest(BaseModel):
    entry_type: str   # error | success | decision | state | artifact | note
    content: str
    tags: Optional[List[str]] = None
    # G-004: sensitive entries show as placeholders in memory_recall until user grants access
    sensitive: bool = False


class AgentEntryUpdateRequest(BaseModel):
    entry_type: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None


# ── Q-03: response models ────────────────────────────────────────────────────
# Several endpoints below return only a subset of these fields (e.g.
# add_user_fact never sets updated_at, update_user_fact never sets source) --
# fields are Optional so one shared model covers every real return shape
# without over-declaring what a given endpoint promises.

class ChatSummaryItem(BaseModel):
    conversation_id: str
    summary_text: str
    token_estimate: int
    updated_at: Optional[str] = None


class UserFactItem(BaseModel):
    id: str
    fact: str
    category: Optional[str] = None
    slot_key: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserFactsSummaryResponse(BaseModel):
    summary: str
    source: str  # "file" | "rebuilt" | "fallback"


class DeleteFactResponse(BaseModel):
    deleted: bool
    fact_id: str


class ClearFactsResponse(BaseModel):
    deleted_count: int


class AgentScopeItem(BaseModel):
    scope_key: str
    entry_count: int


class AgentEntryItem(BaseModel):
    id: str
    entry_type: str
    content: str
    tags: Optional[List[str]] = None
    sensitive: Optional[bool] = None
    scope_key: Optional[str] = None
    source_conversation_id: Optional[str] = None
    source_execution_id: Optional[str] = None
    created_at: Optional[str] = None


class DeleteEntryResponse(BaseModel):
    deleted: bool
    entry_id: str


class GrantSensitiveAccessResponse(BaseModel):
    granted: bool
    entry_id: str
    scope_key: str
    expires_in: int


class SensitiveAccessStatusResponse(BaseModel):
    entry_id: str
    granted: bool
    expires_in_seconds: Optional[int] = None


class RevokeSensitiveAccessResponse(BaseModel):
    revoked: bool
    entry_id: str


class ClearAgentScopeResponse(BaseModel):
    deleted_count: int
    scope_key: str


class MemorySummaryResponse(BaseModel):
    chat_summaries_count: int
    user_facts_count: int
    agent_entries_count: int
    agent_active_scopes: List[str]


# ── Helper ────────────────────────────────────────────────────────────────────

def _dt_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


# ── Scope 1: Chat summaries (read-only) ──────────────────────────────────────

@router.get("/chats", response_model=List[ChatSummaryItem])
async def list_chat_summaries(
    limit: int = 10,
    auth=Depends(get_current_auth),
):
    """
    Return recent in-conversation compression summaries for the authenticated user.
    These are produced by MemoryEngine when a single conversation grows too long
    and older messages are compressed out of the context window. They are NOT
    injected between conversations (cross-session chat memory was removed in V4.40).
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        summaries = await MemoryRepo.get_recent_chat_summaries(
            db, user_id=user_id, limit=limit
        )

    return [
        {
            "conversation_id": s.conversation_id,
            "summary_text":    s.summary_text,
            "token_estimate":  s.token_estimate,
            "updated_at":      _dt_str(s.updated_at),
        }
        for s in summaries
    ]


# ── Scope 2: User facts ───────────────────────────────────────────────────────

@router.get("/user-facts", response_model=List[UserFactItem])
async def list_user_facts(
    category: Optional[str] = None,
    limit: int = 50,
    auth=Depends(get_current_auth),
):
    """
    List all stored user-level memory facts.
    Optionally filter by category (preference|skill|style|context|misc).
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        facts = await MemoryRepo.get_user_facts(
            db, user_id=user_id, category=category, limit=limit
        )

    return [
        {
            "id":         f.id,
            "fact":       f.fact,
            "category":   f.category,
            "slot_key":   f.slot_key,
            "source":     f.source,
            "confidence": f.confidence,
            "created_at": _dt_str(f.created_at),
            "updated_at": _dt_str(f.updated_at),
        }
        for f in facts
    ]


@router.get("/user-facts/summary", response_model=UserFactsSummaryResponse)
async def get_user_facts_summary(
    refresh: bool = False,
    auth=Depends(get_current_auth),
):
    """
    Return the auto-generated markdown summary of all user facts (B-041).

    The summary is normally read from the pre-built .md file which is kept
    current by the fact write hooks.  Pass ?refresh=true to force an immediate
    rebuild from the DB before returning (useful for testing or after bulk edits).

    Returns:
        { "summary": "<markdown string>", "source": "file" | "rebuilt" | "fallback" }
    """
    user_id = auth["user_id"]

    if refresh:
        md = await force_rebuild_summary(user_id)
        source = "rebuilt"
    else:
        from core.memory.fact_summary import read_summary
        md = read_summary(user_id)
        source = "file"

    if not md:
        # File doesn't exist yet — build it now
        md = await force_rebuild_summary(user_id)
        source = "fallback"

    return {"summary": md or "", "source": source}


@router.post("/user-facts", response_model=UserFactItem)
async def add_user_fact(
    body: UserFactRequest,
    auth=Depends(get_current_auth),
):
    """Manually add a user-level memory fact."""
    user_id = auth["user_id"]

    if not body.fact.strip():
        raise HTTPException(status_code=422, detail="fact must not be empty")

    async with AsyncSessionLocal() as db:
        row = await MemoryRepo.add_user_fact(
            db,
            user_id=user_id,
            fact=body.fact.strip(),
            category=body.category,
            slot_key=body.slot_key,
            source="manual",
            confidence=body.confidence if body.confidence is not None else 1.0,
        )

    schedule_summary_rebuild(user_id)

    return {
        "id":         row.id,
        "fact":       row.fact,
        "category":   row.category,
        "slot_key":   row.slot_key,
        "source":     row.source,
        "confidence": row.confidence,
        "created_at": _dt_str(row.created_at),
    }


@router.put("/user-facts/{fact_id}", response_model=UserFactItem)
async def update_user_fact(
    fact_id: str,
    body: UserFactUpdateRequest,
    auth=Depends(get_current_auth),
):
    """Edit a user-level memory fact."""
    user_id = auth["user_id"]

    updates: Dict[str, Any] = {}
    if body.fact is not None:
        if not body.fact.strip():
            raise HTTPException(status_code=422, detail="fact must not be empty")
        updates["fact"] = body.fact.strip()
    if body.category is not None:
        updates["category"] = body.category
    if body.slot_key is not None:
        updates["slot_key"] = body.slot_key
    if body.confidence is not None:
        updates["confidence"] = body.confidence

    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    async with AsyncSessionLocal() as db:
        row = await MemoryRepo.update_user_fact(db, fact_id, user_id, updates)

    if not row:
        raise HTTPException(status_code=404, detail="Fact not found")

    schedule_summary_rebuild(user_id)

    return {
        "id":         row.id,
        "fact":       row.fact,
        "category":   row.category,
        "slot_key":   row.slot_key,
        "confidence": row.confidence,
        "updated_at": _dt_str(row.updated_at),
    }


@router.delete("/user-facts/{fact_id}", status_code=200, response_model=DeleteFactResponse)
async def delete_user_fact(
    fact_id: str,
    auth=Depends(get_current_auth),
):
    """Delete a specific user-level memory fact."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        deleted = await MemoryRepo.delete_user_fact(db, fact_id, user_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Fact not found")

    schedule_summary_rebuild(user_id)
    return {"deleted": True, "fact_id": fact_id}


@router.delete("/user-facts", status_code=200, response_model=ClearFactsResponse)
async def clear_user_facts(
    confirm: bool = False,
    auth=Depends(get_current_auth),
):
    """
    Delete ALL user-level memory facts.
    Requires ?confirm=true to prevent accidental data loss.
    """
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="Pass ?confirm=true to clear all user facts",
        )
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        count = await MemoryRepo.clear_user_facts(db, user_id)

    schedule_summary_rebuild(user_id)
    return {"deleted_count": count}


# ── Scope 3: Agent entries ────────────────────────────────────────────────────

@router.get("/agent", summary="List all agent memory scope keys for the current user", response_model=List[AgentScopeItem])
async def list_agent_scopes(auth=Depends(get_current_auth)):
    """
    Return all distinct agent_scope_key values for the authenticated user,
    along with an entry count per scope.  (B-012)

    Use this to discover available scopes before calling
    GET /memory/agent/{scope_key}.

    Returns: [ { "scope_key": str, "entry_count": int }, ... ]
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        scopes = await MemoryRepo.list_agent_scopes(db, user_id=user_id)
    return scopes


@router.get("/agent/{scope_key}", response_model=List[AgentEntryItem])
async def list_agent_entries(
    scope_key: str,
    type: Optional[str] = None,
    limit: int = 50,
    auth=Depends(get_current_auth),
):
    """
    List all agent-scope memory entries for a given scope_key.
    Optionally filter by type (error|success|decision|state|artifact|note).
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        entries = await MemoryRepo.get_agent_entries(
            db,
            user_id=user_id,
            scope_key=scope_key,
            entry_type=type,
            limit=limit,
        )

    return [
        {
            "id":                     e.id,
            "entry_type":             e.entry_type,
            "content":                e.content,
            "tags":                   e.tags,
            "sensitive":              bool(getattr(e, "sensitive", False)),
            "source_conversation_id": e.source_conversation_id,
            "source_execution_id":    e.source_execution_id,
            "created_at":             _dt_str(e.created_at),
        }
        for e in entries
    ]


@router.post("/agent/{scope_key}", response_model=AgentEntryItem)
async def add_agent_entry(
    scope_key: str,
    body: AgentEntryRequest,
    auth=Depends(get_current_auth),
):
    """Manually add an agent-scope memory entry."""
    user_id = auth["user_id"]

    valid_types = {"error", "success", "decision", "state", "artifact", "note"}
    if body.entry_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"entry_type must be one of: {sorted(valid_types)}",
        )
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="content must not be empty")

    async with AsyncSessionLocal() as db:
        row = await MemoryRepo.add_agent_entry(
            db,
            user_id=user_id,
            scope_key=scope_key,
            entry_type=body.entry_type,
            content=body.content.strip(),
            tags=body.tags,
            sensitive=body.sensitive,
        )

    return {
        "id":          row.id,
        "scope_key":   scope_key,
        "entry_type":  row.entry_type,
        "content":     row.content,
        "tags":        row.tags,
        "sensitive":   bool(row.sensitive),
        "created_at":  _dt_str(row.created_at),
    }


@router.put("/agent/{scope_key}/{entry_id}", response_model=AgentEntryItem)
async def update_agent_entry(
    scope_key: str,
    entry_id: str,
    body: AgentEntryUpdateRequest,
    auth=Depends(get_current_auth),
):
    """Edit an agent-scope memory entry."""
    user_id = auth["user_id"]

    updates: Dict[str, Any] = {}
    if body.entry_type is not None:
        valid_types = {"error", "success", "decision", "state", "artifact", "note"}
        if body.entry_type not in valid_types:
            raise HTTPException(
                status_code=422,
                detail=f"entry_type must be one of: {sorted(valid_types)}",
            )
        updates["entry_type"] = body.entry_type
    if body.content is not None:
        if not body.content.strip():
            raise HTTPException(status_code=422, detail="content must not be empty")
        updates["content"] = body.content.strip()
    if body.tags is not None:
        updates["tags"] = body.tags

    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    async with AsyncSessionLocal() as db:
        row = await MemoryRepo.update_agent_entry(db, entry_id, user_id, updates)

    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")

    return {
        "id":         row.id,
        "entry_type": row.entry_type,
        "content":    row.content,
        "tags":       row.tags,
    }


@router.delete("/agent/{scope_key}/{entry_id}", status_code=200, response_model=DeleteEntryResponse)
async def delete_agent_entry(
    scope_key: str,
    entry_id: str,
    auth=Depends(get_current_auth),
):
    """Delete a specific agent-scope memory entry."""
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        deleted = await MemoryRepo.delete_agent_entry(db, entry_id, user_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Entry not found")

    return {"deleted": True, "entry_id": entry_id}


# ── B-033: Sensitive memory grant-access ─────────────────────────────────────

@router.post("/agent/{scope_key}/{entry_id}/grant-access", response_model=GrantSensitiveAccessResponse)
async def grant_sensitive_access(
    scope_key: str,
    entry_id: str,
    auth=Depends(get_current_auth),
):
    """
    Temporarily grant the agent read access to a sensitive memory entry (B-033).

    Stores a broker key ``sensitive_grant:{user_id}:{entry_id}`` with a 1-hour
    TTL.  While the grant is active, ``memory_recall`` returns the real content
    instead of the blocked placeholder.

    - 404 if the entry does not exist or is not owned by the user.
    - 422 if the entry is not marked sensitive (grant not needed).
    """
    from core.execution.redis_registry import redis_execution_registry

    user_id = auth["user_id"]

    async with AsyncSessionLocal() as db:
        entry = await MemoryRepo.get_agent_entry(db, entry_id, user_id)

    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.agent_scope_key != scope_key:
        raise HTTPException(status_code=404, detail="Entry not found in this scope")
    if not entry.sensitive:
        raise HTTPException(
            status_code=422,
            detail="Entry is not marked sensitive — grant-access is not required",
        )

    client = await redis_execution_registry.connect()
    await client.set(_grant_key(user_id, entry_id), "1", ex=_SENSITIVE_GRANT_TTL)

    return {
        "granted":    True,
        "entry_id":   entry_id,
        "scope_key":  scope_key,
        "expires_in": _SENSITIVE_GRANT_TTL,
    }


@router.get("/agent/{scope_key}/{entry_id}/grant-access", response_model=SensitiveAccessStatusResponse)
async def get_sensitive_access_status(
    scope_key: str,
    entry_id: str,
    auth=Depends(get_current_auth),
):
    """
    Check whether an active grant exists for a sensitive memory entry (B-033).

    Returns ``granted=True`` when a grant key is present (not expired).
    Returns ``granted=False`` when no grant exists or it has expired.
    ``expires_in_seconds`` is always null in LocalBroker mode.
    """
    from core.execution.redis_registry import redis_execution_registry

    user_id = auth["user_id"]

    client = await redis_execution_registry.connect()

    granted = (await client.exists(_grant_key(user_id, entry_id))) > 0

    return {
        "entry_id":           entry_id,
        "granted":            granted,
        "expires_in_seconds": None,
    }


@router.delete("/agent/{scope_key}/{entry_id}/grant-access", status_code=200, response_model=RevokeSensitiveAccessResponse)
async def revoke_sensitive_access(
    scope_key: str,
    entry_id: str,
    auth=Depends(get_current_auth),
):
    """
    Revoke a previously granted temporary access to a sensitive memory entry
    (B-033).  Safe to call even if no grant exists.
    """
    from core.execution.redis_registry import redis_execution_registry

    user_id = auth["user_id"]

    client = await redis_execution_registry.connect()
    await client.delete(_grant_key(user_id, entry_id))
    return {"revoked": True, "entry_id": entry_id}


@router.delete("/agent/{scope_key}", status_code=200, response_model=ClearAgentScopeResponse)
async def clear_agent_scope(
    scope_key: str,
    confirm: bool = False,
    auth=Depends(get_current_auth),
):
    """
    Delete ALL entries for a given agent scope.
    Requires ?confirm=true to prevent accidental data loss.
    """
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="Pass ?confirm=true to clear all entries for this scope",
        )
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        count = await MemoryRepo.clear_agent_scope(db, user_id, scope_key)

    return {"deleted_count": count, "scope_key": scope_key}


# ── Diagnostic ────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=MemorySummaryResponse)
async def memory_summary(auth=Depends(get_current_auth)):
    """
    Return an overview of all memory stores for the authenticated user.
    Useful for diagnostics and the memory management UI.
    """
    user_id = auth["user_id"]
    async with AsyncSessionLocal() as db:
        summary = await MemoryRepo.get_memory_summary(db, user_id)

    return summary
