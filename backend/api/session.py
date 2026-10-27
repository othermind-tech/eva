# api/session.py

import asyncio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from logging import getLogger
from api.schemas.requests import SessionRequest, PreviewEndpointRequest
from api.schemas.responses import SessionUpdateResponse, StatusResponse, PreviewEndpointsResponse

from config.settings import (
    hydrate_user_session_from_db,
    get_user_session,
    set_user_session,
    ToolApprovalMode,
    AgentMode,
    DEFAULT_SESSION,
)
from core.auth.dependencies import get_current_auth
from core.db.repository import DBRepo, MCPRepo
from core.db.session import AsyncSessionLocal
from core.providers.capability_registry import get_model
from core.hooks import HookRunner
from core.mcp import preview_registry
from core.mcp.manager import MCPManager
from core.mcp.session_pool import mcp_session_pool
from providers import registry as _registry
from providers.capability.base import is_provider_key_configured
from api._provider_validation import (
    REASON_NOT_CONFIGURED,
    check_provider_usable,
    problem_to_http,
)
from utils.workspace import get_user_workspace

logger = getLogger(__name__)

router = APIRouter()

# Valid authority level values — must match ToolAuthority enum in core/tools/base.py.
_VALID_AUTHORITIES = {"read_only", "mutating", "destructive"}


@router.get("/session", response_model=SessionUpdateResponse)
async def get_session(auth=Depends(get_current_auth)):
    """
    Return the current session configuration for the authenticated user. (B-006)

    After a server restart the in-memory session may be missing.  The auth
    dependency (B-011) re-hydrates it from the DB on every request, so by the
    time this endpoint runs the session is always guaranteed to be fresh.
    """
    user_id = auth["user_id"]
    session = get_user_session(user_id)

    return SessionUpdateResponse(
        status="ok",
        # No hardcoded fallback (2026-07-27 cleanup): None means genuinely
        # unconfigured -- a real, expected state, not guessed away here.
        provider=session.get("provider") or None,
        model=session.get("model") or None,
        reasoning=session.get("reasoning", DEFAULT_SESSION["reasoning"]),
        tools_enabled=session.get("tools") or [],
        system_prompt=session.get("system_prompt") or "",
        fallback_chain=session.get("fallback_chain") or [],
        tool_approval_mode=session.get("tool_approval_mode") or DEFAULT_SESSION["tool_approval_mode"],
        allowed_authorities=session.get("allowed_authorities") or DEFAULT_SESSION["allowed_authorities"],
        agent_mode=session.get("agent_mode") or DEFAULT_SESSION["agent_mode"],
        require_plan_approval=bool(session.get("require_plan_approval", False)),
        max_step_extensions=int(session.get("max_step_extensions", 2)),
        user_memory_enabled=bool(session.get("user_memory_enabled", True)),
        agent_scope_key=session.get("agent_scope_key"),
        agent_memory_enabled=bool(session.get("agent_memory_enabled", True)),
        skills_mode=session.get("skills_mode") or DEFAULT_SESSION["skills_mode"],
        tool_hooks=session.get("tool_hooks") or [],
        shell_readonly=bool(session.get("shell_readonly", False)),
        user_timezone=session.get("user_timezone") or None,
        user_location=session.get("user_location") or None,
        user_language=session.get("user_language") or None,
        pii_redaction_enabled=bool(session.get("pii_redaction_enabled", False)),
        pev_lookahead_skip_enabled=bool(session.get("pev_lookahead_skip_enabled", True)),
        warnings=[],
    )


@router.post("/set-session", response_model=SessionUpdateResponse)
async def set_session(
    data: SessionRequest,
    auth=Depends(get_current_auth),):
    """
    Update the user's session:
    - provider
    - model
    - enabled tool list
    - system prompt
    - fallback_chain
    - tool_approval_mode  ("auto" | "always_ask" | "per_conversation")
    - allowed_authorities ("read_only,mutating,destructive" — comma-separated subset)
    - agent_mode          ("auto" | "chat" | "plan_execute")
    """
    user_id = auth["user_id"]

    # Collected across this function and returned to the caller so config
    # problems are surfaced, not silently absorbed. Declared early because
    # the fallback_chain credential check below needs to append to it too.
    warnings: List[str] = []

    # Serialise fallback_chain to a plain list-of-dicts for storage/session.
    #
    # V6.5 Phase 6: drop any entry that duplicates the primary provider/model,
    # or an earlier entry already kept in the chain (first occurrence wins --
    # chain order is priority order). A duplicate can never do anything a
    # candidate ahead of it hasn't already tried and failed at, so keeping it
    # would just waste a retry-and-backoff cycle mid-execution for no chance
    # of a different outcome. Compared as an exact (provider, model) string
    # pair -- no case-folding, since model identifiers are provider-defined
    # and not guaranteed case-insensitive.
    #
    # V6.5 Phase 1: each surviving entry's provider must also have a
    # resolvable API key (either the entry's own explicit api_key, or that
    # provider's env var) *at save time*. Entries that fail this check are
    # dropped rather than saved -- one warning per dropped entry -- rather
    # than persisted as a fallback candidate that's guaranteed to fail with
    # an AUTH error the moment it's ever actually tried.
    #
    # Together these are the single point where the fallback chain is
    # validated: AgentEngine's fallback loop trusts that every entry it
    # receives is a distinct, usable candidate and does not re-check either
    # property itself (see core/agent_engine.py). The chain's overall length
    # is capped separately, at the schema level (api/schemas/requests.py),
    # since an over-length submission has no partial-acceptance story.
    fallback_chain_dicts = []
    seen_candidates = {(_registry.canonical_name(data.provider), data.model)}
    for _idx, e in enumerate(data.fallback_chain or []):
        # V6.59 Phase 4b: a provider EVA cannot use at all (unknown, or known
        # but not chat-ready) can never become usable by adding a key, so it
        # is an error the caller must fix (422) -- unlike a merely
        # unconfigured provider, which is dropped with a warning below (the
        # frontend resends the whole stored chain on every save, so a key
        # removed later must not make unrelated saves fail).
        _problem = check_provider_usable(e.provider)
        if _problem is not None and _problem.reason != REASON_NOT_CONFIGURED:
            raise problem_to_http(_problem, f"fallback_chain[{_idx}].provider")
        e.provider = _registry.canonical_name(e.provider)
        key = (e.provider, e.model)
        if key in seen_candidates:
            warnings.append(
                f"Fallback entry '{e.provider}/{e.model}' was not saved: "
                f"duplicates the primary provider/model or an earlier "
                f"fallback entry."
            )
            logger.warning(
                "set_session: dropping duplicate fallback_chain entry",
                extra={"user_id": user_id, "provider": e.provider, "model": e.model},
            )
            continue
        seen_candidates.add(key)
        if is_provider_key_configured(e.provider, explicit_key=e.api_key):
            fallback_chain_dicts.append(e.model_dump(exclude_none=True))
        else:
            warnings.append(
                f"Fallback entry '{e.provider}/{e.model}' was not saved: "
                f"no API key configured for provider '{e.provider}'."
            )
            logger.warning(
                "set_session: dropping fallback_chain entry with no configured API key",
                extra={"user_id": user_id, "provider": e.provider, "model": e.model},
            )

    # Validate and normalise tool_approval_mode.
    raw_mode = data.tool_approval_mode or ToolApprovalMode.AUTO.value
    valid_modes = {m.value for m in ToolApprovalMode}
    tool_approval_mode = raw_mode if raw_mode in valid_modes else ToolApprovalMode.AUTO.value

    # Validate and normalise allowed_authorities.
    raw_authorities = data.allowed_authorities or "read_only,mutating,destructive"
    requested_set = {a.strip() for a in raw_authorities.split(",") if a.strip()}
    invalid = requested_set - _VALID_AUTHORITIES
    if invalid:
        logger.warning(
            "set_session: invalid authority values requested — falling back to all",
            extra={"user_id": user_id, "invalid": list(invalid)},
        )
        requested_set = _VALID_AUTHORITIES
    allowed_authorities = ",".join(sorted(requested_set)) if requested_set else "read_only,mutating,destructive"

    # Validate and normalise agent_mode.
    raw_agent_mode = data.agent_mode or AgentMode.AUTO.value
    valid_agent_modes = {m.value for m in AgentMode}
    if raw_agent_mode not in valid_agent_modes:
        logger.warning(
            "set_session: invalid agent_mode requested — falling back to auto",
            extra={"user_id": user_id, "invalid": raw_agent_mode},
        )
        raw_agent_mode = AgentMode.AUTO.value
    agent_mode = raw_agent_mode
    require_plan_approval = bool(data.require_plan_approval)
    max_step_extensions = int(data.max_step_extensions)

    # ── Long-Term Memory settings ──────────────────────────────────────────
    user_memory_enabled  = bool(data.user_memory_enabled)  if data.user_memory_enabled  is not None else True
    agent_scope_key      = data.agent_scope_key or None
    agent_memory_enabled = bool(data.agent_memory_enabled) if data.agent_memory_enabled is not None else True

    # ── Skills mode (V3.1) ────────────────────────────────────────────────
    _VALID_SKILLS_MODES = {"auto", "enabled", "disabled"}
    raw_skills_mode = (data.skills_mode or "auto").strip().lower()
    if raw_skills_mode not in _VALID_SKILLS_MODES:
        logger.warning(
            "set_session: invalid skills_mode requested — falling back to auto",
            extra={"user_id": user_id, "invalid": raw_skills_mode},
        )
        raw_skills_mode = "auto"
    skills_mode = raw_skills_mode

    # ── PreToolUse / PostToolUse Hooks (B-053) ────────────────────────────
    # Serialise HookDefinition objects → plain list-of-dicts for DB / session.
    tool_hooks_dicts = [
        h.model_dump(exclude_none=True)
        for h in (data.tool_hooks or [])
    ]

    # ── Shell safety (B-052) ─────────────────────────────────────────────
    shell_readonly = bool(data.shell_readonly) if data.shell_readonly is not None else False

    # ── PII Redaction (S-03) ──────────────────────────────────────────────
    pii_redaction_enabled = bool(data.pii_redaction_enabled) if data.pii_redaction_enabled is not None else False

    # ── Adaptable PEV runner (A-21) ───────────────────────────────────────
    pev_lookahead_skip_enabled = (
        bool(data.pev_lookahead_skip_enabled) if data.pev_lookahead_skip_enabled is not None else True
    )

    # ── Environment context (V5.5) ────────────────────────────────────────
    # Strip whitespace; treat empty string as None (not configured).
    user_timezone = (data.user_timezone or "").strip() or None
    user_location = (data.user_location or "").strip() or None
    user_language = (data.user_language or "").strip() or None

    # ------------------------------------------------------------------
    # V6.5: primary provider credential gate.
    # ------------------------------------------------------------------
    # Unlike fallback_chain entries above (dropped + warned), the primary
    # is what actually runs -- no configured API key here is a config
    # mistake, not a "warn and proceed" situation, so this hard-rejects.
    # This mirrors GET /providers/{provider}/models' existing 424 gate; it
    # closes the loophole where /set-session is called directly rather than
    # through the frontend's model picker, which already cannot offer an
    # unconfigured provider as a selectable option.
    # V6.59 Phase 4b: a provider EVA cannot use at all is a 422 with a
    # structured reason (see api/_provider_validation.py); the "key just not
    # stored yet" case keeps its established 424 below.
    _primary_problem = check_provider_usable(data.provider)
    if _primary_problem is not None and _primary_problem.reason != REASON_NOT_CONFIGURED:
        raise problem_to_http(_primary_problem, "provider")
    data.provider = _registry.canonical_name(data.provider)
    if not is_provider_key_configured(data.provider):
        raise HTTPException(
            status_code=424,
            detail=(
                f"API key for provider '{data.provider}' is not configured. "
                "Configure it via POST /secrets before setting it as your "
                "session provider."
            ),
        )

    # ------------------------------------------------------------------
    # Model capability validation
    # ------------------------------------------------------------------
    # Check whether the requested parameters are actually supported by the
    # chosen model.  If not, silently correct the value and include a
    # human-readable warning in the response so the client can surface it.

    # ── V4.3: Deprecation warning for legacy provider="litellm" alias ──
    if data.provider.lower().strip() == "litellm":
        warnings.append(
            "Provider 'litellm' is a deprecated alias. "
            "You can now specify the underlying provider directly "
            "(e.g. provider='anthropic', provider='groq'). "
            "The 'litellm' alias will continue working indefinitely."
        )
    # V6.7: reasoning is now a level string, not a bool -- validated in two
    # steps. (1) Binary gate: capability_registry's supports_reasoning,
    # unchanged in spirit from before. (2) Level gate: if the model
    # supports reasoning but only a subset of the unified vocabulary (or
    # can't disable it at all -- some newer Claude models), snap the
    # requested level to the nearest one it actually accepts rather than
    # rejecting outright -- same "snap to nearest" design OpenRouter uses
    # (see core/providers/reasoning_support.py).
    reasoning = data.reasoning

    capability = get_model(data.provider, data.model)
    if capability is not None:
        if reasoning is not None and not capability.supports_reasoning:
            reasoning = None
            warnings.append(
                f"Model '{data.model}' does not support reasoning (extended thinking). "
                "The 'reasoning' parameter has been automatically set to off."
            )
            logger.warning(
                "Capability mismatch: reasoning disabled for model that does not support it",
                extra={"user_id": user_id, "provider": data.provider, "model": data.model},
            )
    else:
        logger.debug(
            "No capability record found for model — skipping binary reasoning validation",
            extra={"provider": data.provider, "model": data.model},
        )

    if reasoning is not None and (capability is None or capability.supports_reasoning):
        from core.providers.reasoning_support import get_reasoning_support, snap_to_nearest_level  # noqa: PLC0415
        support = get_reasoning_support(data.provider, data.model)
        snapped = snap_to_nearest_level(reasoning, support.levels)
        if snapped != reasoning and snapped is not None:
            warnings.append(
                f"Model '{data.model}' does not support reasoning level '{reasoning}'. "
                f"Using the closest supported level, '{snapped}', instead."
            )
            logger.info(
                "Reasoning level snapped to nearest supported value",
                extra={"user_id": user_id, "provider": data.provider, "model": data.model,
                       "requested": reasoning, "snapped_to": snapped},
            )
        reasoning = snapped
    elif reasoning is None and capability is not None and capability.supports_reasoning:
        from core.providers.reasoning_support import get_reasoning_support  # noqa: PLC0415
        support = get_reasoning_support(data.provider, data.model)
        if not support.can_disable:
            reasoning = "medium"
            warnings.append(
                f"Model '{data.model}' always reasons and cannot be set to off. "
                "Defaulted to 'medium'."
            )
            logger.info(
                "Reasoning forced on for always-on model",
                extra={"user_id": user_id, "provider": data.provider, "model": data.model},
            )

    logger.info(
        "Session update requested",
        extra={
            "user_id": user_id,
            "provider": data.provider,
            "model": data.model,
            "tools_count": len(data.tools or []),
            "reasoning_enabled": reasoning,
            "fallback_chain_len": len(fallback_chain_dicts),
            "tool_approval_mode": tool_approval_mode,
            "allowed_authorities": allowed_authorities,
            "agent_mode": agent_mode,
            "require_plan_approval": require_plan_approval,
            "max_step_extensions": max_step_extensions,
            "capability_warnings": len(warnings),
        },
    )
    # Default tools list if not provided
    tools = data.tools if data.tools is not None else []

    # Persist to DB first so that if the write fails the in-memory session
    # is never updated (keeps the two stores consistent).
    async with AsyncSessionLocal() as db:
        logger.debug(
            "Persisting session preferences",
            extra={"user_id": user_id},
        )
        await DBRepo.upsert_user_preferences(
            db,
            user_id,
            {
                "provider": data.provider,
                "model": data.model,
                "reasoning": reasoning,
                # B-054: system_prompt no longer persisted at session level
                "tools": tools,
                "fallback_chain": fallback_chain_dicts,
                "tool_approval_mode": tool_approval_mode,
                "allowed_authorities": allowed_authorities,
                "agent_mode": agent_mode,
                "require_plan_approval": require_plan_approval,
                "max_step_extensions": max_step_extensions,
                "user_memory_enabled":  user_memory_enabled,
                "agent_scope_key":      agent_scope_key,
                "agent_memory_enabled": agent_memory_enabled,
                "skills_mode":          skills_mode,
                "tool_hooks":           tool_hooks_dicts,
                "shell_readonly":       shell_readonly,
                "user_timezone":        user_timezone,
                "user_location":        user_location,
                "user_language":        user_language,
                "pii_redaction_enabled": pii_redaction_enabled,
                "pev_lookahead_skip_enabled": pev_lookahead_skip_enabled,
            }
        )

    set_user_session(
        user_id,
        provider=data.provider,
        model=data.model,
        reasoning=reasoning,
        # B-054: system_prompt arg accepted but ignored (see config/settings.py)
        tools=tools,
        fallback_chain=fallback_chain_dicts,
        tool_approval_mode=tool_approval_mode,
        allowed_authorities=allowed_authorities,
        agent_mode=agent_mode,
        require_plan_approval=require_plan_approval,
        max_step_extensions=max_step_extensions,
        user_memory_enabled=user_memory_enabled,
        agent_scope_key=agent_scope_key,
        agent_memory_enabled=agent_memory_enabled,
        skills_mode=skills_mode,
        tool_hooks=tool_hooks_dicts,
        shell_readonly=shell_readonly,
        user_timezone=user_timezone,
        user_location=user_location,
        user_language=user_language,
        pii_redaction_enabled=pii_redaction_enabled,
        pev_lookahead_skip_enabled=pev_lookahead_skip_enabled,
    )

    logger.info(
        "Session updated successfully",
        extra={"user_id": user_id},
    )

    # ── SessionStart hooks (B-077 Bug 1) ──────────────────────────────────
    # Fire non-blocking, best-effort.  Failure must never block the response.
    if tool_hooks_dicts:
        asyncio.create_task(
            HookRunner.fire_event(
                event="session_start",
                hook_definitions=tool_hooks_dicts,
                user_id=user_id,
                session_id=user_id,          # no conversation yet; use user_id
                workspace_root=get_user_workspace(user_id),
            ),
        )

    return {
        "status": "session updated",
        "provider": data.provider,
        "model": data.model,
        "reasoning": reasoning,
        "tools_enabled": tools,
        "system_prompt": "",  # B-054: deprecated at session level; always empty
        "fallback_chain": fallback_chain_dicts,
        "tool_approval_mode": tool_approval_mode,
        "allowed_authorities": allowed_authorities,
        "agent_mode": agent_mode,
        "require_plan_approval": require_plan_approval,
        "max_step_extensions": max_step_extensions,
        "user_memory_enabled":  user_memory_enabled,
        "agent_scope_key":      agent_scope_key,
        "agent_memory_enabled": agent_memory_enabled,
        "skills_mode":          skills_mode,
        "tool_hooks":           tool_hooks_dicts,
        "shell_readonly":       shell_readonly,
        "user_timezone":        user_timezone,
        "user_location":        user_location,
        "user_language":        user_language,
        "pii_redaction_enabled": pii_redaction_enabled,
        "pev_lookahead_skip_enabled": pev_lookahead_skip_enabled,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Tool permission endpoints
# ---------------------------------------------------------------------------

class PermissionsUpdateRequest(BaseModel):
    """Request body for PATCH /session/permissions."""
    allowed_authorities: Optional[str] = None
    tool_approval_mode: Optional[str] = None


class PermissionsResponse(BaseModel):
    allowed_authorities: str
    tool_approval_mode: str


class PermissionsUpdateResponse(BaseModel):
    status: str
    allowed_authorities: str
    tool_approval_mode: str


@router.get("/session/permissions", response_model=PermissionsResponse)
async def get_permissions(auth=Depends(get_current_auth)):
    """
    Get the current user's tool permission settings.

    Returns:
        allowed_authorities : Comma-separated list of permitted tool authority
                              levels (read_only / mutating / destructive).
        tool_approval_mode  : Human-in-the-Loop gate setting.
    """
    user_id = auth["user_id"]
    session = get_user_session(user_id)

    return {
        "allowed_authorities": session.get("allowed_authorities", "read_only,mutating,destructive"),
        "tool_approval_mode": session.get("tool_approval_mode", ToolApprovalMode.AUTO.value),
    }


@router.patch("/session/permissions", response_model=PermissionsUpdateResponse)
async def update_permissions(
    body: PermissionsUpdateRequest,
    auth=Depends(get_current_auth),
):
    """
    Update the current user's tool permission settings without touching other
    session configuration.

    Accepted fields (all optional; omit to leave unchanged):
        allowed_authorities : Comma-separated authority levels to permit.
        tool_approval_mode  : "auto" | "always_ask" | "per_conversation"
    """
    user_id = auth["user_id"]

    # Fetch current session (defaults applied if session not yet initialised).
    session = get_user_session(user_id)

    # No hardcoded provider/model fallback (2026-07-27 cleanup): this
    # endpoint is scoped to permission-related fields only (see the upsert
    # warning below) and must never become the first thing that writes a
    # UserPreference row for a user -- that would silently create a
    # DB-persisted "" provider/model instead of the real value the user
    # will eventually choose via POST /set-session. Require a primary to
    # already exist rather than risk that.
    if not session.get("provider") or not session.get("model"):
        raise HTTPException(
            status_code=424,
            detail={
                "error_code": "provider_not_configured",
                "message": (
                    "No provider/model configured for this session. "
                    "Configure one via POST /set-session before changing permissions."
                ),
            },
        )

    # --- allowed_authorities ---
    if body.allowed_authorities is not None:
        requested_set = {a.strip() for a in body.allowed_authorities.split(",") if a.strip()}
        invalid = requested_set - _VALID_AUTHORITIES
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid authority values: {sorted(invalid)}. "
                    f"Allowed values: {sorted(_VALID_AUTHORITIES)}"
                ),
            )
        session["allowed_authorities"] = ",".join(sorted(requested_set))

    # --- tool_approval_mode ---
    if body.tool_approval_mode is not None:
        valid_modes = {m.value for m in ToolApprovalMode}
        if body.tool_approval_mode not in valid_modes:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid tool_approval_mode '{body.tool_approval_mode}'. "
                    f"Allowed values: {sorted(valid_modes)}"
                ),
            )
        session["tool_approval_mode"] = body.tool_approval_mode

    # Write back through the canonical hydration path so validation is applied.
    hydrate_user_session_from_db(user_id, session)

    # Persist to DB — intentionally writes only the permission-related subset of
    # UserPreference columns (provider, model, approval mode, authorities, etc.).
    # This is correct: PATCH /session/permissions is scoped to those fields only.
    #
    # WARNING FOR FUTURE DEVELOPERS: this upsert is NOT a full UserPreference
    # write. It omits: require_plan_approval, max_step_extensions, user_memory_enabled,
    # agent_scope_key, agent_memory_enabled, skills_mode, tool_hooks, shell_readonly,
    # user_timezone, user_location, user_language.  Those fields fall back to
    # ORM-level defaults on a first INSERT (if the user calls this endpoint
    # before /set-session).
    #
    # Rule: any new column added to UserPreference MUST satisfy at least one of:
    #   1. Has a SQLAlchemy-level default (default=... or nullable=True), OR
    #   2. Is added to this upsert dict below AND to the /set-session upsert.
    # Violating this will cause a NOT NULL constraint failure on this INSERT path
    # for users who call /session/permissions before /set-session.
    async with AsyncSessionLocal() as db:
        await DBRepo.upsert_user_preferences(
            db,
            user_id,
            {
                # provider/model guaranteed non-empty by the guard above --
                # no DEFAULT_SESSION fallback (2026-07-27 cleanup).
                "provider": session.get("provider"),
                "model": session.get("model"),
                "reasoning": session.get("reasoning", DEFAULT_SESSION["reasoning"]),
                "system_prompt": session.get("system_prompt", DEFAULT_SESSION["system_prompt"]),
                "tools": session.get("tools", DEFAULT_SESSION["tools"]),
                "fallback_chain": session.get("fallback_chain", DEFAULT_SESSION["fallback_chain"]),
                "tool_approval_mode": session.get("tool_approval_mode", DEFAULT_SESSION["tool_approval_mode"]),
                "allowed_authorities": session.get("allowed_authorities", DEFAULT_SESSION["allowed_authorities"]),
                "agent_mode": session.get("agent_mode", DEFAULT_SESSION["agent_mode"]),
            },
        )

    logger.info(
        "User permissions updated",
        extra={
            "user_id": user_id,
            "allowed_authorities": session.get("allowed_authorities"),
            "tool_approval_mode": session.get("tool_approval_mode"),
        },
    )

    return {
        "status": "permissions updated",
        "allowed_authorities": session.get("allowed_authorities", "read_only,mutating,destructive"),
        "tool_approval_mode": session.get("tool_approval_mode", ToolApprovalMode.AUTO.value),
    }


# =========================
# CDP-Embedded Preview Pane (browser automation)
# =========================
#
# Registers/unregisters live CDP endpoints for EVA's embedded browser
# preview pane -- see core/mcp/preview_registry.py for the in-memory
# registry itself and why it's kept separate from SessionConfig. These
# three endpoints are the only way that registry is ever written to; the
# actual pane, its relay, and the Electron BrowserView are built
# separately from this backend work.

async def _resync_playwright_if_enabled(user_id: str) -> None:
    """
    Hardcoded v1 relationship: a "preview" registration change (register OR
    unregister) resyncs the user's "playwright" MCP config, if they have
    one and it's currently enabled. A future multi-session feature with
    more than one named endpoint/config pair would need real dependency
    tracking instead of this -- not built until there's a second consumer.

    Mirrors PUT /mcp/{id}'s own resync, but uses bump_generation() +
    discard() (the same active-close pair api/mcp.py's DELETE handler
    uses) rather than bump_generation() alone: bump_generation() alone is
    lazy -- per core/mcp/session_pool.py's own docstring, it "does NOT
    force-cancel an in-flight call" and only takes effect on the *next*
    tool call. That would leave a standalone browser window a fallback
    connection may have already spawned sitting open indefinitely if no
    further tool call happens to trigger the swap. discard() closes it
    immediately, right now.

    sync_tools_for_config() afterward mirrors update_mcp_config()'s
    existing auto-sync-on-enable behaviour and doubles as a connectivity
    check for the freshly-resolved args (CDP-attach or fallback,
    whichever now applies) -- best-effort, never raises back to the
    caller, matching every other sync-on-write call site in this codebase.
    """
    async with AsyncSessionLocal() as db:
        cfg = await MCPRepo.get_config_by_name(db, user_id, "playwright")
        if not cfg or not cfg.enabled:
            return

        mcp_session_pool.bump_generation(cfg.id)
        await mcp_session_pool.discard(cfg.id)

        try:
            mgr = MCPManager(registry=None)
            await mgr.sync_tools_for_config(db, cfg)
        except Exception as exc:
            logger.warning(
                "Preview-pane resync: playwright tool sync failed (non-fatal)",
                extra={"user_id": user_id, "config_id": cfg.id, "error": str(exc)},
            )


@router.post("/session/preview-endpoints", response_model=StatusResponse)
async def register_preview_endpoint(
    payload: PreviewEndpointRequest,
    auth=Depends(get_current_auth),
):
    """
    Called once by Electron right after its CDP relay comes up. Upserts the
    named registration; if the user's "playwright" MCP config is currently
    enabled, immediately resyncs it onto the newly-registered endpoint (see
    _resync_playwright_if_enabled).
    """
    preview_registry.register(payload.name, payload.cdp_endpoint)
    await _resync_playwright_if_enabled(auth["user_id"])
    return {"status": "ok"}


@router.delete("/session/preview-endpoints/{name}", response_model=StatusResponse)
async def unregister_preview_endpoint(
    name: str,
    auth=Depends(get_current_auth),
):
    """
    Called when the pane closes or the app is shutting down, so the backend
    stops trying to attach to a now-dead address and falls back cleanly.
    Idempotent -- removing an already-absent name is not an error. Also
    resyncs "playwright" if enabled, the same as registering does, so a
    CDP-attached connection doesn't sit pointed at a relay that just closed.
    """
    preview_registry.unregister(name)
    await _resync_playwright_if_enabled(auth["user_id"])
    return {"status": "ok"}


@router.get("/session/preview-endpoints", response_model=PreviewEndpointsResponse)
async def list_preview_endpoints(auth=Depends(get_current_auth)):
    """
    Current registrations. Not load-bearing for the feature itself --
    useful for debugging what the backend currently thinks is registered.
    """
    return {
        "endpoints": {
            e.name: {"name": e.name, "cdp_endpoint": e.cdp_endpoint, "registered_at": e.registered_at}
            for e in preview_registry.list_all()
        }
    }
