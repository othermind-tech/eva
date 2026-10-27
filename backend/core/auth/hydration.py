# core/auth/hydration.py
#
# Single canonical mapping from a UserPreference DB row → session prefs dict.
#
# Previously this dict was copy-pasted in three places with slight divergence:
#   api/auth.py              — login path (was missing tool_hooks, shell_readonly,
#                              user_timezone, user_location, user_language)
#   core/auth/dependencies.py — per-request hydration (was missing tool_hooks,
#                              shell_readonly)
#   core/execution/worker.py  — execution hydration (was missing allowed_authorities,
#                              agent_mode, tool_hooks, shell_readonly)
#
# The divergence was the root cause of B-011 (session fields silently missing
# for freshly logged-in users).  Any future field added to UserPreference must
# only be added here — it will automatically propagate to all three call sites.
#
# Usage:
#   from core.auth.hydration import build_prefs_dict
#   hydrate_user_session_from_db(user_id, build_prefs_dict(prefs))

from __future__ import annotations

from typing import Any, Dict, Optional


def build_prefs_dict(prefs, default_session: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build the full session preferences dict from a UserPreference ORM row.

    Parameters
    ----------
    prefs : UserPreference
        ORM row from DBRepo.get_user_preferences().  Must not be None —
        callers should pass DEFAULT_SESSION directly when prefs is None.
    default_session : dict, optional
        Fallback dict for fields missing from prefs.  Defaults to importing
        DEFAULT_SESSION from config.settings if not supplied.

    Returns
    -------
    dict
        Complete prefs dict ready to pass to hydrate_user_session_from_db().
    """
    if default_session is None:
        from config.settings import DEFAULT_SESSION
        default_session = DEFAULT_SESSION

    def _bool(val, default: bool) -> bool:
        return bool(val) if val is not None else default

    return {
        # ── Provider / model ────────────────────────────────────────────
        # No default_session fallback here (2026-07-27 cleanup): prefs.provider
        # /.model are NOT NULL DB columns -- if `prefs` is a real row, these
        # are never falsy. A fallback would only ever mask a genuine data
        # integrity bug, not handle a real case.
        "provider":  prefs.provider,
        "model":     prefs.model,
        "reasoning": prefs.reasoning if prefs.reasoning is not None else default_session["reasoning"],

        # ── Tools ───────────────────────────────────────────────────────
        "tools":          prefs.tools          or default_session["tools"],
        "fallback_chain": prefs.fallback_chain or default_session["fallback_chain"],

        # ── Approval / authorities ───────────────────────────────────────
        "tool_approval_mode":  prefs.tool_approval_mode  or default_session["tool_approval_mode"],
        "allowed_authorities": prefs.allowed_authorities or default_session["allowed_authorities"],

        # ── Agent / routing ──────────────────────────────────────────────
        "agent_mode":           prefs.agent_mode or default_session["agent_mode"],
        "require_plan_approval": _bool(prefs.require_plan_approval,
                                       default_session.get("require_plan_approval", False)),

        # ── Step-limit ask-to-continue (V6.12) ───────────────────────────
        # Was missing from this dict, so hydrate_user_session_from_db() fell back to
        # its default of 2 on every login and every execution start, silently
        # discarding the stored value (found live: /set-session stored 6, the gate
        # logged extensions_max=2). 0 is meaningful ("no gate"), hence `is not None`.
        "max_step_extensions": (
            prefs.max_step_extensions if prefs.max_step_extensions is not None
            else default_session.get("max_step_extensions", 2)
        ),

        # ── Memory ──────────────────────────────────────────────────────
        "user_memory_enabled":  _bool(prefs.user_memory_enabled,
                                      default_session.get("user_memory_enabled", True)),
        "agent_scope_key":      prefs.agent_scope_key or None,
        "agent_memory_enabled": _bool(prefs.agent_memory_enabled,
                                      default_session.get("agent_memory_enabled", True)),

        # ── Skills ──────────────────────────────────────────────────────
        "skills_mode": prefs.skills_mode or default_session.get("skills_mode", "auto"),

        # ── Hooks (B-053) ────────────────────────────────────────────────
        "tool_hooks": prefs.tool_hooks or [],

        # ── Shell safety (B-052) ────────────────────────────────────────
        "shell_readonly": _bool(prefs.shell_readonly, False),

        # ── Environment context (V5.5) ───────────────────────────────────
        "user_timezone": prefs.user_timezone or None,
        "user_location": prefs.user_location or None,
        "user_language": prefs.user_language or None,

        # ── PII redaction (S-03) ─────────────────────────────────────────
        "pii_redaction_enabled": _bool(prefs.pii_redaction_enabled, False),

        # ── Adaptable PEV runner (A-21) ───────────────────────────────────
        "pev_lookahead_skip_enabled": _bool(prefs.pev_lookahead_skip_enabled, True),
    }
