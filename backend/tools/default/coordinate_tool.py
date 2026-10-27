# tools/default/coordinate_tool.py
#
# G-006: coordinate tool — Coordinator Agent
#
# Meta-orchestration tool that plans and drives a multi-agent workflow.
#
# Execution flow:
#   1. Resolve available subagents (all, or filtered by caller's subagents list)
#   2. Build an execution plan:
#        "adaptive"   — coordinator LLM decides phases, ordering, and per-agent tasks
#        "parallel"   — fast path: all agents run concurrently on the goal
#        "sequential" — fast path: agents run one-by-one, each gets prior output as context
#   3. Execute each phase (parallel or sequential within phase)
#        Reuses _run_one + _ExecutionView from parallel_delegate_tool — no duplication.
#   4. Synthesize all results with a final LLM call (graceful fallback to raw results)
#   5. Return synthesized answer + phase summary
#
# Design notes:
#   - The coordinator LLM call (planning) is non-streaming and non-blocking from the
#     orchestrator's perspective — it happens inside the tool's run() coroutine.
#   - If the planning LLM call fails (e.g. no API key), a sensible fallback plan is used.
#   - If the synthesis LLM call fails, raw phase results are returned instead.
#   - _run_one from parallel_delegate_tool handles SSE emission, blackboard writes,
#     and event_bus publishing for each child agent — no duplication needed here.
#   - Each child gets its own _ExecutionView so caller_agent_name is scoped correctly
#     for L2 blackboard ownership (G-004).
#
# Authority: READ_ONLY (same as delegate_to_agent / parallel_delegate)

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from core.tools.base import ToolBase, ToolAuthority, ToolResult
from core.tools.registry import default_tool
from prompts.coordinator import (
    build_coordinator_plan_system,
    COORDINATOR_PLAN_USER_TEMPLATE,
    COORDINATOR_SYNTHESIS_SYSTEM,
    COORDINATOR_SYNTHESIS_USER_TEMPLATE,
)
from config.settings import (
    MAX_COORDINATE_PHASES,
    MAX_COORDINATE_TASKS_PER_PHASE,
    MAX_COORDINATE_DYNAMIC_AGENTS,
)
from tools.default.parallel_delegate_tool import ParallelTask, _ExecutionView, _run_one
from core.agents.dynamic import build_dynamic_config

logger = logging.getLogger(__name__)

_MAX_PHASES = MAX_COORDINATE_PHASES
_MAX_TASKS_PER_PHASE = MAX_COORDINATE_TASKS_PER_PHASE
_MAX_DYNAMIC_AGENTS = MAX_COORDINATE_DYNAMIC_AGENTS


# ---------------------------------------------------------------------------
# Argument schema
# ---------------------------------------------------------------------------

class CoordinateArgs(BaseModel):
    goal: str = Field(
        description=(
            "The high-level goal or question to accomplish. "
            "The coordinator will analyse this, decide which subagents to use "
            "and in what order, then synthesize all their outputs into one answer."
        )
    )
    subagents: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of specific subagent names to use. "
            "Names must match saved subagents exactly. If omitted, all configured "
            "subagents are considered (recommended unless you know the exact names)."
        ),
    )
    strategy: Literal["adaptive", "parallel", "sequential"] = Field(
        default="adaptive",
        description=(
            "'adaptive' — coordinator LLM plans phases and ordering (default, recommended); "
            "'parallel' — all agents run simultaneously, each gets the full goal as their task; "
            "'sequential' — agents run one-by-one, each receives the previous agent's output "
            "as context (good for pipeline workflows: gather → analyse → write)."
        ),
    )


# ---------------------------------------------------------------------------
# Internal plan schema (coordinator LLM output)
# ---------------------------------------------------------------------------

class _PlanTask(BaseModel):
    agent_name: str
    task: str
    context: Optional[str] = None
    # A-05: dynamic-persona fields, only consulted when agent_name is NOT a
    # registered subagent -- mirrors ParallelTask's identical fields
    # (parallel_delegate_tool.py) so the same underlying mechanism
    # (core.agents.dynamic.build_dynamic_config) can resolve either.
    system_prompt: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    max_steps: Optional[int] = None
    model: Optional[str] = None
    provider: Optional[str] = None


class _PlanPhase(BaseModel):
    phase: int
    execution: Literal["parallel", "sequential"]
    tasks: List[_PlanTask]


class _CoordinationPlan(BaseModel):
    goal_summary: str
    phases: List[_PlanPhase]
    synthesis_instruction: str


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

async def _call_llm_raw(
    system_prompt: str,
    user_message: str,
    session: Dict[str, Any],
    user_id: str,
    caller_tag: str,
) -> str:
    """
    Non-streaming LLM call used for planning and synthesis. Returns raw text.

    Reactive Loop tier (V6.8): a silent failure here derails the whole
    coordination task, not just one utility call -- retries the same
    candidate with backoff on Tier A errors, then walks the user's
    fallback_chain, recording/consulting the shared cross-execution
    cooldown store, before ever raising. Both callers already wrap this in
    their own try/except with a graceful fallback (a hand-built plan, or
    raw phase results), unchanged by this.
    """
    from core.tools.user_registry import UserToolRegistry
    from core.providers.fallback_call import call_with_full_fallback

    return await call_with_full_fallback(
        session=session,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        user_id=user_id,
        tools=UserToolRegistry(default_tools=False, enabled_tools=[]),
        caller_tag=caller_tag,
    )


def _parse_plan(raw: str) -> Optional[_CoordinationPlan]:
    """Parse coordinator LLM JSON output into a _CoordinationPlan. Returns None on failure."""
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    # Find the first {...} JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return _CoordinationPlan(**data)
    except Exception as exc:
        logger.debug("coordinate: plan parse error: %s | raw=%s", exc, raw[:300])
        return None


def _format_agent_list(configs: Dict[str, Any]) -> str:
    """Format subagent configs into a readable bullet list for the planning prompt."""
    lines = []
    for name, cfg in configs.items():
        desc = getattr(cfg, "description", "") or ""
        if not desc:
            # Fall back to first 120 chars of system_prompt as description
            desc = (getattr(cfg, "system_prompt", "") or "")[:120]
        lines.append(f"- {name}: {desc or '(no description)'}")
    return "\n".join(lines) if lines else "(none configured)"


def _fallback_plan(
    goal: str,
    configs: Dict[str, Any],
    strategy: str,
) -> _CoordinationPlan:
    """
    Fallback plan used when the coordinator LLM call fails (e.g. no API key).
    All agents receive the full goal in a single phase (parallel by default).
    """
    agent_names = list(configs.keys())
    exec_mode: Literal["parallel", "sequential"] = (
        "sequential" if strategy == "sequential" else "parallel"
    )
    tasks = [_PlanTask(agent_name=n, task=goal) for n in agent_names]
    return _CoordinationPlan(
        goal_summary=goal,
        phases=[_PlanPhase(phase=1, execution=exec_mode, tasks=tasks)],
        synthesis_instruction="Combine all agent outputs into a coherent answer.",
    )


def _fast_plan(
    goal: str,
    configs: Dict[str, Any],
    strategy: Literal["parallel", "sequential"],
) -> _CoordinationPlan:
    """
    Fast-path plan (no LLM call) for 'parallel' or 'sequential' strategies.
    Each agent gets the full goal as its task.
    """
    agent_names = list(configs.keys())
    if strategy == "parallel":
        tasks = [_PlanTask(agent_name=n, task=goal) for n in agent_names]
        phases = [_PlanPhase(phase=1, execution="parallel", tasks=tasks)]
    else:  # sequential
        phases = [
            _PlanPhase(
                phase=i + 1,
                execution="sequential",
                tasks=[_PlanTask(agent_name=n, task=goal)],
            )
            for i, n in enumerate(agent_names)
        ]
    return _CoordinationPlan(
        goal_summary=goal,
        phases=phases,
        synthesis_instruction="Combine all agent outputs into a coherent final answer.",
    )


# ---------------------------------------------------------------------------
# Phase execution helpers
# ---------------------------------------------------------------------------

async def _execute_parallel_phase(
    phase: _PlanPhase,
    execution: Any,
    configs: Dict[str, Any],
    provider: str,
    model: str,
    api_key: str,
    prior_context: str,
    dynamic_flags: Dict[str, bool],
) -> List[dict]:
    """Run all tasks in a phase concurrently via asyncio.gather."""
    coroutines = []
    for pt in phase.tasks:
        # Inject prior phase outputs as context if none was explicitly set
        effective_context = pt.context
        if prior_context and not effective_context:
            effective_context = prior_context

        parallel_task = ParallelTask(
            agent_name=pt.agent_name,
            task=pt.task,
            context=effective_context or None,
        )
        view = _ExecutionView(execution, pt.agent_name)
        cfg = configs[pt.agent_name]
        coroutines.append(
            _run_one(
                parallel_task, view, cfg, provider, model, api_key, True,
                dynamic=dynamic_flags.get(pt.agent_name, False),
            )
        )

    return list(await asyncio.gather(*coroutines))


async def _execute_sequential_phase(
    phase: _PlanPhase,
    execution: Any,
    configs: Dict[str, Any],
    provider: str,
    model: str,
    api_key: str,
    prior_context: str,
    dynamic_flags: Dict[str, bool],
) -> List[dict]:
    """
    Run tasks in a phase one-by-one.
    Each agent's output is passed as context to the next (pipeline pattern).
    """
    results = []
    running_context = prior_context

    for pt in phase.tasks:
        effective_context = pt.context
        if running_context and not effective_context:
            effective_context = running_context
        elif running_context and effective_context:
            effective_context = f"{effective_context}\n\nPrior output:\n{running_context}"

        sequential_task = ParallelTask(
            agent_name=pt.agent_name,
            task=pt.task,
            context=effective_context or None,
        )
        view = _ExecutionView(execution, pt.agent_name)
        cfg = configs[pt.agent_name]
        result = await _run_one(
            sequential_task, view, cfg, provider, model, api_key, True,
            dynamic=dynamic_flags.get(pt.agent_name, False),
        )
        results.append(result)

        # Carry this agent's output forward as context for the next
        if result.get("status") == "completed":
            running_context = result.get("output", "")

    return results


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

@default_tool(
    name="coordinate",
    description=(
        "Meta-orchestration tool: plan and execute a multi-agent workflow to accomplish a complex goal. "
        "The coordinator analyses the goal, decides which subagents to use and in what order "
        "(parallel, sequential, or a mix of both), drives execution across phases, and synthesizes "
        "all results into a single coherent answer. "
        "Use this for tasks that benefit from multiple specialised agents working together — "
        "e.g. research + analysis + writing, or parallel fact-gathering across different domains. "
        "Strategies: 'adaptive' (coordinator LLM plans the workflow — default), "
        "'parallel' (all agents run simultaneously on the goal), "
        "'sequential' (agents chain outputs — each receives the prior agent's result as context)."
    ),
    args_schema=CoordinateArgs,
    authority=ToolAuthority.READ_ONLY,
    category="delegation",
    chat_excluded=True,
    summary="Coordinate a multi-agent workflow: plan phases, run subagents, synthesise results.",
)
class CoordinateTool(ToolBase):
    """
    Coordinator Agent (G-006): plans and drives multi-agent workflows.

    For 'adaptive' strategy, makes two LLM calls:
      - Planning call: coordinator decides phases and per-agent tasks
      - Synthesis call: coordinator combines all results into one answer

    Both calls gracefully degrade: planning falls back to a simple all-agents plan;
    synthesis falls back to returning the raw phase results.
    """

    async def run(self, args: Dict[str, Any]) -> ToolResult:
        # ------------------------------------------------------------------
        # 1. Parse args
        # ------------------------------------------------------------------
        parsed = CoordinateArgs(**args)
        goal = parsed.goal.strip()
        requested_agents: Optional[List[str]] = parsed.subagents
        strategy: str = parsed.strategy

        if not goal:
            return ToolResult(result={"error": "goal must not be empty."})

        # ------------------------------------------------------------------
        # 2. Resolve execution context (injected by dispatcher)
        # ------------------------------------------------------------------
        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={"error": "coordinate: no execution context."})

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
                "error": "coordinate: no provider/model configured for this session."
            })

        # ------------------------------------------------------------------
        # 4. Resolve subagent configs — fail-fast on unknown names
        # ------------------------------------------------------------------
        from core.agents.registry import subagent_registry

        if requested_agents:
            # Caller specified explicit names — validate all exist before starting
            configs: Dict[str, Any] = {}
            for name in requested_agents:
                cfg = await subagent_registry.get(user_id, name)
                if cfg is None:
                    return ToolResult(result={
                        "error": (
                            f"No subagent named '{name}' is configured for this user. "
                            "Omit `agents` to let the coordinator choose from all configured "
                            "subagents, or ask the user for the exact name."
                        )
                    })
                configs[name] = cfg
        else:
            # Use all configured subagents for this user
            all_cfgs = await subagent_registry.list(user_id)
            configs = {cfg.name: cfg for cfg in all_cfgs}

        if not configs:
            return ToolResult(result={
                "error": (
                    "No subagents are configured. "
                    "Create at least one subagent (create_subagent_config, or ask the user "
                    "to add one in the Agents panel) before using coordinate."
                )
            })

        # ------------------------------------------------------------------
        # 5. Build execution plan
        # ------------------------------------------------------------------
        plan: Optional[_CoordinationPlan] = None

        if strategy == "adaptive":
            # Ask the coordinator LLM to produce a structured plan
            agent_list_str = _format_agent_list(configs)
            user_msg = COORDINATOR_PLAN_USER_TEMPLATE.format(
                goal=goal,
                agent_list=agent_list_str,
            )
            try:
                raw_plan = await _call_llm_raw(
                    system_prompt=build_coordinator_plan_system(
                        _MAX_TASKS_PER_PHASE, _MAX_DYNAMIC_AGENTS
                    ),
                    user_message=user_msg,
                    session=session,
                    user_id=user_id,
                    caller_tag="coordinate.plan",
                )
                plan = _parse_plan(raw_plan)
                if plan is None:
                    logger.warning(
                        "coordinate: coordinator LLM returned unparseable plan — using fallback",
                        extra={"raw_snippet": raw_plan[:300]},
                    )
            except Exception as exc:
                logger.warning(
                    "coordinate: coordinator LLM planning call failed (%s) — using fallback plan",
                    exc,
                )

            if plan is None:
                plan = _fallback_plan(goal, configs, strategy)
        else:
            # Fast path: no LLM planning call
            plan = _fast_plan(goal, configs, strategy)  # type: ignore[arg-type]

        # ------------------------------------------------------------------
        # 6. Validate plan — resolve unknown agents (dynamic, A-05) or drop
        #    them, cap phases/tasks/distinct dynamic agents.
        # ------------------------------------------------------------------
        # dynamic_flags is threaded through to _run_one (SSE "dynamic" field,
        # same as delegate_to_agent/parallel_delegate); reused across phases
        # so a name invented in phase 1 and referenced again in phase 2 does
        # NOT count a second time against _MAX_DYNAMIC_AGENTS -- matches the
        # planning prompt's own "reuse an already-invented name" instruction.
        dynamic_flags: Dict[str, bool] = {}
        dynamic_agent_count = 0

        validated_phases: List[_PlanPhase] = []
        for ph in plan.phases[:_MAX_PHASES]:
            valid_tasks: List[_PlanTask] = []
            for t in ph.tasks:
                if t.agent_name in configs:
                    valid_tasks.append(t)
                    continue
                if t.agent_name in dynamic_flags:
                    # Already resolved as dynamic earlier in this plan --
                    # reuse the same synthesized config, no re-count.
                    valid_tasks.append(t)
                    continue
                if dynamic_agent_count >= _MAX_DYNAMIC_AGENTS:
                    logger.debug(
                        "coordinate: dropping task for '%s' -- MAX_COORDINATE_DYNAMIC_AGENTS "
                        "(%d) already reached", t.agent_name, _MAX_DYNAMIC_AGENTS,
                    )
                    continue
                configs[t.agent_name] = build_dynamic_config(t.agent_name, t.dict())
                dynamic_flags[t.agent_name] = True
                dynamic_agent_count += 1
                logger.info(
                    "coordinate: dynamic subagent spawned (no registered config)",
                    extra={
                        "agent_name": t.agent_name,
                        "max_steps": configs[t.agent_name].max_steps,
                        "tools": configs[t.agent_name].tools,
                    },
                )
                valid_tasks.append(t)

            if not valid_tasks:
                logger.debug(
                    "coordinate: phase %d has no valid tasks after filtering — skipping", ph.phase
                )
                continue
            validated_phases.append(
                _PlanPhase(
                    phase=ph.phase,
                    execution=ph.execution,
                    tasks=valid_tasks[:_MAX_TASKS_PER_PHASE],
                )
            )
        plan.phases = validated_phases

        if not plan.phases:
            return ToolResult(result={
                "error": (
                    "Coordinator produced an empty plan — no valid tasks to execute. "
                    f"Available agents: {list(configs.keys())}"
                ),
                "goal": goal,
            })

        logger.info(
            "coordinate: plan ready",
            extra={
                "strategy": strategy,
                "phases": len(plan.phases),
                "goal_summary": plan.goal_summary,
            },
        )

        # ------------------------------------------------------------------
        # 7. Execute phases sequentially; within each phase, run as directed
        # ------------------------------------------------------------------
        all_phase_results: List[Dict[str, Any]] = []
        prior_context = ""

        for ph in plan.phases:
            logger.info(
                "coordinate: executing phase %d (%s, %d tasks)",
                ph.phase, ph.execution, len(ph.tasks),
            )

            if ph.execution == "parallel":
                phase_results = await _execute_parallel_phase(
                    ph, execution, configs, provider, model, api_key, prior_context,
                    dynamic_flags,
                )
            else:
                phase_results = await _execute_sequential_phase(
                    ph, execution, configs, provider, model, api_key, prior_context,
                    dynamic_flags,
                )

            all_phase_results.append({
                "phase": ph.phase,
                "execution": ph.execution,
                "results": phase_results,
            })

            # Carry successful outputs from this phase into the next as context
            successful_outputs = [
                f"[{r['agent_name']}]: {r.get('output', '')}"
                for r in phase_results
                if r.get("status") == "completed"
            ]
            if successful_outputs:
                prior_context = "\n\n".join(successful_outputs)

        # ------------------------------------------------------------------
        # 8. Synthesize all results into a final answer
        # ------------------------------------------------------------------
        results_text_parts = []
        for phase_data in all_phase_results:
            results_text_parts.append(
                f"### Phase {phase_data['phase']} ({phase_data['execution']})"
            )
            for r in phase_data["results"]:
                if r.get("status") == "completed":
                    results_text_parts.append(
                        f"**{r['agent_name']}**: {r.get('output', '(no output)')}"
                    )
                else:
                    results_text_parts.append(
                        f"**{r['agent_name']}** (failed): {r.get('error', 'unknown error')}"
                    )
        results_combined = "\n\n".join(results_text_parts)

        synthesis = ""
        try:
            synthesis_user = COORDINATOR_SYNTHESIS_USER_TEMPLATE.format(
                goal=goal,
                results_text=results_combined,
                synthesis_instruction=plan.synthesis_instruction,
            )
            synthesis = await _call_llm_raw(
                system_prompt=COORDINATOR_SYNTHESIS_SYSTEM,
                user_message=synthesis_user,
                session=session,
                user_id=user_id,
                caller_tag="coordinate.synthesis",
            )
        except Exception as exc:
            logger.warning(
                "coordinate: synthesis LLM call failed (%s) — returning raw phase results", exc
            )
            synthesis = results_combined  # Graceful fallback

        # ------------------------------------------------------------------
        # 9. Aggregate stats and return
        # ------------------------------------------------------------------
        total_tasks = sum(len(ph["results"]) for ph in all_phase_results)
        completed   = sum(
            1 for ph in all_phase_results
            for r in ph["results"]
            if r.get("status") == "completed"
        )
        failed = total_tasks - completed

        logger.info(
            "coordinate: all phases complete",
            extra={
                "goal_summary": plan.goal_summary,
                "strategy": strategy,
                "phases_run": len(plan.phases),
                "total_tasks": total_tasks,
                "completed": completed,
                "failed": failed,
            },
        )

        return ToolResult(result={
            "goal_summary":  plan.goal_summary,
            "strategy":      strategy,
            "phases_run":    len(plan.phases),
            "total_tasks":   total_tasks,
            "completed":     completed,
            "failed":        failed,
            "synthesis":     synthesis,
            "phase_details": all_phase_results,
        })
