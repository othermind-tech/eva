# api/_provider_validation.py
#
# Save-time validation of a provider choice (V6.59 Phase 4b).
#
# The selectors only offer chat-ready providers (GET /providers/list), but an
# API client can send anything: a typo, a provider EVA doesn't run, one whose
# key was just deleted. Without a check here the bad choice is stored and only
# surfaces later as a run that silently does nothing (a daemon with an
# unusable provider is skipped at run time) or a chat that fails mid-turn.
# Every place a user picks a provider -- the session's primary, its fallback
# chain, a sub-agent's or daemon's override -- goes through this module.
#
# A choice is usable when the provider is (1) one EVA knows, (2) chat-ready
# (has a working handler path -- not Azure/Bedrock/... yet) and (3) configured
# (a key is stored; local Ollama needs none). "Inherit from the session"
# (provider None/empty) is always valid and is never passed here.

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

from providers import registry

from core.providers.usability import (  # noqa: E402  (single source for the reason strings)
    REASON_NOT_CHAT_READY,
    REASON_NOT_CONFIGURED,
    REASON_UNKNOWN_PROVIDER as REASON_UNKNOWN,
)

#: Error code carried in every structured detail this module raises.
ERROR_CODE = "EVA_PROVIDER_NOT_USABLE"


@dataclass(frozen=True)
class ProviderProblem:
    reason: str
    provider: str          # canonical name (or the raw input if unknown)
    message: str

    @property
    def status_code(self) -> int:
        # A key that is simply not stored yet is a 424 (as POST /set-session
        # has always answered); a provider EVA cannot use at all is a 422.
        return 424 if self.reason == REASON_NOT_CONFIGURED else 422


def check_provider_usable(provider: str) -> Optional[ProviderProblem]:
    """None if `provider` can be used right now, else why not."""
    from core.providers.usability import check_provider  # noqa: PLC0415
    problem = check_provider(provider)
    if problem is None:
        return None
    return ProviderProblem(problem.reason, problem.provider, problem.message)


def problem_to_http(problem: ProviderProblem, field: str) -> HTTPException:
    return HTTPException(
        status_code=problem.status_code,
        detail={
            "code": ERROR_CODE,
            "field": field,
            "provider": problem.provider,
            "reason": problem.reason,
            "message": problem.message,
        },
    )


def validate_provider_override(
    provider: Optional[str],
    model: Optional[str],
    *,
    existing_provider: Optional[str] = None,
    field: str = "provider",
) -> Optional[str]:
    """
    Validate an agent/daemon provider override on save and return the value to
    store (the canonical provider name, or None to inherit from the session).

    * empty / None provider  -> None (inherit; model, if any, is just a model
      override for the session's provider)
    * a provider override must come with a model: the run would otherwise pair
      this provider with the *session's* model, which belongs to another
      provider -- a guaranteed failure (422)
    * unchanged provider (same as the stored row's) is accepted as is, so
      editing e.g. a description is not blocked because a key was removed
      later (key removal already reported what it left dangling)
    * otherwise the provider must be known, chat-ready and configured
    """
    raw = (provider or "").strip()
    if not raw:
        return None

    canonical = registry.canonical_name(raw)
    if not (model or "").strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": ERROR_CODE,
                "field": "model",
                "provider": canonical,
                "reason": "model_required_with_provider",
                "message": "A provider override needs a model too. Pick a model, "
                           "or clear the provider to inherit from the session.",
            },
        )

    if existing_provider and registry.canonical_name(existing_provider) == canonical:
        return canonical

    problem = check_provider_usable(canonical)
    if problem is not None:
        raise problem_to_http(problem, field)
    return canonical
