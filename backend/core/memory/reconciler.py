# core/memory/reconciler.py
"""
A-04 Phase C — one-time memory reconciliation (backfill).

Distinct from core/memory/extractor.py's per-conversation extraction: this
module looks at an EXISTING stored list (no new conversation involved) and
proposes deletions for redundant history that accumulated before the
dedupe/supersedes pipeline in extractor.py existed (Scope 3 had no such
pipeline until A-04 Phase A; Scope 2 has had one since B-041, so its yield
here is expected to be much smaller).

User-triggered only, never scheduled/automatic -- called directly by
scripts/reconcile_memory.py, not exposed as an API endpoint (deliberately;
see that script's own module docstring for why):
  - propose_*() is a pure dry-run. It never deletes anything, only returns
    candidate {"delete_id", "reason"} proposals for a human to review.
  - apply_*() deletes exactly the IDs it is given — the caller decides what
    that list is (after the user has explicitly confirmed which proposals
    to act on). This module never re-derives or expands it, and never
    re-runs the LLM to decide.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from core.db.session import AsyncSessionLocal
from core.db.repository import MemoryRepo
from core.tools.user_registry import UserToolRegistry
from core.providers.fallback_call import resolve_and_call_once
from prompts.memory import (
    AGENT_MEMORY_RECONCILE_SYSTEM,
    AGENT_MEMORY_RECONCILE_USER_TEMPLATE,
    USER_FACT_RECONCILE_SYSTEM,
    USER_FACT_RECONCILE_USER_TEMPLATE,
)

# Bounded review size per pass -- a single reconciliation call reviews at
# most this many stored rows. Matches the same "recent, for context" shape
# extractor.py already uses (get_user_facts(limit=100) / get_agent_entries
# (limit=50)), just wider since this is a deliberate one-time review, not a
# per-conversation extraction call that fires constantly.
_MAX_ROWS_PER_RECONCILE = 200


def _parse_json_list(raw: str, max_items: int) -> List[Dict[str, Any]]:
    """Same tolerant-parsing shape as MemoryExtractor._parse_json_list --
    handles markdown fences / trailing prose around the JSON array.

    Uses find()/rfind() (first "[" to last "]"), not a non-greedy regex --
    see MemoryExtractor._parse_json_list's own comment for the real bug a
    non-greedy regex has here (truncates at a nested array's own closing
    bracket, e.g. AGENT_MEMORY_RECONCILE_SYSTEM's future-proofing against a
    schema change adding one)."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        result = json.loads(cleaned[start : end + 1])
        if isinstance(result, list):
            return result[:max_items]
    except json.JSONDecodeError:
        pass
    return []


async def _call_llm(
    system_prompt: str,
    user_content: str,
    session: Dict[str, Any],
    user_id: str,
    caller_tag: str,
    max_tokens: int = 900,
) -> str:
    """Same call shape as MemoryExtractor._call_llm -- non-streaming,
    resolve-attempt-record fallback tier, no tools.

    max_tokens: 900 -- same fix and same reasoning as
    MemoryExtractor._call_llm (see its docstring): a reasoning-mandatory
    model can silently consume an unset max_tokens budget entirely on
    hidden chain-of-thought, producing an empty (not erroring) response.
    """
    return await resolve_and_call_once(
        session=session,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        user_id=user_id,
        max_tokens=max_tokens,
        tools=UserToolRegistry(default_tools=False, enabled_tools=[]),
        caller_tag=caller_tag,
    )


# ── Scope 3: agent memory ──────────────────────────────────────────────────

async def propose_agent_reconciliation(
    user_id: str,
    scope_key: str,
    session: Dict[str, Any],
) -> List[Dict[str, str]]:
    """
    Dry-run: examine every stored entry for this scope and propose which
    ones to delete as redundant history. Deletes nothing.

    Returns [{"delete_id": ..., "reason": ...}, ...] -- empty if the scope
    has fewer than 2 entries (nothing to compare) or is already clean.
    """
    async with AsyncSessionLocal() as db:
        entries = await MemoryRepo.get_agent_entries(
            db, user_id=user_id, scope_key=scope_key, limit=_MAX_ROWS_PER_RECONCILE
        )

    if len(entries) < 2:
        return []

    # get_agent_entries orders newest-first already -- matches the prompt's
    # "newest first" framing without needing to re-sort here.
    entries_block = "\n".join(
        f"  [ID: {e.id}] [{e.entry_type}] {e.content.strip()}" for e in entries
    )
    user_content = AGENT_MEMORY_RECONCILE_USER_TEMPLATE.format(entries_block=entries_block)

    raw = await _call_llm(
        AGENT_MEMORY_RECONCILE_SYSTEM, user_content, session, user_id,
        caller_tag="MemoryReconciler.agent_entries",
    )

    valid_ids = {e.id for e in entries}
    proposals = _parse_json_list(raw, max_items=len(entries))
    return [
        {"delete_id": p["delete_id"], "reason": (p.get("reason") or "").strip()}
        for p in proposals
        if isinstance(p, dict) and p.get("delete_id") in valid_ids
    ]


async def apply_agent_reconciliation(user_id: str, entry_ids: List[str]) -> int:
    """Delete exactly the given agent-entry IDs -- no re-derivation, no LLM
    call. Returns the number actually deleted (an already-deleted or
    not-owned ID is silently skipped, not an error)."""
    deleted = 0
    for entry_id in entry_ids:
        async with AsyncSessionLocal() as db:
            if await MemoryRepo.delete_agent_entry(db, entry_id, user_id):
                deleted += 1
    return deleted


# ── Scope 2: user facts ────────────────────────────────────────────────────

async def propose_user_fact_reconciliation(
    user_id: str,
    session: Dict[str, Any],
) -> List[Dict[str, str]]:
    """
    Dry-run: examine every stored user fact and propose which ones to
    delete as redundant history. Deletes nothing.

    Expected to find much less than Scope 3 -- extraction has deduped
    against existing facts since B-041, so pre-existing duplicates should
    be rare here, but a one-time check costs little and catches anything
    that predates B-041 or slipped through (e.g. manually-added facts).
    """
    async with AsyncSessionLocal() as db:
        facts = await MemoryRepo.get_user_facts(
            db, user_id=user_id, limit=_MAX_ROWS_PER_RECONCILE
        )

    if len(facts) < 2:
        return []

    facts_block = "\n".join(
        f"  [ID: {f.id}] [{f.slot_key or 'user.misc'}] {f.fact.strip()}" for f in facts
    )
    user_content = USER_FACT_RECONCILE_USER_TEMPLATE.format(facts_block=facts_block)

    raw = await _call_llm(
        USER_FACT_RECONCILE_SYSTEM, user_content, session, user_id,
        caller_tag="MemoryReconciler.user_facts",
    )

    valid_ids = {f.id for f in facts}
    proposals = _parse_json_list(raw, max_items=len(facts))
    return [
        {"delete_id": p["delete_id"], "reason": (p.get("reason") or "").strip()}
        for p in proposals
        if isinstance(p, dict) and p.get("delete_id") in valid_ids
    ]


async def apply_user_fact_reconciliation(user_id: str, fact_ids: List[str]) -> int:
    """Delete exactly the given fact IDs -- no re-derivation, no LLM call.
    Returns the number actually deleted. Caller is responsible for
    triggering schedule_summary_rebuild(user_id) afterward (same
    responsibility api/memory.py's existing single-fact DELETE already
    owns -- kept there rather than duplicated here)."""
    deleted = 0
    for fact_id in fact_ids:
        async with AsyncSessionLocal() as db:
            if await MemoryRepo.delete_user_fact(db, fact_id, user_id):
                deleted += 1
    return deleted
