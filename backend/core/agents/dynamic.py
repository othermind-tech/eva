# core/agents/dynamic.py
#
# Shared helper for building an ephemeral, non-DB-registered subagent config
# from inline tool-call arguments. Used whenever an orchestration tool's
# requested agent_name isn't found in subagent_registry -- delegate_to_agent
# and parallel_delegate (V5.0 Sprint 3), and coordinate (A-05 fix) all share
# this single implementation rather than each carrying their own copy.

from types import SimpleNamespace
from typing import Any, Dict

_DEFAULT_DYNAMIC_MAX_STEPS = 10


def default_system_prompt(agent_name: str) -> str:
    """
    Generate a minimal persona for a dynamic subagent when the caller
    did not provide an explicit system_prompt.
    """
    return (
        f"You are {agent_name}, a specialised AI assistant. "
        "Complete the assigned task thoroughly, accurately, and concisely. "
        "Return a clear, well-structured result. "
        "If you cannot complete the task, explain exactly what is missing."
    )


def build_dynamic_config(agent_name: str, args: Dict[str, Any]) -> SimpleNamespace:
    """
    Construct a SimpleNamespace that quacks like a SubagentConfig for use
    when the requested agent_name is not registered in the DB.

    Fields read from *args* (all optional):
      system_prompt   — persona for the child; defaults to a generic one
      allowed_tools   — list of tool names; None/omitted = inherit all
      max_steps       — ReAct step cap; defaults to _DEFAULT_DYNAMIC_MAX_STEPS
      model           — model override; None = inherit from session
      provider        — provider override; None = inherit from session
    """
    raw_prompt = args.get("system_prompt") or ""
    system_prompt = raw_prompt.strip() or default_system_prompt(agent_name)

    allowed_tools = args.get("allowed_tools")  # None = inherit full tool set
    if isinstance(allowed_tools, list) and len(allowed_tools) == 0:
        allowed_tools = None  # treat [] same as None (inherit all)

    max_steps = args.get("max_steps")
    try:
        max_steps = int(max_steps) if max_steps is not None else _DEFAULT_DYNAMIC_MAX_STEPS
        max_steps = max(1, min(max_steps, 50))  # clamp to [1, 50]
    except (TypeError, ValueError):
        max_steps = _DEFAULT_DYNAMIC_MAX_STEPS

    return SimpleNamespace(
        id=None,                        # no DB UUID — caller uses f"dyn-{name}"
        name=agent_name,
        system_prompt=system_prompt,
        tools=allowed_tools,            # None = all; list = whitelist
        max_steps=max_steps,
        model=args.get("model") or None,
        provider=args.get("provider") or None,
    )
