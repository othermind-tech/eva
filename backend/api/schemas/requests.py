from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Dict, Optional, List

# Valid built-in hook handler names — used for validation in HookDefinition.
_VALID_PRE_HANDLERS  = {"audit_log", "deny_always", "path_guard", "rate_limit"}
_VALID_POST_HANDLERS = {"audit_log", "redact_env_vars", "redact_pii"}
_VALID_HOOK_HANDLERS = _VALID_PRE_HANDLERS | _VALID_POST_HANDLERS

# Valid hook types (B-064)
_VALID_HOOK_TYPES = {"builtin", "command", "http"}

# Valid hook events — expanded in B-064 from 2 → 5; model_call added in B-076
_VALID_HOOK_EVENTS = {"pre", "post", "session_start", "prompt_submit", "stop", "model_call"}


class HookDefinition(BaseModel):
    """
    A single hook entry stored in the user's session (B-053 / B-064).

    Fields
    ------
    tool    : Tool name to match, or "*" for all tools.
              Only meaningful for event="pre" and event="post".
    event   : "pre"           — fires before a tool executes (can deny or mutate args).
              "post"          — fires after a tool executes (can mutate result).
              "session_start" — fires when the user session is initialised.
              "prompt_submit" — fires when the user submits a chat message.
              "stop"          — fires when an execution completes.
    type    : "builtin"  — use a built-in named handler (default, backward-compatible).
              "command"  — run a user-supplied shell script stored in
                           ~/eva_workspace/{user_id}/hooks/{command}.
              "http"     — POST the hook payload to an external URL.

    For type="builtin":
        handler : Built-in handler name (audit_log / deny_always / path_guard /
                  redact_env_vars / redact_pii).
        config  : Handler-specific config dict.

    For type="command":
        command : Script filename (no path) in ~/eva_workspace/{user_id}/hooks/.
                  The script receives a JSON payload on stdin and must exit 0
                  (allow) or 2 (block).  stdout JSON may contain:
                    decision, reason, updated_input, additional_context.
        timeout : Max seconds to wait for the script (default 30).
        async_  : If true, fire-and-forget — result is ignored.
                  Allowed only for event="post"/"session_start"/"stop".
                  event="pre" ("prompt_submit") hooks are always blocking.

    For type="http":
        url     : HTTP endpoint to POST the payload JSON to.
        headers : Optional dict of extra request headers.
        timeout : Max seconds to wait (default 10).
        async_  : Same semantics as command async_.
    """
    tool:    str  = "*"
    event:   str  = "pre"
    type:    str  = "builtin"

    # type="builtin"
    handler: Optional[str]            = None
    config:  Optional[Dict[str, Any]] = None

    # type="command"
    command: Optional[str]  = None
    timeout: Optional[int]  = 30
    async_:  Optional[bool] = False

    # type="http"
    url:     Optional[str]            = None
    headers: Optional[Dict[str, str]] = None

    @field_validator("event")
    @classmethod
    def _validate_event(cls, v: str) -> str:
        if v not in _VALID_HOOK_EVENTS:
            raise ValueError(
                f"event must be one of {sorted(_VALID_HOOK_EVENTS)}, got '{v}'"
            )
        return v

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in _VALID_HOOK_TYPES:
            raise ValueError(
                f"type must be one of {sorted(_VALID_HOOK_TYPES)}, got '{v}'"
            )
        return v

    @model_validator(mode="after")
    def _validate_type_fields(self) -> "HookDefinition":
        if self.type == "builtin":
            if not self.handler:
                raise ValueError("type='builtin' requires a 'handler' field")
            if self.handler not in _VALID_HOOK_HANDLERS:
                raise ValueError(
                    f"Unknown built-in handler '{self.handler}'. "
                    f"Valid: {sorted(_VALID_HOOK_HANDLERS)}"
                )
        elif self.type == "command":
            if not self.command:
                raise ValueError("type='command' requires a 'command' field (script filename)")
            if "/" in self.command or "\\" in self.command or ".." in self.command:
                raise ValueError(
                    "command must be a plain filename with no path separators or '..'."
                )
            if self.async_ and self.event == "pre":
                raise ValueError(
                    "async_=true is not allowed for event='pre' — "
                    "PreToolUse hooks must be blocking to enforce policy."
                )
        elif self.type == "http":
            if not self.url:
                raise ValueError("type='http' requires a 'url' field")
            if self.async_ and self.event == "pre":
                raise ValueError(
                    "async_=true is not allowed for event='pre' — "
                    "PreToolUse hooks must be blocking to enforce policy."
                )
            # B-076 Gap 2: validate httpx is installed — HTTP hooks cannot run without it.
            try:
                import httpx  # noqa: F401
            except ImportError:
                raise ValueError(
                    "HTTP hooks require the 'httpx' package: pip install httpx. "
                    "Install it and restart EVA to use type='http' hooks."
                )
        return self


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    # Link this conversation to a project. Only used when conversation_id is
    # None (new conversation) — ignored if resuming an existing conversation.
    project_id: Optional[str] = None
    # Scope 3 — Agent memory: opt into a named shared scope for this conversation.
    # Overrides the session-level agent_scope_key for this specific request.
    agent_scope_key: Optional[str] = None


class FallbackChainEntry(BaseModel):
    """
    A single entry in the provider fallback chain.

    `api_key` is optional and normally omitted: the engine resolves the key
    from the OS credential store (providers.credentials), the same place
    Profile → API Keys writes to.

    V6.5: POST /set-session checks every entry against
    is_provider_key_configured() at save time. An entry whose provider has
    no resolvable key (explicit api_key here, else the stored key) is dropped
    rather than saved, with a warning in the response — AgentEngine's
    fallback loop trusts every entry it receives is usable and never
    re-checks credentials itself.
    """
    provider: str
    model: str
    api_key: Optional[str] = None


class SessionRequest(BaseModel):
    provider: str
    model: str
    # V6.7: level string ("minimal"/"low"/"medium"/"high"/"xhigh"), or
    # None/"off" to disable -- was a plain bool. See _normalize_reasoning
    # below for the legacy-bool transition shim.
    reasoning: Optional[str] = None
    tools: Optional[List[str]] = None
    system_prompt: Optional[str] = ""
    # Ordered list of {provider, model} fallbacks tried when the primary
    # provider raises rate_limit / timeout / provider_down / invalid_model /
    # content_policy, or auth (scoped -- see core/error_handling.py's
    # ERROR_POLICIES and core/agent_engine.py's skip_same_provider handling).
    # See FallbackChainEntry above for the save-time credential check.
    #
    # V6.5 Phase 6: capped at 5 entries. A longer chain means more
    # retry+backoff latency the user has to wait through before an
    # execution can even reach an error, let alone a response -- 5 keeps
    # the worst case bounded. Enforced here (422, before the handler even
    # runs) rather than in api/session.py, since -- unlike the credential
    # gate or dedup below -- there's no partial-acceptance story that makes
    # sense for "the request itself asked for too many": the frontend is
    # expected to disable adding a 6th entry, so this is a hard backstop,
    # not a normal user-facing validation path.
    fallback_chain: Optional[List[FallbackChainEntry]] = Field(default=None, max_length=5)
    # Human-in-the-Loop tool approval setting.
    # "auto"             – never require approval (default)
    # "always_ask"       – ask before every mutating/destructive tool call
    # "per_conversation" – ask once per conversation, then auto-approve
    tool_approval_mode: Optional[str] = "auto"
    # Comma-separated list of permitted tool authority levels.
    # Any subset of: "read_only", "mutating", "destructive"
    # Default: all levels permitted ("read_only,mutating,destructive")
    allowed_authorities: Optional[str] = "read_only,mutating,destructive"
    # Intent routing mode.
    # "auto"         – IntentRouter decides dynamically (default)
    # "chat"         – always treat messages as conversational
    # "plan_execute" – always route through Plan→Execute→Verify loop
    agent_mode: Optional[str] = "auto"
    # When True, the PEV runner pauses after generating the plan and waits for
    # the user to call POST /executions/{id}/plan-decision (decision="approve"
    # | "edit" | "reject" — UX-01) before executing.
    # Defaults to False (auto-proceed without explicit approval).
    require_plan_approval: Optional[bool] = False
    # Maximum number of extra step extensions the agent may request during
    # execution before being forced to conclude. Defaults to 2.
    max_step_extensions: Optional[int] = 2
    # ── Long-Term Memory settings (V3.0) ──────────────────────────────────
    # Scope 2 — User
    user_memory_enabled:  Optional[bool] = True
    # Scope 3 — Agent
    agent_scope_key:      Optional[str]  = None
    agent_memory_enabled: Optional[bool] = True
    # ── Skills mode (V3.1) ────────────────────────────────────────────────
    # "auto"     — inject available_skills block if any skills are loaded (default)
    # "enabled"  — same as auto; user has explicitly opted in
    # "disabled" — never inject (zero token overhead)
    skills_mode: Optional[str] = "auto"
    # ── Hook definitions (B-053 / B-064) ─────────────────────────────────
    # Ordered list of hook definitions.  Hooks are evaluated in list order.
    # A pre-hook denial short-circuits all subsequent hooks and blocks the call.
    # Default: [] (no hooks)
    tool_hooks: Optional[List[HookDefinition]] = None
    # ── Shell safety (B-052) ─────────────────────────────────────────────
    # When True, the shell tool blocks all non-read-only commands.
    # Useful for inspection/review sessions where filesystem changes are not allowed.
    # Default: False
    shell_readonly: Optional[bool] = False
    # ── Environment context (V5.5) ────────────────────────────────────────
    # Optional user-configured fields injected into every system prompt.
    # user_timezone — IANA tz identifier (e.g. "Asia/Kolkata").
    # user_location — free-text city/country (e.g. "Mumbai, India").
    # user_language — BCP-47 tag (e.g. "en-IN"). Omit for default (en-US).
    user_timezone: Optional[str] = None
    user_location: Optional[str] = None
    user_language: Optional[str] = None
    # ── PII Redaction (S-03) ──────────────────────────────────────────────
    # When True, the user's own chat message is scrubbed of PII (email,
    # phone, SSN, credit card, etc.) before it is persisted or sent to the
    # LLM. Off by default -- opt-in. Tool-output redaction is separate and
    # already opt-in via a "redact_pii" entry in tool_hooks.
    pii_redaction_enabled: Optional[bool] = False
    # ── Adaptable PEV runner (A-21) ─────────────────────────────────────────
    # When True (the default -- opt-out, unlike most booleans here), the PEV
    # per-step verifier also checks whether a step's evidence already
    # satisfies upcoming steps, and the runner skips invoking the agent for
    # them. Never skips the plan's own final step regardless of this setting.
    pev_lookahead_skip_enabled: Optional[bool] = True

    @field_validator("reasoning", mode="before")
    @classmethod
    def _normalize_reasoning(cls, v):
        """
        V6.7: accepts a level string, None, or "off". Also accepts a
        legacy bool (True -> "medium", False -> None) as a transition
        shim for a frontend that hasn't updated yet -- not a permanent
        dual-type contract, just avoids a hard break mid-rollout.
        """
        if isinstance(v, bool):
            return "medium" if v else None
        if v in (None, "off", ""):
            return None
        from core.providers.reasoning_support import REASONING_LEVELS  # noqa: PLC0415
        if v not in REASONING_LEVELS:
            raise ValueError(
                f"reasoning must be one of {REASONING_LEVELS}, \"off\", or null — got {v!r}"
            )
        return v


class PreviewEndpointRequest(BaseModel):
    """
    CDP-embedded preview pane (browser automation): registers or updates a
    named live CDP endpoint. Called once by Electron right after its relay
    comes up — see core/mcp/preview_registry.py for the in-memory registry
    this upserts into and why it's deliberately not part of SessionRequest.
    """
    name: str
    cdp_endpoint: str


class SetKeepAliveOnAppShutdownRequest(BaseModel):
    """
    Session-scoped background processes: the Activity panel's per-process
    "keep running after EVA closes" toggle. See
    core/db/models.py::BackgroundProcess.keep_alive_on_app_shutdown.
    """
    keep_alive_on_app_shutdown: bool
