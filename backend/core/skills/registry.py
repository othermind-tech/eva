# core/skills/registry.py
"""
In-memory skill registry — per-user, lazily populated.

Lifecycle:
  1. First access for a user_id triggers load from ~/eva_workspace/{user_id}/skills/
  2. Consulted by the worker to build the available_skills injection block
  3. Consulted by api/skills.py for CRUD endpoints
  4. reset_skill_registry(user_id) forces a reload on next access

Thread safety: the registry is read-mostly (only modified on install/delete/reload).
For EVA's async single-process model this is safe; no locks are needed.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from core.skills.loader import SkillLoader, SkillMeta

logger = logging.getLogger(__name__)

# Per-user registry cache — keyed by user_id, populated lazily on first access.
_registries: Dict[str, "SkillRegistry"] = {}


class SkillRegistry:
    """Holds all loaded skills for a single user in memory."""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)
        self._skills: Dict[str, SkillMeta] = {}
        self._loader = SkillLoader(self.skills_dir)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """(Re)load all skills from disk.  Safe to call multiple times."""
        self._skills = self._loader.load_all()
        logger.info(
            "[SKILLS] Registry loaded — %d skill(s): %s",
            len(self._skills),
            list(self._skills.keys()),
        )

    # ── Read operations ────────────────────────────────────────────────────────

    def list_skills(self) -> List[SkillMeta]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def get_skill(self, name: str) -> Optional[SkillMeta]:
        return self._skills.get(name)

    def get_skill_content(self, name: str) -> Optional[str]:
        """Return the full text of SKILL.md for *name*, or None if not found."""
        meta = self.get_skill(name)
        if not meta:
            return None
        skill_md = meta.path / "SKILL.md"
        try:
            return skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("[SKILLS] Cannot read SKILL.md for '%s': %s", name, exc)
            return None

    @property
    def count(self) -> int:
        return len(self._skills)

    # ── Write operations ───────────────────────────────────────────────────────

    def register(self, meta: SkillMeta) -> None:
        """Add or replace a skill in the registry (does not write to disk)."""
        self._skills[meta.name] = meta
        logger.info("[SKILLS] Registered skill: %s", meta.name)

    def remove(self, name: str) -> bool:
        """Remove a skill from the in-memory registry. Returns True if found."""
        if name in self._skills:
            del self._skills[name]
            logger.info("[SKILLS] Removed skill: %s", name)
            return True
        return False

    # ── System prompt injection ────────────────────────────────────────────────

    def build_available_skills_block(self, user_message: str = "") -> str:
        """
        Build the metadata-only block injected into the system prompt.

        Progressive disclosure: only name + description + absolute file path are
        included.  The agent reads the full SKILL.md via the read_file tool when
        it decides a skill is relevant — exactly as in the Anthropic Skills spec.

        P-14: trigger-scoped filtering, applied before injection rather than
        listing unconditionally --
        - No trigger (the default -- every pre-existing skill) -> always listed,
          identical to pre-P-14 behavior.
        - KeywordTrigger/TaskTrigger -> listed only if one of its keywords
          appears as a whole token in ``user_message`` (case-insensitive).
          ``user_message=""`` (the default) matches nothing, so an existing
          caller that doesn't pass it degrades to "keyword skills excluded"
          rather than crashing or silently reverting to always-listed.
        - PathTrigger -> never listed here. It's a file-touch trigger with no
          message-time signal to filter on; matches OpenHands' own choice to
          keep path-scoped skills out of the general advertised list. See
          SkillMeta.matches_path() for the (currently unwired) per-file check.

        Returns an empty string if no skills are registered or none pass
        the filter.
        """
        if not self._skills:
            return ""

        lines = [
            "## 🛠 Available Skills",
            "",
            "You have access to the following specialised skills. When a user's request",
            "matches a skill's description, read the skill file using the read_file tool",
            "before responding — do not rely solely on general knowledge.",
            "",
        ]

        matched_any = False
        for meta in self.list_skills():
            if meta.trigger is not None and not meta.matches_message(user_message):
                continue
            matched_any = True
            # Use absolute path so the agent can read the file regardless of CWD.
            skill_path = str(meta.path / "SKILL.md")
            lines.append(
                f"- **{meta.name}** (`{skill_path}`): {meta.description}"
            )

        if not matched_any:
            return ""
        return "\n".join(lines)


# ── Module-level factory ───────────────────────────────────────────────────────

def get_skill_registry(user_id: str) -> SkillRegistry:
    """
    Return the SkillRegistry for *user_id*, creating and loading it on first call.

    Skills are stored at ~/eva_workspace/{user_id}/skills/ — fully isolated per user.
    """
    if user_id not in _registries:
        from utils.workspace import get_user_skills_dir  # noqa: PLC0415
        skills_dir = get_user_skills_dir(user_id)
        reg = SkillRegistry(skills_dir)
        reg.load()
        _registries[user_id] = reg
    return _registries[user_id]


def reset_skill_registry(user_id: Optional[str] = None) -> None:
    """
    Evict the cached registry so the next call to get_skill_registry() reloads
    from disk.

    - reset_skill_registry(user_id)  — evicts only that user's registry
    - reset_skill_registry()         — evicts all cached registries (e.g. tests)
    """
    if user_id is not None:
        _registries.pop(user_id, None)
    else:
        _registries.clear()
