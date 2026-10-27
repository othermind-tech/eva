# core/skills/trigger.py
#
# P-14: Skill trigger types (keyword / task / path-scoped).
#
# Trigger data model and matching logic adapted from OpenHands SDK
# (https://github.com/OpenHands/software-agent-sdk,
# openhands-sdk/openhands/sdk/skills/trigger.py and skill.py), MIT License,
# Copyright (c) OpenHands. Confirmed MIT via the GitHub API before reuse
# Adapted for EVA's
# simpler SKILL.md/registry model -- ported: the three trigger types, the
# whole-token keyword matcher, and the gitignore-style path glob matcher.
# Not ported: OpenHands' broader Skill class (MCP tools, AgentSkills-vs-
# legacy distinction, resource directories) -- EVA's own SkillMeta covers
# what EVA actually needs.
#
# Only KeywordTrigger and PathTrigger are wired into filtering today
# (core/skills/registry.py's build_available_skills_block) -- the gap
# report's own stated phase-1 scope. TaskTrigger's data model exists for
# forward-compatibility but has no consumer yet.

from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Literal, Optional, Union

from pydantic import BaseModel


class BaseTrigger(BaseModel):
    """Base class for all trigger types."""

    pass


class KeywordTrigger(BaseTrigger):
    """Activates when specific keywords appear in the user's message."""

    type: Literal["keyword"] = "keyword"
    keywords: List[str]


class TaskTrigger(BaseTrigger):
    """Activates for specific task types. No consumer yet in EVA -- see
    module docstring."""

    type: Literal["task"] = "task"
    triggers: List[str]


class PathTrigger(BaseTrigger):
    """Activates when the agent touches a file whose path matches one of
    the ``paths`` glob patterns (gitignore-style ``**`` semantics).

    Deliberately excluded from build_available_skills_block()'s listing
    entirely, not conditionally shown -- EVA has no per-tool-call file-touch
    hook feeding back into skill selection yet (that reactive wiring is a
    separate future step), so a path-triggered skill has no message-time
    signal to filter on. Matches OpenHands' own design choice here: their
    Skill._path_rules_are_trigger_only forces disable_model_invocation for
    path rules, keeping them out of the normal advertised list too.
    """

    type: Literal["path"] = "path"
    paths: List[str]


TriggerType = Union[KeywordTrigger, TaskTrigger, PathTrigger]


def _keyword_matches(keyword: str, message_lower: str) -> bool:
    """Return True if ``keyword`` appears as a whole token in ``message_lower``.

    Uses alphanumeric boundaries (not ``\\b``) so ``git`` does not fire on
    ``github`` while slash-prefixed keywords like ``/linear`` still match.
    ``message_lower`` is expected to already be lowercased by the caller.
    """
    keyword_lower = keyword.lower()
    if not keyword_lower:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(keyword_lower)}(?![a-z0-9])"
    return re.search(pattern, message_lower) is not None


def matches_keyword_trigger(trigger: Union[KeywordTrigger, TaskTrigger], message: str) -> bool:
    """Return True if any of the trigger's keywords/triggers appear as a
    whole token in ``message``. Shared by KeywordTrigger and TaskTrigger --
    both are just a list of trigger strings under a different field name."""
    if not message:
        return False
    keywords = trigger.keywords if isinstance(trigger, KeywordTrigger) else trigger.triggers
    message_lower = message.lower()
    return any(_keyword_matches(kw, message_lower) for kw in keywords)


# ── Path glob matching ────────────────────────────────────────────────────────
# One glob token per match; longest operator first so `**/` and `**` beat `*`.
_GLOB_TOKENS = re.compile(r"\*\*/|\*\*|\*|\?|[^*?]+")
_GLOB_TO_REGEX = {
    "**/": "(?:.*/)?",  # any number of leading path segments (including zero)
    "**": ".*",         # anything, crossing `/` separators
    "*": "[^/]*",        # any run within a single segment
    "?": "[^/]",         # a single non-separator character
}


@lru_cache(maxsize=512)
def _compile_path_glob(pattern: str) -> re.Pattern:
    """Compile a gitignore-style path glob into an anchored regex.

    Supported syntax (matched against a POSIX path):
    - ``**`` matches any number of path segments (including zero).
    - ``*`` matches any run of characters within a single segment.
    - ``?`` matches a single non-separator character.
    - A pattern without a ``/`` matches the basename at any depth, e.g.
      ``*.sql`` behaves like ``**/*.sql`` (gitignore semantics).

    Matching is case-sensitive. Results are cached per pattern string.
    """
    if "/" not in pattern:
        pattern = "**/" + pattern
    tokens = _GLOB_TOKENS.findall(pattern)
    body = "".join(_GLOB_TO_REGEX.get(t, re.escape(t)) for t in tokens)
    return re.compile(f"(?s:{body})\\Z")


def path_matches_glob(file_path: str, pattern: str) -> bool:
    """Return True if ``file_path`` matches the gitignore-style glob ``pattern``.

    ``file_path`` is expected to be a POSIX path (typically workspace-relative).
    """
    if not pattern:
        return False
    return _compile_path_glob(pattern).fullmatch(file_path) is not None


def matches_path_trigger(trigger: PathTrigger, file_path: str) -> Optional[str]:
    """Return the first glob pattern in ``trigger.paths`` matching
    ``file_path``, or None."""
    for pattern in trigger.paths:
        if path_matches_glob(file_path, pattern):
            return pattern
    return None
