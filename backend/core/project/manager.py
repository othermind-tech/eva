# core/project/manager.py
"""
ProjectManager (B-045).

Manages the lifecycle of a Project's .eva workspace:
  - init_workspace()   — create the .eva/ directory and skeleton files on first use
  - load_context()     — assemble the LLM context block (3 tiers: compact / focused / full)
  - read_artifact()    — return raw content of a named .eva file
  - append_journal()   — append a turn entry to journal.eva
  - mark_updated()     — update the `updated:` field in project.eva

The workspace lives at:
    ~/eva_workspace/{user_id}/projects/{project_id}/.eva/
    (or $EVA_WORKSPACE_ROOT/{user_id}/projects/{project_id}/.eva/)
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.workspace import get_project_dir as _get_project_dir
from core.project.eva_format import (
    read_file,
    write_file,
    strip_comments,
    get_section,
    set_section,
    append_entry,
    render_project_eva,
    render_tasks_eva,
    render_knowledge_eva,
    render_journal_eva,
    render_errors_eva,
    render_policies_eva,
    _now_iso,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# .eva filenames within the .eva/ subdirectory
_FILE_PROJECT   = "project.eva"
_FILE_POLICIES  = "policies.eva"   # always injected — behavioural rules
_FILE_TASKS     = "tasks.eva"
_FILE_KNOWLEDGE = "knowledge.eva"
_FILE_JOURNAL   = "journal.eva"
_FILE_ERRORS    = "errors.eva"

# Context tiers — which files are included
# policies.eva is ALWAYS included regardless of tier
_TIER_COMPACT = "compact"   # project.eva + policies.eva           (~400 tokens)
_TIER_FOCUSED = "focused"   # + tasks.eva                          (~700 tokens)
_TIER_FULL    = "full"      # all 6 files                          (~1800 tokens)

# Policy section names
_POLICY_DOS       = "DOS"
_POLICY_DONTS     = "DONTS"
_POLICY_ASK_FIRST = "ASK_FIRST"
_POLICY_SECTIONS  = (_POLICY_DOS, _POLICY_DONTS, _POLICY_ASK_FIRST)

# Rough token-per-character ratio for budget estimation.
# 3 is conservative: prose ~4 chars/token but code/JSON ~2–3 chars/token.
# Using 3 prevents budget overruns when project files contain code.
_CHARS_PER_TOKEN = 3


class ProjectManager:
    """
    Static helper class for .eva workspace operations.
    All methods work directly on the filesystem — no DB access.
    """

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_eva_dir(cls, user_id: str, project_id: str) -> Path:
        """Return the managed .eva/ directory path for a project (may not exist yet)."""
        return _get_project_dir(user_id, project_id) / ".eva"

    @classmethod
    def _resolve_eva_dir(
        cls,
        user_id: str,
        project_id: str,
        workspace_path: Optional[str] = None,
    ) -> Path:
        """
        Resolve the .eva/ directory location.

        If workspace_path is provided (either managed root or external user path),
        .eva/ is expected at workspace_path/.eva/.
        Falls back to the managed location when workspace_path is None.
        """
        if workspace_path:
            return Path(workspace_path) / ".eva"
        return cls.get_eva_dir(user_id, project_id)

    @classmethod
    def get_artifact_path(
        cls,
        user_id: str,
        project_id: str,
        filename: str,
        workspace_path: Optional[str] = None,
    ) -> Path:
        return cls._resolve_eva_dir(user_id, project_id, workspace_path) / filename

    # ------------------------------------------------------------------
    # Workspace initialisation
    # ------------------------------------------------------------------

    @classmethod
    def init_workspace(
        cls,
        user_id: str,
        project_id: str,
        name: str,
        description: Optional[str] = None,
        synopsis: Optional[str] = None,
        created: Optional[str] = None,
        external_path: Optional[str] = None,
    ) -> str:
        """
        Create the .eva/ directory and write skeleton files for a new project.
        Returns the workspace ROOT path (for storing in the DB).
        Safe to call multiple times — will not overwrite existing files.

        external_path: if provided, .eva/ is created inside that folder
                       (e.g. /home/user/my-app → /home/user/my-app/.eva/).
                       If None, the managed location is used:
                       ~/eva_workspace/{user_id}/projects/{project_id}/.eva/
        """
        if external_path:
            root_path = Path(external_path)
            eva_dir = root_path / ".eva"
            workspace_root = str(root_path)
        else:
            eva_dir = cls.get_eva_dir(user_id, project_id)
            workspace_root = str(eva_dir.parent)

        eva_dir.mkdir(parents=True, exist_ok=True)

        # project.eva — always re-render (metadata may have changed on retry)
        project_path = eva_dir / _FILE_PROJECT
        if not project_path.exists():
            write_file(
                project_path,
                render_project_eva(
                    project_id=project_id,
                    name=name,
                    description=description,
                    synopsis=synopsis,
                    created=created,
                ),
            )

        # Skeleton files — written once, never overwritten
        for filename, renderer in [
            (_FILE_POLICIES,  render_policies_eva),
            (_FILE_TASKS,     render_tasks_eva),
            (_FILE_KNOWLEDGE, render_knowledge_eva),
            (_FILE_JOURNAL,   render_journal_eva),
            (_FILE_ERRORS,    render_errors_eva),
        ]:
            p = eva_dir / filename
            if not p.exists():
                write_file(p, renderer())

        return workspace_root

    # ------------------------------------------------------------------
    # Context injection
    # ------------------------------------------------------------------

    @classmethod
    def load_context(
        cls,
        user_id: str,
        project_id: str,
        tier: str = _TIER_COMPACT,
        token_budget: Optional[int] = None,
        workspace_path: Optional[str] = None,
    ) -> str:
        """
        Assemble the project context block for LLM injection.

        tier:
          'compact' — project.eva only  (always; ~300 tokens)
          'focused' — + tasks.eva        (~600 tokens)
          'full'    — all 5 files        (~1500 tokens)

        token_budget: if provided, auto-select tier:
          >4000  → full
          1500-4000 → focused
          <1500  → compact

        Returns a formatted string ready for system-prompt injection.
        Returns empty string if workspace doesn't exist.
        """
        eva_dir = cls._resolve_eva_dir(user_id, project_id, workspace_path)
        if not eva_dir.exists():
            return ""

        if token_budget is not None:
            if token_budget > 4000:
                tier = _TIER_FULL
            elif token_budget >= 1500:
                tier = _TIER_FOCUSED
            else:
                tier = _TIER_COMPACT

        # policies.eva is injected on EVERY tier — rules are always enforced
        files_to_load: list[tuple[str, str]] = [
            ("Project",  _FILE_PROJECT),
            ("Policies", _FILE_POLICIES),
        ]
        if tier in (_TIER_FOCUSED, _TIER_FULL):
            files_to_load.append(("Tasks", _FILE_TASKS))
        if tier == _TIER_FULL:
            files_to_load.extend([
                ("Knowledge", _FILE_KNOWLEDGE),
                ("Errors",    _FILE_ERRORS),
                ("Journal",   _FILE_JOURNAL),
            ])

        parts: list[str] = []
        for label, filename in files_to_load:
            raw = read_file(eva_dir / filename)
            if raw:
                clean = strip_comments(raw)
                if clean:
                    parts.append(clean)

        if not parts:
            return ""

        body = "\n\n".join(parts)
        return f"--- PROJECT CONTEXT ---\n{body}\n--- END PROJECT CONTEXT ---"

    # ------------------------------------------------------------------
    # Artifact access
    # ------------------------------------------------------------------

    @classmethod
    def read_artifact(
        cls, user_id: str, project_id: str, filename: str,
        workspace_path: Optional[str] = None,
    ) -> str:
        """Return raw content of a named .eva file (empty string if absent)."""
        return read_file(cls.get_artifact_path(user_id, project_id, filename, workspace_path))

    @classmethod
    def write_artifact(
        cls, user_id: str, project_id: str, filename: str, content: str,
        workspace_path: Optional[str] = None,
    ) -> None:
        """Overwrite a named .eva file."""
        write_file(cls.get_artifact_path(user_id, project_id, filename, workspace_path), content)

    # tasks.eva line format (per prompts/project.py):
    # [STATUS ts=ISO id=T-NNN] Title
    # STATUS values: PENDING | ACTIVE | DONE | BLOCKED
    _TASK_STATUS_RE = re.compile(r"^\[([A-Z]+)\s", re.MULTILINE)

    @classmethod
    def count_tasks_by_status(
        cls,
        user_id: str,
        project_id: str,
        workspace_path: Optional[str] = None,
    ) -> dict:
        """
        Parse tasks.eva and return task counts grouped by status.

        Returns:
            {
                "pending": int, "active": int,
                "blocked": int, "done":   int,
                "open":    int,            # pending + active + blocked
                "total":   int,
            }
        All zeros when tasks.eva is absent, empty, or has no STATUS lines.
        """
        raw = cls.read_artifact(user_id, project_id, "tasks.eva", workspace_path)
        counts: dict = {"PENDING": 0, "ACTIVE": 0, "DONE": 0, "BLOCKED": 0}
        for match in cls._TASK_STATUS_RE.finditer(raw):
            status = match.group(1)
            if status in counts:
                counts[status] += 1
        pending = counts["PENDING"]
        active  = counts["ACTIVE"]
        done    = counts["DONE"]
        blocked = counts["BLOCKED"]
        return {
            "pending": pending,
            "active":  active,
            "done":    done,
            "blocked": blocked,
            "open":    pending + active + blocked,
            "total":   pending + active + done + blocked,
        }

    # ------------------------------------------------------------------
    # Journal
    # ------------------------------------------------------------------

    @classmethod
    def append_journal(
        cls,
        user_id: str,
        project_id: str,
        content: str,
        agent: str = "eva",
        turn: Optional[int] = None,
        workspace_path: Optional[str] = None,
    ) -> None:
        """
        Append a turn entry to journal.eva.
        Format: [ISO ts agent=NAME turn=N] content
        """
        ts = _now_iso()
        turn_part = f" turn={turn}" if turn is not None else ""
        entry = f"[{ts} agent={agent}{turn_part}] {content.strip()}"
        append_entry(
            cls.get_artifact_path(user_id, project_id, _FILE_JOURNAL, workspace_path),
            entry,
        )

    # ------------------------------------------------------------------
    # project.eva metadata updates
    # ------------------------------------------------------------------

    @classmethod
    def touch_updated(
        cls, user_id: str, project_id: str,
        workspace_path: Optional[str] = None,
    ) -> None:
        """Update the `updated:` timestamp in project.eva."""
        path = cls.get_artifact_path(user_id, project_id, _FILE_PROJECT, workspace_path)
        if not path.exists():
            return
        text = read_file(path)
        ts = _now_iso()
        lines = []
        for line in text.splitlines():
            if line.startswith("updated:"):
                lines.append(f"updated: {ts}")
            else:
                lines.append(line)
        write_file(path, "\n".join(lines) + "\n")

    @classmethod
    def update_status_in_eva(
        cls, user_id: str, project_id: str, status: str,
        workspace_path: Optional[str] = None,
    ) -> None:
        """Update the `status:` line in project.eva."""
        path = cls.get_artifact_path(user_id, project_id, _FILE_PROJECT, workspace_path)
        if not path.exists():
            return
        text = read_file(path)
        lines = []
        for line in text.splitlines():
            if line.startswith("status:"):
                lines.append(f"status: {status}")
            else:
                lines.append(line)
        write_file(path, "\n".join(lines) + "\n")

    # ------------------------------------------------------------------
    # Policy management
    # ------------------------------------------------------------------

    @classmethod
    def add_policy(cls, user_id: str, project_id: str, section: str, rule: str) -> bool:
        """
        Append a rule line to a policy section (DOS, DONTS, or ASK_FIRST).
        Returns True if written, False if project workspace doesn't exist or
        section name is invalid.

        Usage:
            ProjectManager.add_policy("user_1", "proj_abc", "DONTS", "Never delete user data")
            ProjectManager.add_policy("user_1", "proj_abc", "DOS", "Run tests before committing")
        """
        section = section.upper().strip()
        if section not in _POLICY_SECTIONS:
            return False
        path = cls.get_artifact_path(user_id, project_id, _FILE_POLICIES)
        if not path.parent.exists():
            return False
        existing = read_file(path)
        current_body = get_section(existing, section)
        # Build new body: keep existing lines, append the new rule
        rule_line = f"- {rule.strip().lstrip('- ')}"
        new_lines = [l for l in current_body.splitlines() if l.strip()]
        new_lines.append(rule_line)
        set_section(path, section, "\n".join(new_lines))
        cls.touch_updated(user_id, project_id)
        return True

    @classmethod
    def remove_policy(cls, user_id: str, project_id: str, section: str, rule: str) -> bool:
        """
        Remove a rule line from a policy section by matching substring.
        Returns True if a line was removed, False otherwise.

        The match is case-insensitive substring — "never push to main" will
        match "- Never push directly to main or master branch."
        """
        section = section.upper().strip()
        if section not in _POLICY_SECTIONS:
            return False
        path = cls.get_artifact_path(user_id, project_id, _FILE_POLICIES)
        if not path.exists():
            return False
        existing = read_file(path)
        current_body = get_section(existing, section)
        needle = rule.strip().lower()
        new_lines = [
            l for l in current_body.splitlines()
            if needle not in l.lower()
        ]
        if len(new_lines) == len(current_body.splitlines()):
            return False  # nothing removed
        set_section(path, section, "\n".join(new_lines))
        cls.touch_updated(user_id, project_id)
        return True

    @classmethod
    def get_policies(cls, user_id: str, project_id: str) -> dict:
        """
        Return parsed policy sections as a dict:
            {"DOS": [...], "DONTS": [...], "ASK_FIRST": [...]}
        Lines starting with # are excluded (they are placeholder comments).
        """
        path = cls.get_artifact_path(user_id, project_id, _FILE_POLICIES)
        text = read_file(path)
        result = {}
        for section in _POLICY_SECTIONS:
            body = get_section(text, section)
            rules = [
                l.strip().lstrip("- ").strip()
                for l in body.splitlines()
                if l.strip() and not l.strip().startswith("#")
            ]
            result[section] = rules
        return result

    @classmethod
    def set_policy_section(cls, user_id: str, project_id: str, section: str, content: str) -> bool:
        """
        Fully replace a policy section's content.
        Used by the artifact updater when the LLM rewrites an entire section.
        Returns True on success.
        """
        section = section.upper().strip()
        if section not in _POLICY_SECTIONS:
            return False
        path = cls.get_artifact_path(user_id, project_id, _FILE_POLICIES)
        if not path.parent.exists():
            return False
        set_section(path, section, content)
        cls.touch_updated(user_id, project_id)
        return True

    # ------------------------------------------------------------------
    # Existence check
    # ------------------------------------------------------------------

    @classmethod
    def workspace_exists(
        cls, user_id: str, project_id: str,
        workspace_path: Optional[str] = None,
    ) -> bool:
        return cls._resolve_eva_dir(user_id, project_id, workspace_path).exists()
