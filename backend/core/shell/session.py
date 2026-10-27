# core/shell/session.py
"""
ShellSession — broker-backed per-conversation shell state (B-049, Option A).

Stores:
  cwd       — current working directory (persists across shell tool calls)
  env_vars  — user-set environment variables (export, venv activation, etc.)
  history   — last N command strings (for context/debugging)

Key:    shell_session:{conversation_id}
TTL:    24 hours, refreshed on every save

Design:
  - The session is stored in LocalRedisAdapter (in-memory, no network).
  - All methods are async and safe to call from tool context.
  - Session is keyed by conversation_id so each conversation has independent
    shell state. For project mode, the conversation is already linked to a
    project, so cwd naturally tracks within project workspace.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import SHELL_SESSION_TTL_SECONDS, SHELL_SESSION_MAX_HISTORY

logger = logging.getLogger(__name__)

_KEY_PREFIX   = "shell_session:"
_TTL_SECONDS  = SHELL_SESSION_TTL_SECONDS
_MAX_HISTORY  = SHELL_SESSION_MAX_HISTORY


@dataclass
class ShellSession:
    conversation_id: str
    cwd: str = ""                          # empty → use default user workspace
    env_vars: Dict[str, str] = field(default_factory=dict)
    history: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Broker key
    # ------------------------------------------------------------------

    @property
    def _key(self) -> str:
        return f"{_KEY_PREFIX}{self.conversation_id}"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    async def load(cls, conversation_id: str) -> "ShellSession":
        """
        Load session from the broker.
        Returns a fresh (empty) session if the key is absent.
        """
        session = cls(conversation_id=conversation_id)
        client = await _get_broker_client()
        if client is None:
            return session
        try:
            raw = await client.get(session._key)
            if raw:
                data = json.loads(raw)
                session.cwd      = data.get("cwd", "")
                session.env_vars = data.get("env_vars", {})
                session.history  = data.get("history", [])
        except Exception as exc:
            logger.debug("ShellSession.load: broker error (non-fatal)", exc_info=exc)
        return session

    async def save(self) -> None:
        """Persist session to the broker with TTL refresh."""
        client = await _get_broker_client()
        if client is None:
            return
        try:
            payload = json.dumps({
                "cwd":      self.cwd,
                "env_vars": self.env_vars,
                "history":  self.history[-_MAX_HISTORY:],
            })
            await client.set(self._key, payload, ex=_TTL_SECONDS)
        except Exception as exc:
            logger.debug("ShellSession.save: broker error (non-fatal)", exc_info=exc)

    async def reset(self) -> None:
        """Clear all session state and persist the empty session."""
        self.cwd      = ""
        self.env_vars = {}
        self.history  = []
        await self.save()

    # ------------------------------------------------------------------
    # State mutators (call save() after using these)
    # ------------------------------------------------------------------

    def set_cwd(self, new_cwd: str) -> None:
        """Resolve and store the new working directory."""
        new_cwd = os.path.expanduser(new_cwd)
        p = Path(new_cwd)
        if not p.is_absolute():
            base = Path(self.cwd) if self.cwd else Path.cwd()
            p = (base / p).resolve()
        self.cwd = str(p)

    def set_env(self, key: str, value: str) -> None:
        self.env_vars[key] = value

    def unset_env(self, key: str) -> None:
        self.env_vars.pop(key, None)

    def add_history(self, cmd_str: str) -> None:
        self.history.append(cmd_str)
        if len(self.history) > _MAX_HISTORY:
            self.history = self.history[-_MAX_HISTORY:]

    # ------------------------------------------------------------------
    # Environment merge
    # ------------------------------------------------------------------

    def effective_env(self) -> Dict[str, str]:
        """
        Return the full environment for subprocess execution:
        host os.environ (minus EVA-managed provider-key variables) merged
        with session env_vars (session wins on conflict).
        """
        from utils.child_env import child_environment
        return child_environment(extra=self.env_vars)

    # ------------------------------------------------------------------
    # Serialisation helper (for tests / API responses)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "cwd":             self.cwd,
            "env_vars":        self.env_vars,
            "history":         self.history,
        }


# ---------------------------------------------------------------------------
# Internal helper — LocalBroker adapter via existing registry shim
# ---------------------------------------------------------------------------

async def _get_broker_client():
    """Return the shared LocalRedisAdapter, or None on unexpected failure."""
    try:
        from core.execution.redis_registry import redis_execution_registry
        return await redis_execution_registry.connect()
    except Exception:
        return None
