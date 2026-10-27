# core/project/eva_format.py
"""
.eva file format utilities (B-045).

.eva is a line-oriented, token-efficient format designed for LLM context injection.

Design rules:
  - Section headers:   ### SECTION_NAME
  - Key-value:         key: value  (single line)
  - Multi-line value:  key:\n  indented lines
  - Typed log entry:   [TYPE ts=ISO id=ID] content
  - Comments:          # comment line  (stripped before LLM injection)
  - Empty lines separate logical blocks
  - No closing tags, no JSON brackets, no YAML fences

This module provides:
  - read_file(path)           — raw file content (creates empty file if missing)
  - strip_comments(text)      — remove # comment lines for LLM injection
  - get_section(text, name)   — extract content of a named section
  - set_section(path, name, content) — write/replace a section in-place
  - append_entry(path, entry) — append a log entry line (journal, errors)
  - render_project_eva(...)   — render a full project.eva from structured data
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Low-level read/write
# ---------------------------------------------------------------------------

def read_file(path: str | Path) -> str:
    """Read an .eva file, returning empty string if it doesn't exist."""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def write_file(path: str | Path, content: str) -> None:
    """Write content to an .eva file, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def strip_comments(text: str) -> str:
    """
    Remove comment lines (starting with #) and collapse multiple blank lines.
    Used before injecting .eva content into LLM context.
    """
    lines = []
    prev_blank = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        is_blank = stripped == ""
        if is_blank and prev_blank:
            continue  # collapse consecutive blanks
        lines.append(line)
        prev_blank = is_blank
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Section-level operations
# ---------------------------------------------------------------------------

def get_section(text: str, section_name: str) -> str:
    """
    Extract the body of a named section from .eva text.
    Returns empty string if section not found.
    """
    pattern = rf"^### {re.escape(section_name)}\s*$"
    lines = text.splitlines()
    in_section = False
    body_lines: list[str] = []

    for line in lines:
        if re.match(pattern, line):
            in_section = True
            continue
        if in_section:
            if line.startswith("### "):
                break
            body_lines.append(line)

    return "\n".join(body_lines).strip()


def set_section(path: str | Path, section_name: str, new_content: str) -> None:
    """
    Replace the content of a named section in an .eva file.
    If the section doesn't exist, it is appended.
    new_content should NOT include the ### header line.
    """
    text = read_file(path)
    header = f"### {section_name}"
    pattern = rf"^### {re.escape(section_name)}\s*$"

    lines = text.splitlines()
    result: list[str] = []
    i = 0
    replaced = False

    while i < len(lines):
        line = lines[i]
        if re.match(pattern, line):
            # Write header + new content
            result.append(header)
            result.append(new_content)
            result.append("")
            replaced = True
            # Skip old section body
            i += 1
            while i < len(lines) and not lines[i].startswith("### "):
                i += 1
        else:
            result.append(line)
            i += 1

    if not replaced:
        # Section didn't exist — append it
        result.append("")
        result.append(header)
        result.append(new_content)
        result.append("")

    write_file(path, "\n".join(result).rstrip() + "\n")


def append_entry(path: str | Path, entry: str) -> None:
    """
    Append a single entry line to an .eva file (used for journal, errors).
    Creates the file if it doesn't exist.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(entry.rstrip() + "\n")


# ---------------------------------------------------------------------------
# Skeleton file generators
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_project_eva(
    project_id: str,
    name: str,
    description: Optional[str],
    synopsis: Optional[str],
    created: Optional[str] = None,
) -> str:
    """
    Render the initial project.eva content from structured metadata.
    """
    ts = created or _now_iso()
    desc_line = description or ""
    synopsis_block = synopsis or "(not set — update with your project intent)"

    return f"""\
### PROJECT
id: {project_id}
name: {name}
status: active
created: {ts}
updated: {ts}

### SYNOPSIS
{synopsis_block}

### DESCRIPTION
{desc_line}

### GOAL
# Describe the primary goal and success criteria here.
# Example: Redesign the checkout flow — target <2% error rate, <3s load time.

### SCOPE
# in: list what is in scope
# out: list what is explicitly out of scope

### CONSTRAINTS
# stack, deploy, deadlines, access restrictions, etc.

### ENV
# List environment variables / secrets that are configured (values masked).
# Example:  API_KEY: set (masked)
"""


def render_tasks_eva() -> str:
    return """\
### TASKS
# Format: [STATUS ts=ISO id=ID] Short title
#   subtask: description — status
#
# STATUS values: PENDING | ACTIVE | DONE | BLOCKED
#
# Example:
# [DONE ts=2026-01-01T00:00Z id=T-001] Initial setup
# [ACTIVE ts=2026-01-02T00:00Z id=T-002] Core implementation
#   subtask: unit tests — pending
# [PENDING id=T-003] Documentation
# [BLOCKED id=T-004] Deployment — blocked: T-003
"""


def render_knowledge_eva() -> str:
    return """\
### KNOWLEDGE
# Format: [TYPE ts=ISO] title
#   key details...
#
# TYPE is a short, free-form ALL-CAPS label you choose to fit the entry --
# not a fixed list. A few examples that fit many projects:
#
# CODE      — important code location / pattern / known issue
# DECISION  — architectural or design decision with rationale
# REFERENCE — external doc/link relevant to the project
# TEST      — test results, coverage status
# NOTE      — general knowledge entry
#
# Use one of these when it genuinely fits. Otherwise, pick a label that
# describes the entry better (e.g. CAMPAIGN, DATASET, POLICY) -- staying
# consistent with TYPE labels already used elsewhere in this file matters
# more than picking from this specific list.
"""


def render_journal_eva() -> str:
    return """\
### JOURNAL
# Append-only execution log. Each entry is auto-written after a turn.
# Format: [ISO ts agent=NAME turn=N] summary of what happened
"""


def render_errors_eva() -> str:
    return """\
### ERRORS
# Format: [ISO ts turn=N status=open|resolved]
#   error description
#   file: path:line (if known)
#   cause: root cause
#   fix: what was done to resolve (for resolved entries)
"""


def render_policies_eva() -> str:
    return """\
### POLICIES
# Behavioural rules for the agent on this project.
# Written during project setup; editable at any time via conversation.
# The agent reads this file on EVERY turn — rules here are always enforced.

### DOS
# Things the agent must always do on this project.
# Examples:
#   - Always run the test suite before committing any code.
#   - Follow conventional commits format (feat:/fix:/chore: prefixes).
#   - Document every architectural decision in knowledge.eva.

### DONTS
# Hard prohibitions — never do these without explicit user instruction.
# Examples:
#   - Never push directly to main or master branch.
#   - Never hardcode API keys, secrets, or credentials.
#   - Never drop or alter existing database columns without user confirmation.
#   - Never install new dependencies without asking first.

### ASK_FIRST
# Situations where the agent must pause and confirm before proceeding.
# Examples:
#   - Any change that touches authentication or payment code.
#   - Refactors affecting more than 3 files at once.
#   - If estimated effort for a task exceeds 2 hours, surface a plan first.
"""
