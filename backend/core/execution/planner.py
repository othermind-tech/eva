# core/execution/planner.py
#
# PlannerEngine — generates a structured execution plan from a user message.
#
# Called at the start of every plan_execute execution.  The plan is stored
# in the DB (execution_plans table) and streamed to the user as formatted
# text so they can see what EVA intends to do before execution begins.
#
# Prompt templates live in prompts/planner.py — edit those to tune behaviour
# without touching this engine file.

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default fallback plan returned when the LLM call or JSON parse fails.
_FALLBACK_PLAN = {
    "goal": "Complete the requested task",
    "success_criteria": ["Complete the requested task"],
    "steps": [
        {
            "index": 0,
            "title": "Execute task",
            "description": "Complete the user's request using available tools",
            "tool_hint": None,
        }
    ],
}


def _make_default_step(index: int, title: str, description: str, tool_hint=None) -> Dict[str, Any]:
    """Return a fully-initialised step dict with all runtime fields."""
    return {
        "index": index,
        "title": title,
        "description": description,
        "tool_hint": tool_hint,
        "status": "pending",
        "result_summary": None,
        "verified": False,
        "verification_notes": None,
    }


# A-20: cap matches the planner prompt's own "1-4" instruction -- a runaway
# or malformed list from either the LLM or a user edit is truncated, never
# rejected outright (this constant is shared by both the permissive
# LLM-output path and the strict user-edit path below).
_MAX_SUCCESS_CRITERIA = 4


def _normalise_success_criteria(raw: Any, goal: str) -> List[str]:
    """
    Filter to non-empty strings, cap at _MAX_SUCCESS_CRITERIA. Falls back to
    [goal] when the input is missing, not a list, or has nothing usable in
    it -- a plan is never left with zero criteria for the closing verifier to
    check against; treating the goal itself as the sole criterion degrades
    to a plain holistic check, never a crash or a silently-skipped check.
    """
    criteria = raw if isinstance(raw, list) else []
    cleaned = [c.strip() for c in criteria if isinstance(c, str) and c.strip()]
    return cleaned[:_MAX_SUCCESS_CRITERIA] if cleaned else [goal]


def _normalise_plan(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add runtime fields (status, result_summary, verified, verification_notes)
    to each step dict returned by the LLM.  The LLM only returns the static
    fields; we add the mutable ones here so the rest of the code can
    always assume a complete step dict.
    """
    steps = raw.get("steps", [])
    normalised_steps = []
    for i, step in enumerate(steps):
        normalised_steps.append(
            _make_default_step(
                index=step.get("index", i),
                title=step.get("title", f"Step {i + 1}"),
                description=step.get("description", ""),
                tool_hint=step.get("tool_hint"),
            )
        )
    goal = raw.get("goal", _FALLBACK_PLAN["goal"])
    return {
        "goal": goal,
        "success_criteria": _normalise_success_criteria(raw.get("success_criteria"), goal),
        "steps": normalised_steps,
    }


def _ensure_executable_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    A-22: a plan with no steps cannot be executed.

    _normalise_plan() is deliberately permissive (a flaky LLM must never block the
    PEV loop), which also means a model that answers {"goal": "...", "steps": []}
    produced a plan with nothing to run -- observed live with gpt-oss:20b on a request
    about an MCP tool the planner did not know how to schedule. The PEV runner then
    failed with an internal UnboundLocalError ('step_output': it is only assigned once a
    step has run) and the user saw a failed run with no explanation.

    Same recovery the planner already uses for every other unusable-plan case (no
    provider, unparseable JSON, LLM error): the single catch-all "Execute task" step,
    which hands the whole request to the executing agent with all its tools. Unlike
    those cases the model did return a usable goal and success criteria, so those are
    kept -- only the missing steps are substituted.
    """
    if plan.get("steps"):
        return plan
    logger.warning(
        "[PlannerEngine] Planner returned a plan with no steps -- substituting the "
        "single catch-all step (goal and success criteria kept)",
        extra={"goal": plan.get("goal")},
    )
    plan["steps"] = _normalise_plan(_FALLBACK_PLAN)["steps"]
    return plan


class PlanValidationError(ValueError):
    """
    Raised when a user-submitted (edited) plan does not match the shape
    PlannerEngine.generate() produces.

    Used by the UX-01 "edit plan before approving" flow: the API layer and
    the PEV runner's plan-approval gate both call validate_plan_shape() on
    any client-submitted plan and surface this as a clear error instead of
    letting a malformed dict crash the PEV loop downstream.
    """


def validate_plan_shape(raw: Any) -> Dict[str, Any]:
    """
    Validate a user-submitted edited plan and return a normalised plan dict
    (same shape as PlannerEngine.generate()'s return value — see its
    docstring for the exact fields).

    Unlike _normalise_plan() above (used for raw LLM output, which silently
    fills in missing/malformed fields with defaults so a flaky LLM never
    blocks the PEV loop), this is intentionally strict: a malformed
    *user-edited* plan is a client bug and should be rejected with a clear
    PlanValidationError (surfaced by the API as a 422) rather than silently
    coerced or allowed to crash execution later.

    Required shape:
        {
            "goal": str,                 # non-empty
            "success_criteria": [str],    # optional -- see the note below the
                                           # steps loop; falls back to [goal]
            "steps": [
                {
                    "index": int,        # optional per-step, defaults to position
                    "title": str,        # non-empty
                    "description": str,
                    "tool_hint": str | None,   # optional
                },
                ...
            ],                            # non-empty
        }
    """
    if not isinstance(raw, dict):
        raise PlanValidationError("Plan must be a JSON object with 'goal' and 'steps'.")

    goal = raw.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise PlanValidationError("Plan must include a non-empty 'goal' string.")

    steps = raw.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        raise PlanValidationError("Plan must include a non-empty 'steps' array.")

    normalised_steps: List[Dict[str, Any]] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise PlanValidationError(f"Step {i} must be an object.")

        title = step.get("title")
        if not isinstance(title, str) or not title.strip():
            raise PlanValidationError(f"Step {i} must include a non-empty 'title' string.")

        description = step.get("description", "")
        if not isinstance(description, str):
            raise PlanValidationError(f"Step {i} 'description' must be a string.")

        index = step.get("index", i)
        if not isinstance(index, int) or isinstance(index, bool):
            raise PlanValidationError(f"Step {i} 'index' must be an integer.")

        tool_hint = step.get("tool_hint")
        if tool_hint is not None and not isinstance(tool_hint, str):
            raise PlanValidationError(f"Step {i} 'tool_hint' must be a string or null.")

        normalised_steps.append(
            _make_default_step(
                index=index,
                title=title,
                description=description,
                tool_hint=tool_hint,
            )
        )

    # success_criteria (A-20) is deliberately NOT validated strictly here,
    # unlike every other field above: it's internal-only (the plan-approval
    # UI shows goal + steps, never this), so a user editing a plan has no
    # way to know it exists or supply it -- requiring it would break the
    # existing edit-plan flow for every caller that predates this field.
    # Same permissive fallback as the LLM-output path (_normalise_plan).
    success_criteria = _normalise_success_criteria(raw.get("success_criteria"), goal)

    return {"goal": goal, "success_criteria": success_criteria, "steps": normalised_steps}


def _keep_remaining_steps(
    new_steps: List[Dict[str, Any]],
    remaining_steps: List[Dict[str, Any]],
    first_index: int,
) -> List[Dict[str, Any]]:
    """
    Backstop for a replan that drops the rest of the goal.

    The runner replaces the failed step AND every step after it with whatever replan() returns,
    and a plan ends when its list ends. Live 2026-09-21 (gpt-oss:120b): a 5-step plan, step 1
    failed, the re-plan returned ONE step (a retry of step 1), that step passed, and the run
    finished as "completed" with the browser, fix and clean-up steps never attempted. A replan that
    returns a single step while original steps remain is that "retry only" shape (the prompt now
    asks for the whole remainder, but a model can still ignore it, and the no-provider / unparseable
    fallbacks are single-step by design), so the original remaining steps are put back after it.
    Redundant steps are cheap -- the later steps see what the earlier ones did and can say "already
    done" -- silently dropped work is not.
    """
    if not remaining_steps or len(new_steps) > 1:
        return new_steps
    logger.warning(
        "[PlannerEngine] Re-plan dropped the %d remaining step(s) -- restoring them after the retry step",
        len(remaining_steps),
    )
    restored = list(new_steps)
    for offset, step in enumerate(remaining_steps):
        restored.append(
            _make_default_step(
                index=first_index + len(new_steps) + offset,
                title=step.get("title", f"Step {first_index + len(new_steps) + offset + 1}"),
                description=step.get("description", ""),
                tool_hint=step.get("tool_hint"),
            )
        )
    return restored


class PlannerEngine:
    """
    Generates a structured JSON execution plan for a given user task.

    Uses the user's own configured provider/model, with fallback_chain
    support (core/providers/fallback_call.py, V6.5 Phase 3), routed
    through EVA's standard provider-handler system underneath. Falls back
    to a single-step plan on any error so the PEV loop is never blocked by
    a planning failure.
    """

    async def generate(
        self,
        *,
        user_message: str,
        user_id: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """
        Call the LLM with the planning prompt and return a normalised plan dict.

        The returned dict has the shape:
        {
            "goal": str,
            "steps": [
                {
                    "index": int,
                    "title": str,
                    "description": str,
                    "tool_hint": str | None,
                    "status": "pending",
                    "result_summary": None,
                    "verified": False,
                    "verification_notes": None,
                },
                ...
            ]
        }

        On error, returns a single-step fallback plan so execution always
        has something to work with.
        """
        try:
            from config.settings import get_user_session
            from core.providers.fallback_call import call_llm_with_fallback
            from prompts.planner import PLANNING_SYSTEM_PROMPT, PLANNING_USER_TEMPLATE

            session  = get_user_session(user_id)
            provider = session.get("provider")
            model    = session.get("model")

            # No hardcoded provider/model fallback (V6.4): EVA has no default
            # model of its own -- a genuinely unconfigured session is the
            # failure condition, not something to guess a value for.
            if not provider or not model:
                logger.warning(
                    "[PlannerEngine] No provider/model configured for user — "
                    "using fallback plan",
                    extra={"user_id": user_id},
                )
                return _normalise_plan(_FALLBACK_PLAN)

            user_content = PLANNING_USER_TEMPLATE.format(user_message=user_message)

            logger.info(
                "[PlannerEngine] Calling LLM for plan generation",
                extra={"user_id": user_id, "provider": provider, "model": model},
            )

            # Routed through EVA's standard provider-handler system, with
            # session.fallback_chain support (V6.5 Phase 3) -- see
            # core/providers/fallback_call.py.
            raw_text = await call_llm_with_fallback(
                session=session,
                system_prompt=PLANNING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=max_tokens,
                temperature=temperature,
                user_id=user_id,
                caller_tag="PlannerEngine.generate",
            )

            # Strip markdown fences if the model added them despite instructions
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                raw_text = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                )

            raw_plan = json.loads(raw_text)
            plan = _ensure_executable_plan(_normalise_plan(raw_plan))

            logger.info(
                "[PlannerEngine] Plan generated",
                extra={
                    "user_id":    user_id,
                    "goal":       plan["goal"],
                    "step_count": len(plan["steps"]),
                },
            )
            return plan

        except json.JSONDecodeError as exc:
            logger.warning(
                "[PlannerEngine] JSON parse failed — using fallback plan",
                exc_info=exc,
                extra={"user_id": user_id},
            )
        except Exception as exc:
            logger.warning(
                "[PlannerEngine] Plan generation failed — using fallback plan",
                exc_info=exc,
                extra={"user_id": user_id},
            )

        # Return a normalised fallback so the PEV runner always has a plan
        return _normalise_plan(_FALLBACK_PLAN)

    async def replan(
        self,
        *,
        goal: str,
        completed_steps: List[Dict[str, Any]],
        failed_step: Dict[str, Any],
        remaining_steps: List[Dict[str, Any]],
        user_id: str,
        max_tokens: int = 2000,
        temperature: float = 0.2,
    ) -> List[Dict[str, Any]]:
        """
        Generate a revised set of remaining steps after a step failure.

        Returns a list of normalised step dicts that should replace the failed
        step and all steps after it.  The returned steps have correct sequential
        indices starting at ``failed_step["index"]``.

        On any error, returns a single-step fallback so execution always continues.

        max_tokens: 2000 (A-18, 2026-09-22; was 900, which was itself raised
        from 600) -- 900 was STILL confirmed insufficient: live-reproduced
        against gpt-oss:120b (`Re-plan JSON parse failed`,
        `json.decoder.JSONDecodeError: Unterminated string...`, the exact
        symptom from production logs) on the first live trial at that
        budget. Root cause is the same "reasoning-mandatory" behaviour noted
        on VerifierEngine.verify()'s max_tokens: gpt-oss always spends an
        unpredictable, uncontrollable number of tokens on chain-of-thought
        first (confirmed elsewhere in this codebase -- see
        OllamaCloudHandler's docstring -- that no request-level parameter
        suppresses it, not even reasoning_effort="none"), and max_tokens caps
        the *total* completion (reasoning + JSON output combined), so a
        tight budget can exhaust itself before any JSON is ever emitted.
        2000 was chosen empirically, not guessed: 1200/1500/1800 each
        produced a real multi-step re-plan in 4/4 live trials against
        gpt-oss:120b (no fallback), so 2000 keeps real margin above the
        highest of those confirmed-reliable values rather than sitting
        right at the edge of what was actually tested.

        A capability-registry-gated bump (raise the budget only for models
        with supports_reasoning=True, considered instead of a flat raise)
        was rejected: LiteLLM's merged data -- the registry's only source
        for this field -- doesn't reliably carry supports_reasoning even for
        already-integrated providers (confirmed missing for openai's own o1
        and anthropic's claude-opus-4-1, not just this model), so gating on
        it would silently fail to raise the budget for the exact model that
        motivated this fix. A flat raise has no such blind spot.
        """
        next_index = failed_step.get("index", len(completed_steps))

        def _fmt_steps(steps: List[Dict[str, Any]]) -> str:
            if not steps:
                return "(none)"
            lines = []
            for s in steps:
                lines.append(
                    f"  Step {s.get('index', '?') + 1}: {s.get('title', '')} — {s.get('description', '')}"
                )
            return "\n".join(lines)

        try:
            from config.settings import get_user_session
            from core.providers.fallback_call import call_llm_with_fallback
            from prompts.planner import REPLAN_SYSTEM_PROMPT, REPLAN_USER_TEMPLATE

            session  = get_user_session(user_id)
            provider = session.get("provider")
            model    = session.get("model")

            # No hardcoded provider/model fallback (V6.4) -- see generate() above.
            if not provider or not model:
                logger.warning(
                    "[PlannerEngine] No provider/model configured for user — "
                    "using single-step fallback",
                    extra={"user_id": user_id},
                )
                return _keep_remaining_steps([
                    _make_default_step(
                        index=next_index,
                        title="Retry failed step",
                        description=(
                            f"Retry the previous step that failed: "
                            f"{failed_step.get('title', 'unknown step')}. "
                            f"Try a different approach if the same method failed."
                        ),
                        tool_hint=failed_step.get("tool_hint"),
                    )
                ], remaining_steps, next_index)

            user_content = REPLAN_USER_TEMPLATE.format(
                goal=goal,
                completed_steps_text=_fmt_steps(completed_steps),
                failed_step_index=failed_step.get("index", "?"),
                failed_step_title=failed_step.get("title", ""),
                failed_step_description=failed_step.get("description", ""),
                failed_step_result=failed_step.get("result_summary") or "(no output)",
                verification_issue=failed_step.get("verification_notes") or "Did not meet success criteria",
                remaining_steps_text=_fmt_steps(remaining_steps),
                next_index=next_index,
            )

            logger.info(
                "[PlannerEngine] Calling LLM for adaptive re-planning",
                extra={
                    "user_id": user_id, "provider": provider, "model": model,
                    "next_index": next_index,
                },
            )

            # Routed through EVA's standard provider-handler system, with
            # session.fallback_chain support (V6.5 Phase 3) -- see
            # core/providers/fallback_call.py.
            raw_text = await call_llm_with_fallback(
                session=session,
                system_prompt=REPLAN_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=max_tokens,
                temperature=temperature,
                user_id=user_id,
                caller_tag="PlannerEngine.replan",
            )

            if raw_text.startswith("```"):
                lines_list = raw_text.splitlines()
                raw_text = "\n".join(
                    line for line in lines_list
                    if not line.strip().startswith("```")
                )

            raw_steps = json.loads(raw_text)
            if not isinstance(raw_steps, list):
                raise ValueError(f"Expected JSON array, got {type(raw_steps)}")

            # Normalise each step; force sequential indices starting at next_index
            new_steps = []
            for offset, raw_step in enumerate(raw_steps):
                new_steps.append(
                    _make_default_step(
                        index=next_index + offset,
                        title=raw_step.get("title", f"Step {next_index + offset + 1}"),
                        description=raw_step.get("description", ""),
                        tool_hint=raw_step.get("tool_hint"),
                    )
                )

            logger.info(
                "[PlannerEngine] Adaptive re-plan generated",
                extra={
                    "user_id":          user_id,
                    "new_step_count":   len(new_steps),
                    "starting_index":   next_index,
                },
            )
            return _keep_remaining_steps(new_steps, remaining_steps, next_index)

        except json.JSONDecodeError as exc:
            logger.warning(
                "[PlannerEngine] Re-plan JSON parse failed — using single-step fallback",
                exc_info=exc,
                extra={"user_id": user_id},
            )
        except Exception as exc:
            logger.warning(
                "[PlannerEngine] Re-plan generation failed — using single-step fallback",
                exc_info=exc,
                extra={"user_id": user_id},
            )

        # Fallback: a single "retry" step so execution never gets stuck -- followed by the original
        # remaining steps, which are still part of the goal.
        return _keep_remaining_steps([
            _make_default_step(
                index=next_index,
                title="Retry failed step",
                description=(
                    f"Retry the previous step that failed: {failed_step.get('title', 'unknown step')}. "
                    f"Try a different approach if the same method failed."
                ),
                tool_hint=failed_step.get("tool_hint"),
            )
        ], remaining_steps, next_index)

    async def revise(
        self,
        *,
        goal: str,
        rejected_steps: List[Dict[str, Any]],
        feedback: str,
        user_id: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """
        Generate a revised, complete plan after the user rejected the
        previous one with feedback (UX-01 Phase 2 — multi-round plan
        revision), used at the pre-execution approval gate.

        Unlike replan() (which patches only the remaining steps after a
        *verification* failure mid-execution), nothing has executed yet
        here — this regenerates the whole plan from scratch, informed by
        the user's own rejection feedback. Same return shape as generate().

        On any error, returns a single-step fallback plan so the approval
        gate is never blocked by a revision failure.
        """
        def _fmt_steps(steps: List[Dict[str, Any]]) -> str:
            if not steps:
                return "(none)"
            lines = []
            for s in steps:
                lines.append(
                    f"  Step {s.get('index', '?') + 1}: {s.get('title', '')} — {s.get('description', '')}"
                )
            return "\n".join(lines)

        try:
            from config.settings import get_user_session
            from core.providers.fallback_call import call_llm_with_fallback
            from prompts.planner import PLAN_REVISION_SYSTEM_PROMPT, PLAN_REVISION_USER_TEMPLATE

            session  = get_user_session(user_id)
            provider = session.get("provider")
            model    = session.get("model")

            # No hardcoded provider/model fallback (V6.4) -- see generate() above.
            if not provider or not model:
                logger.warning(
                    "[PlannerEngine] No provider/model configured for user — "
                    "using fallback plan for revision",
                    extra={"user_id": user_id},
                )
                return _normalise_plan(_FALLBACK_PLAN)

            user_content = PLAN_REVISION_USER_TEMPLATE.format(
                goal=goal,
                rejected_steps_text=_fmt_steps(rejected_steps),
                feedback=feedback,
            )

            logger.info(
                "[PlannerEngine] Calling LLM for plan revision",
                extra={"user_id": user_id, "provider": provider, "model": model},
            )

            # Routed through EVA's standard provider-handler system, with
            # session.fallback_chain support (V6.5 Phase 3) -- see
            # core/providers/fallback_call.py.
            raw_text = await call_llm_with_fallback(
                session=session,
                system_prompt=PLAN_REVISION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=max_tokens,
                temperature=temperature,
                user_id=user_id,
                caller_tag="PlannerEngine.revise",
            )

            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                raw_text = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                )

            raw_plan = json.loads(raw_text)
            plan = _ensure_executable_plan(_normalise_plan(raw_plan))

            logger.info(
                "[PlannerEngine] Plan revised",
                extra={
                    "user_id":    user_id,
                    "goal":       plan["goal"],
                    "step_count": len(plan["steps"]),
                },
            )
            return plan

        except json.JSONDecodeError as exc:
            logger.warning(
                "[PlannerEngine] Revision JSON parse failed — using fallback plan",
                exc_info=exc,
                extra={"user_id": user_id},
            )
        except Exception as exc:
            logger.warning(
                "[PlannerEngine] Plan revision failed — using fallback plan",
                exc_info=exc,
                extra={"user_id": user_id},
            )

        return _normalise_plan(_FALLBACK_PLAN)


# Module-level singleton
planner_engine = PlannerEngine()
