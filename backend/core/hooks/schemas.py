# core/hooks/schemas.py
"""
Hook payload and response schemas (B-064).

JSON contract between EVA and user hook scripts.
Field names deliberately match Claude Code's hook JSON schema wherever
possible to make scripts portable between the two systems.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Event names  (maps internal EVA events → Claude Code equivalents)
# ---------------------------------------------------------------------------

class HookEventName(str, Enum):
    PRE_TOOL_USE     = "PreToolUse"       # before tool executes
    POST_TOOL_USE    = "PostToolUse"      # after tool executes (success)
    SESSION_START    = "SessionStart"     # session initialised / login
    PROMPT_SUBMIT    = "UserPromptSubmit" # user submitted a chat message
    STOP             = "Stop"             # execution completed
    MODEL_CALL       = "ModelCall"        # before each LLM API call (EVA extension)

    # Internal → event name mapping used in HookDefinition.event field
    @classmethod
    def from_hook_event(cls, event: str) -> "HookEventName":
        _MAP = {
            "pre":           cls.PRE_TOOL_USE,
            "post":          cls.POST_TOOL_USE,
            "session_start": cls.SESSION_START,
            "prompt_submit": cls.PROMPT_SUBMIT,
            "stop":          cls.STOP,
            "model_call":    cls.MODEL_CALL,
        }
        try:
            return _MAP[event]
        except KeyError:
            raise ValueError(f"Unknown hook event: '{event}'")


# ---------------------------------------------------------------------------
# HookPayload — what EVA sends to the hook script on stdin
# ---------------------------------------------------------------------------

@dataclass
class HookPayload:
    """
    JSON payload written to hook script stdin.

    Common fields (all events) match Claude Code's schema:
      hook_event_name, session_id, user_id, cwd

    Event-specific fields:
      PreToolUse / PostToolUse : tool_name, tool_input, tool_use_id
      PostToolUse              : + tool_response
      UserPromptSubmit         : prompt
      Stop                     : execution_id
    """
    hook_event_name: str              # HookEventName value string
    session_id:      str              # EVA session / conversation ID
    user_id:         str
    cwd:             str              # active workspace root

    # PreToolUse / PostToolUse
    tool_name:    Optional[str]  = None
    tool_input:   Optional[Dict[str, Any]] = None
    tool_use_id:  Optional[str]  = None

    # PostToolUse only
    tool_response: Optional[Dict[str, Any]] = None

    # UserPromptSubmit only
    prompt: Optional[str] = None

    # Stop only
    execution_id: Optional[str] = None

    # ModelCall only (EVA extension — fires before each LLM API call)
    model:          Optional[str] = None
    provider:       Optional[str] = None
    turn_number:    Optional[int] = None
    token_estimate: Optional[int] = None

    def to_json(self) -> str:
        """Serialise to JSON string for stdin."""
        return json.dumps(
            {k: v for k, v in asdict(self).items() if v is not None},
            default=str,
        )


# ---------------------------------------------------------------------------
# HookResponse — what EVA reads from hook script stdout
# ---------------------------------------------------------------------------

@dataclass
class HookResponse:
    """
    Parsed response from a hook script (stdout JSON or exit-code-only).

    decision         : "allow" | "block" | "warn"
    reason           : Human-readable explanation (shown when blocked/warned)
    updated_input    : Replacement tool arguments dict (PreToolUse only).
                       If present, EVA passes these args to the tool instead
                       of the original ones.  Matches Claude Code's updatedInput.
    additional_context: Free-form text injected into the LLM's next context
                        block.  Matches Claude Code's additionalContext.
    """
    decision:           str = "allow"
    reason:             str = ""
    updated_input:      Optional[Dict[str, Any]] = None
    additional_context: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.decision == "block"

    @property
    def warned(self) -> bool:
        return self.decision == "warn"

    @classmethod
    def allow(cls) -> "HookResponse":
        return cls(decision="allow")

    @classmethod
    def block(cls, reason: str) -> "HookResponse":
        return cls(decision="block", reason=reason)

    @classmethod
    def warn(cls, reason: str) -> "HookResponse":
        return cls(decision="warn", reason=reason)

    @classmethod
    def from_exit_code(cls, exit_code: int, stderr: str = "") -> "HookResponse":
        """
        Produce a HookResponse from exit code alone (no stdout JSON).

        Exit code contract (matches Claude Code):
          0        → allow
          2        → block  (stderr message used as reason)
          anything → allow  (non-standard exit, log and continue)
        """
        if exit_code == 0:
            return cls.allow()
        if exit_code == 2:
            return cls.block(reason=stderr.strip() or "Hook script blocked this action.")
        # Non-standard exit — allow but surface stderr
        return cls(decision="allow", reason=f"Hook exited {exit_code}: {stderr.strip()}")

    @classmethod
    def from_stdout(cls, stdout: str, exit_code: int, stderr: str = "") -> "HookResponse":
        """
        Parse stdout JSON into a HookResponse.
        Falls back to exit-code-only interpretation if stdout is empty or not JSON.
        """
        stdout = stdout.strip()
        if not stdout:
            return cls.from_exit_code(exit_code, stderr)

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # stdout was not JSON — honour exit code only
            return cls.from_exit_code(exit_code, stderr)

        decision = data.get("decision", "allow" if exit_code == 0 else "block")
        if decision not in ("allow", "block", "warn"):
            decision = "allow" if exit_code == 0 else "block"

        return cls(
            decision=decision,
            reason=data.get("reason", ""),
            updated_input=data.get("updated_input") or data.get("updatedInput"),
            additional_context=data.get("additional_context") or data.get("additionalContext"),
        )
