# core/providers/param_support.py
#
# V6.4 — Provider-agnostic generation parameter support.
#
# Answers one question: for a given EVA (provider, model) pair, is it safe to
# send `temperature`, and which token-limit parameter name does the model
# expect ("max_tokens" vs "max_completion_tokens")?
#
# Background
# ----------
# EVA's provider handlers expose a generic create_message(max_tokens=,
# temperature=) interface to callers -- callers should never need to know
# provider-specific quirks. Each handler is responsible for translating the
# generic request into whatever its underlying API actually accepts. This
# module is the shared lookup those handlers consult before deciding whether
# to include either parameter in the outbound request.
#
# IMPORTANT — why this is NOT a call to litellm.get_supported_openai_params()
# -----------------------------------------------------------------------
# An earlier version of this module used that LiteLLM utility function
# directly. It was wrong, and caught by testing before shipping: that
# function describes LiteLLM's OWN unified-completion abstraction layer, not
# the raw API surface underneath. Five of EVA's six provider handlers
# (anthropic, groq, openai, ollama, ollama_cloud) call each provider's
# native SDK DIRECTLY -- they never pass through LiteLLM at all. Verified
# example of why this matters: the LiteLLM lookup reported that Anthropic
# models "support" `max_completion_tokens` (true only in the sense that
# LiteLLM's own completion() function accepts that name as input and
# translates it internally) -- but the actual Anthropic Messages API, called
# directly via the `anthropic` SDK in anthropic_handler.py, has never heard
# of that name; it only ever accepts `max_tokens`. Using the LiteLLM answer
# here would have sent the wrong field name to the real API.
#
# The one exception is `litellm_handler.py`, which genuinely does route
# through LiteLLM's own completion() function -- for that one handler, the
# LiteLLM lookup IS the correct source of truth, since LiteLLM will itself
# be doing the underlying translation.
#
# Concrete provider quirks this module encodes:
#   - Anthropic Claude Opus 4.7 and later (including 4.8) reject
#     `temperature` with a 400 error if set to a non-default value.
#     Anthropic's Messages API has exactly one token-limit parameter name,
#     `max_tokens`, across every model generation -- no equivalent of
#     OpenAI's max_completion_tokens split exists on this API at all.
#   - OpenAI o-series (o1, o3, o4, ...) and GPT-5 reasoning models require
#     `max_completion_tokens` instead of `max_tokens`, and are treated
#     conservatively here as not supporting a custom `temperature` value.
#   - Groq's OpenAI-compatible API accepts `max_completion_tokens` (their
#     current documented/preferred name) and `temperature` normally,
#     including for the reasoning-capable openai/gpt-oss-20b/120b models.
#   - Ollama (local) nests both under a top-level "options" dict as
#     `num_predict` / `temperature` -- never top-level keys. This module
#     only answers the temperature/token-limit *support* question; the
#     nesting itself is transport shape, handled directly in
#     ollama_handler.py, not here.
#   - Ollama Cloud exposes an OpenAI-compatible endpoint (ollama_cloud_
#     handler.py uses AsyncOpenAI directly) and accepts top-level
#     `max_tokens` / `temperature` with no known model-specific restriction.
#
# Design principle: FAIL SAFE, NOT FAIL OPEN.
#   Any uncertainty -- an unrecognised provider, an unknown/future model --
#   resolves to the most conservative answer: temperature is NOT sent, and
#   no token-limit parameter is sent either. A caller that doesn't get its
#   requested max_tokens cap still gets a working response (just not
#   length-capped); a caller that gets a 400 error because we guessed wrong
#   does not.
#
# Maintenance note
# ----------------
# The Anthropic and OpenAI restricted-model lists below are point-in-time,
# hand-curated knowledge, not a dynamically-updated source. Review this
# module when EVA adds support for a new Claude or OpenAI model generation.

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParamSupport:
    """
    supports_temperature : True if the model accepts `temperature` without
        erroring. False (the safe default) if unsupported, restricted, or
        unknown.
    token_limit_param : the parameter name a handler should use to cap
        output length -- "max_tokens", "max_completion_tokens", or None if
        neither is confidently known (caller should omit any cap).
    """
    supports_temperature: bool
    token_limit_param: Optional[str]


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
# max_tokens is the ONLY token-limit name the Anthropic Messages API has
# ever accepted, across every model generation -- always safe to send.
# temperature is rejected outright (400) on Opus 4.7 and later.
_ANTHROPIC_TEMPERATURE_RESTRICTED_SUBSTRINGS = (
    "opus-4-7",
    "opus-4-8",
    "opus-4-9",  # not yet released at time of writing; included pre-emptively
)


def _anthropic_param_support(model: str) -> ParamSupport:
    model_l = model.lower()
    restricted = any(s in model_l for s in _ANTHROPIC_TEMPERATURE_RESTRICTED_SUBSTRINGS)
    return ParamSupport(
        supports_temperature=not restricted,
        token_limit_param="max_tokens",
    )


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
# o-series and GPT-5 reasoning models require max_completion_tokens instead
# of max_tokens, and are treated conservatively as not supporting a custom
# temperature (some reject a non-default value outright; others silently
# ignore it -- fail-safe applies either way).
_OPENAI_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def _openai_param_support(model: str) -> ParamSupport:
    model_l = model.lower()
    is_reasoning = any(model_l.startswith(p) or f"/{p}" in model_l for p in _OPENAI_REASONING_MODEL_PREFIXES)
    if is_reasoning:
        return ParamSupport(supports_temperature=False, token_limit_param="max_completion_tokens")
    return ParamSupport(supports_temperature=True, token_limit_param="max_tokens")


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------
# OpenAI-compatible API surface. max_completion_tokens is Groq's current
# documented/preferred name (max_tokens is a still-working but deprecated
# alias -- using the current name is more future-proof). temperature is
# supported normally, including for the reasoning-capable gpt-oss models
# (only their separate `reasoning_effort` / `reasoning_format` params have
# special handling -- unrelated to temperature/token-limit).
def _groq_param_support(model: str) -> ParamSupport:  # noqa: ARG001 — no per-model variance currently
    return ParamSupport(supports_temperature=True, token_limit_param="max_completion_tokens")


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------
# Always supported; ollama_handler.py nests these under "options" itself.
# This function only answers "is it safe" -- the caller decides the shape.
def _ollama_param_support(model: str) -> ParamSupport:  # noqa: ARG001
    return ParamSupport(supports_temperature=True, token_limit_param="max_tokens")


# ---------------------------------------------------------------------------
# Ollama Cloud
# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint, open-weight hosted models, no known
# temperature restriction. Confirmed top-level "max_tokens" naming.
def _ollama_cloud_param_support(model: str) -> ParamSupport:  # noqa: ARG001
    return ParamSupport(supports_temperature=True, token_limit_param="max_tokens")


# ---------------------------------------------------------------------------
# LiteLLM passthrough
# ---------------------------------------------------------------------------
# The one handler that genuinely calls litellm.acompletion() underneath --
# LiteLLM's own get_supported_openai_params() IS the correct source of
# truth here, since LiteLLM performs the real-API translation itself.
#
# Known residual gap: LiteLLM's own metadata does not encode Anthropic's
# Opus 4.7+ temperature restriction either (same blind spot as the earlier,
# rejected design of this whole module) -- a user who routes to Anthropic
# via the generic "litellm" provider (e.g. model="anthropic/claude-opus-4-8")
# would otherwise hit the same 400 error LiteLLM itself doesn't protect
# against. The Anthropic restriction check is layered on top here as a
# belt-and-suspenders guard, independent of what LiteLLM reports.
def _litellm_param_support(model: str) -> ParamSupport:
    try:
        import litellm
        supported = litellm.get_supported_openai_params(model=model)
    except Exception as exc:
        logger.warning(
            "param_support: LiteLLM lookup failed for model='%s' — using conservative fallback",
            model,
            exc_info=exc,
        )
        return ParamSupport(supports_temperature=False, token_limit_param=None)

    if supported is None:
        logger.debug("param_support: LiteLLM has no data for model='%s' — using conservative fallback", model)
        return ParamSupport(supports_temperature=False, token_limit_param=None)

    supports_temperature = "temperature" in supported
    if "max_completion_tokens" in supported:
        token_limit_param: Optional[str] = "max_completion_tokens"
    elif "max_tokens" in supported:
        token_limit_param = "max_tokens"
    else:
        token_limit_param = None

    # Belt-and-suspenders: apply the known Anthropic restriction regardless
    # of what LiteLLM's own metadata says, if this model string is routed
    # to Anthropic underneath (litellm convention: "anthropic/<model>").
    model_l = model.lower()
    if "anthropic/" in model_l or model_l.startswith("claude-"):
        if any(s in model_l for s in _ANTHROPIC_TEMPERATURE_RESTRICTED_SUBSTRINGS):
            supports_temperature = False

    return ParamSupport(supports_temperature=supports_temperature, token_limit_param=token_limit_param)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_PROVIDER_HANDLERS = {
    "anthropic":    _anthropic_param_support,
    "openai":       _openai_param_support,
    "groq":         _groq_param_support,
    "ollama":       _ollama_param_support,
    "ollama_cloud": _ollama_cloud_param_support,
    "litellm":      _litellm_param_support,
}


def get_param_support(provider: str, model: str) -> ParamSupport:
    """
    Return which optional generation parameters are safe to send for this
    EVA (provider, model) pair. Never raises -- any failure resolves to the
    conservative fallback (see module docstring).
    """
    fn = _PROVIDER_HANDLERS.get(provider.lower())
    if fn is None:
        logger.debug(
            "param_support: no rules for provider '%s' — using conservative fallback",
            provider,
        )
        return ParamSupport(supports_temperature=False, token_limit_param=None)

    try:
        return fn(model)
    except Exception as exc:
        logger.warning(
            "param_support: lookup raised for provider='%s' model='%s' — using conservative fallback",
            provider, model,
            exc_info=exc,
        )
        return ParamSupport(supports_temperature=False, token_limit_param=None)
