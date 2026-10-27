# core/tools/registry.py

from typing import Dict, Type
from pydantic import BaseModel
from .base import ToolBase, ToolAuthority


# -----------------------------------------------------------
# DEFAULT tool registry (system tools)
# -----------------------------------------------------------

class DefaultToolRegistry:
    _registry: Dict[str, Type[ToolBase]] = {}

    @classmethod
    def register(cls, name: str, tool_cls: Type[ToolBase]):
        cls._registry[name] = tool_cls

    @classmethod
    def all(cls) -> Dict[str, Type[ToolBase]]:
        return dict(cls._registry)

    @classmethod
    def custom(cls):
        """Compatibility stub. Defaults have no custom subset."""
        return {}


# -----------------------------------------------------------
# CUSTOM tool registry (user tools)
# -----------------------------------------------------------

class CustomToolRegistry:
    _registry: Dict[str, Type[ToolBase]] = {}

    @classmethod
    def register(cls, name: str, tool_cls: Type[ToolBase]):
        cls._registry[name] = tool_cls

    @classmethod
    def all(cls) -> Dict[str, Type[ToolBase]]:
        return dict(cls._registry)


# -----------------------------------------------------------
# Decorators for registering tools
# -----------------------------------------------------------

def default_tool(
    name: str,
    description: str,
    args_schema: BaseModel,
    authority: ToolAuthority = ToolAuthority.READ_ONLY,
    agent_only: bool = False,
    # V5.0 Sprint 2: Progressive Tool Disclosure fields
    category: str = "general",
    chat_excluded: bool = False,
    summary: str = None,
):
    """
    Register a class as a default (system) tool.

    ``authority`` controls whether the Human-in-the-Loop approval gate fires
    for this tool.  Defaults to READ_ONLY (no gate).

    ``chat_excluded`` marks tools that must only be available in agent/PEV
    modes.  When True, the tool is excluded from conversational (Chat) mode.
    Replaces the old ``agent_only`` flag (kept as backward-compat alias).

    ``category`` groups tools by function for mode-based manifest filtering.
    Vocabulary: filesystem | shell | memory | search | communication |
                delegation | git | general

    ``summary`` is a single sentence (~60 tokens) used in the manifest block
    in the system prompt.  Defaults to description[:80] when None.
    """
    def wrapper(cls: Type[ToolBase]):
        cls.name = name
        cls.description = description
        cls.args_schema = args_schema
        cls.authority = authority
        # Both flags kept: agent_only for legacy compat, chat_excluded as canonical
        cls.agent_only = agent_only or chat_excluded
        cls.chat_excluded = chat_excluded or agent_only
        cls.category = category
        cls.summary = summary
        DefaultToolRegistry.register(name, cls)
        return cls
    return wrapper


def custom_tool(
    name: str,
    description: str,
    args_schema: BaseModel,
    authority: ToolAuthority = ToolAuthority.READ_ONLY,
):
    """
    Register a class as a custom (user-defined) tool.

    ``authority`` controls whether the Human-in-the-Loop approval gate fires
    for this tool.  Defaults to READ_ONLY (no gate).
    """
    def wrapper(cls: Type[ToolBase]):
        cls.name = name
        cls.description = description
        cls.args_schema = args_schema
        cls.authority = authority
        CustomToolRegistry.register(name, cls)
        return cls
    return wrapper
