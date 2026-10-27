# tools/default/subagent_config_tools.py
"""
Subagent (agent) configuration builder tools -- let the model create, read,
edit, and delete persistent SubagentConfig rows directly, so the user can
say "make me a subagent that does X" in a normal conversation instead of
going through the /agents panel. Backs the agent-builder skill
(skills/agent-builder/SKILL.md).

Four tools, mirroring the CRUD shape already established for memory
(memory_recall/memory_store/memory_forget) rather than one polymorphic
tool -- see the design discussion this shipped from: create/edit share the
MUTATING tier (matches memory_store's insert-or-update precedent), delete
is DESTRUCTIVE (matches memory_forget/delete_file/git_push -- the "hard to
undo" tier, not just "is this a delete"), get is READ_ONLY.

Category is deliberately "general", not "delegation" -- confirmed via
core/execution/worker.py's TOOL_CATEGORIES_BY_MODE that "delegation" is NOT
in the conversational-mode allowlist. These tools exist specifically to
work in plain conversational chat (the primary use case), so a category
excluded from that mode would silently break it.

Reuses core/agents/registry.py's SubagentRegistry directly -- no new DB
logic. Two things these tools do that the existing POST/PUT /agents API
endpoints don't: (1) refuse a duplicate name on create instead of silently
overwriting (the app-level upsert() has no such guard -- fine for a human
who sees the existing list, risky for a model that can't), an explicit
edit tool is the correct path for changing an existing one instead;
(2) validate `tools` against the real tool registry and `model`/`provider`
against capability_registry, neither of which the API validates today.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.tools.registry import default_tool
from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.config_validation import validate_tool_names, validate_model_provider


# ── Argument schemas ──────────────────────────────────────────────────────────

class CreateSubagentConfigArgs(BaseModel):
    name: str = Field(..., description="Unique slug for the new subagent (e.g. 'researcher').")
    description: Optional[str] = Field(
        None, description="What this subagent does -- shown to the orchestrator when deciding whether to delegate to it.",
    )
    system_prompt: Optional[str] = Field(None, description="Specialised system prompt for the subagent.")
    model: Optional[str] = Field(None, description="Model override. Omit to inherit the current session's model.")
    provider: Optional[str] = Field(None, description="Provider override. Omit to inherit the current session's provider.")
    tools: List[str] = Field(
        default_factory=list,
        description="Tool names this subagent may use. Empty list = inherits all tools the user has enabled.",
    )
    max_steps: int = Field(5, ge=1, le=20, description="Maximum ReAct steps the subagent may take per run.")


class GetSubagentConfigArgs(BaseModel):
    name: str = Field(..., description="Name of the subagent to look up.")


class EditSubagentConfigArgs(BaseModel):
    name: str = Field(..., description="Name of the existing subagent to edit.")
    description: Optional[str] = Field(None, description="New description. Omit to leave unchanged.")
    system_prompt: Optional[str] = Field(None, description="New system prompt. Omit to leave unchanged.")
    model: Optional[str] = Field(None, description="New model. Omit to leave unchanged.")
    provider: Optional[str] = Field(None, description="New provider. Omit to leave unchanged.")
    tools: Optional[List[str]] = Field(
        None,
        description="New tool list, replacing the old one entirely. Omit to leave unchanged.",
    )
    max_steps: Optional[int] = Field(None, ge=1, le=20, description="New max_steps. Omit to leave unchanged.")


class DeleteSubagentConfigArgs(BaseModel):
    name: str = Field(..., description="Name of the subagent to permanently delete.")


# ── create_subagent_config ────────────────────────────────────────────────────

@default_tool(
    name="create_subagent_config",
    description=(
        "Create a new, persistent, reusable subagent that can later be invoked by name via "
        "delegate_to_agent. Use this when the user explicitly asks you to build/save a "
        "subagent for a recurring task."
    ),
    args_schema=CreateSubagentConfigArgs,
    authority=ToolAuthority.MUTATING,
    category="general",
    summary="Save a new reusable subagent from a name, system prompt, and tool list.",
)
class CreateSubagentConfigTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available -- cannot create a subagent."})

        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(result={"error": "name must not be empty."})

        from core.agents.registry import subagent_registry

        existing = await subagent_registry.get(user_id, name)
        if existing is not None:
            return ToolResult(result={
                "error": (
                    f"A subagent named '{name}' already exists. Use edit_subagent_config to "
                    "modify it, or choose a different name."
                ),
            })

        tools = args.get("tools") or []
        invalid_tools = validate_tool_names(tools, execution)
        if invalid_tools:
            return ToolResult(result={
                "error": (
                    f"Unknown tool name(s): {', '.join(invalid_tools)}. "
                    "Check the Available Tools list for exact names."
                ),
            })

        warnings: List[str] = []
        model_warning = validate_model_provider(args.get("provider"), args.get("model"))
        if model_warning:
            warnings.append(model_warning)

        row = await subagent_registry.upsert(user_id, {
            "name": name,
            "description": args.get("description"),
            "system_prompt": args.get("system_prompt"),
            "model": args.get("model"),
            "provider": args.get("provider"),
            "tools": tools,
            "max_steps": args.get("max_steps", 5),
        })

        result: Dict[str, Any] = {
            "created": True,
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "system_prompt": row.system_prompt,
            "model": row.model,
            "provider": row.provider,
            "tools": row.tools,
            "max_steps": row.max_steps,
            "message": (
                f"Subagent '{name}' created. Invoke it via "
                f'delegate_to_agent(agent_name="{name}", task="...").'
            ),
        }
        if warnings:
            result["warnings"] = warnings
        return ToolResult(result=result)


# ── get_subagent_config ───────────────────────────────────────────────────────

@default_tool(
    name="get_subagent_config",
    description=(
        "Look up an existing subagent's current configuration by name. Call this before "
        "editing one so you know the current values -- edit_subagent_config replaces whichever "
        "fields you pass, and only those; knowing what's already there avoids overwriting "
        "something you didn't mean to touch."
    ),
    args_schema=GetSubagentConfigArgs,
    authority=ToolAuthority.READ_ONLY,
    category="general",
    summary="Look up an existing subagent's current configuration by name.",
)
class GetSubagentConfigTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available."})

        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(result={"error": "name must not be empty."})

        from core.agents.registry import subagent_registry

        row = await subagent_registry.get(user_id, name)
        if row is None:
            return ToolResult(result={"found": False, "error": f"No subagent named '{name}' found."})

        return ToolResult(result={
            "found": True,
            "name": row.name,
            "description": row.description,
            "system_prompt": row.system_prompt,
            "model": row.model,
            "provider": row.provider,
            "tools": row.tools,
            "max_steps": row.max_steps,
            "is_default": row.is_default,
        })


# ── edit_subagent_config ──────────────────────────────────────────────────────

@default_tool(
    name="edit_subagent_config",
    description=(
        "Change one or more fields of an existing subagent. Only the fields you pass are "
        "changed -- everything else is left exactly as it was. Use get_subagent_config first "
        "if you're not sure what the current values are."
    ),
    args_schema=EditSubagentConfigArgs,
    authority=ToolAuthority.MUTATING,
    category="general",
    summary="Change one or more fields of an existing subagent, leaving the rest untouched.",
)
class EditSubagentConfigTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available -- cannot edit a subagent."})

        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(result={"error": "name must not be empty."})

        from core.agents.registry import subagent_registry

        existing = await subagent_registry.get(user_id, name)
        if existing is None:
            return ToolResult(result={
                "error": (
                    f"No subagent named '{name}' found. Use create_subagent_config to create it, "
                    "or get_subagent_config to check the exact name."
                ),
            })

        update_data: Dict[str, Any] = {"name": name}
        for field in ("description", "system_prompt", "model", "provider", "max_steps"):
            value = args.get(field)
            if value is not None:
                update_data[field] = value

        tools = args.get("tools")
        if tools is not None:
            # Mirrors PUT /agents/{name}'s own restriction (api/agents.py) --
            # a default agent's tool list is managed by EVA's seeder, not
            # editable directly. Without this check the tool would be a
            # silent bypass of a safety rail the human-facing API enforces.
            if getattr(existing, "is_default", False):
                existing_tools = set(existing.tools or [])
                requested_tools = set(tools)
                if existing_tools != requested_tools:
                    return ToolResult(result={
                        "error": (
                            f"'{name}' is a default agent -- its tool list is managed by EVA and "
                            "cannot be changed directly. Ask the user to clone it in the Agents "
                            "panel first if a customised tool list is needed."
                        ),
                    })
            invalid_tools = validate_tool_names(tools, execution)
            if invalid_tools:
                return ToolResult(result={
                    "error": f"Unknown tool name(s): {', '.join(invalid_tools)}.",
                })
            update_data["tools"] = tools

        if len(update_data) == 1:  # only "name" present -- nothing was actually requested
            return ToolResult(result={
                "error": "No fields to change were provided. Pass at least one field to update.",
            })

        warnings: List[str] = []
        if "model" in update_data or "provider" in update_data:
            model_warning = validate_model_provider(
                update_data.get("provider", existing.provider),
                update_data.get("model", existing.model),
            )
            if model_warning:
                warnings.append(model_warning)

        row = await subagent_registry.upsert(user_id, update_data)

        result: Dict[str, Any] = {
            "updated": True,
            "name": row.name,
            "changed_fields": [k for k in update_data if k != "name"],
            "description": row.description,
            "system_prompt": row.system_prompt,
            "model": row.model,
            "provider": row.provider,
            "tools": row.tools,
            "max_steps": row.max_steps,
        }
        if warnings:
            result["warnings"] = warnings
        return ToolResult(result=result)


# ── delete_subagent_config ────────────────────────────────────────────────────

@default_tool(
    name="delete_subagent_config",
    description=(
        "Permanently delete a subagent configuration by name. This cannot be undone. Fine to "
        "use for self-correction (e.g. deleting a subagent you just created by mistake), not "
        "just on explicit user request."
    ),
    args_schema=DeleteSubagentConfigArgs,
    authority=ToolAuthority.DESTRUCTIVE,
    category="general",
    summary="Permanently delete a subagent configuration by name.",
)
class DeleteSubagentConfigTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available -- cannot delete a subagent."})

        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(result={"error": "name must not be empty."})

        from core.agents.registry import subagent_registry

        deleted = await subagent_registry.delete(user_id, name)
        if not deleted:
            return ToolResult(result={"error": f"No subagent named '{name}' found."})

        return ToolResult(result={"deleted": True, "name": name})
