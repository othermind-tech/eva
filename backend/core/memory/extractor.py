# core/memory/extractor.py
"""
Memory Extractor (V4.0 — B-041 Hybrid Memory)

Runs asynchronously after an execution completes to extract and store
long-term memories across two scopes:

  Scope 2 — User facts  : preferences, style, skills inferred from conversation
  Scope 3 — Agent entries: task state, errors, decisions for the agent scope

V4.0 changes (B-041):
  - Passes existing user facts to the LLM so it can classify each candidate as
    "new", "redundant" (skip), or "supersedes" (update existing row).
  - Assigns a slot_key to every fact, enabling singular-slot enforcement in Python
    (one fact per attribute like occupation, location, name).
  - After all writes, calls schedule_summary_rebuild() to regenerate the .md file.

The extractor uses the same provider/model as the main session.
All failures are non-fatal — a failed extraction must never affect the user.

Extraction is triggered from the worker via asyncio.create_task() so it
never blocks the response.  The extractor creates its own DB sessions
independently of the outer worker session.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from logging import getLogger
from typing import Any, Dict, List, Optional

from core.db.session import AsyncSessionLocal
from core.db.repository import DBRepo, MemoryRepo
from core.tools.user_registry import UserToolRegistry
from core.db.models import Conversation
from core.memory.fact_summary import (
    SINGULAR_SLOTS,
    schedule_summary_rebuild,
)
from core.providers.fallback_call import resolve_and_call_once
from config.settings import (
    MEMORY_MIN_MESSAGES_FOR_EXTRACTION,
    MEMORY_MAX_FACTS_PER_EXTRACTION,
    MEMORY_MAX_ENTRIES_PER_EXTRACTION,
    AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS,
)
from prompts.memory import USER_EXTRACTION_SYSTEM, AGENT_EXTRACTION_SYSTEM

logger = getLogger(__name__)

# ── Extraction config (values from config.settings) ──────────────────────────
_MIN_MESSAGES_FOR_EXTRACTION = MEMORY_MIN_MESSAGES_FOR_EXTRACTION
_MAX_FACTS_PER_EXTRACTION    = MEMORY_MAX_FACTS_PER_EXTRACTION
_MAX_ENTRIES_PER_EXTRACTION  = MEMORY_MAX_ENTRIES_PER_EXTRACTION
# A-04 Phase B: only "error"-type agent entries get a default expiry -- see
# the constant's own comment in config/settings.py for why the other types
# (and all user facts) stay permanent.
_ERROR_ENTRY_TTL = timedelta(days=AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS)


class MemoryExtractor:
    """
    Post-execution memory extractor.

    Call extract_and_store() as an asyncio background task after each execution.
    It will fetch the conversation messages, call the LLM, and persist results.
    """

    async def extract_and_store(
        self,
        conversation_id: str,
        user_id: str,
        session: Dict[str, Any],
        execution_id: Optional[str] = None,
        execution_mode: str = "conversational",
    ) -> None:
        """
        Main entry point.  Safe to call from asyncio.create_task().
        All exceptions are caught — this method must never raise.

        execution_mode: "conversational" | "plan_execute"
          - "conversational": user-fact extraction (Scope 2) runs; agent-entry
            extraction (Scope 3) is SKIPPED — chat mode does not accumulate
            agent-scope state (task decisions, errors, artefacts).
          - "plan_execute": both Scope 2 and Scope 3 extraction run.
        """
        try:
            await self._run(conversation_id, user_id, session, execution_id, execution_mode)
        except Exception as exc:
            logger.warning(
                "MemoryExtractor: extraction failed (non-fatal)",
                exc_info=exc,
                extra={
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "execution_id": execution_id,
                    "execution_mode": execution_mode,
                },
            )

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _run(
        self,
        conversation_id: str,
        user_id: str,
        session: Dict[str, Any],
        execution_id: Optional[str],
        execution_mode: str = "conversational",
    ) -> None:
        # 1. Load conversation messages + conversation metadata
        async with AsyncSessionLocal() as db:
            messages = await DBRepo.list_messages_for_conversation(
                db, conversation_id
            )
            conversation = await DBRepo.get_conversation(db, conversation_id)

        # Capture project_id for B-069 slot gating
        conversation_project_id = getattr(conversation, "project_id", None) if conversation else None

        # Only extract from substantive conversations
        user_messages = [m for m in messages if m.role in ("user", "assistant")]
        if len(user_messages) < _MIN_MESSAGES_FOR_EXTRACTION:
            logger.debug(
                "MemoryExtractor: skipping (too few messages)",
                extra={
                    "conversation_id": conversation_id,
                    "message_count": len(user_messages),
                },
            )
            return

        # Build a flat text representation for the LLM
        conversation_text = "\n".join(
            f"{m.role.upper()}: {(m.content or '').strip()}"
            for m in user_messages
            if m.content
        )

        provider  = session.get("provider", "")
        model     = session.get("model", "")
        # B-076: auto-derive agent scope key from project when not explicitly set.
        # Priority: explicit session.agent_scope_key → conversation.project_id → None
        # This ensures project conversations automatically accumulate agent memory
        # without requiring the user to manually configure a matching scope key.
        scope_key = session.get("agent_scope_key") or conversation_project_id

        if not provider or not model:
            logger.debug(
                "MemoryExtractor: no provider/model configured — skipping",
                extra={"conversation_id": conversation_id},
            )
            return

        # B-071 — Signal quality gate (pre-LLM)
        # Skip user-fact extraction for conversations with no durable preference
        # signal (greetings, factual lookups, one-off tool queries, etc.).
        # Agent-scope extraction is exempt — its signals are independent.
        _user_extraction_enabled = (
            session.get("user_memory_enabled", True)
            and self._has_extraction_signal(user_messages)
        )
        if session.get("user_memory_enabled", True) and not _user_extraction_enabled:
            logger.debug(
                "MemoryExtractor: no durable preference signal detected — "
                "skipping user-fact extraction",
                extra={"conversation_id": conversation_id},
            )

        # 2. Extract user facts (Scope 2) — if enabled and signal gate passed
        if _user_extraction_enabled:
            try:
                wrote_any = await self._extract_and_store_user_facts(
                    conversation_text=conversation_text,
                    user_id=user_id,
                    session=session,
                    conversation_id=conversation_id,
                    allow_project_slot=bool(conversation_project_id),
                )
                if wrote_any:
                    # Trigger debounced .md rebuild (non-blocking)
                    schedule_summary_rebuild(user_id)
            except Exception as exc:
                logger.warning(
                    "MemoryExtractor: user fact extraction failed (skipped)",
                    exc_info=exc,
                    extra={"conversation_id": conversation_id},
                )

        # 3. Extract agent entries (Scope 3) — only in plan_execute mode.
        # Chat (conversational) mode does not produce the kind of structured
        # task decisions / errors / artefacts that agent memory is designed for.
        # Writing Scope 3 entries from every chat turn pollutes the agent scope
        # with trivial conversational noise and leaks one conversation's context
        # into a future unrelated plan_execute run.
        if scope_key and session.get("agent_memory_enabled", True) and execution_mode == "plan_execute":
            try:
                await self._extract_and_store_agent_entries(
                    conversation_text=conversation_text,
                    session=session,
                    user_id=user_id,
                    scope_key=scope_key,
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                )
            except Exception as exc:
                logger.warning(
                    "MemoryExtractor: agent entry extraction failed (skipped)",
                    exc_info=exc,
                    extra={
                        "conversation_id": conversation_id,
                        "scope_key": scope_key,
                    },
                )

    # ── User fact pipeline ────────────────────────────────────────────────────

    async def _extract_and_store_user_facts(
        self,
        conversation_text: str,
        user_id: str,
        session: Dict[str, Any],
        conversation_id: str,
        allow_project_slot: bool = False,
    ) -> bool:
        """
        Full pipeline:
          1. Fetch existing facts → build context block for LLM.
          2. Call LLM with enriched prompt (existing facts + conversation).
          3. Post-filter: drop user.project if conversation is not project-attached
             (B-069); cap user.misc at 5 existing entries (B-072).
          4. Process each result: new / redundant / supersedes.
          5. Return True if any DB write occurred (triggers .md rebuild).
        """
        # Fetch existing facts (all, for context)
        async with AsyncSessionLocal() as db:
            existing = await MemoryRepo.get_user_facts(db, user_id=user_id, limit=100)

        # Build existing-facts context block for the prompt
        if existing:
            existing_lines = [
                f"  [ID: {f.id}] [{f.slot_key or 'user.misc'}] {f.fact.strip()}"
                for f in existing
            ]
            existing_block = "\n".join(existing_lines)
        else:
            existing_block = "  (none yet)"

        # Inject existing facts into the prompt template.
        # NOTE: Use str.replace() — NOT .format() — because USER_EXTRACTION_SYSTEM
        # contains JSON examples like {"fact": "..."} whose braces confuse
        # Python's .format() and raise KeyError: '"fact"'.
        system_prompt = USER_EXTRACTION_SYSTEM.replace(
            "{existing_facts_block}", existing_block
        )

        raw = await self._call_llm(
            system_prompt=system_prompt,
            conversation_text=conversation_text,
            session=session,
            user_id=user_id,
            caller_tag="MemoryExtractor.user_facts",
        )

        items = self._parse_json_list(raw, _MAX_FACTS_PER_EXTRACTION)
        # Filter: must have a non-empty "fact" field
        items = [i for i in items if isinstance(i, dict) and i.get("fact")]

        # B-069 — Drop user.project facts when conversation has no project attached.
        # Project-scoped facts belong to the Project entity (ProjectArtifactUpdater),
        # not the generic user-fact table.
        if not allow_project_slot:
            before = len(items)
            items = [i for i in items if i.get("slot_key") != "user.project"]
            if len(items) < before:
                logger.debug(
                    "MemoryExtractor: dropped user.project facts (no project attached)",
                    extra={"conversation_id": conversation_id, "dropped": before - len(items)},
                )

        # B-072 — Cap user.misc at 5 entries to prevent trash accumulation.
        # Count how many user.misc facts already exist in the DB.
        existing_misc_count = sum(
            1 for f in existing if (f.slot_key or "user.misc") == "user.misc"
        )
        if existing_misc_count >= 5:
            before = len(items)
            items = [i for i in items if i.get("slot_key") != "user.misc"]
            if len(items) < before:
                logger.debug(
                    "MemoryExtractor: dropped user.misc facts (cap reached)",
                    extra={
                        "conversation_id": conversation_id,
                        "existing_misc": existing_misc_count,
                        "dropped": before - len(items),
                    },
                )

        if not items:
            return False

        # Build a quick lookup of existing facts by ID for validation
        existing_by_id: Dict[str, Any] = {f.id: f for f in existing}

        wrote_any = False
        inserted  = 0
        updated   = 0
        skipped   = 0

        for item in items:
            action       = (item.get("action") or "new").strip().lower()
            fact_text    = item.get("fact", "").strip()
            category     = item.get("category") or "misc"
            slot_key     = item.get("slot_key") or "user.misc"
            supersedes_id = item.get("supersedes_id") or None

            if not fact_text:
                continue

            # ── "redundant" → discard
            if action == "redundant":
                skipped += 1
                continue

            # ── "supersedes" → update existing row by ID
            if action == "supersedes" and supersedes_id:
                if supersedes_id in existing_by_id:
                    # V6.0 / S-10 — Profile-lock guard: the "new" branch below
                    # already refuses to overwrite a source="profile" fact for
                    # a matched singular slot, but this branch updates by ID
                    # directly and had no equivalent check -- the LLM could
                    # bypass the guard entirely just by classifying the same
                    # update as "supersedes" instead of "new". Mirrors the
                    # exact check below, applied here too.
                    target_fact = existing_by_id[supersedes_id]
                    if getattr(target_fact, "source", None) == "profile":
                        skipped += 1
                        continue
                    async with AsyncSessionLocal() as db:
                        await MemoryRepo.update_user_fact(
                            db,
                            fact_id=supersedes_id,
                            user_id=user_id,
                            updates={
                                "fact":     fact_text,
                                "category": category,
                                "slot_key": slot_key,
                            },
                        )
                    updated += 1
                    wrote_any = True
                    continue
                # supersedes_id not found — fall through to "new" handling

            # ── "new" (or supersedes with bad/missing ID)
            # Singular slot enforcement: if a fact already occupies this slot,
            # update it rather than inserting a duplicate.
            if slot_key in SINGULAR_SLOTS:
                async with AsyncSessionLocal() as db:
                    existing_slot_fact = await MemoryRepo.get_user_fact_by_slot(
                        db, user_id, slot_key
                    )
                if existing_slot_fact:
                    # V6.0 — Profile-lock guard: slots written by PATCH /auth/profile
                    # (source="profile") are owned by the user, not the LLM extractor.
                    # Skip silently so the extractor never overwrites formal name/bio.
                    if getattr(existing_slot_fact, "source", None) == "profile":
                        skipped += 1
                        continue

                    # Only update if the text actually changed
                    if existing_slot_fact.fact.strip() != fact_text:
                        async with AsyncSessionLocal() as db:
                            await MemoryRepo.update_user_fact(
                                db,
                                fact_id=existing_slot_fact.id,
                                user_id=user_id,
                                updates={
                                    "fact":     fact_text,
                                    "category": category,
                                    "slot_key": slot_key,
                                },
                            )
                        updated += 1
                        wrote_any = True
                    else:
                        skipped += 1
                    continue

            # Insert as genuinely new fact
            async with AsyncSessionLocal() as db:
                await MemoryRepo.add_user_fact(
                    db,
                    user_id=user_id,
                    fact=fact_text,
                    category=category,
                    slot_key=slot_key,
                    source="auto_extracted",
                    confidence=0.85,
                )
            inserted += 1
            wrote_any = True

        logger.info(
            "MemoryExtractor: user facts processed",
            extra={
                "conversation_id": conversation_id,
                "inserted":        inserted,
                "updated":         updated,
                "skipped":         skipped,
            },
        )
        return wrote_any

    # ── Signal quality gate ───────────────────────────────────────────────────

    @staticmethod
    def _has_extraction_signal(messages: list) -> bool:
        """
        B-071 — Cheap pre-LLM heuristic: return True only when the user messages
        contain a plausible durable preference signal.

        This avoids firing a full extraction LLM call on greetings, factual
        lookups, weather queries, one-shot tool requests, etc.

        Rules (applied in order):
          1. Total user text < 40 chars → skip (too short to contain a durable fact)
          2. Pure greeting/confirmation tokens → skip
          3. Any first-person identity/preference marker → signal present → extract

        This is a safety net — the prompt-level gate (STEP 0 in USER_EXTRACTION_SYSTEM)
        is the primary defence. Both layers together minimise bad extractions.
        """
        user_text = " ".join(
            (m.content or "").strip()
            for m in messages
            if m.role == "user" and m.content
        ).strip().lower()

        # 1. Too short to contain a durable fact
        if len(user_text) < 40:
            return False

        # 2. Pure greeting / confirmation → no signal
        TRIVIAL_TOKENS = {
            "hi", "hello", "hey", "yo", "ok", "okay", "thanks", "thank you",
            "bye", "goodbye", "yes", "no", "sure", "got it", "great", "cool",
            "nice", "perfect", "done", "stop", "cancel",
        }
        if user_text in TRIVIAL_TOKENS:
            return False

        # 3. First-person identity / preference markers → durable signal
        SIGNAL_MARKERS = (
            "i am ", "i'm ", "i am a ", "i'm a ",
            "my name", "call me ",
            "i work ", "i work at", "i work on",
            "i live ", "i'm based", "i am based",
            "my job", "my role", "my title",
            "i prefer ", "i like ", "i don't like", "i hate ",
            "i use ", "i mainly use", "i usually use",
            "i always ", "i never ", "please always", "please never",
            "my timezone", "my language",
            "i've been ", "i have been ", "i've worked",
            "i'm building", "i am building", "my project",
        )
        return any(marker in user_text for marker in SIGNAL_MARKERS)

    # ── LLM helpers ──────────────────────────────────────────────────────────

    async def _call_llm(
        self,
        system_prompt: str,
        conversation_text: str,
        session: Dict[str, Any],
        user_id: str,
        caller_tag: str = "MemoryExtractor",
        max_tokens: int = 900,
    ) -> str:
        """
        Make a non-streaming LLM call and return the raw text response.

        Resolve-Attempt-Record tier (V6.8): tries the first candidate not
        currently cooling down from a recent failure (session's
        provider/model, or the first fallback_chain entry), records a
        failure into the shared cross-execution cooldown store on error.
        This also fixes a real bug: this method used to read
        session["api_key"] with no environment-variable fallback at all
        (unlike title_generator.py / prompts.py's own, separately
        maintained copies of that logic) -- resolve_and_call_once's shared
        candidate builder resolves it correctly for every provider now.

        max_tokens: 900 -- live-found (2026-09-14, A-04 live verification)
        that omitting max_tokens entirely (the previous behaviour) leaves a
        "reasoning-mandatory" model (e.g. gpt-oss) free to consume the
        provider's own default budget entirely on hidden chain-of-thought
        before ever emitting the JSON array, producing a silent empty
        response -- no exception, just [] parsed from empty text. Same root
        cause already fixed for VerifierEngine (500) and
        PlannerEngine.generate()/replan() (800/900); matches replan()'s
        value since extraction's output (up to 5 items, each with
        type/content/tags/action/supersedes_id) is comparable in shape to a
        replanned step list. Investigated wiring this from
        capability_registry.get_model().max_output_tokens instead (a real
        per-model field) but it's unused everywhere in the codebase today
        and empty for ollama_cloud entirely (confirmed: zero registry
        entries for the provider) -- a fixed constant matching the existing
        precedent is the right scope for now.
        """
        return await resolve_and_call_once(
            session=session,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": conversation_text}],
            user_id=user_id,
            max_tokens=max_tokens,
            tools=UserToolRegistry(default_tools=False, enabled_tools=[]),
            caller_tag=caller_tag,
        )

    def _parse_json_list(self, raw: str, max_items: int) -> List[Dict[str, Any]]:
        """
        Parse a JSON array from raw LLM text.
        Handles common formatting issues (markdown code fences, trailing text).
        Returns an empty list on any parse error.
        """
        # Strip markdown fences if present
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

        # Find the first [ ... last ] in the output. Bug found live
        # (2026-09-15, A-04 investigation): a non-greedy regex (r"\[.*?\]")
        # used here previously matched only up to the FIRST "]" -- but every
        # extraction schema includes a nested "tags": [...] array, so the
        # regex always truncated at THAT array's closing bracket instead of
        # the outer array's, producing invalid JSON that silently parsed to
        # [] on every real call. This was root-caused directly against a
        # real LLM response (a well-formed entry, confirmed via temporary
        # diagnostic logging) and reproduced in isolation before this fix.
        # find()/rfind() mirrors the already-correct pattern
        # core/execution/verifier.py's _extract_json() uses for the
        # single-object case.
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []

        try:
            result = json.loads(raw[start : end + 1])
            if isinstance(result, list):
                return result[:max_items]
        except json.JSONDecodeError:
            pass

        return []

    async def _extract_and_store_agent_entries(
        self,
        conversation_text: str,
        session: Dict[str, Any],
        user_id: str,
        scope_key: str,
        conversation_id: str,
        execution_id: Optional[str],
    ) -> None:
        """
        Full pipeline for Scope 3 (agent memory), mirroring
        _extract_and_store_user_facts()'s dedupe/supersedes shape:
          1. Fetch existing entries for this scope -> build context block for LLM.
          2. Call LLM with enriched prompt (existing entries + conversation).
          3. Process each result: new / redundant / supersedes.

        A-04 fix: previously this scope had no awareness of what was already
        stored, so every extraction blindly inserted -- duplicates accumulated
        and a "state" entry never replaced the last one. Scope 2 (user facts)
        has had this dedupe/supersedes shape since B-041; this brings Scope 3
        to parity with it.
        """
        valid_types = {
            "error", "success", "decision", "state", "artifact", "note"
        }

        # Fetch existing entries for this scope (all, for context -- same
        # shape as _extract_and_store_user_facts' `existing` fetch).
        async with AsyncSessionLocal() as db:
            existing = await MemoryRepo.get_agent_entries(
                db, user_id=user_id, scope_key=scope_key, limit=50
            )

        if existing:
            existing_lines = [
                f"  [ID: {e.id}] [{e.entry_type}] {e.content.strip()}"
                for e in existing
            ]
            existing_block = "\n".join(existing_lines)
        else:
            existing_block = "  (none yet)"

        # NOTE: Use str.replace() -- NOT .format() -- same reason as
        # USER_EXTRACTION_SYSTEM above: AGENT_EXTRACTION_SYSTEM's JSON
        # examples contain literal {"type": ...} braces that break .format().
        system_prompt = AGENT_EXTRACTION_SYSTEM.replace(
            "{existing_agent_entries_block}", existing_block
        )

        raw = await self._call_llm(
            system_prompt=system_prompt,
            conversation_text=conversation_text,
            session=session,
            user_id=user_id,
            caller_tag="MemoryExtractor.agent_entries",
        )

        items = self._parse_json_list(raw, _MAX_ENTRIES_PER_EXTRACTION)
        items = [i for i in items if isinstance(i, dict) and i.get("content")]
        if not items:
            return

        # Quick lookup of existing entries by ID for supersedes validation.
        existing_by_id: Dict[str, Any] = {e.id: e for e in existing}

        inserted = 0
        updated  = 0
        skipped  = 0

        for item in items:
            action       = (item.get("action") or "new").strip().lower()
            entry_type   = item.get("type") if item.get("type") in valid_types else "note"
            content      = (item.get("content") or "").strip()
            tags         = item.get("tags")
            # G-004 self-flagging: default false for older-style/malformed
            # items that omit the key.
            sensitive    = bool(item.get("sensitive", False))
            supersedes_id = item.get("supersedes_id") or None

            if not content:
                continue

            # -- "redundant" -> discard
            if action == "redundant":
                skipped += 1
                continue

            # -- "supersedes" -> update existing row by ID
            if action == "supersedes" and supersedes_id:
                if supersedes_id in existing_by_id:
                    async with AsyncSessionLocal() as db:
                        await MemoryRepo.update_agent_entry(
                            db,
                            entry_id=supersedes_id,
                            user_id=user_id,
                            updates={
                                "entry_type": entry_type,
                                "content":    content,
                                "tags":       tags,
                                # Refresh provenance to the confirming
                                # conversation/execution rather than leaving
                                # it pointed at whichever session first wrote
                                # this entry.
                                "source_conversation_id": conversation_id,
                                "source_execution_id":    execution_id,
                            },
                        )
                    updated += 1
                    continue
                # supersedes_id not found (hallucinated/stale) -- fall
                # through to "new" handling, same fallback as user facts.

            # -- "new" (or supersedes with bad/missing ID)
            # A-04 Phase B: "error" entries get a default TTL since nothing
            # else naturally revisits/supersedes them; every other type
            # (state is kept current by the Phase A supersedes rule; the
            # rest are durable by design) stays permanent.
            entry_expires_at = (
                datetime.now(timezone.utc) + _ERROR_ENTRY_TTL
                if entry_type == "error" else None
            )
            async with AsyncSessionLocal() as db:
                await MemoryRepo.add_agent_entry(
                    db,
                    user_id=user_id,
                    scope_key=scope_key,
                    entry_type=entry_type,
                    content=content,
                    tags=tags,
                    source_conversation_id=conversation_id,
                    source_execution_id=execution_id,
                    sensitive=sensitive,
                    expires_at=entry_expires_at,
                )
            inserted += 1

        logger.info(
            "MemoryExtractor: agent entries processed",
            extra={
                "conversation_id": conversation_id,
                "scope_key":       scope_key,
                "inserted":        inserted,
                "updated":         updated,
                "skipped":         skipped,
            },
        )


# Singleton — import and use directly
memory_extractor = MemoryExtractor()
