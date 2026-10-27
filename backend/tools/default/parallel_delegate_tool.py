# tools/default/parallel_delegate_tool.py
#
# G-005: parallel_delegate tool
#
# Runs multiple named subagents CONCURRENTLY via asyncio.gather.
# All agents within the same execution share one blackboard and one shadow
# buffer.  Each child writes its result to the blackboard under
# its own namespace slot ({agent_name}:result) when write_to_blackboard=True.
#
# Design:
#   - _ExecutionView proxies the shared ConversationExecution but overrides
#     caller_agent_name per child — satisfying L2 blackboard write-ownership
#     without mutating the shared execution object.
#   - AgentEngine is constructed with max_steps from the subagent config
#     (no module-level patching — G-005 clean-up of G-003 approach).
#   - asyncio.gather runs all children concurrently; each child handles its
#     own exceptions and returns a result dict — no exception propagates to
#     the gather level.
#   - SSE chunks from all children are interleaved on the shadow buffer,
#     tagged by agent_name so the frontend can route to the correct panel.
#
# Authority: READ_ONLY (same as delegate_to_agent — does not directly mutate
#   state; child agents may call mutating tools subject to their own approval
#   mode settings).
#
# Args:
#   tasks           — list of {agent_name, task, context?}, max 5 items
#   write_to_blackboard — if True (default), each agent's final output is
#                         written to {agent_name}:result on the blackboard

from __future__ import annotations

import asyncio
import time
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from config.settings import MAX_PARALLEL_TASKS
from core.tools.base import ToolBase, ToolAuthority, ToolResult
from core.tools.registry import default_tool
from core.agents.dynamic import build_dynamic_config as _build_dynamic_config

logger = logging.getLogger(__name__)

_MAX_PARALLEL_TASKS = MAX_PARALLEL_TASKS


# ---------------------------------------------------------------------------
# Argument schemas
# ---------------------------------------------------------------------------

class ParallelTask(BaseModel):
    agent_name: str = Field(
        description=(
            "Name of the subagent to run.  If a saved subagent has this name its "
            "saved config is used.  Otherwise a NEW dynamic subagent is created from "
            "the optional fields below -- there is no error for an unknown name, so "
            "only use names you know exist."
        )
    )
    task: str = Field(
        description=(
            "Full description of the task this subagent should complete. "
            "Be specific — the subagent has no memory of the current conversation."
        )
    )
    context: Optional[str] = Field(
        default=None,
        description="Optional additional context or prior results the subagent needs.",
    )
    # ── Dynamic subagent fields (used only when agent_name is not registered) ──
    system_prompt: Optional[str] = Field(
        default=None,
        description=(
            "System prompt / persona for the dynamic subagent.  Only consulted "
            "when agent_name is NOT a registered subagent."
        ),
    )
    allowed_tools: Optional[List[str]] = Field(
        default=None,
        description=(
            "Explicit list of tool names the dynamic subagent may use.  "
            "null / omitted = inherit the full parent tool set.  "
            "Only consulted when agent_name is NOT a registered subagent."
        ),
    )
    max_steps: Optional[int] = Field(
        default=None,
        description=(
            "Maximum ReAct iterations for the dynamic subagent (default 10).  "
            "Only consulted when agent_name is NOT a registered subagent."
        ),
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            "Model override for the dynamic subagent (null = inherit from session).  "
            "Only consulted when agent_name is NOT a registered subagent."
        ),
    )
    provider: Optional[str] = Field(
        default=None,
        description=(
            "Provider override for the dynamic subagent (null = inherit from session).  "
            "Only consulted when agent_name is NOT a registered subagent."
        ),
    )


class ParallelDelegateArgs(BaseModel):
    tasks: List[ParallelTask] = Field(
        description=(
            f"List of tasks to run in parallel (1–{_MAX_PARALLEL_TASKS} items). "
            "Each task runs concurrently — wall-clock time equals the slowest agent."
        )
    )
    write_to_blackboard: bool = Field(
        default=True,
        description=(
            "If true (default), each agent's final output is written to the blackboard "
            "under the slot '{agent_name}:result' so the orchestrator can read all "
            "results via blackboard_read after this call returns."
        ),
    )


# ---------------------------------------------------------------------------
# _ExecutionView — per-child execution proxy
# ---------------------------------------------------------------------------

class _ExecutionView:
    """
    Lightweight proxy around ConversationExecution that shadows caller_agent_name
    for one specific child agent.

    All other attributes (blackboard, shadow, user_id, execution_id, cancel_token,
    session_id, etc.) are delegated transparently to the real execution object.

    This lets concurrent children each have the correct L2 blackboard namespace
    without mutating the shared execution dataclass.
    """

    def __init__(self, real_execution: Any, agent_name: str) -> None:
        # Use object.__setattr__ to bypass our own __setattr__
        object.__setattr__(self, "_real", real_execution)
        object.__setattr__(self, "caller_agent_name", agent_name)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "caller_agent_name":
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_real"), name, value)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _emit_subagent_chunk(shadow: Any, payload: dict) -> None:
    """Append a subagent SSE chunk to the parent's shadow buffer (no-op if None)."""
    if shadow is None:
        return
    try:
        await shadow.append(payload)
    except Exception as exc:
        logger.debug("parallel_delegate: failed to emit subagent chunk: %s", exc)


async def _run_one(
    task: ParallelTask,
    view: _ExecutionView,
    cfg: Any,
    provider: str,
    model: str,
    api_key: str,
    write_to_blackboard: bool,
    fallback_chain: Optional[List[dict]] = None,
    dynamic: bool = False,
) -> dict:
    """
    Run a single subagent and return a result dict.

    Never raises — all exceptions are caught and returned as
    {status: "failed", error: "..."} so asyncio.gather always collects all results.
    """
    from core.tools.dispatcher import ToolDispatcher
    from core.agent_engine import AgentEngine
    from core.tools.user_registry import UserToolRegistry
    from core.event_bus import event_bus
    from core.events import SubagentStarted, SubagentCompleted

    agent_name = task.agent_name
    execution_id = getattr(view, "execution_id", "")
    shadow = getattr(view, "shadow", None)
    blackboard = getattr(view, "blackboard", None)

    # Check for cancellation before starting
    cancel_token = getattr(view, "cancel_token", None)
    if cancel_token and getattr(cancel_token, "cancelled", False):
        return {
            "agent_name": agent_name,
            "status": "cancelled",
            "error": "Execution was cancelled before this agent could start.",
            "duration_ms": 0,
        }

    # V6.59 Phase 5: run-time usability gate for this agent's target. Never
    # raises (this function's contract): an unusable target is a failed result
    # for this agent only, the others still run.
    from core.providers.usability import resolve_child_target
    _plan = resolve_child_target(
        getattr(cfg, "provider", None), getattr(cfg, "model", None),
        provider, model, fallback_chain,
    )
    if not _plan.ok:
        return {
            "agent_name": agent_name,
            "status": "failed",
            "error": _plan.failure_message(),
            "duration_ms": 0,
        }
    provider, model = _plan.primary["provider"], _plan.primary["model"]
    fallback_chain = _plan.fallbacks

    child_max_steps = max(1, cfg.max_steps or 5)
    allowed_tools: List[str] = cfg.tools or []

    # Inherit the parent execution's already-synced MCP tools (if any) --
    # gated by the same allowed_tools list as custom tools, mirroring
    # delegate_tool.py's single-subagent path. `view` proxies attribute
    # access through to the real execution object (see _ExecutionView).
    parent_dynamic_registry = getattr(
        getattr(view, "user_registry", None), "dynamic_registry", None
    )

    child_tools = UserToolRegistry(
        default_tools=True,
        enabled_tools=allowed_tools,
        dynamic_registry=parent_dynamic_registry,
    )
    child_dispatcher = ToolDispatcher()

    # Sprint 3: dynamic agents may override provider/model per-task
    child_provider = provider
    child_model    = model

    # Emit: started
    await _emit_subagent_chunk(shadow, {
        "type": "subagent",
        "event": "started",
        "agent_name": agent_name,
        "dynamic": dynamic,
        "content": "",
        "tool_name": None,
    })
    event_bus.publish(SubagentStarted(
        execution_id=execution_id,
        agent_name=agent_name,
        parent_call_id=execution_id,
    ))

    child_agent = AgentEngine(
        provider=child_provider,
        api_key=api_key,
        model=child_model,
        system_prompt=cfg.system_prompt or "",
        reasoning=None,
        tools=child_tools,
        dispatcher=child_dispatcher,
        session_id=getattr(view, "session_id", None),
        memory_provider=None,
        execution=view,            # inject _ExecutionView so tools see correct caller_agent_name
        fallback_chain=fallback_chain or [],  # V6.5 Phase 3: inherit parent's fallback chain
        max_steps=child_max_steps, # G-005: per-instance cap, no module patching
        dynamic_registry=parent_dynamic_registry,
    )

    user_message = task.task
    if task.context:
        user_message = f"{task.task}\n\nContext:\n{task.context}"
    messages = [{"role": "user", "content": user_message}]

    start_ms = time.monotonic()
    text_parts: list[str] = []
    success = True
    error_msg = ""

    try:
        async for chunk in child_agent.call(messages, stream=True):
            chunk_type = getattr(chunk, "type", "")

            if chunk_type == "text":
                content = getattr(chunk, "content", "")
                text_parts.append(content)
                await _emit_subagent_chunk(shadow, {
                    "type": "subagent",
                    "event": "text",
                    "agent_name": agent_name,
                    "content": content,
                    "tool_name": None,
                })

            elif chunk_type == "tool_call":
                tool_name = getattr(chunk, "tool_name", "")
                await _emit_subagent_chunk(shadow, {
                    "type": "subagent",
                    "event": "tool_call",
                    "agent_name": agent_name,
                    "content": "",
                    "tool_name": tool_name,
                })

            elif chunk_type == "error":
                error_msg = getattr(chunk, "message", "Unknown error")
                success = False
                await _emit_subagent_chunk(shadow, {
                    "type": "subagent",
                    "event": "failed",
                    "agent_name": agent_name,
                    "content": error_msg,
                    "tool_name": None,
                })
                break

            # Cooperatively yield — lets other parallel coroutines make progress
            await asyncio.sleep(0)

    except Exception as exc:
        success = False
        error_msg = str(exc)
        logger.exception(
            "parallel_delegate: subagent raised exception",
            extra={"agent_name": agent_name, "execution_id": execution_id},
        )
        await _emit_subagent_chunk(shadow, {
            "type": "subagent",
            "event": "failed",
            "agent_name": agent_name,
            "content": error_msg,
            "tool_name": None,
        })

    duration_ms = int((time.monotonic() - start_ms) * 1000)
    final_text = "".join(text_parts)

    # Emit: completed / failed
    await _emit_subagent_chunk(shadow, {
        "type": "subagent",
        "event": "completed" if success else "failed",
        "agent_name": agent_name,
        "content": final_text if success else error_msg,
        "tool_name": None,
    })

    event_bus.publish(SubagentCompleted(
        execution_id=execution_id,
        agent_name=agent_name,
        parent_call_id=execution_id,
        success=success,
        duration_ms=duration_ms,
    ))

    # Write result to blackboard if enabled (slot: {agent_name}:result)
    if write_to_blackboard and blackboard is not None:
        slot = f"{agent_name}:result"
        content_to_write = final_text if success else f"[FAILED] {error_msg}"
        try:
            await blackboard.write(
                slot=slot,
                content=content_to_write,
                written_by=agent_name,   # satisfies L2: slot starts with agent_name:
                mode="overwrite",
                sensitive=False,
            )
        except Exception as exc:
            logger.warning(
                "parallel_delegate: blackboard write failed for %s: %s",
                agent_name, exc,
            )

    logger.info(
        "parallel_delegate: subagent finished",
        extra={
            "agent_name": agent_name,
            "success": success,
            "duration_ms": duration_ms,
            "output_chars": len(final_text),
        },
    )

    if success:
        return {
            "agent_name": agent_name,
            "status": "completed",
            "output": final_text,
            "duration_ms": duration_ms,
        }
    else:
        return {
            "agent_name": agent_name,
            "status": "failed",
            "error": error_msg,
            "duration_ms": duration_ms,
        }


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

@default_tool(
    name="parallel_delegate",
    description=(
        "Run multiple subagents CONCURRENTLY and return all their results. "
        "Each task can target a registered subagent (by name) or spawn a dynamic "
        "one on-the-fly using the optional system_prompt, allowed_tools, max_steps, "
        "model, and provider fields.  All agents start simultaneously; total wall-clock "
        "time equals the slowest agent, not the sum.  Results are written to the shared "
        "blackboard under {agent_name}:result.  Max 5 tasks per call."
    ),
    args_schema=ParallelDelegateArgs,
    authority=ToolAuthority.READ_ONLY,
    category="delegation",
    chat_excluded=True,
    summary="Run multiple named or dynamic subagents concurrently and aggregate results.",
)
class ParallelDelegateTool(ToolBase):
    """
    Runs multiple subagents concurrently via asyncio.gather.
    Each child gets a _ExecutionView proxy with its own caller_agent_name
    so blackboard L2 ownership is enforced per-child without mutation of the
    shared ConversationExecution object.
    """

    async def run(self, args: Dict[str, Any]) -> ToolResult:
        # ------------------------------------------------------------------
        # 1. Parse and validate args
        # ------------------------------------------------------------------
        parsed = ParallelDelegateArgs(**args)
        tasks = parsed.tasks
        write_to_blackboard = parsed.write_to_blackboard

        if not tasks:
            return ToolResult(result={"error": "tasks list must not be empty."})

        if len(tasks) > _MAX_PARALLEL_TASKS:
            return ToolResult(result={
                "error": f"Too many tasks: {len(tasks)} supplied, max is {_MAX_PARALLEL_TASKS}."
            })

        # ------------------------------------------------------------------
        # 2. Resolve execution context
        # ------------------------------------------------------------------
        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={"error": "parallel_delegate: no execution context."})

        user_id = getattr(execution, "user_id", "")

        # ------------------------------------------------------------------
        # 3. Resolve provider / model / api_key from session
        # ------------------------------------------------------------------
        from config.settings import get_user_session
        session = get_user_session(user_id)
        provider = session.get("provider")
        model    = session.get("model")
        api_key  = session.get("api_key", "")
        # No hardcoded provider/model fallback (V6.4): EVA has no default
        # model of its own -- an unconfigured session is a real error here.
        if not provider or not model:
            return ToolResult(result={
                "error": "parallel_delegate: no provider/model configured for this session."
            })

        # Hook propagation (B-076 Gap 5):
        # Parent tool_hooks automatically apply to all parallel subagents because
        # the ToolDispatcher reads hook_definitions from get_user_session(user_id),
        # which is shared across parent and all child agents (same user_id).
        # This matches Claude Code behaviour — no explicit hook copy needed.

        # ------------------------------------------------------------------
        # 4. Resolve all subagent configs before starting any agent.
        #    Sprint 3: unknown names fall back to dynamic construction instead
        #    of failing — allowing ad-hoc agents without pre-registration.
        # ------------------------------------------------------------------
        from core.agents.registry import subagent_registry

        configs: Dict[str, Any] = {}       # agent_name → config object
        dynamic_flags: Dict[str, bool] = {}  # agent_name → is_dynamic

        for task in tasks:
            if task.agent_name in configs:
                # Duplicate agent_name in task list — allowed, each gets own view
                continue
            cfg = await subagent_registry.get(user_id, task.agent_name)
            if cfg is None:
                # Dynamic mode: build synthetic config from the task's inline args
                task_args = task.dict()
                cfg = _build_dynamic_config(task.agent_name, task_args)
                dynamic_flags[task.agent_name] = True
                logger.info(
                    "parallel_delegate: dynamic subagent spawned (no registered config)",
                    extra={"user_id": user_id, "agent_name": task.agent_name,
                           "max_steps": cfg.max_steps, "tools": cfg.tools},
                )
            else:
                dynamic_flags[task.agent_name] = False
            configs[task.agent_name] = cfg

        # ------------------------------------------------------------------
        # 5. Build per-child execution views and run concurrently
        # ------------------------------------------------------------------
        coroutines = [
            _run_one(
                task=task,
                view=_ExecutionView(execution, task.agent_name),
                cfg=configs[task.agent_name],
                provider=provider,
                model=model,
                api_key=api_key,
                write_to_blackboard=write_to_blackboard,
                fallback_chain=session.get("fallback_chain") or [],  # V6.5 Phase 3
                dynamic=dynamic_flags.get(task.agent_name, False),
            )
            for task in tasks
        ]

        results: List[dict] = await asyncio.gather(*coroutines)

        # ------------------------------------------------------------------
        # 6. Aggregate and return
        # ------------------------------------------------------------------
        completed = sum(1 for r in results if r.get("status") == "completed")
        failed    = sum(1 for r in results if r.get("status") != "completed")

        logger.info(
            "parallel_delegate: all agents finished",
            extra={
                "tasks_run": len(results),
                "completed": completed,
                "failed": failed,
            },
        )

        return ToolResult(result={
            "tasks_run":          len(results),
            "completed":          completed,
            "failed":             failed,
            "write_to_blackboard": write_to_blackboard,
            "results":            results,
        })


# ---------------------------------------------------------------------------
# Sprint 3 dynamic subagent config builder moved to core/agents/dynamic.py
# (shared with delegate_tool.py and coordinate_tool.py, A-05 fix) — see
# module-level import above.
