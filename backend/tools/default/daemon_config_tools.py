# tools/default/daemon_config_tools.py
"""
Daemon configuration builder tools -- let the model create, read, edit, and
delete persistent DaemonConfig rows directly, so the user can say "set up a
daemon that does X every morning" in a normal conversation instead of going
through the /daemons panel. Backs the daemon-builder skill
(skills/daemon-builder/SKILL.md).

Same shape and reasoning as subagent_config_tools.py -- see that file's
module docstring for the authority-tier and category rationale, both of
which apply identically here.

One daemon-specific addition: delete_daemon_config reports whether the
target was actively running (lifecycle_status == "running") at the moment
of deletion, rather than silently no-opping that fact away the way
DELETE /daemons/{name} does today. Deliberately NOT a hard block on
deleting a running daemon -- self-correction (the model cleaning up a
daemon it just created and realised was wrong, even mid-run) is a
legitimate, low-risk case a blanket refusal would break; the DESTRUCTIVE
authority tier + tool_approval_mode is already the right control for
"should a human bless this," consistent with every other destructive tool
in EVA. This is purely a transparency addition -- the fact is now visible
in the tool's result instead of disappearing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.tools.registry import default_tool
from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.config_validation import validate_tool_names, validate_model_provider


# ── Argument schemas ──────────────────────────────────────────────────────────

class CreateDaemonConfigArgs(BaseModel):
    name: str = Field(..., description="Unique slug for the new daemon (e.g. 'daily-monitor').")
    description: Optional[str] = Field(None, description="What this daemon does.")
    system_prompt: Optional[str] = Field(None, description="System prompt for the daemon's agent.")
    trigger_message: Optional[str] = Field(
        None, description="The message sent to the daemon each time it wakes. Omit for a generic default.",
    )
    model: Optional[str] = Field(None, description="Model override. Omit to inherit the current session's model.")
    provider: Optional[str] = Field(None, description="Provider override. Omit to inherit the current session's provider.")
    tools: List[str] = Field(
        default_factory=list,
        description="Tool names this daemon may use. Empty list = inherits all tools the user has enabled.",
    )
    max_steps: int = Field(5, ge=1, le=20, description="Maximum ReAct steps per run.")
    run_interval_seconds: Optional[int] = Field(
        None, ge=60,
        description="Seconds between automatic runs -- minimum 60. Omit for manual-trigger-only (no schedule).",
    )
    enabled: bool = Field(True, description="Whether the scheduler should fire this daemon automatically.")


class GetDaemonConfigArgs(BaseModel):
    name: str = Field(..., description="Name of the daemon to look up.")


class EditDaemonConfigArgs(BaseModel):
    name: str = Field(..., description="Name of the existing daemon to edit.")
    description: Optional[str] = Field(None, description="New description. Omit to leave unchanged.")
    system_prompt: Optional[str] = Field(None, description="New system prompt. Omit to leave unchanged.")
    trigger_message: Optional[str] = Field(None, description="New trigger message. Omit to leave unchanged.")
    model: Optional[str] = Field(None, description="New model. Omit to leave unchanged.")
    provider: Optional[str] = Field(None, description="New provider. Omit to leave unchanged.")
    tools: Optional[List[str]] = Field(
        None, description="New tool list, replacing the old one entirely. Omit to leave unchanged.",
    )
    max_steps: Optional[int] = Field(None, ge=1, le=20, description="New max_steps. Omit to leave unchanged.")
    run_interval_seconds: Optional[int] = Field(
        None, ge=60, description="New schedule interval in seconds (minimum 60). Omit to leave unchanged.",
    )
    enabled: Optional[bool] = Field(None, description="Enable/disable automatic scheduling. Omit to leave unchanged.")


class DeleteDaemonConfigArgs(BaseModel):
    name: str = Field(..., description="Name of the daemon to permanently delete.")


# ── create_daemon_config ──────────────────────────────────────────────────────

@default_tool(
    name="create_daemon_config",
    description=(
        "Create a new, persistent, scheduled daemon that runs automatically on its own "
        "interval. Use this when the user asks you to set up a recurring/scheduled task -- "
        "an alternative to using the daemon panel, done entirely in conversation."
    ),
    args_schema=CreateDaemonConfigArgs,
    authority=ToolAuthority.MUTATING,
    category="general",
    summary="Save a new scheduled daemon from a name, trigger message, and run interval.",
)
class CreateDaemonConfigTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available -- cannot create a daemon."})

        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(result={"error": "name must not be empty."})

        from core.daemons.registry import daemon_registry

        existing = await daemon_registry.get(user_id, name)
        if existing is not None:
            return ToolResult(result={
                "error": (
                    f"A daemon named '{name}' already exists. Use edit_daemon_config to modify "
                    "it, or choose a different name."
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

        row = await daemon_registry.upsert(user_id, {
            "name": name,
            "description": args.get("description"),
            "system_prompt": args.get("system_prompt"),
            "trigger_message": args.get("trigger_message"),
            "model": args.get("model"),
            "provider": args.get("provider"),
            "tools": tools,
            "max_steps": args.get("max_steps", 5),
            "run_interval_seconds": args.get("run_interval_seconds"),
            "enabled": args.get("enabled", True),
        })

        result: Dict[str, Any] = {
            "created": True,
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "system_prompt": row.system_prompt,
            "trigger_message": row.trigger_message,
            "model": row.model,
            "provider": row.provider,
            "tools": row.tools,
            "max_steps": row.max_steps,
            "run_interval_seconds": row.run_interval_seconds,
            "enabled": row.enabled,
            "message": (
                f"Daemon '{name}' created"
                + (f" and will run automatically every {row.run_interval_seconds}s."
                   if row.run_interval_seconds else ", manual trigger only (no schedule set).")
            ),
        }
        if warnings:
            result["warnings"] = warnings
        return ToolResult(result=result)


# ── get_daemon_config ─────────────────────────────────────────────────────────

@default_tool(
    name="get_daemon_config",
    description=(
        "Look up an existing daemon's current configuration and live status by name. Call this "
        "before editing one so you know the current values."
    ),
    args_schema=GetDaemonConfigArgs,
    authority=ToolAuthority.READ_ONLY,
    category="general",
    summary="Look up an existing daemon's current configuration and status by name.",
)
class GetDaemonConfigTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available."})

        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(result={"error": "name must not be empty."})

        from core.daemons.registry import daemon_registry

        row = await daemon_registry.get(user_id, name)
        if row is None:
            return ToolResult(result={"found": False, "error": f"No daemon named '{name}' found."})

        return ToolResult(result={
            "found": True,
            "name": row.name,
            "description": row.description,
            "system_prompt": row.system_prompt,
            "trigger_message": row.trigger_message,
            "model": row.model,
            "provider": row.provider,
            "tools": row.tools,
            "max_steps": row.max_steps,
            "run_interval_seconds": row.run_interval_seconds,
            "enabled": row.enabled,
            "lifecycle_status": row.lifecycle_status,
            "last_run_at": str(row.last_run_at) if row.last_run_at else None,
            "next_run_at": str(row.next_run_at) if row.next_run_at else None,
        })


# ── edit_daemon_config ────────────────────────────────────────────────────────

@default_tool(
    name="edit_daemon_config",
    description=(
        "Change one or more fields of an existing daemon. Only the fields you pass are changed "
        "-- everything else, including its running schedule, is left exactly as it was. Use "
        "get_daemon_config first if you're not sure what the current values are."
    ),
    args_schema=EditDaemonConfigArgs,
    authority=ToolAuthority.MUTATING,
    category="general",
    summary="Change one or more fields of an existing daemon, leaving the rest untouched.",
)
class EditDaemonConfigTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available -- cannot edit a daemon."})

        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(result={"error": "name must not be empty."})

        from core.daemons.registry import daemon_registry

        existing = await daemon_registry.get(user_id, name)
        if existing is None:
            return ToolResult(result={
                "error": (
                    f"No daemon named '{name}' found. Use create_daemon_config to create it, "
                    "or get_daemon_config to check the exact name."
                ),
            })

        update_data: Dict[str, Any] = {"name": name}
        for field in (
            "description", "system_prompt", "trigger_message", "model", "provider",
            "max_steps", "run_interval_seconds", "enabled",
        ):
            value = args.get(field)
            if value is not None:
                update_data[field] = value

        tools = args.get("tools")
        if tools is not None:
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

        row = await daemon_registry.upsert(user_id, update_data)

        result: Dict[str, Any] = {
            "updated": True,
            "name": row.name,
            "changed_fields": [k for k in update_data if k != "name"],
            "description": row.description,
            "system_prompt": row.system_prompt,
            "trigger_message": row.trigger_message,
            "model": row.model,
            "provider": row.provider,
            "tools": row.tools,
            "max_steps": row.max_steps,
            "run_interval_seconds": row.run_interval_seconds,
            "enabled": row.enabled,
        }
        if warnings:
            result["warnings"] = warnings
        return ToolResult(result=result)


# ── delete_daemon_config ──────────────────────────────────────────────────────

@default_tool(
    name="delete_daemon_config",
    description=(
        "Permanently delete a daemon configuration by name. This cannot be undone and stops its "
        "schedule immediately for future runs. Fine to use for self-correction (e.g. deleting a "
        "daemon you just created by mistake, even if it's mid-run), not just on explicit user "
        "request."
    ),
    args_schema=DeleteDaemonConfigArgs,
    authority=ToolAuthority.DESTRUCTIVE,
    category="general",
    summary="Permanently delete a daemon configuration by name.",
)
class DeleteDaemonConfigTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available -- cannot delete a daemon."})

        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(result={"error": "name must not be empty."})

        from core.daemons.registry import daemon_registry

        existing = await daemon_registry.get(user_id, name)
        if existing is None:
            return ToolResult(result={"error": f"No daemon named '{name}' found."})

        was_running = getattr(existing, "lifecycle_status", None) == "running"

        deleted = await daemon_registry.delete(user_id, name)
        if not deleted:
            return ToolResult(result={"error": f"No daemon named '{name}' found."})

        result: Dict[str, Any] = {"deleted": True, "name": name}
        if was_running:
            result["was_running"] = True
            result["note"] = (
                "This daemon had an active execution in progress when deleted. The in-flight "
                "run will complete on its own; its result will not be recorded since the "
                "daemon config no longer exists."
            )
        return ToolResult(result=result)
