# tools/default/blackboard_tools.py
#
# G-004: Execution-scoped blackboard tools for multi-agent coordination.
#
# Two tools are provided:
#
#   blackboard_write(slot, content, mode, sensitive)
#       Write a value to a named slot in the shared execution blackboard.
#       The slot MUST start with the calling agent's name as a prefix
#       (e.g. "researcher_a:findings") to enforce write ownership (L2).
#       Slots starting with "orchestrator:" are writable by any agent to
#       allow the orchestrator to post plans and coordination data.
#
#   blackboard_read(slot)
#       Read one or all slots from the shared blackboard.
#       Any agent within the same execution may read any slot.
#
# Security properties:
#   - The execution_id (and therefore the blackboard key) is NEVER a
#     tool parameter — derived server-side from self.execution only.
#   - written_by is derived from self.execution.caller_agent_name (set
#     by the delegate_to_agent tool before running a child) — the LLM
#     cannot fake its own identity in a slot write.
#   - Cross-execution reads are structurally impossible: no API exists.
#
# Authority levels:
#   blackboard_write — MUTATING
#   blackboard_read  — READ_ONLY

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from core.tools.registry import default_tool
from core.tools.base import ToolBase, ToolResult, ToolAuthority


# ── Argument schemas ──────────────────────────────────────────────────────────

class BlackboardWriteArgs(BaseModel):
    slot: str = Field(
        ...,
        description=(
            "Slot name to write to. MUST start with your agent name as a prefix "
            "(e.g. 'researcher_a:findings', 'coder_b:diff'). "
            "Use 'orchestrator:plan' for coordination data shared by all agents."
        ),
    )
    content: str = Field(
        ...,
        description="The content to store in this slot.",
    )
    mode: str = Field(
        "overwrite",
        description=(
            "Write mode: 'overwrite' replaces any existing value (default); "
            "'append' adds to a growing list (useful for multi-step accumulation)."
        ),
    )
    sensitive: bool = Field(
        False,
        description=(
            "Mark this slot as sensitive. Sensitive entries are stored normally "
            "but shown as placeholders when recalled from long-term memory, "
            "requiring user approval before the full content is returned."
        ),
    )


class BlackboardReadArgs(BaseModel):
    slot: Optional[str] = Field(
        None,
        description=(
            "Slot name to read. If omitted, all slots in the blackboard are returned. "
            "Append-mode slots (lists) return all accumulated entries."
        ),
    )


# ── blackboard_write ──────────────────────────────────────────────────────────

@default_tool(
    name="blackboard_write",
    description=(
        "Write a value to a named slot in the shared execution blackboard. "
        "All agents within this execution share one blackboard — use it to pass "
        "intermediate results between subagents without re-running work. "
        "Your slot name MUST start with your agent name as a prefix "
        "(e.g. 'researcher_a:findings'). "
        "Use mode='append' to accumulate results across multiple writes."
    ),
    args_schema=BlackboardWriteArgs,
    authority=ToolAuthority.MUTATING,
    category="general",
    chat_excluded=True,
    summary="Write a value to a named slot in the shared execution blackboard for cross-agent sharing.",
)
class BlackboardWriteTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={"error": "blackboard_write: no execution context"})

        blackboard = getattr(execution, "blackboard", None)
        if blackboard is None:
            return ToolResult(result={
                "error": "Blackboard is not available for this execution."
            })

        # written_by is ALWAYS derived from execution context — never from LLM args
        written_by: str = getattr(execution, "caller_agent_name", "orchestrator")
        slot: str       = args["slot"]
        content: str    = args["content"]
        mode: str       = args.get("mode", "overwrite")
        sensitive: bool = bool(args.get("sensitive", False))

        if mode not in ("overwrite", "append"):
            return ToolResult(result={
                "error": f"Invalid mode '{mode}'. Must be 'overwrite' or 'append'."
            })

        try:
            await blackboard.write(
                slot=slot,
                content=content,
                written_by=written_by,
                mode=mode,
                sensitive=sensitive,
            )
        except PermissionError as exc:
            return ToolResult(result={"error": str(exc)})
        except Exception as exc:
            return ToolResult(result={"error": f"Blackboard write failed: {exc}"})

        return ToolResult(result={
            "written": True,
            "slot": slot,
            "written_by": written_by,
            "mode": mode,
            "sensitive": sensitive,
        })


# ── blackboard_read ───────────────────────────────────────────────────────────

@default_tool(
    name="blackboard_read",
    description=(
        "Read from the shared execution blackboard. "
        "Pass a slot name to read a specific entry, or omit it to read all slots. "
        "Returns the content plus metadata (written_by, written_at, sensitive flag). "
        "Any agent within the same execution can read any slot regardless of prefix."
    ),
    args_schema=BlackboardReadArgs,
    authority=ToolAuthority.READ_ONLY,
    category="general",
    chat_excluded=True,
    summary="Read a slot (or all slots) from the shared execution blackboard.",
)
class BlackboardReadTool(ToolBase):
    async def run(self, args: Dict[str, Any]) -> ToolResult:
        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={"error": "blackboard_read: no execution context"})

        blackboard = getattr(execution, "blackboard", None)
        if blackboard is None:
            return ToolResult(result={
                "error": "Blackboard is not available for this execution."
            })

        slot: Optional[str] = args.get("slot")

        try:
            if slot:
                entry = await blackboard.read(slot)
                if entry is None:
                    return ToolResult(result={
                        "found": False,
                        "slot": slot,
                        "message": f"Slot '{slot}' does not exist in the blackboard.",
                    })
                return ToolResult(result={
                    "found": True,
                    "slot": slot,
                    "entry": entry,
                })
            else:
                all_slots = await blackboard.read_all()
                return ToolResult(result={
                    "found": True,
                    "slot_count": len(all_slots),
                    "slots": all_slots,
                })
        except Exception as exc:
            return ToolResult(result={"error": f"Blackboard read failed: {exc}"})
