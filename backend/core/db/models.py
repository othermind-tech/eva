# core/db/models.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, DateTime, Text, Integer, Boolean, JSON, ForeignKey, Float,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def new_uuid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=new_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # V6.0 user profile fields
    full_name   = Column(String, nullable=True)   # formal full name, e.g. "Alex Morgan"
    description = Column(Text,   nullable=True)   # short bio / about text
    # is_active (user-deactivation flag) removed B1-2.1 (2026-08-19, audit
    # finding): dated from an earlier web-hosted/multi-tenant design phase,
    # never enforced anywhere in application code. If EVA moves toward a
    # hosted multi-user mode again (strategic report Pillar 3), reinstate a
    # real is_active column then -- see alembic/versions/0008_drop_user_is_active_and_auth_audit_event_metadata.py.

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), index=True)

    user_agent = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True))
    # B2-3.3 (2026-08-18): indexed -- purge_expired_sessions() (DB-01,
    # core/db/repository.py) filters on this column every 24h; without an
    # index this becomes a full table scan as the table grows between
    # purges.
    revoked = Column(Boolean, default=False, index=True)
    # ── Refresh token columns (B-010) ────────────────────────────────────────
    # Unique token for refreshing the access token without re-logging in.
    # Rotated on each use (invalidate old token, issue new one).
    refresh_token = Column(String, nullable=True, unique=True, index=True)
    # B2-3.3 (2026-08-18): indexed, same rationale as `revoked` above --
    # also filtered by purge_expired_sessions() on every daily sweep.
    refresh_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)


class AuthAuditLog(Base):
    __tablename__ = "auth_audit_logs"

    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    session_id = Column(String, ForeignKey("auth_sessions.id"), nullable=True)

    event_type = Column(String, nullable=False)

    ip_address = Column(String, nullable=True)
    user_agent = Column(Text, nullable=True)

    # B2-3.2 (2026-08-18): indexed -- purge_old_audit_logs() (DB-01,
    # core/db/repository.py) filters on this column every 24h, and it's
    # the one column filtered on every single scheduled run for the life
    # of the deployment as the audit log accumulates rows. Without an
    # index the daily purge does an increasingly full table scan.
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

# DB-09 (2026-09-14): SessionModel/"sessions" table removed -- dead code,
# never written or read by any live path. Conversation.session_id's real
# values have always come from AuthSession.id (auth/dependencies.py resolves
# it from the JWT "sid" claim; see api/chat.py) via a FK that pointed at this
# unused table instead. Repointed below to auth_sessions.id to reflect what
# the column actually stores. See migration 0009_drop_sessions_table.py.

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True, default=new_uuid)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, ForeignKey("auth_sessions.id"), nullable=True)
    title = Column(String, nullable=True)
    # B-045: optional link to a Project (enables project-mode context injection)
    project_id = Column(String, nullable=True, index=True)
    # B-054: per-conversation system prompt — replaces session-level system prompt
    system_prompt = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    messages = relationship("Message", back_populates="conversation")

class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True, default=new_uuid)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String, nullable=False)  # user | assistant | tool | system
    content = Column(Text, nullable=True)  # final aggregated content
    reasoning = Column(Text, nullable=True)  # optional hidden reasoning
    type = Column(String, nullable=True)  # text | reasoning | tool_call | error etc (optional)
    tool_name = Column(String, nullable=True)
    tool_args = Column(JSON, nullable=True)
    tool_result = Column(JSON, nullable=True)
    finish_reason = Column(String, nullable=True)
    chunked = Column(Boolean, default=False)  # indicates streamed originally
    # Monotonically increasing position of this message within its conversation.
    # Set by add_message() at insert time using SELECT COUNT(*).  Nullable so
    # that rows inserted before this column existed are not affected.
    # Sort key priority: sequence (non-null) → created_at (fallback for old rows).
    sequence = Column(Integer, nullable=True, index=True)
    # NOTE: use a lambda so the expression is evaluated per-insert, not once
    # at class-definition time (a common Python/SQLAlchemy gotcha).
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")
    tool_calls = relationship("ToolCall", uselist=True, back_populates="message")


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id = Column(String, primary_key=True, default=new_uuid)
    message_id = Column(String, ForeignKey("messages.id"), nullable=False, index=True)
    tool_name = Column(String, nullable=False)
    arguments = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    message = relationship("Message", back_populates="tool_calls")


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id = Column(String, primary_key=True, default=new_uuid)

    user_id = Column(String, nullable=False, index=True)
    conversation_id = Column(
        String,
        ForeignKey("conversations.id"),
        nullable=False,
        index=True,
        unique=True,
    )
    summary_text = Column(Text, nullable=False)
    last_message_id = Column(String, nullable=False)
    token_estimate = Column(Integer, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id = Column(String, primary_key=True)

    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)

    # V6.7: level string ("minimal"/"low"/"medium"/"high"/"xhigh"), NULL =
    # off. Was Boolean -- see alembic/versions/0002_reasoning_level.py for
    # the data-preserving migration (True -> "medium", False -> NULL).
    reasoning = Column(String, nullable=True, default=None)

    system_prompt = Column(Text, nullable=True)
    tools = Column(JSON, nullable=False, default=list)

    # Ordered list of fallback entries: [{provider, model, api_key?}, ...]
    # api_key is optional per entry; if absent the engine resolves from env.
    #
    # nullable=True BY DESIGN — a user may choose not to configure a fallback
    # chain at all, which is a valid and common state.  Rows created before this
    # column was added (via ALTER TABLE) will have NULL here.
    # CONVENTION: every read site MUST use `prefs.fallback_chain or []` to
    # normalise NULL and the empty-list default to the same value.
    fallback_chain = Column(JSON, nullable=True, default=list)

    # Human-in-the-Loop tool approval preference.
    # Values: "auto" | "always_ask" | "per_conversation"  (see ToolApprovalMode)
    tool_approval_mode = Column(String, default="auto", nullable=False)

    # Allowed tool authority levels (comma-separated).
    # Controls which tool side-effect classes the user permits.
    # Values: any subset of "read_only,mutating,destructive"
    # Default: all levels allowed.
    allowed_authorities = Column(
        String,
        default="read_only,mutating,destructive",
        nullable=False,
    )

    # Intent routing mode: "auto" | "chat" | "plan_execute"
    # "auto" = 3-layer heuristic+LLM routing; "chat" = always conversational;
    # "plan_execute" = always route to Plan→Execute→Verify loop.
    agent_mode = Column(String, default="auto", nullable=False)

    # Whether the user must explicitly approve the generated plan before
    # execution begins.  Added via ALTER TABLE migration in main.py.
    require_plan_approval = Column(Boolean, default=False, nullable=False)

    # Maximum number of extra step extensions the agent may request during
    # execution before being forced to conclude.  Added via Alembic migration.
    max_step_extensions = Column(Integer, default=2, nullable=False)

    # ── Long-Term Memory Settings (added V3.0) ────────────────────────────
    # Scope 2 — User: inject extracted user-level facts (preferences, style).
    user_memory_enabled  = Column(Boolean, default=True,  nullable=False)

    # Scope 3 — Agent: shared memory across conversations in the same mission.
    # agent_scope_key is a user-defined string key (e.g. "eva-backend-dev").
    agent_scope_key      = Column(String,  nullable=True)
    agent_memory_enabled = Column(Boolean, default=True,  nullable=False)

    # ── Skills Mode (added V3.1) ────────────────────────────────────────
    # Controls whether the available_skills block is injected into the system prompt.
    # "auto"     — inject if any skills are loaded (default)
    # "enabled"  — same as auto; user has explicitly opted in
    # "disabled" — never inject (zero token overhead)
    skills_mode = Column(String, default="auto", nullable=False)

    # ── PreToolUse / PostToolUse Hooks (B-053) ──────────────────────────
    # JSON list of hook definitions.  Each entry:
    # {
    #   "tool":    "<tool_name>" | "*",   # exact name or wildcard
    #   "event":   "pre" | "post",
    #   "handler": "<built-in handler name>",
    #   "config":  {<handler-specific config>}  # optional
    # }
    # Built-in handlers: audit_log, deny_always, path_guard, redact_env_vars, redact_pii
    # Default: [] (no hooks)
    tool_hooks = Column(JSON, nullable=True, default=list)

    # ── Shell safety (B-052) ─────────────────────────────────────────────
    # When True, the shell tool blocks all non-read-only commands.
    # Useful for review/inspection sessions where no filesystem changes should occur.
    shell_readonly = Column(Boolean, default=False, nullable=False, server_default="0")

    # ── Environment context (V5.5) ────────────────────────────────────────
    # Injected into the system prompt on every execution so EVA knows the
    # user's timezone, location, and language without relying on conversation
    # memory.  All fields are optional — omit → not shown in the prompt.
    #
    # user_timezone — IANA timezone identifier (e.g. "Asia/Kolkata").
    #   Used to convert the server's UTC clock to the user's local time.
    # user_location — Free-text city/country (e.g. "Mumbai, India").
    #   Included in the environment block so web searches / weather queries
    #   default to the right place without the user repeating it.
    # user_language — BCP-47 language tag (e.g. "en-IN", "hi-IN").
    #   Injected when non-default so EVA can format dates, units, etc.
    user_timezone = Column(String, nullable=True)
    user_location = Column(String, nullable=True)
    user_language = Column(String, nullable=True)

    # ── PII Redaction (S-03) ────────────────────────────────────────────
    # When True, the user's own chat message is scrubbed of PII (email,
    # phone, SSN, credit card, etc. -- see core/security/pii_redactor.py's
    # DEFAULT_PII_ENTITIES) before it is persisted or sent to the LLM.
    # Off by default -- opt-in, matching S-03's existing scope. Tool-output
    # redaction is separate and already opt-in via a "redact_pii" entry in
    # tool_hooks; this flag only covers the user's own typed message, which
    # cannot go through tool_hooks (there's no tool call involved).
    pii_redaction_enabled = Column(Boolean, default=False, nullable=False, server_default="0")

    # A-21: when True (the default -- opt-out, not opt-in, unlike pii_redaction_enabled above),
    # the PEV per-step verifier also checks whether a step's evidence already satisfies one or
    # more of the immediately-following steps, and the runner skips invoking the agent for them.
    # Never skips the plan's own final step regardless of this setting (see pev_runner.py).
    pev_lookahead_skip_enabled = Column(Boolean, default=True, nullable=False, server_default="1")

    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

# ===============================
# MCP (Model Context Protocol)
# ===============================

class MCPConfig(Base):
    """
    Per-user MCP server configuration.
    Supports STDIO, HTTP, and SSE transports.

    project_id     — NULL = global (available in all user contexts).
                     non-NULL = scoped to a specific project; only surfaced
                     when executing within that project's context.
    project_paused — True when the config was disabled by a project
                     pause/archive cascade.  Distinct from `enabled` so we
                     can restore cascade-disabled MCPs automatically when
                     the project is reactivated, without touching MCPs the
                     user manually disabled.
    """
    __tablename__ = "mcp_configs"

    id = Column(String, primary_key=True, default=new_uuid)

    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)

    # Optional project scope.  None = global user-level MCP.
    project_id = Column(String, nullable=True, index=True)

    # Friendly name (e.g. "kite", "github", "yfmcp")
    name = Column(String, nullable=False)

    # User-controlled enabled/disabled flag
    enabled = Column(Boolean, default=True, nullable=False)

    # Set True by project cascade (pause/archive); cleared on project resume.
    # Never touched by the user directly.
    project_paused = Column(Boolean, default=False, nullable=False)

    # Transport type: "stdio", "http", or "sse"
    transport = Column(String, nullable=False)

    # ----- STDIO fields -----
    command = Column(String, nullable=True)
    args = Column(JSON, nullable=True)

    # Environment variables for STDIO server (optional)
    env = Column(JSON, nullable=True)

    # Working directory (for STDIO server)
    cwd = Column(String, nullable=True)

    # Optional .env file path
    env_file = Column(String, nullable=True)

    # ----- HTTP/SSE fields -----
    url = Column(String, nullable=True)

    # Optional headers (for HTTP/SSE)
    headers = Column(JSON, nullable=True)

    # ── Credential security (S-11) ──────────────────────────────────────
    # Secret-shaped keys in `env`/`headers` (matched by
    # core/mcp/credentials.py's is_secret_key()) have their real value
    # moved to the OS keyring and replaced in-place with a redaction
    # placeholder -- env/headers on this row (and every API response) only
    # ever hold the placeholder. This column is the list of
    # {"key": ..., "credential_ref": "mcp:{config_id}:{key}"} entries
    # marking which keys were extracted, so the real value can be resolved
    # back from keyring only at actual connection time.
    # NULL (not []) is a real, load-bearing sentinel: it means this row
    # predates S-11 and hasn't been migrated yet -- the startup migration
    # pass (main.py's lifespan) uses that distinction to know which rows
    # still need a pass, vs. an already-migrated row that simply has no
    # secret-shaped keys (a real, persisted []).
    credential_refs = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tools = relationship(
        "MCPToolMeta",
        back_populates="config",
        cascade="all, delete-orphan",
    )


class MCPToolMeta(Base):
    """
    Cached MCP tool metadata discovered from a server.
    """
    __tablename__ = "mcp_tool_meta"

    id = Column(String, primary_key=True, default=new_uuid)

    config_id = Column(
        String,
        ForeignKey("mcp_configs.id"),
        index=True,
        nullable=False,
    )

    # Tool name as reported by MCP server
    name = Column(String, nullable=False)

    description = Column(Text, nullable=True)

    input_schema = Column(JSON, nullable=True)
    output_schema = Column(JSON, nullable=True)

    # B1-2.3 (2026-08-19): onupdate added so this refreshes on the
    # update-branch of MCPRepo.upsert_tool_meta() too, not just on initial
    # insert -- today's sync flow always deletes-then-reinserts (so the
    # insert-time default already produces a correct value on every sync),
    # but onupdate makes this self-correct if that strategy ever changes to
    # a true incremental upsert without needing another code change here.
    last_synced_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    config = relationship("MCPConfig", back_populates="tools")

    __table_args__ = (
        # Prevent duplicate tools per MCP server
        UniqueConstraint("config_id", "name", name="uq_mcp_tool_config_name"),
    )


class MCPResourceMeta(Base):
    """
    Cached MCP resource metadata discovered from a server.

    Resources are addressable data items (files, database rows, API responses,
    etc.) that an MCP server exposes via the resources capability.  Each
    resource has a URI that can be passed to read_resource() to fetch its
    content at runtime.
    """
    __tablename__ = "mcp_resource_meta"

    id = Column(String, primary_key=True, default=new_uuid)

    config_id = Column(
        String,
        ForeignKey("mcp_configs.id"),
        index=True,
        nullable=False,
    )

    # Resource URI as reported by the MCP server (e.g. "file:///repo/README.md")
    uri = Column(String, nullable=False)

    # Human-readable name
    name = Column(String, nullable=True)

    description = Column(Text, nullable=True)

    # MIME type hint (e.g. "text/plain", "application/json")
    mime_type = Column(String, nullable=True)

    # B1-2.3: see MCPToolMeta.last_synced_at above for the onupdate rationale.
    last_synced_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    config = relationship("MCPConfig", backref="resources")


class MCPPromptMeta(Base):
    """
    Cached MCP prompt template metadata discovered from a server.

    Prompts are named, parameterised message templates that an MCP server
    exposes via the prompts capability.  Clients render them by calling
    get_prompt(name, arguments={...}).
    """
    __tablename__ = "mcp_prompt_meta"

    id = Column(String, primary_key=True, default=new_uuid)

    config_id = Column(
        String,
        ForeignKey("mcp_configs.id"),
        index=True,
        nullable=False,
    )

    # Prompt template name as reported by the MCP server
    name = Column(String, nullable=False)

    description = Column(Text, nullable=True)

    # JSON list of {name, description, required} argument descriptors
    arguments = Column(JSON, nullable=True)

    # B1-2.3: see MCPToolMeta.last_synced_at above for the onupdate rationale.
    last_synced_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    config = relationship("MCPConfig", backref="prompts")


# ===============================
# Execution Metrics
# ===============================

class ExecutionMetrics(Base):
    """
    One row per execution.  Written by the worker after run_execution()
    returns (terminal state), sourcing data from:
      - MetricsCollector (event-bus accumulators): tool call counts,
        latencies, fallbacks, total_steps, duration, termination_reason.
      - Worker session context: model_used, provider_used.
      - Finalizer closure: prompt/completion/reasoning tokens.

    Primary key is execution_id (UUID string) — never changes.
    The endpoint GET /executions/{id}/metrics returns this record.
    """
    __tablename__ = "execution_metrics"

    # ── Identity ──────────────────────────────────────────────────────
    execution_id    = Column(String, primary_key=True)
    conversation_id = Column(
        String,
        ForeignKey("conversations.id"),
        index=True,
        nullable=False,
    )
    user_id = Column(String, index=True, nullable=False)

    # ── Execution outcome ─────────────────────────────────────────────
    # Mirrors ExecutionStatus enum values: completed/failed/cancelled/aborted
    status             = Column(String,  nullable=False)
    # "success" | error_type (e.g. "RuntimeError") | "user" | "task_cancelled"
    termination_reason = Column(String,  nullable=True)
    # Full exception message text (str(exc)) — None on success/cancel.
    # Complements termination_reason: reason gives the class/category,
    # error_message gives the human-readable detail.
    error_message      = Column(Text,    nullable=True)
    duration_ms        = Column(Integer, nullable=True)

    # ── Provider / model ──────────────────────────────────────────────
    model_used    = Column(String, nullable=True)
    provider_used = Column(String, nullable=True)

    # ── Token counts (from UsageChunk / finalizer) ────────────────────
    prompt_tokens     = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    reasoning_tokens  = Column(Integer, default=0, nullable=False)
    # Subset of prompt_tokens served from the provider's prompt cache (V6.59
    # Phase 6). prompt_tokens is the TOTAL input; reasoning_tokens is likewise a
    # SUBSET of completion_tokens -- so total = prompt + completion.
    cached_tokens     = Column(Integer, default=0, nullable=False, server_default="0")

    # ── ReAct / step metrics ──────────────────────────────────────────
    # Number of distinct tool invocations (attempt==1 ToolCalled events).
    # 0 for a direct single-response (no tools) execution.
    total_steps         = Column(Integer, default=0, nullable=False)
    fallbacks_triggered = Column(Integer, default=0, nullable=False)

    # ── Cost intelligence (V4.3) ──────────────────────────────────────
    # USD cost of the call, computed via litellm.cost_per_token().
    # None when pricing data is unavailable (e.g. Ollama = 0.0).
    computed_cost_usd = Column(Float, nullable=True)

    # ── Per-tool breakdown ────────────────────────────────────────────
    # JSON list matching ToolCallMetric schema:
    # [{"tool_name": str, "call_count": int, "avg_latency_ms": int,
    #   "failure_count": int, "success_count": int,
    #   "last_denial_reason": str | null}, ...]
    tool_metrics = Column(JSON, nullable=True)

    # ── Provider fallback detail (Gap 8) ─────────────────────────────
    # JSON list of per-fallback dicts from ProviderFallbackTriggered events:
    # [{"failed_provider": str, "failed_model": str, "failure_reason": str,
    #   "fallback_provider": str, "fallback_model": str,
    #   "fallback_index": int}, ...]
    fallback_details = Column(JSON, nullable=True)

    # ── Subagent stats (Gap 10) ───────────────────────────────────────
    # Counts of SubagentCompleted events observed within this execution.
    subagent_calls    = Column(Integer, default=0, nullable=False)
    subagent_failures = Column(Integer, default=0, nullable=False)

    # ── Execution source / lineage (B-057) ───────────────────────────
    # triggered_by: top-level origin — "conversation" | "daemon"
    # Subagents are never top-level; use parent_execution_id for chain.
    triggered_by       = Column(String,  nullable=True)
    # daemon_name: set when triggered_by="daemon"
    daemon_name        = Column(String,  nullable=True)
    # subagent_name: set when this execution IS a subagent run
    subagent_name      = Column(String,  nullable=True)
    # project_id: copied from conversation.project_id or daemon.project_id
    # at write time — enables direct per-project execution queries
    project_id         = Column(String,  nullable=True, index=True)
    # parent_execution_id: links child execution back to the parent that
    # spawned it (e.g. orchestrator → subagent, daemon → sub-daemon)
    parent_execution_id = Column(String, nullable=True)

    # ── Timestamps ───────────────────────────────────────────────────
    created_at   = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # started_at (Gap 11): actual execution start time (RUNNING transition).
    # Distinct from created_at which is when the metrics row was inserted.
    # For daemon runs set in phase-1 INSERT; for regular runs set by worker.
    started_at   = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


# ===============================
# Plan → Execute → Verify (PEV)
# ===============================

class ExecutionPlan(Base):
    """
    One row per plan_execute execution.  Written by the PlannerEngine at the
    start of the PEV loop and updated by the VerifierEngine after each step.

    success_criteria column schema (JSON list, A-20): ["<checkable statement>", ...]
    -- 1-4 entries, see prompts/planner.py. Nullable for rows predating this field.

    steps column schema (JSON list):
    [
      {
        "index": 0,
        "title": "Short step title",
        "description": "Detailed description of what to do",
        "tool_hint": "<short category, e.g. 'file write', 'shell/code execution', 'web search'> | null",
        "status": "pending | running | completed | failed | skipped",
        "result_summary": null | "brief outcome text",
        "verified": false,
        "verification_notes": null | "verifier feedback"
      },
      ...
    ]
    """
    __tablename__ = "execution_plans"

    id = Column(String, primary_key=True, default=new_uuid)

    # Parent execution (1:1 with execution_metrics)
    execution_id = Column(
        String,
        nullable=False,
        index=True,
        unique=True,
    )
    conversation_id = Column(
        String,
        ForeignKey("conversations.id"),
        nullable=False,
        index=True,
    )
    user_id = Column(String, nullable=False, index=True)

    # LLM-generated one-sentence goal
    goal = Column(Text, nullable=False)

    # A-20: 1-4 checkable statements of what "done" means for this plan
    # (prompts/planner.py's success_criteria rule); nullable -- rows from
    # before this field existed have none, and PlanVerifierEngine's own
    # fallback (treat the goal as the sole criterion) already handles that
    # case at read time, not at the DB layer.
    success_criteria = Column(JSON, nullable=True)

    # Ordered list of step dicts (schema above)
    steps = Column(JSON, nullable=False, default=list)

    # Overall plan status: pending | executing | completed | failed
    status = Column(String, default="pending", nullable=False)

    # Index of the step currently being executed (0-based)
    current_step_index = Column(Integer, default=0, nullable=False)

    # A-20: PlanVerifierEngine's holistic verdict on the FINISHED plan,
    # written once, right after that check runs (core/execution/pev_runner.py).
    # All nullable: None means the check never ran for this row (predates
    # the field, or the run ended via an early-return path that skips it --
    # cancelled, plan rejected, AWAITING_WAKEUP), never "ran and found
    # nothing". Before this, the verdict only ever existed as an SSE-only
    # StepProgressChunk -- confirmed gone from the shadow buffer shortly
    # after a run completed, with no durable trace anywhere.
    goal_verdict = Column(String, nullable=True)               # "success" | "pending" | "not_successful"
    goal_verification_message = Column(Text, nullable=True)    # "" on "success"; required by the prompt otherwise
    goal_criteria_results = Column(JSON, nullable=True)         # [{"criterion": str, "met": true|false|null}, ...]

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ScheduledWakeup(Base):
    """
    P-19 Phase 1 — persisted "resume me later" record.

    Written by the schedule_wakeup tool when an agent pauses an execution
    instead of blocking on it. Resolved by core/execution/wakeup_scheduler.py's
    poll loop, either when `wake_at` elapses or when `watch_execution_id`
    (if set) reaches a terminal state -- whichever comes first. `wake_at` is
    always set even when `watch_execution_id` is the primary trigger, as the
    fallback ceiling so "wake me when X finishes" can never hang forever.

    Deliberately not a live in-memory wait: this row is what makes the
    wakeup restart-safe -- if the server is down when wake_at passes, the
    next poll after restart just sees it's overdue and fires late.
    """
    __tablename__ = "scheduled_wakeups"

    id = Column(String, primary_key=True, default=new_uuid)

    # The paused execution that requested this wakeup (1:1 -- an execution
    # calls schedule_wakeup at most once, since the call ends its own loop).
    execution_id = Column(String, nullable=False, index=True, unique=True)
    conversation_id = Column(
        String, ForeignKey("conversations.id"), nullable=False, index=True
    )
    user_id = Column(String, nullable=False, index=True)

    # Fixed-time trigger. Always set (see class docstring) even when
    # watch_execution_id is the intended primary trigger.
    wake_at = Column(DateTime(timezone=True), nullable=False, index=True)

    # Event trigger: resolve as soon as this execution_id reaches a terminal
    # state, without waiting for wake_at. Nullable -- pure time-based
    # wakeups have no execution to watch.
    watch_execution_id = Column(String, nullable=True, index=True)

    # What the paused agent said mattered for when it wakes up -- carried
    # forward into the resume trigger message for the new, continuing
    # execution. The deliberate replacement for true mid-loop state
    # resume (see the plan doc's "resumption model" discussion).
    reason = Column(Text, nullable=False)

    # pending | resolved | cancelled
    status = Column(String, default="pending", nullable=False, index=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # execution_id of the new, continuing execution created on resolution --
    # None until resolved. Lets a caller trace "what actually continued
    # this" without guessing from conversation_id + timestamp.
    resumed_execution_id = Column(String, nullable=True)


# ===============================
# Long-Term Memory (V3.0)
# ===============================

class UserMemoryFact(Base):
    """
    Scope 2 — User-level memory.
    Stores facts, preferences, and inferences about the human user.
    These are NOT conversation-specific; they persist across all conversations.

    Examples:
      "Prefers concise responses — no fluff"
      "Uses conda environments (conda activate myenv)"
      "Primary language: Python 3.11, typed, functional style"
    """
    __tablename__ = "user_memory_facts"

    id           = Column(String,  primary_key=True, default=new_uuid)
    user_id      = Column(String,  nullable=False, index=True)
    fact         = Column(Text,    nullable=False)
    # preference | skill | style | context | misc
    category     = Column(String,  nullable=True)
    # Semantic slot key — see core/memory/fact_summary.py for vocabulary.
    # Singular slots (user.occupation, user.name, …) enforce one-fact-per-slot.
    # Plural slots (user.skill, user.preference, …) allow multiple facts.
    slot_key     = Column(String,  nullable=True, index=True)
    # auto_extracted | manual
    source       = Column(String,  nullable=False, default="manual")
    # 0.0–1.0; lower for inferred/uncertain facts
    confidence   = Column(Float,   nullable=False, default=1.0)
    created_at   = Column(DateTime(timezone=True),
                          default=lambda: datetime.now(timezone.utc),
                          nullable=False)
    updated_at   = Column(DateTime(timezone=True), nullable=True)
    expires_at   = Column(DateTime(timezone=True), nullable=True)


class AgentMemoryEntry(Base):
    """
    Scope 3 — Agent-level shared memory.
    Stores cross-conversation entries for a named mission scope (agent_scope_key).
    Multiple conversations sharing the same scope key read/write the same pool.

    Entry types:
      error     — what failed and why (to avoid repeating)
      success   — what worked well (to replicate)
      decision  — architectural/design choices made
      state     — current status of an ongoing task
      artifact  — reference to a produced file/schema/output
      note      — general note worth remembering
    """
    __tablename__ = "agent_memory_entries"

    id                     = Column(String,  primary_key=True, default=new_uuid)
    # User-defined scope key (e.g. "eva-backend-dev").  Shared by all
    # conversations that opt into this scope via agent_scope_key session param.
    agent_scope_key        = Column(String,  nullable=False, index=True)
    user_id                = Column(String,  nullable=False, index=True)
    # error | success | decision | state | artifact | note
    entry_type             = Column(String,  nullable=False)
    content                = Column(Text,    nullable=False)
    tags                   = Column(JSON,    nullable=True)
    source_conversation_id = Column(String,  nullable=True)
    source_execution_id    = Column(String,  nullable=True)
    # G-004: sensitive flag — entries marked sensitive show as placeholders in
    # memory_recall until the user explicitly grants access (HitL-style gate).
    sensitive              = Column(Boolean, default=False, nullable=False)
    created_at             = Column(DateTime(timezone=True),
                                    default=lambda: datetime.now(timezone.utc),
                                    nullable=False)
    expires_at             = Column(DateTime(timezone=True), nullable=True)


# ===============================
# Subagents (G-003)
# ===============================

class SubagentConfig(Base):
    """
    Per-user named subagent configuration.

    Each subagent is a specialised AgentEngine variant the orchestrator can
    delegate work to via the `delegate_to_agent` tool.

    Columns
    -------
    name          Unique slug within the user's namespace (e.g. "researcher").
    description   Shown to the orchestrator so it knows when to delegate here.
    system_prompt Specialised system prompt for the child agent.
    model         Optional model override (None = inherit from parent session).
    provider      Optional provider override (None = inherit from parent session).
    tools         JSON list of allowed tool names (None / [] = all user tools).
    max_steps     Cap on ReAct iterations for the child agent (default 5).
    """
    __tablename__ = "subagent_configs"

    id          = Column(String, primary_key=True, default=new_uuid)
    user_id     = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name        = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    model       = Column(String, nullable=True)
    provider    = Column(String, nullable=True)
    # JSON list of tool names; None / empty means "all tools the user has".
    tools       = Column(JSON, nullable=True)
    max_steps   = Column(Integer, default=5, nullable=False)
    # B-053: JSON list of MCP server names this subagent may use.
    # None / empty means the subagent inherits all MCP servers the user has enabled.
    allowed_mcp_servers = Column(JSON, nullable=True)
    # Optional link to a Project — mirrors Conversation.project_id pattern.
    # None = user-global agent (available across all projects).
    project_id  = Column(String, nullable=True, index=True)
    # True for agents seeded from default_agents/*.yaml (EVA built-ins).
    # False (default) for user-created agents.
    # Used to enforce tool-list protection and to offer Reset/Clone actions in the UI.
    is_default  = Column(Boolean, default=False, nullable=False, server_default="0")

    created_at  = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at  = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_subagent_user_name"),
    )


# ===============================
# User Hook Scripts (B-064)
# ===============================

class UserHookScript(Base):
    """
    Metadata record for a user-uploaded hook script (B-064).

    The script file itself lives at:
        ~/eva_workspace/{user_id}/hooks/{filename}

    The `name` field is the human-readable identifier used in HookDefinition.command.
    It must be unique per user and contain no path separators.
    """
    __tablename__ = "user_hook_scripts"

    id          = Column(String, primary_key=True, default=new_uuid)
    user_id     = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    # Logical name used in HookDefinition.command — unique per user.
    name        = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    # Actual filename on disk (currently same as name, stored separately for flexibility).
    filename    = Column(String, nullable=False)
    size_bytes  = Column(Integer, nullable=False, default=0)
    created_at  = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at  = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_hook_script_user_name"),
    )


# ===============================
# Daemon Agents (G-008)
# ===============================

class DaemonConfig(Base):
    """
    Per-user autonomous daemon agent configuration.

    A daemon agent is a long-running, schedule-driven agent that wakes up
    automatically on a configured interval (or is manually triggered), runs its
    AgentEngine with its system prompt and tools, and sleeps until the next run.

    Columns
    -------
    name                Unique slug within the user's namespace (e.g. "monitor").
    description         Human-readable purpose of the daemon.
    system_prompt       Specialised system prompt for the daemon's AgentEngine.
    trigger_message     The "user" message sent to the agent each time it wakes.
                        Defaults to a generic activation message if None.
    model               Optional model override (None = inherit from user session).
    provider            Optional provider override (None = inherit from user session).
    tools               JSON list of allowed tool names (None / [] = all user tools).
    max_steps           Cap on ReAct iterations per run (default 5).
    run_interval_seconds  Seconds between automatic runs. None = manual trigger only.
    enabled             Whether the scheduler should auto-fire this daemon.
    last_run_at         Timestamp of the last completed run.
    next_run_at         Scheduler target for the next automatic run.
    """
    __tablename__ = "daemon_configs"

    id          = Column(String, primary_key=True, default=new_uuid)
    user_id     = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name        = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    # kind: "standard" (default, ordinary user-created daemon) | "system"
    # (internal/auto-created, e.g. the per-project sync daemon -- server-
    # reserved, the API rejects a client trying to set this) | "supervisor"
    # (a daemon whose job is managing background processes via the
    # background_process tool -- a semantic/tooling label, not a different
    # execution mechanic: see core/daemons/runner.py's kind-based check,
    # which replaces the previous name-string comparison against
    # sync_daemon_name(project_id)).
    kind        = Column(String, default="standard", nullable=False)
    system_prompt   = Column(Text, nullable=True)
    trigger_message = Column(Text, nullable=True)
    model       = Column(String, nullable=True)
    provider    = Column(String, nullable=True)
    tools       = Column(JSON, nullable=True)
    max_steps   = Column(Integer, default=5, nullable=False)

    run_interval_seconds = Column(Integer, nullable=True)   # None = manual only
    enabled     = Column(Boolean, default=True, nullable=False)

    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)

    # ── Lifecycle & retry policy (B-daemon-lifecycle) ─────────────────────────
    # lifecycle_status: idle | running | paused | retrying | permanently_failed
    lifecycle_status     = Column(String, default="idle", nullable=False)
    # ID of the currently-active (or most-recently-completed) execution run.
    # Links to the broker execution registry so GET /executions/{id}/status works.
    current_execution_id = Column(String, nullable=True)
    # Retry policy
    retry_on_failure     = Column(Boolean, default=True,  nullable=False)
    max_retries          = Column(Integer, default=3,     nullable=False)
    retry_delay_seconds  = Column(Integer, default=60,    nullable=False)
    # Failure tracking
    failure_count        = Column(Integer, default=0,     nullable=False)
    last_error           = Column(Text,    nullable=True)
    last_failure_at      = Column(DateTime(timezone=True), nullable=True)

    # ── Event subscriptions (G-009) ───────────────────────────────────────────
    # JSON list of EVA event types that auto-trigger this daemon.
    # Supported: "execution.completed", "execution.failed"
    # Example: ["execution.completed", "execution.failed"]
    event_subscriptions  = Column(JSON, nullable=True)
    # Optional link to a Project — mirrors Conversation.project_id pattern.
    # None = user-global daemon (not scoped to any project).
    project_id  = Column(String, nullable=True, index=True)

    created_at  = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at  = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_daemon_user_name"),
    )


class DaemonWebhook(Base):
    """
    Per-daemon inbound webhook.

    Provides a public (no-JWT) HTTP endpoint at POST /webhooks/{secret_token}
    that triggers the associated daemon when hit from external systems
    (e.g. GitHub Actions, Zapier, cron services).

    One webhook per daemon (unique constraint on daemon_id).
    The secret_token is a UUID4 that acts as the authentication credential —
    keep it private.
    """
    __tablename__ = "daemon_webhooks"

    id            = Column(String, primary_key=True, default=new_uuid)
    daemon_id     = Column(
        String,
        ForeignKey("daemon_configs.id"),
        nullable=False,
        unique=True,   # one webhook per daemon
        index=True,
    )
    user_id       = Column(String, nullable=False, index=True)
    daemon_name   = Column(String, nullable=False)
    secret_token  = Column(String, nullable=False, unique=True, index=True)
    description   = Column(Text, nullable=True)

    created_at         = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_triggered_at  = Column(DateTime(timezone=True), nullable=True)
    trigger_count      = Column(Integer, default=0, nullable=False)


class Project(Base):
    """
    Scope 4 — Project-level workspace (B-045).
    Registry entry only — all rich project artifacts live in .eva files under workspace_path.

    The DB holds only identity, routing, and status.
    Artifacts (tasks, knowledge, journal, errors) live in:
        blob_storage/projects/{id}/.eva/
    """
    __tablename__ = "projects"

    id             = Column(String,  primary_key=True, default=new_uuid)
    user_id        = Column(String,  nullable=False, index=True)
    name           = Column(String,  nullable=False)
    # 1-line human-readable summary shown in listings
    description    = Column(Text,    nullable=True)
    # Concise statement of intent / purpose (injected into project.eva SYNOPSIS section)
    synopsis       = Column(Text,    nullable=True)
    # Absolute path to the .eva/ workspace directory for this project
    workspace_path = Column(String,  nullable=True)
    # active | paused | archived
    status         = Column(String,  nullable=False, default="active")
    # B-053: JSON list of MCP server names available in this project's conversations.
    # None / empty means all user-enabled MCP servers are available.
    allowed_mcp_servers = Column(JSON, nullable=True)
    created_at     = Column(DateTime(timezone=True),
                            default=lambda: datetime.now(timezone.utc),
                            nullable=False)
    updated_at     = Column(DateTime(timezone=True), nullable=True)


# ===============================
# Background Processes
# ===============================

class BackgroundProcess(Base):
    """
    A tracked, detached OS process started via the background_process tool
    (e.g. a dev server) -- the PID/process registry from the strategic gap
    analysis (2026-08-18): ShellExecutor's normal shell() path always waits
    for a subprocess to exit or kills it at SHELL_MAX_TIMEOUT_SECONDS, so
    there was previously no way to start a long-running process and keep
    tracking it after the tool call returns.

    DB-backed (not an in-memory registry like ExecutionRegistry) on purpose:
    the whole point is not losing track of a process that legitimately
    survives a backend restart (it was started with its own process group,
    detached from the backend's own process tree) -- an in-memory table
    would forget it existed the moment the backend restarts, even though
    the OS process itself is still running. See main.py's lifespan startup
    reconciliation sweep, which re-adopts still-alive rows on boot.
    """
    __tablename__ = "background_processes"

    id              = Column(String, primary_key=True, default=new_uuid)
    user_id         = Column(String, nullable=False, index=True)
    conversation_id = Column(String, nullable=True, index=True)
    project_id      = Column(String, nullable=True, index=True)
    # execution_id of the ConversationExecution whose tool call started this
    # process, if any -- purely informational (e.g. for tracing), never used
    # to gate this row's own lifecycle.
    execution_id    = Column(String, nullable=True, index=True)

    command  = Column(JSON,   nullable=False)   # list[str], the argv that was run
    cwd      = Column(String, nullable=False)
    log_path = Column(String, nullable=False)    # stdout+stderr redirected here
    pid      = Column(Integer, nullable=False)
    # Process identity, not just liveness (2026-09-29): a bare PID lookup
    # can't tell "our process" apart from an unrelated one the OS has since
    # reused the same PID for -- realistic after a long uptime or a reboot,
    # and every consumer of this row (boot reconciliation, stop, the daily
    # reaper, duplicate-detection) previously trusted PID alone. Captured
    # via psutil.Process(pid).create_time() right after spawn; nullable so
    # rows created before this column existed fall back to the old,
    # identity-blind liveness check (see core/shell/executor.py's
    # _pid_matches_process()) rather than breaking on a NULL comparison.
    pid_create_time = Column(Float, nullable=True)

    # Session-scoped background processes (2026-09-29): when False (the
    # default), this process is stopped automatically when EVA quits, same
    # as the old pre-background-process-feature behaviour. When True --
    # settable by the agent at start time, or pinned afterward by the user
    # from the Activity panel -- it deliberately survives an app quit (the
    # opt-in exception, e.g. a dev server the user explicitly wants to keep
    # running). Never affects anything else about this row's lifecycle
    # (still force-stopped by BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS,
    # still stoppable manually) -- it only exempts it from the app-quit
    # cleanup sweep specifically.
    keep_alive_on_app_shutdown = Column(Boolean, default=False, nullable=False, server_default="0")

    # running | stopped | crashed | exited
    status      = Column(String, default="running", nullable=False, index=True)
    exit_code   = Column(Integer, nullable=True)

    # Byte offset into log_path already returned by a previous tail call --
    # lets repeated tail calls return only new output (mirrors the
    # byte-offset tracking industry precedent uses for this exact feature,
    # see the plan doc's research section) instead of re-sending the whole
    # log every time.
    last_output_offset = Column(Integer, default=0, nullable=False)

    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ended_at   = Column(DateTime(timezone=True), nullable=True)


# ===============================
# Conversation Attachments (B-066)
# ===============================

class Attachment(Base):
    """
    Raw file or image stored in the user's file library (B-066).

    One row per uploaded file.  Files are stored in:
      ~/eva_workspace/{user_id}/files/           (user-global)
      ~/eva_workspace/{user_id}/projects/{id}/files/  (project-scoped)

    An Attachment is not tied to any conversation until a ConversationAttachment
    row is created via POST /conversations/{id}/attachments.
    """
    __tablename__ = "attachments"

    id              = Column(String,  primary_key=True, default=new_uuid)
    user_id         = Column(String,  nullable=False, index=True)
    # "file" | "image"
    attachment_type = Column(String,  nullable=False)
    # Original filename as uploaded (used to locate the blob)
    filename        = Column(String,  nullable=True)
    mime_type       = Column(String,  nullable=True)
    # Path to the raw file in the files/ blob store
    # Stored as the filename only (scoped directory is resolved at runtime
    # using project_id so that project reassignment keeps blobs accessible).
    blob_path       = Column(String,  nullable=True)
    # Rough token estimate from the parser (used for inline vs RAG routing)
    token_estimate  = Column(Integer, nullable=True)
    size_bytes      = Column(Integer, nullable=True)
    # When set, the file was stored in the project's files/ sub-directory.
    # NULL = user-global library.
    project_id      = Column(String,  nullable=True, index=True)
    created_at      = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ConversationAttachment(Base):
    """
    Links an attachment (or a conversation/execution reference) to a specific
    conversation (B-066).

    One row per link.  When a file/image is linked, a system-role context
    message is injected into the conversation history and its message_id is
    stored here to enable recovery after truncation.

    attachment_type values
    ----------------------
    "file"             — text/document uploaded to the file library
    "image"            — image uploaded to the file library
    "conversation_ref" — reference to another conversation (summary injected)
    "execution_ref"    — reference to a past execution (metrics injected)
    """
    __tablename__ = "conversation_attachments"

    id              = Column(String, primary_key=True, default=new_uuid)
    conversation_id = Column(
        String,
        ForeignKey("conversations.id"),
        nullable=False,
        index=True,
    )
    user_id         = Column(String, nullable=False, index=True)
    # "file" | "image" | "conversation_ref" | "execution_ref"
    attachment_type = Column(String, nullable=False)
    # FK to attachments table — set for "file" and "image" types
    attachment_id   = Column(String, nullable=True)
    # Set for "conversation_ref"
    ref_conversation_id = Column(String, nullable=True)
    # Set for "execution_ref"
    ref_execution_id    = Column(String, nullable=True)
    # System message injected as context for this attachment.
    # Stored so that the context can be re-injected if the message is truncated.
    message_id      = Column(String, ForeignKey("messages.id"), nullable=True)
    # True if the file was too large for inline injection and was RAG-indexed instead.
    rag_indexed     = Column(Boolean, default=False, nullable=False)
    # RAG ingestion job ID (poll GET /rag/ingest/{job_id}/status)
    rag_job_id      = Column(String, nullable=True)
    created_at      = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
