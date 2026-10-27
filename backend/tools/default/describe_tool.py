"""
describe_tool — Progressive Tool Disclosure meta-tool (V5.0 Sprint 2)

This is the gateway tool for the progressive disclosure mechanism.

When progressive disclosure is active the provider API only includes
`describe_tool` + already-promoted tools in each turn's tool list.  The
full set is listed in the manifest block injected into the system prompt.

Flow:
  1. Model reads the manifest — sees all available tool names + summaries.
  2. Model decides it needs `write_file`.
  3. Model calls `describe_tool(name="write_file")`.
  4. This tool returns the full JSON parameter schema for `write_file` and
     marks it as promoted in the execution's UserToolRegistry.
  5. On the NEXT ReAct iteration the handler's provider_tools is rebuilt
     to include `write_file` — so the model can call it directly.

Design notes:
  - describe_tool is category="general" and always included in the active
    tool set regardless of promotion state (it IS the promotion mechanism).
  - It accesses the global DefaultToolRegistry/CustomToolRegistry for schema
    lookups so it can describe ANY registered tool, not just the currently
    active subset -- and falls back to execution.user_registry's dynamic
    (MCP) registry for tools synced from the user's MCP servers, which
    live outside those two static registries.
  - The promotion is stored in execution.user_registry._promoted_tools.
    If user_registry is None (e.g. non-progressive or daemon mode), the
    tool still returns the schema — just without promotion side-effects.
"""

import json
from typing import Optional

from pydantic import BaseModel

from core.tools.base import ToolBase, ToolResult
from core.tools.registry import default_tool


class DescribeToolArgs(BaseModel):
    name: str


@default_tool(
    name="describe_tool",
    description=(
        "Get the full parameter schema for any available tool before calling it. "
        "Call this first for any tool not yet in your active toolkit -- and only then: a tool you "
        "have already described or used in this run is active, so call it directly instead. "
        "Returns the tool's description and all parameters with types and defaults. "
        "After calling this, the tool becomes available for direct use in your next response."
    ),
    args_schema=DescribeToolArgs,
    category="general",
    summary="Get the full parameter schema for a tool and activate it for direct use.",
)
class DescribeToolTool(ToolBase):
    """
    Progressive disclosure gateway: returns a tool's full JSON schema and
    promotes it into the active provider tool list for the next turn.
    """

    async def run(self, args: dict) -> ToolResult:
        from core.tools.registry import DefaultToolRegistry, CustomToolRegistry  # noqa

        tool_name: str = args["name"]

        execution = getattr(self, "execution", None)
        user_registry = getattr(execution, "user_registry", None)

        # Search all registered (static) tools first.
        all_tools = {
            **DefaultToolRegistry.all(),
            **CustomToolRegistry.all(),
        }

        tool_cls = all_tools.get(tool_name)
        promoted = False

        if tool_cls is not None:
            # Static tool: promote through the UserToolRegistry so the next
            # turn's provider_tools includes the full schema.
            if user_registry is not None:
                try:
                    promoted = user_registry.promote(tool_name)
                except Exception:
                    pass  # non-fatal — schema still returned
        else:
            # Not a static tool -- try resolving it through the dynamic
            # (MCP) registry.  promote() builds a real tool class from the
            # MCP manifest and inserts it into user_registry.tools, so we
            # can read it back from there once promotion succeeds.
            if user_registry is not None:
                try:
                    promoted = user_registry.promote(tool_name)
                except Exception:
                    promoted = False
                if promoted:
                    tool_cls = user_registry.tools.get(tool_name)

        if tool_cls is None:
            # Build a helpful list of available tool names for the model,
            # including any MCP tools synced for this user.
            available = sorted(all_tools.keys())
            dynamic_registry = getattr(user_registry, "dynamic_registry", None)
            if dynamic_registry is not None:
                available += sorted(m.name for m in dynamic_registry.list_tools())
            return ToolResult(result={
                "error": f"Tool '{tool_name}' not found.",
                "available_tools": available,
                "hint": "Check the manifest in your system prompt for the correct tool name.",
            })

        # Build the full JSON schema from the Pydantic args_schema.
        schema: dict = {}
        if tool_cls.args_schema is not None:
            try:
                schema = tool_cls.args_schema.model_json_schema()
            except Exception as exc:
                schema = {"error": f"Could not generate schema: {exc}"}

        return ToolResult(result={
            "tool": tool_name,
            "description": tool_cls.description,
            "category": getattr(tool_cls, "category", "general"),
            "authority": getattr(tool_cls, "authority", "read_only"),
            "parameters": schema,
            "promoted": promoted,
            "message": (
                f"Tool '{tool_name}' is now in your active toolkit. "
                "You can call it directly in your next response."
            ) if promoted else (
                f"Schema for '{tool_name}' returned. "
                "Note: the tool was already active or promotion is not required in this mode."
            ),
        })
