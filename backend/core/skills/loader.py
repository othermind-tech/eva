# core/skills/loader.py
"""
Anthropic-format SKILL.md parser and skill directory scanner.

Implements the Anthropic Skills specification:
- SKILL.md frontmatter: YAML between --- delimiters
- Required fields: name (kebab-case, ≤64 chars), description (≤1024 chars, no <>)
- Optional fields: license, allowed-tools, compatibility, metadata
- Bundle format: .skill files are ZIP archives, auto-unpacked on load
"""

import re
import zipfile
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.skills.trigger import (
    KeywordTrigger,
    PathTrigger,
    TaskTrigger,
    TriggerType,
    matches_keyword_trigger,
    matches_path_trigger,
)

logger = logging.getLogger(__name__)

# ── Validation constants (Anthropic Skills spec) ──────────────────────────────
_MAX_NAME_LEN  = 64
_MAX_DESC_LEN  = 1024
_VALID_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$')
# P-14: "trigger" is EVA's own extension to the Anthropic frontmatter spec,
# not part of it -- optional, absent means "no trigger scoping" (today's
# always-listed behavior, fully backward compatible).
_VALID_FM_KEYS = {"name", "description", "license", "allowed-tools",
                  "metadata", "compatibility", "trigger"}


@dataclass
class SkillMeta:
    """Parsed metadata from a SKILL.md frontmatter block."""
    name: str
    description: str
    path: Path                                     # absolute path to skill directory
    allowed_tools: List[str] = field(default_factory=list)
    license: str = ""
    compatibility: str = ""
    metadata: dict = field(default_factory=dict)
    # P-14: None = no trigger scoping, always listed (pre-existing behavior,
    # unchanged for every skill that doesn't opt in).
    trigger: Optional[TriggerType] = None

    def matches_message(self, message: str) -> bool:
        """True if this skill's trigger (keyword or task type) matches
        ``message``. Always False for a PathTrigger or no trigger -- callers
        that want "no trigger = always include" should check `trigger is
        None` separately, this method only answers "does the trigger fire"."""
        if isinstance(self.trigger, (KeywordTrigger, TaskTrigger)):
            return matches_keyword_trigger(self.trigger, message)
        return False

    def matches_path(self, file_path: str) -> Optional[str]:
        """Return the matching glob pattern if this skill has a PathTrigger
        matching ``file_path``, else None. No consumer yet -- EVA has no
        per-tool-call file-touch hook feeding this back into skill
        selection; this exists so that wiring is a small addition later,
        not a redesign."""
        if isinstance(self.trigger, PathTrigger):
            return matches_path_trigger(self.trigger, file_path)
        return None


class SkillLoader:
    """
    Scans a skills directory, unpacks .skill ZIP bundles, parses SKILL.md
    frontmatter, and validates each skill against the Anthropic specification.
    """

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_all(self) -> Dict[str, SkillMeta]:
        """
        Scan the skills directory and return a dict of {name → SkillMeta}.
        Non-fatal: invalid skills are logged and skipped.
        """
        skills: Dict[str, SkillMeta] = {}

        if not self.skills_dir.exists():
            logger.info("[SKILLS] Skills directory not found: %s", self.skills_dir)
            return skills

        # Unpack any .skill ZIP bundles first
        for skill_file in sorted(self.skills_dir.glob("*.skill")):
            unpacked = self._unpack_skill_zip(skill_file)
            if unpacked:
                logger.info("[SKILLS] Unpacked %s → %s", skill_file.name, unpacked)

        # Scan subdirectories for SKILL.md
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md_path = skill_dir / "SKILL.md"
            if not skill_md_path.exists():
                continue
            try:
                meta = self.parse_skill_md(skill_md_path, skill_dir)
                if meta:
                    skills[meta.name] = meta
                    logger.debug("[SKILLS] Loaded: %s", meta.name)
            except Exception as exc:
                logger.warning(
                    "[SKILLS] Failed to load skill from %s: %s",
                    skill_dir.name, exc,
                )

        return skills

    def parse_skill_md(
        self, skill_md_path: Path, skill_dir: Path
    ) -> Optional[SkillMeta]:
        """
        Parse and validate a single SKILL.md file.
        Returns SkillMeta on success, None on validation failure.
        """
        try:
            content = skill_md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("[SKILLS] Cannot read %s: %s", skill_md_path, exc)
            return None

        frontmatter_str, _ = self._split_frontmatter(content)
        if frontmatter_str is None:
            logger.warning("[SKILLS] No frontmatter in %s", skill_md_path)
            return None

        data = self._parse_yaml_frontmatter(frontmatter_str)

        # ── Required field validation ─────────────────────────────────────────
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()

        if not name:
            logger.warning("[SKILLS] Missing 'name' in %s", skill_md_path)
            return None
        if not description:
            logger.warning("[SKILLS] Missing 'description' in %s", skill_md_path)
            return None

        # Name format: kebab-case, max 64 chars
        if len(name) > _MAX_NAME_LEN or not _VALID_NAME_RE.match(name):
            logger.warning(
                "[SKILLS] Invalid skill name '%s' in %s "
                "(must be kebab-case, 2–64 chars)",
                name, skill_md_path,
            )
            return None

        # Description: max 1024 chars, no < or > (Anthropic spec)
        if len(description) > _MAX_DESC_LEN:
            logger.warning(
                "[SKILLS] Description too long (%d chars, max %d) in %s",
                len(description), _MAX_DESC_LEN, skill_md_path,
            )
            return None
        if "<" in description or ">" in description:
            logger.warning(
                "[SKILLS] Description contains invalid chars (< or >) in %s",
                skill_md_path,
            )
            return None

        # ── Optional fields ───────────────────────────────────────────────────
        raw_tools = data.get("allowed-tools", [])
        allowed_tools = self._parse_tools_list(raw_tools)

        raw_meta = data.get("metadata", {})
        metadata = raw_meta if isinstance(raw_meta, dict) else {}

        trigger = self._parse_trigger(data.get("trigger"), skill_md_path)

        return SkillMeta(
            name=name,
            description=description,
            path=skill_dir.resolve(),
            allowed_tools=allowed_tools,
            license=str(data.get("license", "")).strip(),
            compatibility=str(data.get("compatibility", "")).strip(),
            metadata=metadata,
            trigger=trigger,
        )

    @staticmethod
    def _parse_trigger(raw: object, skill_md_path: Path) -> Optional[TriggerType]:
        """Parse the optional ``trigger:`` frontmatter block (P-14).

        Expected shape:
            trigger:
              type: keyword | task | path
              keywords: [...]   # type: keyword
              triggers: [...]   # type: task
              paths: [...]      # type: path

        Absent or malformed -- logged and treated as no trigger (the skill
        falls back to today's always-listed behavior) rather than failing
        the whole skill load over one bad field.
        """
        if raw is None:
            return None
        if not isinstance(raw, dict):
            logger.warning(
                "[SKILLS] 'trigger' must be a mapping in %s -- ignoring", skill_md_path
            )
            return None

        trigger_type = raw.get("type")
        try:
            if trigger_type == "keyword":
                return KeywordTrigger(keywords=list(raw.get("keywords") or []))
            if trigger_type == "task":
                return TaskTrigger(triggers=list(raw.get("triggers") or []))
            if trigger_type == "path":
                return PathTrigger(paths=list(raw.get("paths") or []))
        except Exception as exc:
            logger.warning(
                "[SKILLS] Invalid 'trigger' block in %s: %s -- ignoring",
                skill_md_path, exc,
            )
            return None

        logger.warning(
            "[SKILLS] Unknown trigger type %r in %s -- ignoring",
            trigger_type, skill_md_path,
        )
        return None

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _unpack_skill_zip(self, zip_path: Path) -> Optional[Path]:
        """Extract a .skill ZIP bundle to a sibling directory."""
        skill_name = zip_path.stem
        target_dir = self.skills_dir / skill_name
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Validate before extracting — ensure SKILL.md is present
                names = zf.namelist()
                has_skill_md = any(
                    n == "SKILL.md" or n.endswith("/SKILL.md") for n in names
                )
                if not has_skill_md:
                    logger.warning(
                        "[SKILLS] .skill bundle %s has no SKILL.md — skipping",
                        zip_path.name,
                    )
                    return None
                zf.extractall(target_dir)
            return target_dir
        except (zipfile.BadZipFile, OSError) as exc:
            logger.warning("[SKILLS] Failed to unpack %s: %s", zip_path.name, exc)
            return None

    @staticmethod
    def _split_frontmatter(content: str) -> Tuple[Optional[str], str]:
        """
        Split YAML frontmatter from markdown body.
        Returns (frontmatter_str, body_str).  frontmatter_str is None if not found.
        """
        lines = content.split("\n")
        if not lines or lines[0].strip() != "---":
            return None, content

        end_idx: Optional[int] = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break

        if end_idx is None:
            return None, content

        frontmatter = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1:])
        return frontmatter, body

    @staticmethod
    def _parse_yaml_frontmatter(yaml_str: str) -> dict:
        """
        Parse YAML frontmatter.  Tries PyYAML first (if installed as a
        transitive dependency); falls back to a lightweight inline parser
        that handles the SKILL.md subset of YAML.
        """
        # Try PyYAML (available in most Python environments as a transitive dep)
        try:
            import yaml  # noqa: PLC0415
            result = yaml.safe_load(yaml_str)
            return result if isinstance(result, dict) else {}
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("[SKILLS] PyYAML parse failed (%s), using fallback", exc)

        # Fallback: lightweight SKILL.md subset parser
        return SkillLoader._simple_yaml_parse(yaml_str)

    @staticmethod
    def _simple_yaml_parse(yaml_str: str) -> dict:
        """
        Minimal YAML parser for the SKILL.md frontmatter subset:
        - Simple key: value pairs
        - Quoted string values
        - Flow sequences: [item1, item2]
        - Multi-line values indented under key (treated as continuation)
        """
        result: dict = {}
        current_key: Optional[str] = None
        current_value_parts: List[str] = []

        def _flush():
            if current_key and current_value_parts:
                val = " ".join(current_value_parts).strip()
                # Flow list e.g.  [bash, files]
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1]
                    items = [
                        i.strip().strip("'\"")
                        for i in inner.split(",")
                        if i.strip()
                    ]
                    result[current_key] = items
                else:
                    result[current_key] = val.strip("'\"")

        kv_re = re.compile(r'^([\w][\w-]*)\s*:\s*(.*)')

        for line in yaml_str.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Continuation line (indented)
            if line.startswith((" ", "\t")) and current_key:
                current_value_parts.append(stripped)
                continue

            # New key: value line
            m = kv_re.match(line)
            if m:
                _flush()
                current_key = m.group(1)
                current_value_parts = [m.group(2).strip()] if m.group(2).strip() else []
            else:
                current_value_parts.append(stripped)

        _flush()
        return result

    @staticmethod
    def _parse_tools_list(raw: object) -> List[str]:
        """Normalise allowed-tools value to a list of strings."""
        if isinstance(raw, list):
            return [str(t).strip() for t in raw if str(t).strip()]
        if isinstance(raw, str):
            # Flow sequence string: "[bash, files]" or "bash, files"
            cleaned = raw.strip().strip("[]")
            return [t.strip().strip("'\"") for t in cleaned.split(",") if t.strip()]
        return []
