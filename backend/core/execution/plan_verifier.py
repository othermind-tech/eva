# core/execution/plan_verifier.py
#
# PlanVerifierEngine (A-20) — judges whether a FINISHED plan, taken as a
# whole, achieved its original goal.
#
# Distinct from VerifierEngine (core/execution/verifier.py): that one runs
# after EVERY step, judging one step against its own narrow description.
# This one runs exactly ONCE, after the entire plan has finished (every step
# already individually passed, or was fixed by a successful replan) --
# nothing in the per-step verifier ever sees more than one step's own
# evidence, so it structurally cannot catch a plan that replanned its way
# into a different (but individually verifiable) sequence than what the
# user actually asked for.
#
# Deliberately NOT a tool-using sub-agent: its job is to judge evidence
# that's already been gathered (core/execution/step_record.py's trail), not
# to go investigate anything itself -- a single tightly-scoped LLM call,
# same shape as VerifierEngine.verify(), not an open-ended agentic loop.
#
# Fallback policy (mirrors verifier.py's V4.41/A-08 policy, extended to a
# tri-state world):
#   On ANY failure (LLM error, JSON parse error, timeout), the fallback
#   verdict is "pending" -- NOT "success" (would silently claim an unproven
#   result) and NOT "not_successful" (would falsely claim the plan is known
#   to be broken when we simply don't know). "pending" is the state that
#   means exactly that: this could not be determined, a human should look.
#   infra_error=True lets the caller tell "we don't know" apart from a real
#   "pending" verdict the model itself returned.

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.execution.verifier import _extract_json

logger = logging.getLogger(__name__)

VALID_VERDICTS = ("success", "pending", "not_successful")


@dataclass
class PlanVerificationResult:
    """
    Result of judging one finished plan's outcome against its goal.

    verdict          : "success" | "pending" | "not_successful"
    criteria_results : one {"criterion": str, "met": true|false|None} per
                        input criterion, in the same order.
    message          : user-facing explanation. Required (non-empty) when
                        verdict != "success"; "" on "success" (the plan's own
                        closing summary already covers that -- see A-19).
    infra_error      : True only when verdict="pending" because the check
                        itself could not run (see module docstring) -- as
                        opposed to the model genuinely returning "pending".
    """
    verdict: str
    criteria_results: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    infra_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "criteria_results": self.criteria_results,
            "message": self.message,
            "infra_error": self.infra_error,
        }


_FALLBACK_RESULT = PlanVerificationResult(
    verdict="pending",
    criteria_results=[],
    message=(
        "I couldn't automatically confirm whether this fully met your request "
        "-- please check the result yourself."
    ),
    infra_error=True,
)


class PlanVerifierEngine:
    """
    Judges a finished plan's outcome as a whole. One call per plan run, made
    once after every step has finished -- see core/execution/pev_runner.py's
    end-of-loop handling.
    """

    async def verify_plan(
        self,
        *,
        goal: str,
        success_criteria: List[str],
        step_record_trail: str,
        draft_closing_summary: str,
        user_id: str,
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> PlanVerificationResult:
        """
        Parameters
        ----------
        goal                  : the plan's own goal (may be a sub-task of a
                                 larger request -- judged only against this,
                                 per rule 7 in prompts/plan_verifier.py).
        success_criteria      : 1-4 checkable statements from the plan
                                 (core/execution/planner.py's
                                 _normalise_success_criteria() guarantees at
                                 least one).
        step_record_trail     : the FULL step record, every step including
                                 the last (core/execution/step_record.py's
                                 build_prior_steps_block(), called with no
                                 max_chars cap for this one call -- unlike a
                                 mid-plan step's own budget-capped prompt,
                                 there is no next step's context window to
                                 protect here).
        draft_closing_summary : the last step's own closing message (A-19) --
                                 given as context, explicitly framed to the
                                 model as a claim to verify, not evidence.
        max_tokens             : 800 -- empirically confirmed (2026-09-22),
                                 the same way A-18's replan() budget was:
                                 live against real gpt-oss:120b across all
                                 three verdicts (success, not_successful,
                                 pending), each producing a real, well-formed
                                 JSON response with no truncation.

        Returns
        -------
        PlanVerificationResult
        """
        try:
            from config.settings import get_user_session
            from core.providers.fallback_call import call_llm_with_fallback
            from prompts.plan_verifier import (
                PLAN_VERIFIER_SYSTEM_PROMPT,
                PLAN_VERIFIER_USER_TEMPLATE,
            )

            session  = get_user_session(user_id)
            provider = session.get("provider")
            model    = session.get("model")

            if not provider or not model:
                logger.warning(
                    "[PlanVerifierEngine] No provider/model configured for user — "
                    "skipping plan verification",
                    extra={"user_id": user_id},
                )
                return _FALLBACK_RESULT

            criteria_list = "\n".join(f"- {c}" for c in success_criteria) or "(none stated)"

            user_content = PLAN_VERIFIER_USER_TEMPLATE.format(
                goal=goal or "(no goal recorded)",
                criteria_list=criteria_list,
                step_record_trail=step_record_trail or "(no step record)",
                draft_closing_summary=draft_closing_summary or "(no closing summary produced)",
            )

            logger.info(
                "[PlanVerifierEngine] Verifying plan outcome",
                extra={"user_id": user_id, "provider": provider, "model": model},
            )

            raw_text = await call_llm_with_fallback(
                session=session,
                system_prompt=PLAN_VERIFIER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=max_tokens,
                temperature=temperature,
                user_id=user_id,
                caller_tag="PlanVerifierEngine",
            )

            raw = _extract_json(raw_text)

            verdict = raw.get("verdict")
            if verdict not in VALID_VERDICTS:
                raise ValueError(f"Model returned an invalid verdict: {verdict!r}")

            criteria_results = raw.get("criteria_results")
            if not isinstance(criteria_results, list):
                criteria_results = []

            message = raw.get("message") or ""
            # Rule 6: a non-"success" verdict must carry a real user-facing
            # message. A model that follows the letter but not the spirit
            # (returns verdict without message) still gets a safe, honest
            # default rather than an empty string reaching the user.
            if verdict != "success" and not message.strip():
                message = (
                    "This didn't fully meet the stated goal, but I don't have "
                    "further detail on what's missing -- please check the "
                    "result yourself."
                )

            result = PlanVerificationResult(
                verdict=verdict,
                criteria_results=criteria_results,
                message=message if verdict != "success" else "",
            )

            logger.info(
                "[PlanVerifierEngine] Plan verification complete",
                extra={"user_id": user_id, "verdict": result.verdict},
            )
            return result

        except json.JSONDecodeError as exc:
            logger.warning(
                "[PlanVerifierEngine] JSON parse failed — treating as pending",
                exc_info=exc,
                extra={"user_id": user_id},
            )
        except Exception as exc:
            logger.warning(
                "[PlanVerifierEngine] Plan verification failed — treating as pending",
                exc_info=exc,
                extra={"user_id": user_id},
            )

        return _FALLBACK_RESULT


# Module-level singleton
plan_verifier_engine = PlanVerifierEngine()
