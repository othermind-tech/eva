# core/tools/base.py

from enum import Enum
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel


class ToolAuthority(str, Enum):
    """
    Describes the side-effect risk of a tool.

    READ_ONLY   – never modifies external state (file reads, lookups, …)
    MUTATING    – changes state but reversibly (file writes, POST requests, …)
    DESTRUCTIVE – permanently removes or overwrites data (deletes, DROP TABLE, …)

    The dispatcher uses this to decide whether a Human-in-the-Loop approval
    gate applies, based on the user's ``tool_approval_mode`` preference.
    """
    READ_ONLY   = "read_only"
    MUTATING    = "mutating"
    DESTRUCTIVE = "destructive"


class ToolResult(BaseModel):
    result: Any


class ToolBase:
    name: str = ""
    description: str = ""
    args_schema: Type[BaseModel] = None  # MUST BE A CLASS
    authority: ToolAuthority = ToolAuthority.READ_ONLY

    # ── V5.0 Sprint 2: Progressive Tool Disclosure ───────────────────────
    # category: broad functional group used for mode-based filtering and
    #   manifest generation.  Vocabulary: "filesystem" | "shell" | "memory"
    #   | "search" | "communication" | "delegation" | "git" | "general" | "mcp"
    category: str = "general"

    # chat_excluded: when True the tool is omitted from conversational
    #   (Chat) mode tool lists.  Replaces the old `agent_only` flag.
    #   agent_only is kept for backward compatibility as an alias.
    chat_excluded: bool = False

    # agent_only: deprecated alias for chat_excluded.  Set on legacy tools
    #   that predate V5.0.  Checked alongside chat_excluded in UserToolRegistry.
    agent_only: bool = False

    # summary: a single concise sentence (~60 tokens) used in the manifest
    #   block injected into the system prompt.  Tells the model what the
    #   tool does so it can decide whether to call describe_tool(name).
    #   Falls back to the first 80 chars of description when absent.
    summary: Optional[str] = None
    # ─────────────────────────────────────────────────────────────────────

    # execution_timeout_seconds: per-tool override for the dispatcher's
    #   wall-clock execution timeout. None (default) = use the global
    #   TOOL_EXECUTION_TIMEOUT_SECONDS. Set this on tools that wrap their
    #   own bounded, independently-protected work (e.g. a nested
    #   AgentEngine with its own step ceiling and per-call idle timeouts)
    #   where the generic timeout would cancel legitimate long-running
    #   work rather than catch a genuine hang.
    execution_timeout_seconds: Optional[int] = None

    # path_arg_name (2026-08-18, B2-1.1): the args_schema field name holding
    #   a filesystem path, for tools whose MUTATING/DESTRUCTIVE effect lands
    #   on that path (write/edit/delete). None (default) = this tool doesn't
    #   take a path or doesn't need the out-of-workspace approval gate.
    #   core/tools/dispatcher.py's _should_require_approval() reads this to
    #   generically find which argument to path-check, without hardcoding
    #   tool names.
    path_arg_name: Optional[str] = None

    async def run(self, args: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError
