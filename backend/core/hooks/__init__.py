# core/hooks/__init__.py
"""
Hook execution engine for EVA (B-064).

Provides HookRunner — a unified dispatcher for all hook types:
  type="builtin"  → built-in Python handler functions (B-053 handlers)
  type="command"  → user-supplied shell scripts via subprocess
  type="http"     → HTTP POST to an external webhook URL

All subprocess runs are wrapped in asyncio.to_thread so they never
block the event loop.  PreToolUse hooks are blocking by default;
all other events are non-blocking (fire-and-forget) by default.
"""
from .schemas import HookPayload, HookResponse, HookEventName
from .runner import HookRunner

__all__ = ["HookPayload", "HookResponse", "HookEventName", "HookRunner"]
