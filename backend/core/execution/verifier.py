# core/execution/verifier.py
#
# VerifierEngine — checks whether a completed plan step was successful.
#
# Called after each step in the PEV loop.  Takes the step definition and
# the agent's execution output, then returns a VerificationResult.
#
# Prompt templates live in prompts/verifier.py — edit those to tune behaviour
# without touching this engine file.
#
# Fallback policy (V4.41, revised A-08):
#   On ANY verifier failure (LLM error, JSON parse error, timeout, rate limit),
#   the fallback result is passed=False, infra_error=True — NOT passed=True.
#   Rationale: verification is a core PEV guarantee. If we cannot verify, the
#   correct response is to never silently treat the step as if it passed —
#   V4.41's original bug was defaulting to passed=True, which silently masked
#   the failure with no visibility at all.
#   A-08 revision: passed=False alone used to also mean "spend a replan
#   attempt on this step," but a verifier-infra outage isn't evidence the step
#   itself is bad, so burning a replan cycle on it was wasted work. The
#   infra_error flag lets the caller (pev_runner.py) keep V4.41's visibility
#   (the step is never silently treated as verified-good) while not paying
#   the replan cost for a failure that has nothing to do with the step --
#   it's surfaced as a warning and execution continues instead.

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """
    Result of verifying one plan step.

    passed             : True if the step was successfully completed
    summary            : 1-2 sentence description of what happened.
                          On the failure path this is LLM-authored (the model
                          is asked for it). On the success path the LLM is no
                          longer asked to generate one at all (see
                          prompts/verifier.py) -- it is synthesized
                          deterministically in VerifierEngine.verify() instead,
                          purely to keep this field populated for display
                          (core/execution/pev_runner.py) without spending
                          generation tokens on prose the caller doesn't need.
    issues             : None if passed; description of what went wrong otherwise
    infra_error        : True only when passed=False because verification itself
                          could not run (LLM call failed, timed out, or its
                          response was unparseable) -- as opposed to the verifier
                          running successfully and judging the step to have
                          failed. The caller (pev_runner.py) uses this to tell
                          "the step is genuinely bad" apart from "we don't know
                          whether the step is good," and treats the latter as a
                          non-blocking warning instead of spending a replan
                          attempt on something that isn't the step's fault.
    also_satisfies_count : A-21 -- how many of the steps immediately following
                          this one (passed as `remaining_steps`, in order) are
                          ALSO fully satisfied by the same evidence -- counted
                          contiguously from the first one; a gap never counts
                          (step 2 not satisfied means step 3 doesn't count even
                          if it looks done). Always 0 when passed=False,
                          infra_error=True, or remaining_steps was empty/not
                          given -- never guessed, only ever a real reported
                          count. The caller (pev_runner.py) additionally clamps
                          this against PEV_LOOKAHEAD_MAX_SKIP -- this field is
                          the verifier's own claim, not the final skip count.
    also_satisfies_reason : One short, evidence-grounded justification for
                          also_satisfies_count > 0 -- carried into the skipped
                          steps' StepRecords so later steps and the final
                          PlanVerifierEngine check still have real context.
                          None when also_satisfies_count is 0.
    """
    passed: bool
    summary: str
    issues: Optional[str] = None
    infra_error: bool = False
    also_satisfies_count: int = 0
    also_satisfies_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "issues": self.issues,
            "infra_error": self.infra_error,
            "also_satisfies_count": self.also_satisfies_count,
            "also_satisfies_reason": self.also_satisfies_reason,
        }


def _extract_json(text: str) -> dict:
    """
    Robustly extract a JSON object from a model response that may include:
    - Markdown code fences (```json ... ```)
    - Prose before or after the JSON object
    - Extra whitespace or newlines

    Strategy:
    1. Strip markdown fences.
    2. Try direct parse.
    3. Find the first '{' ... last '}' substring and parse that.
    Raises json.JSONDecodeError if no valid JSON object can be found.
    """
    # Step 1: strip markdown fences
    cleaned = re.sub(r"```[a-zA-Z]*\n?", "", text).replace("```", "").strip()

    # Step 2: try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Step 3: extract first {...} block
    start = cleaned.find("{")
    end   = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(cleaned[start : end + 1])

    raise json.JSONDecodeError("No JSON object found in response", cleaned, 0)


# Default result used when verification itself fails (e.g. LLM error, JSON parse error).
#
# IMPORTANT: passed=False — NOT True.
# If verification cannot be completed we must not assume the step succeeded.
# The PEV adaptive replanning path handles passed=False correctly (up to
# _MAX_REPLAN_ATTEMPTS times) so a verifier failure degrades to replanning,
# not to silently continuing as if verified.
_FALLBACK_RESULT = VerificationResult(
    passed=False,
    summary="Verification could not be completed — treating step as unverified.",
    issues="Verifier encountered an error (LLM call failed or response was unparseable). "
           "Step has been marked as failed to trigger adaptive replanning.",
    infra_error=True,
)


class VerifierEngine:
    """
    Lightweight LLM-based step verifier.

    Uses the user's own configured provider/model, with fallback_chain
    support (core/providers/fallback_call.py, V6.5 Phase 3), routed
    through EVA's standard provider-handler system underneath. On any
    error, defaults to passed=False so that a verifier failure never
    silently masks an unverified step -- see the module-level comment at
    the top of this file.
    """

    async def verify(
        self,
        *,
        step: Dict[str, Any],
        execution_output: str,
        user_id: str,
        tool_activity: str = "",
        prior_steps_block: str = "",
        remaining_steps: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 500,
        temperature: float = 0.0,
    ) -> VerificationResult:
        """
        Verify that *step* was completed based on *execution_output*.

        Parameters
        ----------
        step             : plan step dict with "title" and "description" keys
        execution_output : aggregated text output from the agent for this step
        user_id          : used to look up the user's provider/model session
        tool_activity    : A-13 -- one line per tool call the step ran (tool, what it acted on,
                            ok/ERROR), from core/execution/step_record.py. Without it the verifier
                            judges from the agent's narration alone and cannot tell "file edited"
                            from a claim that it was.
        prior_steps_block: A-16 -- the same "Work already done in earlier steps" block the step
                            itself was given (core/execution/step_record.py::build_prior_steps_block).
                            Without it the verifier only sees this step's own (possibly empty)
                            ledger, and cannot tell "nothing happened" apart from "an earlier step
                            already did this" -- both look identical from the current step alone.
                            "" for the first step (nothing precedes it) and callers that don't pass it.
        remaining_steps  : A-21 -- the not-yet-executed steps immediately after this one (title/
                            description only), never including the plan's own final step (see
                            pev_runner.py -- that step always runs for real, for A-19's closing
                            synthesis). None/empty when there's nothing left to check, or the
                            caller doesn't want the look-ahead check run at all (e.g. the user's
                            own pev_lookahead_skip_enabled preference is off). When given, the
                            result's also_satisfies_count/also_satisfies_reason may be non-zero.
        max_tokens       : 500 (was 200) -- a "reasoning-mandatory" model
                            (e.g. gpt-oss) can't be told to skip its chain of
                            thought, and that reasoning consumes this same
                            budget before the verdict is ever emitted.
                            Confirmed empirically: 200 truncates to an empty
                            or partial response on short/ambiguous inputs
                            against such a model; 500 left real margin in
                            the same test. For a model with no mandatory
                            reasoning this cap is never actually reached.

        Returns
        -------
        VerificationResult
        """
        try:
            from config.settings import get_user_session
            from core.providers.fallback_call import call_llm_with_fallback
            from prompts.verifier import VERIFIER_SYSTEM_PROMPT, VERIFIER_USER_TEMPLATE

            session  = get_user_session(user_id)
            provider = session.get("provider")
            model    = session.get("model")

            # No hardcoded provider/model fallback (V6.4): EVA has no default
            # model of its own to fall back to -- if the session genuinely
            # has no provider/model configured, that is itself the failure
            # condition, not something to paper over with a guess.
            if not provider or not model:
                logger.warning(
                    "[VerifierEngine] No provider/model configured for user — "
                    "skipping verification",
                    extra={"user_id": user_id, "step_index": step.get("index")},
                )
                return _FALLBACK_RESULT

            # Same spacing convention as core/execution/pev_runner.py's
            # _build_step_messages(): "" when there's nothing to add, so the
            # first step's prompt (and any caller that doesn't pass this)
            # doesn't grow an empty section header.
            prior_steps_section = f"{prior_steps_block}\n\n" if prior_steps_block else ""

            # A-21: same "" convention. Capped at PEV_LOOKAHEAD_STEPS_CHARS --
            # these are unexecuted steps with no tool evidence of their own,
            # just a planned title/description, so this list is naturally
            # small; the cap is a backstop against a pathological plan, not
            # something normally reached.
            remaining_steps_section = ""
            if remaining_steps:
                from config.settings import PEV_LOOKAHEAD_STEPS_CHARS
                lines = [
                    f"  {i + 1}. {s.get('title', '')} — {s.get('description', '')}"
                    for i, s in enumerate(remaining_steps)
                ]
                block = "## Steps planned after this one (not yet run)\n" + "\n".join(lines)
                if len(block) > PEV_LOOKAHEAD_STEPS_CHARS:
                    block = block[: PEV_LOOKAHEAD_STEPS_CHARS - 1].rstrip() + "…"
                remaining_steps_section = f"{block}\n\n"

            user_content = VERIFIER_USER_TEMPLATE.format(
                step_title=step.get("title", ""),
                step_description=step.get("description", ""),
                prior_steps_section=prior_steps_section,
                remaining_steps_section=remaining_steps_section,
                execution_output=execution_output or "(no output)",
                tool_activity=tool_activity or "(not recorded)",
            )

            logger.info(
                "[VerifierEngine] Verifying step",
                extra={
                    "user_id":    user_id,
                    "step_index": step.get("index"),
                    "step_title": step.get("title"),
                    "provider":   provider,
                    "model":      model,
                },
            )

            # Routed through EVA's standard provider-handler system, with
            # session.fallback_chain support (V6.5 Phase 3) -- see
            # core/providers/fallback_call.py. user_id (V6.8) makes this
            # cooldown-aware: skips a candidate already known down from a
            # recent failure (recorded here or by any other caller sharing
            # the same store, including the main chat path), and records
            # its own failures for everyone else to skip in turn.
            raw_text = await call_llm_with_fallback(
                session=session,
                system_prompt=VERIFIER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=max_tokens,
                temperature=temperature,
                user_id=user_id,
                caller_tag="VerifierEngine",
            )

            # Robustly extract JSON — handles markdown fences, prose wrappers,
            # and models that add extra text before/after the JSON object.
            raw = _extract_json(raw_text)

            passed = bool(raw.get("passed", False))
            summary = raw.get("summary", "") or ""

            # Success path: the prompt now asks the model NOT to generate a
            # summary at all (pure wasted generation tokens on the common
            # case -- see prompts/verifier.py). If it followed instructions,
            # summary is empty/whitespace here, so synthesize a deterministic
            # one in Python. If a model doesn't follow instructions and sends
            # real content anyway, keep it -- this is a floor, not an
            # override, since real content is preferable when available.
            if passed and not summary.strip():
                summary = f"{step.get('title', 'Step')} completed successfully."

            # A-21: fail-closed on every axis. A step that didn't pass its
            # own check never gets to claim it also solved future ones --
            # this is enforced here in code, not left to the model's own
            # discipline, even though the prompt also asks for it. Clamped
            # to the actual number of remaining_steps given, never trusting
            # a raw model-reported count past what could possibly be true.
            also_satisfies_count = 0
            also_satisfies_reason = None
            if passed and remaining_steps:
                raw_count = raw.get("also_satisfies_count", 0)
                if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count > 0:
                    also_satisfies_count = min(raw_count, len(remaining_steps))
                    also_satisfies_reason = raw.get("also_satisfies_reason") or None

            result = VerificationResult(
                # Default to False if "passed" key missing — safer than True
                passed=passed,
                summary=summary,
                issues=raw.get("issues") or None,
                also_satisfies_count=also_satisfies_count,
                also_satisfies_reason=also_satisfies_reason,
            )

            logger.info(
                "[VerifierEngine] Step verification complete",
                extra={
                    "user_id":    user_id,
                    "step_index": step.get("index"),
                    "passed":     result.passed,
                    "issues":     result.issues,
                    "also_satisfies_count": result.also_satisfies_count,
                },
            )
            return result

        except json.JSONDecodeError as exc:
            logger.warning(
                "[VerifierEngine] JSON parse failed — treating step as unverified (passed=False)",
                exc_info=exc,
                extra={
                    "user_id":    user_id,
                    "step_index": step.get("index"),
                    "raw_text":   locals().get("raw_text", "<not captured>")[:300],
                },
            )
        except Exception as exc:
            logger.warning(
                "[VerifierEngine] Verification failed — treating step as unverified (passed=False)",
                exc_info=exc,
                extra={"user_id": user_id, "step_index": step.get("index")},
            )

        return _FALLBACK_RESULT


# Module-level singleton
verifier_engine = VerifierEngine()
