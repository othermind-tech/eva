# tools/default/memory_tools.py
"""
Native memory tools — allow the agent to actively manage long-term memory
during a conversation without routing through the REST API.

Three tools are provided:

  memory_recall(query, scope, limit)
      Search user facts (Scope 2) and/or agent entries (Scope 3) using a
      case-insensitive substring match.  Also hints related Skills (B-003).

  memory_store(scope, content, entry_type, scope_key)
      Immediately write a user fact or agent-scope entry to the DB.
      Source is tagged "agent_self_reported" to distinguish from
      auto-extracted facts (MemoryExtractor).

  memory_forget(entry_id, scope)
      Delete a specific memory entry by its ID.

Context injection:
  The ToolDispatcher sets ``tool_instance.execution`` before calling run().
  All three tools read ``self.execution.user_id`` and (where needed)
  ``self.execution.conversation_id`` / ``self.execution.execution_id``
  from that object — no HTTP round-trip required.

Authority levels:
  memory_recall  — READ_ONLY   (never gates HitL)
  memory_store   — MUTATING    (gates when tool_approval_mode=always_ask)
  memory_forget  — DESTRUCTIVE (gates when tool_approval_mode != auto)
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.tools.registry import default_tool
from core.tools.base import ToolBase, ToolResult, ToolAuthority
from config.settings import get_user_session


# ── B-033: Sensitive memory grant helper ─────────────────────────────────────

async def _check_sensitive_grant(user_id: str, entry_id: str) -> bool:
    """
    Return True if an active grant exists in the broker for (user_id, entry_id).
    Fails-safe to False if the key is absent.
    """
    try:
        from core.execution.redis_registry import redis_execution_registry
        client = await redis_execution_registry.connect()
        return (await client.exists(f"sensitive_grant:{user_id}:{entry_id}")) > 0
    except Exception:
        return False


# ── Argument schemas ──────────────────────────────────────────────────────────

class MemoryRecallArgs(BaseModel):
    query: str = Field(..., description="Search term to look for in memory")
    scope: str = Field(
        "all",
        description="Which scope to search: 'user' (personal facts), 'agent' (cross-conversation knowledge), or 'all'",
    )
    limit: int = Field(5, ge=1, le=20, description="Max results to return per scope")


class MemoryStoreArgs(BaseModel):
    scope: str = Field(
        ...,
        description="Where to store: 'user' for personal facts about the user, 'agent' for shared cross-conversation knowledge",
    )
    content: str = Field(..., description="The fact or piece of knowledge to store")
    entry_type: str = Field(
        "general",
        description="Category or type tag for this entry (e.g. 'preference', 'fact', 'insight')",
    )
    scope_key: str = Field(
        "assistant",
        description="Namespace for agent-scope entries — use to organise knowledge by topic (ignored for user scope)",
    )


class MemoryForgetArgs(BaseModel):
    entry_id: str = Field(..., description="ID of the memory entry to delete")
    scope: str = Field(
        "user",
        description="Scope of the entry to delete: 'user' or 'agent'",
    )


# ── memory_recall ─────────────────────────────────────────────────────────────

@default_tool(
    name="memory_recall",
    description=(
        "Search long-term memory for facts or knowledge that match a query. "
        "Use this to recall what you know about the user (scope='user'), any stored "
        "cross-conversation knowledge (scope='agent'), or both (scope='all'). "
        "Returns matching entries plus hints about related Skills if any exist."
    ),
    args_schema=MemoryRecallArgs,
    authority=ToolAuthority.READ_ONLY,
    category="memory",
    summary="Search long-term memory for user facts or agent knowledge matching a query.",
)
class MemoryRecallTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        from core.db.session import AsyncSessionLocal
        from core.db.repository import MemoryRepo

        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available — cannot recall memory"})

        # Respect session memory flags
        session = get_user_session(user_id)
        user_mem_on: bool = session.get("user_memory_enabled", True)
        agent_mem_on: bool = session.get("agent_memory_enabled", True)

        query: str = args.get("query", "")
        scope: str = args.get("scope", "all").lower()
        limit: int = min(int(args.get("limit", 5)), 20)

        user_facts: List[Dict] = []
        agent_entries: List[Dict] = []
        skipped: List[str] = []

        async with AsyncSessionLocal() as db:
            if scope in ("user", "all"):
                if user_mem_on:
                    facts = await MemoryRepo.search_user_facts(db, user_id, query, limit=limit)
                    user_facts = [
                        {
                            "id": str(f.id),
                            "fact": f.fact,
                            "category": f.category,
                            "confidence": f.confidence,
                        }
                        for f in facts
                    ]
                else:
                    skipped.append("user (disabled by session policy)")

            if scope in ("agent", "all"):
                if agent_mem_on:
                    entries = await MemoryRepo.search_agent_entries(db, user_id, query, limit=limit)
                    sensitive_blocked = 0
                    for e in entries:
                        is_sensitive = getattr(e, "sensitive", False)
                        if is_sensitive:
                            # B-033: check broker for an active user grant
                            grant_active = await _check_sensitive_grant(user_id, str(e.id))
                            if grant_active:
                                agent_entries.append({
                                    "id":                 str(e.id),
                                    "scope_key":          e.agent_scope_key,
                                    "entry_type":         e.entry_type,
                                    "content":            e.content,
                                    "sensitive":          True,
                                    "sensitive_unlocked": True,
                                })
                            else:
                                sensitive_blocked += 1
                                agent_entries.append({
                                    "id":         str(e.id),
                                    "scope_key":  e.agent_scope_key,
                                    "entry_type": e.entry_type,
                                    "content": (
                                        "[Sensitive memory entry — access denied by user. "
                                        "Grant access to retrieve this information.]"
                                    ),
                                    "sensitive": True,
                                    "blocked":   True,
                                })
                        else:
                            agent_entries.append({
                                "id":         str(e.id),
                                "scope_key":  e.agent_scope_key,
                                "entry_type": e.entry_type,
                                "content":    e.content,
                                "sensitive":  False,
                            })
                    if sensitive_blocked:
                        skipped.append(
                            f"{sensitive_blocked} sensitive agent entry(s) blocked — "
                            "user approval required to read"
                        )
                else:
                    skipped.append("agent (disabled by session policy)")

        total_found = len(user_facts) + len(agent_entries)

        result: Dict[str, Any] = {
            "query": query,
            "scope": scope,
            "total_found": total_found,
            "user_facts": user_facts,
            "agent_entries": agent_entries,
        }

        if skipped:
            result["skipped_scopes"] = skipped

        if total_found == 0 and not skipped:
            result["message"] = f"No memories found matching '{query}'"
        elif total_found == 0 and skipped:
            result["message"] = f"No memories searched — scopes disabled: {', '.join(skipped)}"

        # ── B-003: hint related Skills ────────────────────────────────────────
        # If the recall query matches any loaded Skill description or name,
        # suggest that skill to the agent.  Non-fatal — failure just skips the hint.
        try:
            from core.skills.registry import get_skill_registry  # noqa: PLC0415
            registry = get_skill_registry(user_id)
            q_lower = query.lower()
            related: List[str] = []
            # Try list_skills() first; fall back to iterating _skills dict
            skills_iter = (
                registry.list_skills()
                if hasattr(registry, "list_skills")
                else registry._skills.values()
                if hasattr(registry, "_skills")
                else []
            )
            for skill in skills_iter:
                name = getattr(skill, "name", "")
                desc = getattr(skill, "description", "")
                if q_lower in name.lower() or q_lower in desc.lower():
                    related.append(name)
            if related:
                result["related_skills"] = related
        except Exception:  # noqa: BLE001
            pass

        return ToolResult(result=result)


# ── memory_store ──────────────────────────────────────────────────────────────

@default_tool(
    name="memory_store",
    description=(
        "Store a fact or piece of knowledge in long-term memory so it persists "
        "across conversations. "
        "Use scope='user' for personal facts about the user (preferences, background, goals). "
        "Use scope='agent' for general knowledge, insights, or cross-conversation context. "
        "Stored entries are immediately available to future memory_recall calls."
    ),
    args_schema=MemoryStoreArgs,
    authority=ToolAuthority.MUTATING,
    category="memory",
    summary="Store a fact or piece of knowledge in long-term memory for future recall.",
)
class MemoryStoreTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        from core.db.session import AsyncSessionLocal
        from core.db.repository import MemoryRepo

        execution = getattr(self, "execution", None)
        user_id      = getattr(execution, "user_id", None)
        conv_id      = getattr(execution, "conversation_id", None)
        exec_id      = getattr(execution, "execution_id", None)

        if not user_id:
            return ToolResult(result={"error": "No user context available — cannot store memory"})

        scope: str      = args.get("scope", "").lower()
        content: str    = args.get("content", "").strip()
        entry_type: str = args.get("entry_type", "general")
        scope_key: str  = args.get("scope_key", "assistant")

        if not content:
            return ToolResult(result={"error": "content must not be empty"})

        if scope not in ("user", "agent"):
            return ToolResult(result={
                "error": f"Invalid scope '{scope}'. Must be 'user' or 'agent'."
            })

        # Respect session memory flags
        session = get_user_session(user_id)
        if scope == "user" and not session.get("user_memory_enabled", True):
            return ToolResult(result={
                "stored": False,
                "scope": scope,
                "error": "User memory is disabled in session settings. Enable 'user_memory_enabled' to store user facts.",
            })
        if scope == "agent" and not session.get("agent_memory_enabled", True):
            return ToolResult(result={
                "stored": False,
                "scope": scope,
                "error": "Agent memory is disabled in session settings. Enable 'agent_memory_enabled' to store agent entries.",
            })

        async with AsyncSessionLocal() as db:
            if scope == "user":
                row = await MemoryRepo.add_user_fact(
                    db,
                    user_id=user_id,
                    fact=content,
                    category=entry_type,
                    source="agent_self_reported",
                )
                return ToolResult(result={
                    "stored": True,
                    "scope": "user",
                    "id": str(row.id),
                    "fact": row.fact,
                    "category": row.category,
                    "message": "Fact stored in user memory.",
                })

            # scope == "agent"
            row = await MemoryRepo.add_agent_entry(
                db,
                user_id=user_id,
                scope_key=scope_key,
                entry_type=entry_type,
                content=content,
                source_conversation_id=conv_id,
                source_execution_id=exec_id,
            )
            return ToolResult(result={
                "stored": True,
                "scope": "agent",
                "scope_key": scope_key,
                "id": str(row.id),
                "entry_type": row.entry_type,
                "content": row.content,
                "message": "Entry stored in agent memory.",
            })


# ── memory_forget ─────────────────────────────────────────────────────────────

@default_tool(
    name="memory_forget",
    description=(
        "Delete a specific memory entry by its ID. "
        "Use scope='user' to delete a user fact, scope='agent' to delete an agent entry. "
        "Returns whether the entry was found and deleted. "
        "To find the ID of an entry, use memory_recall first."
    ),
    args_schema=MemoryForgetArgs,
    authority=ToolAuthority.DESTRUCTIVE,
    category="memory",
    summary="Delete a specific memory entry by its ID (use memory_recall first to find IDs).",
)
class MemoryForgetTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        from core.db.session import AsyncSessionLocal
        from core.db.repository import MemoryRepo

        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available — cannot forget memory"})

        entry_id: str = args.get("entry_id", "").strip()
        scope: str    = args.get("scope", "user").lower()

        if not entry_id:
            return ToolResult(result={"error": "entry_id must not be empty"})

        if scope not in ("user", "agent"):
            return ToolResult(result={
                "error": f"Invalid scope '{scope}'. Must be 'user' or 'agent'."
            })

        # Respect session memory flags
        session = get_user_session(user_id)
        if scope == "user" and not session.get("user_memory_enabled", True):
            return ToolResult(result={
                "deleted": False,
                "entry_id": entry_id,
                "scope": scope,
                "error": "User memory is disabled in session settings. Enable 'user_memory_enabled' to manage user facts.",
            })
        if scope == "agent" and not session.get("agent_memory_enabled", True):
            return ToolResult(result={
                "deleted": False,
                "entry_id": entry_id,
                "scope": scope,
                "error": "Agent memory is disabled in session settings. Enable 'agent_memory_enabled' to manage agent entries.",
            })

        async with AsyncSessionLocal() as db:
            if scope == "user":
                deleted = await MemoryRepo.delete_user_fact(db, entry_id, user_id)
            else:  # "agent"
                deleted = await MemoryRepo.delete_agent_entry(db, entry_id, user_id)

        return ToolResult(result={
            "deleted": deleted,
            "entry_id": entry_id,
            "scope": scope,
            "message": (
                "Memory entry deleted successfully."
                if deleted
                else "Entry not found or not owned by this user."
            ),
        })
