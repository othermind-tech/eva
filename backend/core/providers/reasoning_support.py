# core/providers/reasoning_support.py
#
# V6.7 -- Provider-agnostic reasoning-level support.
#
# Answers: for a given EVA (provider, model) pair, which reasoning levels
# does it actually accept, and how do we build the provider-specific
# request fragment for a chosen level? Mirrors param_support.py's shape
# (per-provider functions behind one dispatch function) but for reasoning
# depth instead of temperature/token-limit naming.
#
# Design principle, arrived at after checking how other gateways solve
# this (litellm, OpenRouter) rather than guessing:
#
#   1. EVA exposes ONE small unified vocabulary (REASONING_LEVELS below)
#      across every provider, not each provider's raw level names --
#      OpenRouter independently converged on the identical design for
#      the same reason: callers (session config, frontend) shouldn't need
#      to know that Groq's Qwen models accept "none"/"default" while its
#      gpt-oss models don't, or that Anthropic only has 3 levels where
#      OpenAI has 6.
#
#   2. Where a well-maintained multi-provider gateway (litellm) already
#      has correct, actively-used-in-production logic for a provider
#      quirk, call it directly rather than re-deriving EVA's own copy.
#      Verified empirically against the actually-installed litellm
#      version, not just its GitHub docs, before relying on it here --
#      see anthropic_reasoning_support()/anthropic_thinking_param()
#      below. This is NOT true of litellm's public, generic
#      get_supported_openai_params()/get_model_info(), which describe
#      litellm's own abstraction layer and were verified (empirically,
#      same rigor) to give wrong answers for exactly this kind of
#      per-model restriction -- param_support.py's docstring documents
#      the same pitfall for a different parameter.
#
#   3. Where no reliable capability data exists ANYWHERE -- confirmed by
#      checking litellm's own source, not assumed -- for most of OpenAI's
#      and Groq's per-model level sets, don't try to pre-validate. Offer
#      the provider's documented level range, send what's requested, and
#      let the provider's own API be the source of truth; a rejection is
#      classified by core/error_handling.py the same way any other
#      provider error is. See providers/handlers/openai_handler.py and
#      groq_handler.py for the try-once/learn implementation.
#

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unified vocabulary
# ---------------------------------------------------------------------------
# Ordered weakest -> strongest. Matches the vocabulary litellm's own
# `reasoning_effort` and OpenRouter's `reasoning.effort` both already use --
# not EVA inventing a seventh naming scheme, reusing the one two established
# multi-provider gateways already converged on independently.
#
# "off" is deliberately NOT a member of this tuple -- it's a separate,
# explicit "no reasoning" state handled wherever a level is resolved, not
# just the weakest point on this scale (some models can't reach it at all --
# see ReasoningSupport.can_disable).
REASONING_LEVELS: Tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh")


@dataclass(frozen=True)
class ReasoningSupport:
    """
    levels       : ordered subset of REASONING_LEVELS this specific model
                   accepts. Empty tuple = no reasoning support at all.
    can_disable  : False for models where reasoning can never be turned off
                   (Claude Fable 5 / Mythos 5 / Mythos Preview reject
                   `type: "disabled"` outright). True is the safe default
                   for every other model.
    """
    levels: Tuple[str, ...]
    can_disable: bool = True


_NO_REASONING = ReasoningSupport(levels=())


def snap_to_nearest_level(requested: str, available: Tuple[str, ...]) -> Optional[str]:
    """
    Given a requested level and a model's actual available set, return the
    closest level in `available` on the REASONING_LEVELS ordinal scale.

    Mirrors OpenRouter's own documented behaviour for models with fewer
    levels than the full vocabulary ("if a model only supports low and
    high, OpenRouter maps your requested effort to the nearest supported
    level") rather than requiring an exact per-model match, which we
    already confirmed no data source (not even litellm) reliably has for
    most models.

    Returns None only if `available` is empty (no reasoning support).
    """
    if not available:
        return None
    if requested in available:
        return requested
    if requested not in REASONING_LEVELS:
        # Not a recognised level at all -- fall back to the middle of
        # whatever this model actually offers rather than guessing further.
        return available[len(available) // 2]
    req_idx = REASONING_LEVELS.index(requested)
    return min(available, key=lambda lvl: abs(REASONING_LEVELS.index(lvl) - req_idx))


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
# Anthropic's real API only ever accepts 3 effort names on adaptive-thinking
# models (low/medium/high) -- collapse EVA's 5-level vocabulary down when
# building the actual request (snap_to_nearest_level handles minimal->low,
# xhigh->high).
_ANTHROPIC_ADAPTIVE_LEVELS: Tuple[str, ...] = ("low", "medium", "high")

# Verified 2026-07 against Anthropic's own docs: these model families run
# adaptive thinking permanently on -- `type: "disabled"` is rejected by the
# API outright, unlike every other reasoning-capable model. No litellm
# equivalent found for this specific fact (litellm just lets the API
# reject an unsupported `type: "disabled"`, the same pass-through pattern
# it uses for OpenAI's per-model param restrictions) -- kept here as the
# one deliberately narrow, EVA-owned exception in this module. Small and
# explicit on purpose; revisit only if Anthropic's own contract changes.
_ANTHROPIC_ALWAYS_ON_SUBSTRINGS: Tuple[str, ...] = (
    "fable-5", "fable_5", "fable5",
    "mythos-5", "mythos_5", "mythos5",
    "mythos-preview", "mythos_preview",
)

# EVA-owned classification, replacing what used to be borrowed from
# litellm's private AnthropicConfig._is_adaptive_thinking_model(). That
# was itself just a hardcoded substring list internally (confirmed by
# reading litellm's source at the time)
# -- borrowing it didn't avoid hand-maintenance, it just meant EVA's
# maintenance burden was invisible until litellm's own internals moved
# (which they did: as of litellm 1.80.7 this method, and
# AnthropicConfig._apply_sampling_param, no longer exist at all -- removed,
# not renamed). Anthropic is a native provider for EVA (providers/handlers/
# anthropic_handler.py uses the `anthropic` SDK directly, litellm is never
# in that request path) -- depending on litellm's *private* internals for
# this one classification was the wrong tradeoff. Source of truth is
# anthropic_handler.py's own header comment (already documents this same
# split) and Anthropic's public docs; update this list when Anthropic ships
# a new model generation -- a rare event compared to litellm's own point
# releases, which is what actually broke this.
_ANTHROPIC_ADAPTIVE_SUBSTRINGS: Tuple[str, ...] = (
    "sonnet-5", "sonnet_5", "sonnet5",
    "opus-4-7", "opus_4_7", "opus47",
    "opus-4-8", "opus_4_8", "opus48",
) + _ANTHROPIC_ALWAYS_ON_SUBSTRINGS  # fable-5 / mythos-5 / mythos-preview are also adaptive-shape, and always-on

# Legacy budget_tokens values per unified level. minimal/low/medium/high
# match litellm's own real per-level constants at the time this was last
# cross-checked (1024/2048/4096 for low/medium/high, 128 for minimal);
# xhigh (a level litellm's current _map_reasoning_effort doesn't even
# offer any more) continues the same progression, matching the value
# originally verified.
_ANTHROPIC_LEGACY_BUDGET_TOKENS: dict[str, int] = {
    "minimal": 128,
    "low": 1024,
    "medium": 2048,
    "high": 4096,
    "xhigh": 8192,
}


def _anthropic_always_on(model: str) -> bool:
    model_l = model.lower()
    return any(s in model_l for s in _ANTHROPIC_ALWAYS_ON_SUBSTRINGS)


def _is_adaptive_thinking_model(model: str) -> bool:
    model_l = model.lower()
    return any(s in model_l for s in _ANTHROPIC_ADAPTIVE_SUBSTRINGS)


def anthropic_reasoning_support(model: str) -> ReasoningSupport:
    """
    Which reasoning levels a Claude model accepts, and whether it can be
    disabled. Shape detection (adaptive vs. legacy budget_tokens) is an
    EVA-owned classification (see _ANTHROPIC_ADAPTIVE_SUBSTRINGS above) --
    previously borrowed from litellm's private internals, which broke when
    those internals changed shape across a litellm upgrade.

    Callers should check capability_registry's binary supports_reasoning
    first (whether this model does reasoning at all) -- this function only
    decides the shape/level-set once that's already established true.
    """
    if _is_adaptive_thinking_model(model):
        return ReasoningSupport(
            levels=_ANTHROPIC_ADAPTIVE_LEVELS,
            can_disable=not _anthropic_always_on(model),
        )
    return ReasoningSupport(levels=REASONING_LEVELS, can_disable=True)


def anthropic_thinking_param(level: Optional[str], model: str) -> Optional[dict]:
    """
    Build the exact `thinking` request dict for an already capability-
    checked level (caller is expected to have already resolved/snapped the
    level via anthropic_reasoning_support() -- this does not re-validate).

    Returns None when reasoning should be omitted from the request
    entirely (level is None/"off").
    """
    if level is None or level == "off":
        return None

    if _is_adaptive_thinking_model(model):
        snapped = snap_to_nearest_level(level, _ANTHROPIC_ADAPTIVE_LEVELS) or "medium"
        return {"type": "adaptive", "effort": snapped}

    budget = _ANTHROPIC_LEGACY_BUDGET_TOKENS.get(level)
    if budget is None:
        return None
    return {"type": "enabled", "budget_tokens": budget}


def anthropic_supports_temperature(model: str, temperature: float) -> bool:
    """
    Whether `temperature` is safe to include for this specific model AND
    value. EVA-owned check, reusing the same restricted-model list
    param_support.py already maintains (core/providers/param_support.py's
    _ANTHROPIC_TEMPERATURE_RESTRICTED_SUBSTRINGS) rather than a second copy
    -- with one addition on top: temperature=1 is always safe to send, even
    on a restricted model (Anthropic's API special-cases the default value).
    Previously borrowed from litellm's private _apply_sampling_param, which
    no longer exists in the installed litellm version.
    """
    if temperature == 1:
        return True
    from core.providers.param_support import _ANTHROPIC_TEMPERATURE_RESTRICTED_SUBSTRINGS
    model_l = model.lower()
    return not any(s in model_l for s in _ANTHROPIC_TEMPERATURE_RESTRICTED_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Ollama (local + Cloud -- same model family, same `think`/`reasoning_effort`
# level contract; ollama_cloud_handler.py just carries it over the
# OpenAI-compatible endpoint's `reasoning_effort` field instead of the
# native API's `think` field)
# ---------------------------------------------------------------------------
# docs.ollama.com/capabilities/thinking, verified 2026-07: "Most models
# accept booleans (true/false) or levels (low, medium, high, max)... GPT-OSS
# requires think to be set to low/medium/high. Passing true/false is
# ignored for that model." Rather than hand-maintaining a model-family list
# to decide bool-vs-string (the exact kind of list we've been removing
# elsewhere), always send the level as a string when reasoning is on --
# per Ollama's own documented contract this is accepted by every reasoning-
# capable model, not just gpt-oss, so one code path correctly covers both.
_OLLAMA_LEVELS: Tuple[str, ...] = ("low", "medium", "high", "max")
_OLLAMA_LEVEL_MAP = {
    "minimal": "low",
    "low":     "low",
    "medium":  "medium",
    "high":    "high",
    "xhigh":   "max",
}


def ollama_think_value(level: Optional[str]) -> Optional[str]:
    """
    Map a unified level to Ollama's own `think` value. Returns None when
    reasoning should be omitted entirely (level is None/"off") -- callers
    should not send the `think` field at all in that case, rather than
    sending a falsy value, since Ollama has no universal "explicitly off"
    contract the way Anthropic's `type: "disabled"` does.
    """
    if level is None or level == "off":
        return None
    return _OLLAMA_LEVEL_MAP.get(level, "medium")


# ---------------------------------------------------------------------------
# OpenAI / Groq / LiteLLM -- no reliable per-model level data exists
# anywhere (confirmed: litellm's own get_supported_openai_params() and its
# real request-transform code both lack model-aware validation for
# reasoning_effort on these providers, and OpenAI's own docs say the same:
# "some models support only a subset of these values, check the model
# page"). These providers get the full unified vocabulary offered
# optimistically; a level a specific model doesn't actually support
# surfaces as a normal classified provider error (core/error_handling.py)
# rather than being pre-validated here -- matching how litellm and every
# other gateway we checked (OpenRouter, Vercel AI SDK) handle this same gap.
# ---------------------------------------------------------------------------

def optimistic_reasoning_support() -> ReasoningSupport:
    """Offer the full unified vocabulary; let the provider's own API be
    the source of truth for what a specific model actually accepts."""
    return ReasoningSupport(levels=REASONING_LEVELS, can_disable=True)


# ---------------------------------------------------------------------------
# Unified dispatch
# ---------------------------------------------------------------------------

def get_reasoning_support(provider: str, model: str) -> ReasoningSupport:
    """
    Single entry point callers (api/session.py's save-time validation,
    AgentEngine's fallback-candidate handling) should use regardless of
    provider -- routes to the provider-specific function where one exists
    (Anthropic, Ollama/Ollama Cloud), otherwise the optimistic full-range
    offer (OpenAI, Groq, LiteLLM, and any provider this module doesn't
    have specific logic for yet).

    Callers are still expected to check capability_registry's binary
    supports_reasoning first for providers it covers (openai/anthropic/
    groq/litellm) -- this function only answers "given that reasoning is
    supported at all, which levels/shape apply."
    """
    provider_l = provider.lower()
    if provider_l == "anthropic":
        return anthropic_reasoning_support(model)
    if provider_l in ("ollama", "ollama_cloud"):
        # Local Ollama and Ollama Cloud share the same level contract
        # (see ollama_think_value docstring) -- no model-family list
        # needed, every reasoning-capable model on either accepts the
        # full unified range as a level string.
        return ReasoningSupport(levels=REASONING_LEVELS, can_disable=True)
    return optimistic_reasoning_support()
