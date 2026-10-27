# core/providers/usability.py
#
# Run-time usability of a provider/model target (V6.59 Phase 5).
#
# Phase 4b validates a provider choice when it is SAVED. A saved choice can go
# stale afterwards -- the key is removed (Phase 4a), the model turns out to be
# an embedding model, a legacy sub-agent row has a provider with no model -- and
# before this module the first symptom was whatever the provider SDK happened to
# raise mid-turn (or, for Anthropic / Ollama Cloud, a handler-construction crash
# that took the whole run down even when a perfectly good fallback was
# configured). This is the one check every run path now makes at the moment of
# use, so a stale target fails early and legibly, or is skipped in favour of the
# next usable candidate.
#
# A target is usable when:
#   1. the provider is known, chat-ready and configured (providers/registry.py,
#      providers/credentials.py) -- the same rule Phase 4b enforces on save;
#   2. the model, if LiteLLM's dataset knows it, is a chat model that supports
#      tool calling (EVA's agent loop always offers tools). A model the dataset
#      has no entry for is "unknown" and is allowed: a freshly released model
#      must not be blocked because the dataset has not caught up. Only an
#      explicit negative blocks. PROVIDER_GATE_MODEL_CHECK_ENABLED=false turns
#      the model half off entirely if the dataset is ever wrong about a model
#      someone needs.

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from providers import registry
from providers.capability.base import is_provider_key_configured

logger = logging.getLogger(__name__)

REASON_UNKNOWN_PROVIDER = "unknown_provider"
REASON_NOT_CHAT_READY = "provider_not_chat_ready"
REASON_NOT_CONFIGURED = "provider_not_configured"
REASON_MODEL_MISSING = "model_required_with_provider"
REASON_NOT_CHAT_MODEL = "model_not_a_chat_model"
REASON_NO_TOOL_CALLING = "model_lacks_tool_calling"


@dataclass(frozen=True)
class Problem:
    reason: str
    provider: str
    model: str
    message: str

    @property
    def status_code(self) -> int:
        """HTTP status when surfaced synchronously: 424 for the 'add a key'
        case (as /set-session and /chat have always answered), else 422."""
        return 424 if self.reason == REASON_NOT_CONFIGURED else 422


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

def check_provider(provider: str, *, explicit_key: Optional[str] = None) -> Optional[Problem]:
    """None if `provider` can be used right now, else why not."""
    spec = registry.get_spec(provider)
    if spec is None:
        return Problem(REASON_UNKNOWN_PROVIDER, provider, "",
                       f"Provider '{provider}' is not supported by EVA.")
    if not spec.chat_ready:
        return Problem(REASON_NOT_CHAT_READY, spec.name, "",
                       f"{spec.display_name} cannot be used for chat yet: {spec.not_ready_reason}.")
    if not is_provider_key_configured(spec.name, explicit_key=explicit_key):
        return Problem(REASON_NOT_CONFIGURED, spec.name, "",
                       f"No API key is configured for {spec.display_name}. "
                       "Add it under Profile → API Keys.")
    return None


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def model_facts(provider: str, model: str) -> Optional[Dict[str, Any]]:
    """
    LiteLLM's dataset entry for (provider, model), or None when it has none.
    Looks the model up the same three ways the capabilities endpoint does:
    the routing-prefixed key, "{provider}/{model}", then the bare name.
    """
    try:
        import litellm  # type: ignore[import]
        from core.providers.resolver import resolver
    except ImportError:
        return None
    resolution = resolver.resolve(provider, model)
    info = (
        litellm.model_cost.get(resolution.prefixed_model)
        or litellm.model_cost.get(f"{provider}/{model}")
        or litellm.model_cost.get(model)
    )
    return info if isinstance(info, dict) else None


def model_usability(provider: str, model: str) -> Tuple[Optional[bool], bool, Optional[str]]:
    """
    (supports_tool_calls, agent_usable, reason) for one model.

    supports_tool_calls: True/False, or None when the dataset does not say.
    agent_usable: False only on an explicit negative -- a non-chat mode
    (embedding, image, audio, ...) or supports_function_calling == False.
    reason: the Problem reason when not usable, else None.
    """
    info = model_facts(provider, model)
    if info is None:
        return None, True, None
    raw = info.get("supports_function_calling")
    tools = bool(raw) if raw is not None else None
    if info.get("mode", "chat") != "chat":
        return tools, False, REASON_NOT_CHAT_MODEL
    if tools is False:
        return tools, False, REASON_NO_TOOL_CALLING
    return tools, True, None


def check_model(provider: str, model: str) -> Optional[Problem]:
    from config.settings import PROVIDER_GATE_MODEL_CHECK_ENABLED
    if not PROVIDER_GATE_MODEL_CHECK_ENABLED:
        return None
    spec = registry.get_spec(provider)
    if spec is not None and spec.is_local:
        # A locally pulled model is whatever the user pulled; LiteLLM's dataset
        # is not authoritative for it, so never block on it.
        return None
    _tools, usable, reason = model_usability(provider, model)
    if usable:
        return None
    name = spec.display_name if spec else provider
    if reason == REASON_NOT_CHAT_MODEL:
        msg = f"'{model}' is not a chat model, so it cannot be used by {name} for conversation."
    else:
        msg = (f"'{model}' does not support tool calling, which EVA's agent needs. "
               "Pick a model that does.")
    return Problem(reason, registry.canonical_name(provider), model, msg)


def check_target(
    provider: str,
    model: str,
    *,
    explicit_key: Optional[str] = None,
) -> Optional[Problem]:
    """Full run-time check of one (provider, model) target."""
    if not (model or "").strip():
        return Problem(REASON_MODEL_MISSING, registry.canonical_name(provider), "",
                       "No model is set for this provider.")
    return check_provider(provider, explicit_key=explicit_key) or check_model(provider, model)


# ---------------------------------------------------------------------------
# A whole run: primary + fallback chain
# ---------------------------------------------------------------------------

@dataclass
class RunPlan:
    """What a run should actually use, after dropping stale targets."""
    candidates: List[Dict[str, Any]] = field(default_factory=list)   # usable, priority order
    skipped: List[Tuple[Dict[str, Any], Problem]] = field(default_factory=list)
    primary_problem: Optional[Problem] = None                         # why the configured primary was dropped

    @property
    def ok(self) -> bool:
        return bool(self.candidates)

    @property
    def primary(self) -> Optional[Dict[str, Any]]:
        return self.candidates[0] if self.candidates else None

    @property
    def fallbacks(self) -> List[Dict[str, Any]]:
        return self.candidates[1:]

    @property
    def promoted(self) -> bool:
        """True when the configured primary was unusable and a fallback took its place."""
        return self.ok and self.primary_problem is not None

    def failure_message(self) -> str:
        """User-facing explanation when nothing is usable."""
        first = self.primary_problem or (self.skipped[0][1] if self.skipped else None)
        base = first.message if first else "No usable provider is configured for this session."
        extra = len(self.skipped) - 1
        if extra > 0:
            base += f" ({extra} fallback entr{'y was' if extra == 1 else 'ies were'} also unusable.)"
        return base + " You can also pick another provider with POST /set-session."


def session_for_plan(session: Dict[str, Any], plan: RunPlan) -> Dict[str, Any]:
    """
    The session a run should use after the usability gate: a COPY with the
    plan's primary, and its fallback chain reduced to the usable entries. The
    user's stored session is never altered by one run's pruning. Returns the
    input unchanged (same object) when nothing was skipped.
    """
    if not plan.ok or not plan.skipped:
        return session
    updated = {
        **session,
        "provider": plan.primary["provider"],
        "model": plan.primary["model"],
        "fallback_chain": plan.fallbacks,
    }
    if plan.promoted:
        # A promoted fallback may carry its own explicit key; the old
        # primary's key must not travel with it.
        updated["api_key"] = plan.primary.get("api_key")
    return updated


def plan_run(
    provider: str,
    model: str,
    fallback_chain: Optional[Sequence[Dict[str, Any]]] = None,
) -> RunPlan:
    """
    Check the session's primary and each fallback entry; keep the usable ones
    in priority order. If the primary is unusable but a fallback is not, that
    fallback is promoted to primary (plan.promoted) -- the run proceeds
    instead of crashing at handler construction.
    """
    plan = RunPlan()
    entries: List[Dict[str, Any]] = [{"provider": provider, "model": model}] + [
        e for e in (fallback_chain or []) if e.get("provider") and e.get("model")
    ]
    for index, entry in enumerate(entries):
        problem = check_target(entry["provider"], entry["model"], explicit_key=entry.get("api_key"))
        if problem is None:
            plan.candidates.append(entry)
            continue
        if not problem.model:
            problem = replace(problem, model=entry["model"])   # provider-level problems know no model
        plan.skipped.append((entry, problem))
        if index == 0:
            plan.primary_problem = problem
        logger.warning(
            "Provider target unusable at run time -- skipping",
            extra={"provider": entry["provider"], "model": entry["model"],
                   "reason": problem.reason, "is_primary": index == 0},
        )
    return plan


def resolve_child_target(
    cfg_provider: Optional[str],
    cfg_model: Optional[str],
    default_provider: str,
    default_model: str,
    fallback_chain: Optional[Sequence[Dict[str, Any]]] = None,
) -> RunPlan:
    """
    The usable target for a sub-agent / dynamic agent, given its own optional
    override and the parent session's defaults.

    A provider override is a provider+model PAIR: it is never combined with the
    parent's model (which belongs to another provider -- a guaranteed failure).
    An override with a provider but no model (a legacy row saved before Phase
    4b) therefore resolves to an unusable target with REASON_MODEL_MISSING,
    rather than silently running the wrong pairing. With no provider override,
    a model-only override applies to the parent's provider.
    """
    if cfg_provider:
        return plan_run(cfg_provider, cfg_model or "", fallback_chain)
    return plan_run(default_provider, cfg_model or default_model, fallback_chain)
