# core/memory/fact_summary.py
"""
User Fact Summary Generator (B-041 — Hybrid Memory)

Maintains a per-user markdown file that reflects all stored UserMemoryFact rows.
The file is the primary injection artifact for Scope 2 memory — compact, human-
readable, and always consistent with the DB.

Design:
  - DB rows are the source of truth (CRUD via MemoryRepo).
  - This file is DERIVED from the DB, never edited directly.
  - Any write to user_memory_facts (insert / update / delete) calls
    schedule_summary_rebuild(user_id), which debounces 3 s and then regenerates.
  - Regeneration is template-based (no LLM cost): rows are grouped by slot
    section and rendered as clean markdown.
  - The injector (core/memory/long_term.py) reads the file at context time
    instead of querying raw rows.

File location:
  blob_storage/memory/<user_id>_facts.md
  (blob_storage/ is already used for file uploads — a known-good writable dir.)

Slot vocabulary (keep in sync with prompts/memory.py _SLOT_VOCABULARY):
  SINGULAR_SLOTS — at most one fact per slot per user
  PLURAL_SLOTS   — multiple facts allowed, deduplicated by the extractor
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from core.db.session import AsyncSessionLocal
from core.db.repository import MemoryRepo

logger = logging.getLogger(__name__)

# ── Slot vocabulary ───────────────────────────────────────────────────────────

SINGULAR_SLOTS: frozenset[str] = frozenset({
    "user.name",
    "user.full_name",   # V6.0 — profile-locked formal name (PATCH /auth/profile)
    "user.about",       # V6.0 — profile-locked bio / about text (PATCH /auth/profile)
    "user.occupation",
    "user.location",
    "user.primary_language",
    "user.experience_level",
    "user.timezone",
})

PLURAL_SLOTS: frozenset[str] = frozenset({
    "user.skill",
    "user.tool",
    "user.preference",
    "user.style",
    "user.project",
    "user.misc",
})

ALL_SLOTS: frozenset[str] = SINGULAR_SLOTS | PLURAL_SLOTS

# ── Section metadata for .md rendering ───────────────────────────────────────

# Maps slot_key → (section heading, emoji)
_SLOT_SECTIONS: Dict[str, tuple[str, str]] = {
    "user.name":             ("Identity",              "👤"),
    "user.full_name":        ("Identity",              "👤"),  # V6.0 profile-locked
    "user.about":            ("Identity",              "👤"),  # V6.0 profile-locked
    "user.occupation":       ("Identity",              "👤"),
    "user.location":         ("Identity",              "👤"),
    "user.timezone":         ("Identity",              "👤"),
    "user.experience_level": ("Identity",              "👤"),
    "user.primary_language": ("Technical Stack",       "🛠️"),
    "user.skill":            ("Skills & Expertise",    "🎯"),
    "user.tool":             ("Technical Stack",       "🛠️"),
    "user.preference":       ("Preferences",           "⚙️"),
    "user.style":            ("Working Style",         "🎨"),
    "user.project":          ("Projects & Context",    "📂"),
    "user.misc":             ("Other",                 "📌"),
}

# Ordered list of section headings (controls rendering order in the .md)
_SECTION_ORDER: List[str] = [
    "Identity",
    "Technical Stack",
    "Skills & Expertise",
    "Preferences",
    "Working Style",
    "Projects & Context",
    "Other",
]

# ── File path ─────────────────────────────────────────────────────────────────

def get_summary_path(user_id: str) -> Path:
    """Return the absolute path to the user's facts .md file."""
    from utils.workspace import get_user_memory_dir
    return get_user_memory_dir(user_id) / "facts.md"


def read_summary(user_id: str) -> Optional[str]:
    """
    Read and return the current .md summary for a user.
    Returns None if the file does not exist yet.
    Non-fatal — caller must handle None gracefully.
    """
    path = get_summary_path(user_id)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(
            "fact_summary: failed to read summary file for %s: %s", user_id, exc
        )
    return None


# ── Debounce registry ─────────────────────────────────────────────────────────

_pending: Dict[str, asyncio.Task] = {}
_DEBOUNCE_SECONDS = 3.0


def schedule_summary_rebuild(user_id: str) -> None:
    """
    Schedule a summary rebuild for user_id, debounced by _DEBOUNCE_SECONDS.

    If a rebuild is already pending for this user, the timer is reset so that
    rapid batch writes (e.g. extraction inserting 5 facts) produce a single
    rebuild rather than 5.

    Safe to call from sync or async context — uses the running event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop — skip (should not happen in normal operation)
        logger.debug("fact_summary: no running loop — skipping rebuild schedule for %s", user_id)
        return

    # Cancel any already-pending rebuild for this user
    existing = _pending.get(user_id)
    if existing and not existing.done():
        existing.cancel()

    async def _deferred():
        await asyncio.sleep(_DEBOUNCE_SECONDS)
        await _rebuild_summary(user_id)
        _pending.pop(user_id, None)

    task = loop.create_task(_deferred())
    _pending[user_id] = task


# ── Summary generation ────────────────────────────────────────────────────────

async def _rebuild_summary(user_id: str) -> None:
    """
    Fetch all facts from DB and write the .md summary file.
    All errors are non-fatal — a failed rebuild just leaves the previous file in place.
    """
    try:
        async with AsyncSessionLocal() as db:
            facts = await MemoryRepo.get_user_facts(db, user_id=user_id, limit=200)

        content = _render_markdown(user_id, facts)

        path = get_summary_path(user_id)  # get_user_memory_dir() creates dir
        path.write_text(content, encoding="utf-8")

        logger.info(
            "fact_summary: rebuilt summary for user %s — %d fact(s), %d bytes",
            user_id,
            len(facts),
            len(content),
        )
    except Exception as exc:
        logger.warning(
            "fact_summary: rebuild failed for user %s (non-fatal): %s",
            user_id,
            exc,
            exc_info=True,
        )


def _render_markdown(user_id: str, facts) -> str:
    """
    Render the facts list as a clean markdown document, grouped by section.
    Facts without a slot_key fall into "Other".
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Group facts by section heading
    sections: Dict[str, List[str]] = {s: [] for s in _SECTION_ORDER}

    for f in facts:
        slot = f.slot_key or "user.misc"
        heading, _ = _SLOT_SECTIONS.get(slot, ("Other", "📌"))
        # Format: "Fact text" — keep it clean
        line = f.fact.strip()
        if line:
            sections[heading].append(line)

    lines: List[str] = [
        "# 🧠 User Memory",
        f"*Last updated: {now}*",
        "",
    ]

    any_content = False
    for section_name in _SECTION_ORDER:
        items = sections.get(section_name, [])
        if not items:
            continue
        emoji = ""
        # Find emoji from first slot that maps to this section
        for slot, (heading, em) in _SLOT_SECTIONS.items():
            if heading == section_name:
                emoji = em
                break
        lines.append(f"## {emoji} {section_name}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
        any_content = True

    if not any_content:
        lines.append("*No facts recorded yet.*")
        lines.append("")

    return "\n".join(lines)


# ── Force rebuild (for API use) ───────────────────────────────────────────────

async def force_rebuild_summary(user_id: str) -> str:
    """
    Immediately rebuild the summary (bypasses debounce).
    Returns the rendered markdown string.
    Used by GET /memory/user-facts/summary endpoint.
    """
    await _rebuild_summary(user_id)
    return read_summary(user_id) or ""
