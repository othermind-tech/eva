# tools/default/delegate_tool.py
#
# G-003: delegate_to_agent tool
#
# Allows the orchestrator agent to hand off a specific task to a named
# specialised subagent.  The subagent runs as a child AgentEngine within the
# same execution context (same user, same shadow buffer).  Its streaming
# output is forwarded to the parent's SSE stream tagged as `type: subagent`
# events so the frontend can render a dedicated subagent panel.
#
# Design notes:
# - Subagent inherits the parent user's API key (no separate auth).
# - Subagent's tool set can be restricted to a whitelist in its config.
# - Max ReAct steps are capped at the subagent's configured max_steps (≤ 5 by
#   default) to prevent runaway delegation chains.
# - The final text output is returned as the tool result; the orchestrator
#   continues its own ReAct loop with this result as context.
# - If no subagent with the given name is found, the tool returns an error
#   string so the model can recover gracefully.

from __future__ import annotations

import time
import asyncio
import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from core.tools.base import ToolBase, ToolAuthority, ToolResult
from core.tools.registry import default_tool
from core.memory.conversation_journal import ConversationJournal
from core.agents.dynamic import build_dynamic_config as _build_dynamic_config
from config.settings import DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Args schema
# ---------------------------------------------------------------------------

class DelegateArgs(BaseModel):
    agent_name: str = Field(
        description=(
            "Name of the subagent to delegate to.  If a saved subagent has this "
            "name (see create_subagent_config, or the Agents panel), its saved "
            "configuration is used. If none does, a NEW dynamic subagent is created "
            "on-the-fly from the optional fields below -- it does not raise an "
            "error, so a misspelt or invented name silently spawns an unrelated "
            "agent. Only use names you know exist."
        )
    )
    task: str = Field(
        description=(
            "Full description of the task the subagent should complete.  "
            "Be specific — the subagent has no memory of the current conversation."
        )
    )
    context: Optional[str] = Field(
        default=None,
        description=(
            "Optional additional context (e.g. relevant excerpts, prior "
            "results) the subagent needs to complete the task."
        ),
    )
    # ── Dynamic subagent fields (used only when agent_name is not registered) ──
    system_prompt: Optional[str] = Field(
        default=None,
        description=(
            "System prompt / persona for the dynamic subagent.  Only consulted "
            "when agent_name is NOT a registered subagent.  If omitted, a "
            "sensible default persona is generated from the agent name."
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
            "Maximum number of ReAct iterations for the dynamic subagent.  "
            "Defaults to 10 when not specified.  "
            "Only consulted when agent_name is NOT a registered subagent."
        ),
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            "Model override for the dynamic subagent.  "
            "null = inherit from the current user session.  "
            "Only consulted when agent_name is NOT a registered subagent."
        ),
    )
    provider: Optional[str] = Field(
        default=None,
        description=(
            "Provider override for the dynamic subagent.  "
            "null = inherit from the current user session.  "
            "Only consulted when agent_name is NOT a registered subagent."
        ),
    )
    background: bool = Field(
        default=False,
        description=(
            "If False (default): run the subagent now and wait for its result "
            "before continuing (unchanged behaviour). "
            "If True: start the subagent as a real, independently-tracked "
            "background execution and return its execution_id immediately, "
            "without waiting. Use this for a task that will take a while and "
            "doesn't block what you do next -- pair it with "
            "schedule_wakeup(watch_execution_id=<the returned execution_id>) "
            "to be automatically resumed the moment it finishes, with zero "
            "polling. The background execution has its own independent "
            "lifecycle: cancelling or otherwise ending your own execution "
            "does not stop it, and vice versa."
        ),
    )


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

@default_tool(
    name="delegate_to_agent",
    description=(
        "Delegate a specific subtask to a subagent and return its result.  "
        "If a saved subagent has this name (see create_subagent_config, or the Agents "
        "panel) its saved config is used.  Otherwise a NEW dynamic subagent is spawned "
        "on-the-fly using the optional system_prompt, allowed_tools, max_steps, model, "
        "and provider fields -- there is no error for an unknown name, so a misspelt or "
        "invented name silently creates an unrelated agent.  "
        "Use this when you need focused expertise in one domain rather than doing "
        "everything yourself.  The subagent runs independently and returns its "
        "final answer."
    ),
    args_schema=DelegateArgs,
    authority=ToolAuthority.READ_ONLY,
    category="delegation",
    summary="Delegate a subtask to a named or dynamic subagent and return its result.",
)
class DelegateToAgentTool(ToolBase):
    """
    Runs a named subagent as a child AgentEngine and returns its final output.

    The child agent's streaming chunks are forwarded to the parent's shadow
    buffer as SubagentChunk objects so the SSE stream carries real-time subagent
    progress alongside the orchestrator's own output.
    """

    # A nested AgentEngine already protects itself against a genuine hang
    # (EVA_FIRST_CHUNK_TIMEOUT_S / EVA_INTER_CHUNK_TIMEOUT_S at the
    # individual-call level) and against runaway work (its own max_steps
    # ceiling) -- so the generic dispatcher timeout only needs to be a
    # generous backstop here, not the primary control.
    execution_timeout_seconds = DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS

    async def run(self, args: Dict[str, Any]) -> ToolResult:
        agent_name: str = args["agent_name"]
        task: str       = args["task"]
        context: str    = args.get("context") or ""

        # ------------------------------------------------------------------
        # 1. Resolve execution context (injected by dispatcher)
        # ------------------------------------------------------------------
        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={
                "error": "delegate_to_agent: no execution context available."
            })

        user_id          = getattr(execution, "user_id", "")
        execution_id     = getattr(execution, "execution_id", "")
        parent_conv_id   = getattr(execution, "conversation_id", "")
        shadow           = getattr(execution, "shadow", None)  # LocalShadowBuffer | None

        # ------------------------------------------------------------------
        # 2. Look up subagent config from DB; fall back to dynamic construction
        # ------------------------------------------------------------------
        from core.agents.registry import subagent_registry
        cfg = await subagent_registry.get(user_id, agent_name)
        _dynamic = False

        # G-003 Enforcement: Project-scoped agent access control.
        # Only fires when the calling conversation belongs to a project
        # (execution.parent_project_id set by worker.py after project resolution).
        # Dynamic agents (cfg=None) are always allowed — they are ephemeral and
        # have no DB-registered project affiliation.
        parent_project_id = getattr(execution, "parent_project_id", None)
        if parent_project_id and cfg is not None:
            cfg_project_id = getattr(cfg, "project_id", None)
            cfg_is_default  = getattr(cfg, "is_default", False)
            if not cfg_is_default and cfg_project_id != parent_project_id:
                # Agent is either user-global (project_id=None, is_default=False)
                # or belongs to a different project — deny access.
                _deny_msg = (
                    f"Access denied: agent '{agent_name}' is not available in this project. "
                    "Only agents assigned to this project and default system agents may be used. "
                    "Assign the agent to this project or use a project-specific agent instead."
                )
                logger.warning(
                    "G-003: cross-project agent invocation blocked",
                    extra={
                        "user_id": user_id,
                        "agent_name": agent_name,
                        "agent_project_id": cfg_project_id,
                        "conversation_project_id": parent_project_id,
                    },
                )
                return ToolResult(result={"error": _deny_msg})

        if cfg is None:
            # Sprint 3: dynamic subagent — build a synthetic config from inline args.
            # The caller (orchestrator) supplies system_prompt, allowed_tools, etc.
            # directly in the tool call instead of relying on a pre-registered entry.
            _dynamic = True
            cfg = _build_dynamic_config(agent_name, args)
            logger.info(
                "Dynamic subagent spawned (no registered config found)",
                extra={
                    "user_id": user_id,
                    "agent_name": agent_name,
                    "max_steps": cfg.max_steps,
                    "tools": cfg.tools,
                },
            )

        # ------------------------------------------------------------------
        # 3. Resolve provider / model / api_key from parent session
        # ------------------------------------------------------------------
        from config.settings import get_user_session
        session = get_user_session(user_id)

        # No hardcoded provider/model fallback (V6.4): EVA has no default
        # model of its own -- an unconfigured session is a real error here.
        provider  = getattr(cfg, "provider", None) or session.get("provider")
        model     = getattr(cfg, "model", None)    or session.get("model")
        api_key   = session.get("api_key", "")
        if not provider or not model:
            return ToolResult(result={
                "error": "delegate_to_agent: no provider/model configured for this session."
            })

        # V6.59 Phase 5: run-time usability gate. Resolves the sub-agent's own
        # provider+model override (never mixed with the parent's model), drops
        # targets whose key was removed / model is unusable, and falls back to
        # the next usable candidate -- instead of crashing at handler
        # construction. Returns a readable tool error if nothing is usable.
        from core.providers.usability import resolve_child_target
        _plan = resolve_child_target(
            getattr(cfg, "provider", None), getattr(cfg, "model", None),
            session.get("provider"), session.get("model"),
            session.get("fallback_chain"),
        )
        if not _plan.ok:
            return ToolResult(result={
                "error": f"delegate_to_agent: sub-agent '{agent_name}' cannot run. {_plan.failure_message()}"
            })
        provider, model = _plan.primary["provider"], _plan.primary["model"]
        _child_fallbacks = _plan.fallbacks

        # Hook propagation (B-076 Gap 5):
        # Parent tool_hooks automatically propagate to this subagent because the
        # ToolDispatcher always reads hook_definitions via get_user_session(user_id),
        # and the child agent runs with the same user_id as the parent.  No explicit
        # copy is needed — hooks are user-scoped, not execution-scoped.
        # This matches Claude Code's behaviour: parent hooks apply to all child tools.

        # ------------------------------------------------------------------
        # 4. Build a restricted tool registry for the subagent
        # ------------------------------------------------------------------
        from core.tools.user_registry import UserToolRegistry

        allowed_tools: List[str] = cfg.tools or []
        # allowed_tools=[] means "all user tools" in UserToolRegistry convention

        # Inherit the parent execution's already-synced MCP tools (if any) so
        # the subagent can use them too -- gated by the same allowed_tools
        # list as custom tools, so a subagent only gets the MCP tools its
        # config explicitly lists, not automatic access to every server the
        # user has configured. Non-progressive registries (this one) merge
        # MCP tools in eagerly at construction -- see UserToolRegistry.
        parent_dynamic_registry = getattr(
            getattr(execution, "user_registry", None), "dynamic_registry", None
        )

        child_tools = UserToolRegistry(
            default_tools=True,
            enabled_tools=allowed_tools,
            dynamic_registry=parent_dynamic_registry,
            # allowed_tools=[] means "all user tools" (existing convention);
            # otherwise actually restrict the default toolset too -- previously
            # a configured tools list only gated custom/MCP tools, not defaults.
            allowed_default_tools=set(allowed_tools) if allowed_tools else None,
        )

        # ------------------------------------------------------------------
        # 5. Emit: subagent started
        # ------------------------------------------------------------------
        # Journal: subagent started — written to parent conv's subagents folder.
        # cfg.id is the DB UUID for registered agents; dynamic agents get a
        # synthetic ID so the journal folder has a stable name.
        import datetime as _dt
        agent_id = getattr(cfg, "id", None) or f"dyn-{agent_name}"
        ConversationJournal.log_subagent(user_id, parent_conv_id, agent_id, {
            "type":         "subagent_started",
            "agent_name":   agent_name,
            "agent_id":     agent_id,
            "dynamic":      _dynamic,
            "execution_id": execution_id,
            "task":         task,
            "timestamp":    _dt.datetime.now(_dt.timezone.utc).isoformat(),
        })

        await _emit_subagent_chunk(shadow, {
            "type": "subagent",
            "event": "started",
            "agent_name": agent_name,
            "content": "",
            "tool_name": None,
        })

        from core.event_bus import event_bus
        from core.events import SubagentStarted, SubagentCompleted

        # We don't have a parent call_id easily here — use execution_id as proxy
        parent_call_id = execution_id

        event_bus.publish(SubagentStarted(
            execution_id=execution_id,
            agent_name=agent_name,
            parent_call_id=parent_call_id,
        ))

        # ------------------------------------------------------------------
        # 5b. Background mode: register an independently-tracked execution.
        #
        # Mirrors core/daemons/runner.py::run_daemon()'s own registration
        # block (create_execution_record -> RUNNING -> broker running ->
        # phase-1 metrics row) so a backgrounded subagent is listable /
        # cancellable / watchable via the exact same paths a daemon run is --
        # GET /executions/running, POST /cancel, and schedule_wakeup's
        # watch_execution_id polling (WakeupScheduler reads execution_metrics,
        # so this row must exist before run() returns or a wakeup registered
        # immediately after would never see it become "due").
        #
        # child_execution is the object actually passed to the child
        # AgentEngine below (step 6) for tool-call authorization. When
        # background=False it is literally `execution` (the parent's own
        # object, same reference) -- so every foreground call is byte-
        # identical to pre-Phase-2 behaviour; nothing below this point
        # branches on `background` except by way of this one reference swap
        # plus the dispatch decision at the end of step 8.
        # ------------------------------------------------------------------
        background: bool = bool(args.get("background", False))
        child_execution = execution
        active_execution_id = execution_id

        if background:
            from core.execution import execution_registry
            from core.execution.models import ExecutionStatus
            from core.execution.redis_registry import redis_execution_registry as _rer

            bg_execution_id = str(uuid4())
            child_execution = await execution_registry.create_execution_record(
                conversation_id=parent_conv_id,
                user_id=user_id,
                session_id=getattr(execution, "session_id", ""),
                execution_mode="subagent_background",
                execution_id=bg_execution_id,
                initial_message=task,
            )
            child_execution.status = ExecutionStatus.RUNNING  # skip PENDING/queued, matches daemon runs
            child_execution.project_workspace = getattr(execution, "project_workspace", None)
            child_execution.parent_project_id = getattr(execution, "parent_project_id", None)
            active_execution_id = bg_execution_id

            try:
                await _rer.mark_execution_running(bg_execution_id)
            except Exception as exc:
                logger.warning(
                    "delegate_to_agent(background): could not mark broker running: %s", exc
                )

            try:
                from core.db.session import AsyncSessionLocal as _ASL0
                from core.db.repository import ExecutionMetricsRepo as _EMRepo0
                async with _ASL0() as _db0:
                    await _EMRepo0.upsert(_db0, {
                        "execution_id":    bg_execution_id,
                        "conversation_id": parent_conv_id,
                        "user_id":         user_id,
                        "status":          "running",
                        "triggered_by":    "subagent_background",
                        "daemon_name":     agent_name,
                        "started_at":      _dt.datetime.now(_dt.timezone.utc),
                    })
            except Exception as exc:
                logger.warning(
                    "delegate_to_agent(background): could not write initial metrics: %s", exc
                )

        # ------------------------------------------------------------------
        # 6. Build child AgentEngine
        # ------------------------------------------------------------------
        from core.tools.dispatcher import ToolDispatcher
        from core.agent_engine import AgentEngine

        child_dispatcher = ToolDispatcher()

        # G-005: pass max_steps directly to AgentEngine — no module-level patching needed.
        child_max_steps = max(1, cfg.max_steps or 5)

        # Skills-index priming (V3.1 parity): without this, a child agent told
        # to "read a skill file" gets no framing that distinguishes a Skill
        # from an arbitrary file -- this is the same block worker.py injects
        # for real chat turns (core/execution/worker.py, "Inject Available
        # Skills block"). Non-fatal: failures are logged and the prompt is
        # kept as-is, matching that same call site's error handling.
        child_system_prompt = cfg.system_prompt or ""
        skills_mode = session.get("skills_mode", "auto")
        if skills_mode in ("auto", "enabled"):
            try:
                from core.skills.registry import get_skill_registry
                skills_block = get_skill_registry(user_id).build_available_skills_block(task)
                if skills_block:
                    sep = "\n\n" if child_system_prompt else ""
                    child_system_prompt = child_system_prompt + sep + skills_block
            except Exception as skills_exc:
                logger.warning(
                    "delegate_to_agent: skills injection failed (non-fatal)",
                    exc_info=skills_exc,
                    extra={"agent_name": agent_name, "execution_id": execution_id},
                )

        child_agent = AgentEngine(
            provider=provider,
            api_key=api_key,
            model=model,
            system_prompt=child_system_prompt,
            reasoning=None,          # keep child fast; no extended thinking
            tools=child_tools,
            dispatcher=child_dispatcher,
            session_id=getattr(execution, "session_id", None),
            memory_provider=None,     # child has no memory of its own
            execution=child_execution,  # own execution context in background mode; parent's otherwise
            dynamic_registry=parent_dynamic_registry,
            fallback_chain=_child_fallbacks,  # V6.5 Phase 3: parent's fallback chain (usable entries only -- V6.59 Phase 5)
            max_steps=child_max_steps,
        )

        # ------------------------------------------------------------------
        # 7. Build initial messages for the child
        # ------------------------------------------------------------------
        user_message = task
        if context:
            user_message = f"{task}\n\nContext:\n{context}"

        # Retry continuity (dispatcher-provided, see ToolDispatcher.execute_tool):
        # a plain dict shared across every attempt of THIS SAME tool call within
        # the dispatcher's retry loop. If a previous attempt already made tool
        # calls before being cancelled on timeout, state that as fact so this
        # attempt doesn't blindly repeat the same exploration. Deliberately a
        # short factual list, not a replay of the previous attempt's raw
        # transcript -- a half-finished transcript risks confusing the model
        # more than it helps.
        continuity = getattr(self, "retry_continuity", None)
        prior_calls = continuity.get("tool_calls") if continuity else None
        if prior_calls:
            digest_lines = "\n".join(
                f"- {c['tool_name']}({c.get('args_preview', '')})" for c in prior_calls
            )
            user_message += (
                "\n\nNote: a previous attempt at this exact task ran out of time "
                "partway through and was cancelled. It had already made these "
                f"tool calls before being cut off:\n{digest_lines}\n"
                "Their results are not available, but avoid blindly repeating "
                "the same exploration -- build on it or take a different approach."
            )

        messages = [{"role": "user", "content": user_message}]

        # ------------------------------------------------------------------
        # 8. Run child agent — foreground (await + return) or background
        #    (fire-and-forget via asyncio.create_task, return immediately).
        #
        # Foreground path is byte-identical to pre-Phase-2 behaviour: it's
        # the same call, just now routed through _run_and_finalize() so the
        # streaming/journal/event logic isn't duplicated between the two
        # modes rather than any behavioural change.
        # ------------------------------------------------------------------
        if background:
            async def _bg_run() -> None:
                try:
                    await self._run_and_finalize(
                        child_agent=child_agent,
                        child_execution=child_execution,
                        active_execution_id=active_execution_id,
                        agent_name=agent_name,
                        agent_id=agent_id,
                        dynamic=_dynamic,
                        user_id=user_id,
                        parent_conv_id=parent_conv_id,
                        parent_call_id=parent_call_id,
                        shadow=shadow,
                        messages=messages,
                        continuity=continuity,
                        background=True,
                    )
                except Exception:
                    # Detached task: nothing else will ever observe an
                    # exception raised here, so this is the last line of
                    # defense against a background subagent silently
                    # vanishing without its execution ever reaching a
                    # terminal status.
                    logger.exception(
                        "delegate_to_agent(background): unhandled exception in "
                        "background subagent run",
                        extra={"agent_name": agent_name, "execution_id": active_execution_id},
                    )
                    try:
                        await self._finalize_background_execution(
                            child_execution, agent_name=agent_name, success=False,
                            error_msg="internal error running background subagent",
                            duration_ms=0,
                        )
                    except Exception:
                        pass

            asyncio.create_task(_bg_run(), name=f"subagent-bg-{active_execution_id}")

            return ToolResult(result={
                "agent_name": agent_name,
                "execution_id": active_execution_id,
                "status": "started_in_background",
            })

        return await self._run_and_finalize(
            child_agent=child_agent,
            child_execution=child_execution,
            active_execution_id=active_execution_id,
            agent_name=agent_name,
            agent_id=agent_id,
            dynamic=_dynamic,
            user_id=user_id,
            parent_conv_id=parent_conv_id,
            parent_call_id=parent_call_id,
            shadow=shadow,
            messages=messages,
            continuity=continuity,
            background=False,
        )

    # ------------------------------------------------------------------
    # Streaming + finalization, shared by the foreground (awaited) and
    # background (asyncio.create_task'd) call paths. Behaviour is identical
    # either way except for the two background-only tails: writing the
    # independent execution's terminal status/metrics, and including
    # execution_id in the returned result (the background caller already
    # got execution_id back immediately in run()'s own return, but a
    # consistent shape here costs nothing and helps anything inspecting the
    # task's result directly, e.g. in tests).
    # ------------------------------------------------------------------
    async def _run_and_finalize(
        self,
        *,
        child_agent: "AgentEngine",
        child_execution,
        active_execution_id: str,
        agent_name: str,
        agent_id: str,
        dynamic: bool,
        user_id: str,
        parent_conv_id: str,
        parent_call_id: str,
        shadow,
        messages: list,
        continuity: Optional[dict],
        background: bool,
    ) -> ToolResult:
        import datetime as _dt
        from core.event_bus import event_bus
        from core.events import SubagentCompleted

        # G-004: set caller_agent_name so blackboard_write enforces the correct
        # slot namespace (L2).  Restored in finally regardless of outcome.
        # Safe to mutate child_execution directly here even in background
        # mode: child_execution is this call's own dedicated execution
        # object (created in run()'s step 5b), not shared with anything else.
        _original_caller = getattr(child_execution, "caller_agent_name", "orchestrator")
        child_execution.caller_agent_name = agent_name

        start_ms = time.monotonic()
        final_text_parts: list[str] = []
        success = True
        error_msg = ""

        try:
            async for chunk in child_agent.call(messages, stream=True):
                chunk_type = getattr(chunk, "type", "")

                if chunk_type == "text":
                    content = getattr(chunk, "content", "")
                    final_text_parts.append(content)
                    await _emit_subagent_chunk(shadow, {
                        "type": "subagent",
                        "event": "text",
                        "agent_name": agent_name,
                        "content": content,
                        "tool_name": None,
                    })

                elif chunk_type == "tool_call":
                    tool_name = getattr(chunk, "tool_name", "")
                    if continuity is not None:
                        # Appended synchronously as each call happens, so this
                        # survives even if the coroutine is cancelled mid-flight
                        # by the dispatcher's timeout on a later attempt.
                        continuity.setdefault("tool_calls", []).append({
                            "tool_name": tool_name,
                            "args_preview": str(getattr(chunk, "arguments", ""))[:120],
                        })
                    # Journal: tool call made by subagent
                    # B-095: must use the computed agent_id (falls back to
                    # f"dyn-{agent_name}"), not raw cfg.id -- cfg.id is None
                    # for a dynamic (unregistered) subagent, which collapsed
                    # every dynamic subagent's tool-call events into one
                    # shared "None.jsonl" journal file. The other two
                    # log_subagent call sites in this function already use
                    # agent_id correctly; only this one didn't.
                    ConversationJournal.log_subagent(user_id, parent_conv_id, agent_id, {
                        "type":         "tool_call",
                        "agent_name":   agent_name,
                        "tool_name":    tool_name,
                        "call_id":      getattr(chunk, "call_id", ""),
                        "arguments":    getattr(chunk, "arguments", {}),
                        "execution_id": active_execution_id,
                        "timestamp":    _dt.datetime.now(_dt.timezone.utc).isoformat(),
                    })
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

                # reasoning / usage chunks — silently skip for child agent

        except Exception as exc:
            success = False
            error_msg = str(exc)
            logger.exception(
                "Subagent execution raised an exception",
                extra={"agent_name": agent_name, "execution_id": active_execution_id},
            )
            await _emit_subagent_chunk(shadow, {
                "type": "subagent",
                "event": "failed",
                "agent_name": agent_name,
                "content": error_msg,
                "tool_name": None,
            })

        finally:
            # G-004: restore caller_agent_name so the parent's blackboard ownership is correct.
            child_execution.caller_agent_name = _original_caller

        # ------------------------------------------------------------------
        # 9. Emit: subagent completed
        # ------------------------------------------------------------------
        duration_ms = int((time.monotonic() - start_ms) * 1000)
        final_text = "".join(final_text_parts)

        # Journal: subagent completed
        ConversationJournal.log_subagent(user_id, parent_conv_id, agent_id, {
            "type":         "subagent_completed",
            "agent_name":   agent_name,
            "agent_id":     agent_id,
            "dynamic":      dynamic,
            "execution_id": active_execution_id,
            "success":      success,
            "duration_ms":  duration_ms,
            "output":       final_text,
            "output_chars": len(final_text),
            "error":        error_msg,
            "timestamp":    _dt.datetime.now(_dt.timezone.utc).isoformat(),
        })

        await _emit_subagent_chunk(shadow, {
            "type": "subagent",
            "event": "completed",
            "agent_name": agent_name,
            "content": final_text if success else error_msg,
            "tool_name": None,
        })

        event_bus.publish(SubagentCompleted(
            execution_id=active_execution_id,
            agent_name=agent_name,
            parent_call_id=parent_call_id,
            success=success,
            duration_ms=duration_ms,
        ))

        logger.info(
            "Subagent finished",
            extra={
                "agent_name": agent_name,
                "success": success,
                "duration_ms": duration_ms,
                "output_chars": len(final_text),
            },
        )

        if background:
            await self._finalize_background_execution(
                child_execution, agent_name=agent_name,
                success=success, error_msg=error_msg, duration_ms=duration_ms,
            )

        # ------------------------------------------------------------------
        # 10. Return result to orchestrator (foreground) / detached task
        #     result (background — discarded by the caller, but consistent).
        # ------------------------------------------------------------------
        result: Dict[str, Any] = {"agent_name": agent_name}
        if background:
            result["execution_id"] = active_execution_id
        if success:
            result["output"] = final_text
        else:
            result["error"] = error_msg
        return ToolResult(result=result)

    # ------------------------------------------------------------------
    # Background mode only: transition the independently-tracked execution
    # to its terminal status and record final metrics — the same two things
    # run_daemon() does at the end of a daemon run (broker status +
    # execution_metrics upsert), so this backgrounded subagent is
    # indistinguishable from a daemon run to anything reading those tables
    # (GET /executions/{id}/status, WakeupScheduler's watch_execution_id
    # poll, GET /executions/running history).
    # ------------------------------------------------------------------
    async def _finalize_background_execution(
        self, child_execution, *, agent_name: str, success: bool, error_msg: str, duration_ms: int,
    ) -> None:
        import datetime as _dt3
        from core.execution.models import ExecutionStatus
        from core.execution.redis_registry import redis_execution_registry as _rer

        execution_id = child_execution.execution_id

        try:
            child_execution.status = ExecutionStatus.COMPLETED if success else ExecutionStatus.FAILED
        except Exception as exc:
            logger.warning(
                "delegate_to_agent(background): could not set final execution status: %s", exc
            )

        try:
            if success:
                await _rer.mark_execution_finished(execution_id)
            else:
                await _rer.mark_execution_failed(execution_id, error_msg)
        except Exception as exc:
            logger.warning(
                "delegate_to_agent(background): could not update broker status: %s", exc
            )

        try:
            from core.db.session import AsyncSessionLocal as _ASL
            from core.db.repository import ExecutionMetricsRepo as _EMRepo
            async with _ASL() as _db:
                await _EMRepo.upsert(_db, {
                    "execution_id":       execution_id,
                    # Included even though the row should already exist from
                    # run()'s step 5b -- upsert() falls back to inserting a
                    # fresh row if it doesn't (e.g. the earlier insert failed),
                    # and conversation_id/user_id are NOT NULL columns.
                    "conversation_id":    child_execution.conversation_id,
                    "user_id":            child_execution.user_id,
                    "status":             "completed" if success else "failed",
                    "triggered_by":       "subagent_background",
                    "termination_reason": "success" if success else "error",
                    "error_message":      error_msg if not success else None,
                    "duration_ms":        duration_ms,
                    "completed_at":       _dt3.datetime.now(_dt3.timezone.utc),
                })
        except Exception as exc:
            logger.warning(
                "delegate_to_agent(background): could not write final metrics: %s", exc
            )

        # P-19 Phase 3: reuse the generic execution.completed/execution.failed
        # events rather than inventing a bespoke one -- the frontend's live
        # task-tray already tracks every execution_id it got from
        # GET /executions/running (which already lists subagent_background
        # entries), so it can react by matching execution_id without needing
        # a new event-name case. daemon_name carries the agent name, same
        # repurposing of that field the DB row above already does.
        try:
            from core.events_publisher import publish_user_event
            await publish_user_event(
                user_id=child_execution.user_id,
                event="execution.completed" if success else "execution.failed",
                execution_id=execution_id,
                daemon_name=agent_name,
                summary=(
                    f"Background subagent '{agent_name}' completed."
                    if success
                    else f"Background subagent '{agent_name}' failed: {error_msg[:120]}"
                ),
            )
        except Exception:
            pass


# _build_dynamic_config / _default_system_prompt moved to
# core/agents/dynamic.py (shared with parallel_delegate_tool.py and
# coordinate_tool.py, A-05 fix) — see module-level import above.


# ---------------------------------------------------------------------------
# Helper: emit a subagent chunk to the shadow buffer (no-op if shadow is None)
# ---------------------------------------------------------------------------

async def _emit_subagent_chunk(shadow, payload: dict) -> None:
    """Append a subagent SSE chunk to the parent's shadow buffer."""
    if shadow is None:
        return
    try:
        await shadow.append(payload)
    except Exception as exc:
        logger.debug("Failed to emit subagent chunk: %s", exc)
