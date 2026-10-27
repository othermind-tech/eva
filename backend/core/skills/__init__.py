# core/skills/__init__.py
"""
EVA Skills subsystem — Anthropic-format skill loader and registry.

Skills are file-based, self-contained domain-expertise packages that follow
the Anthropic Skills specification (SKILL.md frontmatter + markdown body).
They are NOT stored in the database; the filesystem is the source of truth.

Each user's skills live in their own workspace directory (V4.33+):
    ~/eva_workspace/{user_id}/skills/
    ├── code-reviewer/
    │   ├── SKILL.md          ← Required
    │   ├── scripts/          ← Optional executable helpers
    │   └── references/       ← Optional large reference docs
    └── my-custom.skill       ← ZIP bundle, auto-unpacked on load

Registries are lazily created per user on first access via get_skill_registry(user_id).
"""
from .loader import SkillLoader, SkillMeta
from .registry import SkillRegistry, get_skill_registry

__all__ = ["SkillLoader", "SkillMeta", "SkillRegistry", "get_skill_registry"]
