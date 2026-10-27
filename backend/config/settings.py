# config/settings.py
"""
Central configuration for EVA.

All runtime-tunable constants live here.  Every value has a sensible default
and can be overridden via environment variable or the eva.env file.

Load order (handled by config/env_loader.py, called first in main.py):
  1. eva.env values          — always used when present
  2. config/settings defaults — used when eva.env has no value for that key
  3. Windows/system env vars  — NEVER used for provider API keys

Usage in other modules:
  from config.settings import <CONSTANT_NAME>
"""

import logging
import os
import pathlib
from enum import Enum
from typing import Any, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent

# In-memory per-user session cache
_USER_SESSIONS: dict[str, dict[str, Any]] = {}


# ===========================================================================
# Enums (consumed by session config and API schemas)
# ===========================================================================

class AgentMode(str, Enum):
    """
    Controls how the IntentRouter routes incoming messages.

    AUTO         – Router decides dynamically using heuristics + LLM classifier.
    CHAT         – Always treat messages as conversational (bypass task routing).
    PLAN_EXECUTE – Always treat messages as tasks for the Plan→Execute→Verify loop.
    """
    AUTO         = "auto"
    CHAT         = "chat"
    PLAN_EXECUTE = "plan_execute"


class ToolApprovalMode(str, Enum):
    """
    Controls when the Human-in-the-Loop approval gate fires.

    AUTO             – Never ask.  All tools run immediately (default).
    ALWAYS_ASK       – Ask before every mutating or destructive tool call.
    PER_CONVERSATION – Ask once per conversation.  Once the user approves a
                       tool call, all subsequent mutating/destructive tool
                       calls within the same conversation auto-execute.
                       Approval resets at the start of each new conversation.
    """
    AUTO             = "auto"
    ALWAYS_ASK       = "always_ask"
    PER_CONVERSATION = "per_conversation"


# ===========================================================================
# Default session configuration
# ===========================================================================

DEFAULT_SESSION = {
    # Intentionally empty, not a guessed provider (2026-07-27 cleanup). EVA
    # has no default model of its own -- a session with these empty means
    # genuinely unconfigured. Every consumer of a hydrated session must
    # treat an empty provider/model as "not configured yet" and reject
    # cleanly (see api/chat.py's pre-flight check and
    # core/execution/worker.py's defensive backstop), never substitute a
    # real provider name here. This previously held "groq"/"openai/gpt-oss-
    # 20b" as a silent fallback, which fired real, billed API calls for any
    # brand-new user's first message(s) before their first POST /set-session
    # call landed -- found via a live server.log trace, not by inspection.
    "provider": "",
    "model": "",
    # V6.7: level string ("minimal"/"low"/"medium"/"high"/"xhigh"), or
    # None for off. Was a plain bool.
    "reasoning": None,
    "system_prompt": None,
    "tools": [],
    "fallback_chain": [],          # List[dict] — [{provider, model, api_key?}, ...]
    "tool_approval_mode": ToolApprovalMode.AUTO.value,
    # Comma-separated allowed tool authority levels.
    # "read_only,mutating,destructive" = all allowed (default).
    "allowed_authorities": "read_only,mutating,destructive",
    # Intent routing mode.  Controls how the IntentRouter classifies messages.
    # "auto" = dynamic (default), "chat" = always conversational,
    # "plan_execute" = always task.
    "agent_mode": AgentMode.AUTO.value,
    # When True, PEV runner pauses after planning and waits for user approval
    # before executing any steps.  Defaults to False (auto-proceed).
    "require_plan_approval": False,
    # V6.12: how many times AgentEngine may extend the ReAct step budget
    # when the user chooses "continue" at the step-limit gate. 0 disables
    # the gate entirely (reverts to today's hard error at the step limit).
    "max_step_extensions": 2,
    # ── Long-Term Memory defaults (V3.0) ──────────────────────────────────
    # Scope 2 — User: inject extracted user-level facts.
    "user_memory_enabled":  True,
    # Scope 3 — Agent: shared scope key for cross-conversation agent memory.
    # None = disabled.
    "agent_scope_key":      None,
    "agent_memory_enabled": True,
    # ── Skills mode (V3.1) ────────────────────────────────────────────────
    # Controls whether the available_skills block is injected into the system prompt.
    # "auto"     — inject skills metadata if any skills are loaded (default)
    # "enabled"  — same as auto; user has explicitly opted in
    # "disabled" — never inject skills (zero token overhead)
    "skills_mode": "auto",
    # ── PreToolUse / PostToolUse Hooks (B-053) ────────────────────────────
    # List of hook definitions.  Each entry is a dict:
    # {"tool": "*"|"<name>", "event": "pre"|"post", "handler": "<name>", "config": {}}
    # Default: [] (no hooks — all tools run unfiltered)
    "tool_hooks": [],
    # ── Shell safety (B-052) ─────────────────────────────────────────────
    # When True, the shell tool blocks all non-read-only commands.
    # Useful for review/inspection sessions where no filesystem changes should occur.
    "shell_readonly": False,
    # ── Environment context (V5.5) ────────────────────────────────────────
    # User-configured fields injected into every system prompt.
    # None = not set; absent fields are silently omitted from the block.
    "user_timezone": None,   # IANA tz string, e.g. "Asia/Kolkata"
    "user_location": None,   # free text, e.g. "Mumbai, India"
    "user_language": None,   # BCP-47 tag, e.g. "en-IN"
    # ── PII Redaction (S-03) ──────────────────────────────────────────────
    # When True, the user's own chat message is scrubbed of PII before it is
    # persisted or sent to the LLM (see core/security/pii_redactor.py).
    # Off by default -- opt-in. Tool-output redaction is separate: already
    # opt-in via a "redact_pii" entry in tool_hooks.
    "pii_redaction_enabled": False,
    # A-21: adaptable PEV runner -- the per-step verifier also checks
    # whether a step's evidence already satisfies upcoming steps, and the
    # runner skips invoking the agent for them. On by default -- opt-out,
    # unlike most booleans here -- the feature's own guardrails (never
    # skips the plan's final step, a hard cap regardless of what the model
    # reports, fail-closed on any uncertain verification) already bound
    # the downside.
    "pev_lookahead_skip_enabled": True,
}


# ===========================================================================
# Session management helpers
# ===========================================================================

def hydrate_user_session_from_db(user_id: str, prefs: dict[str, Any]):
    """
    Load persisted user preferences into runtime session cache.
    Called on signup, login, or server startup.
    """
    _USER_SESSIONS[user_id] = {
        "provider": prefs["provider"],
        "model": prefs["model"],
        "reasoning": prefs.get("reasoning", False),
        # B-054: system_prompt moved to per-conversation; not loaded into session
        "tools": prefs.get("tools", []),
        "fallback_chain": prefs.get("fallback_chain") or [],
        "tool_approval_mode": prefs.get("tool_approval_mode", ToolApprovalMode.AUTO.value),
        "allowed_authorities": prefs.get("allowed_authorities", "read_only,mutating,destructive"),
        "agent_mode": prefs.get("agent_mode", AgentMode.AUTO.value),
        "require_plan_approval": bool(prefs.get("require_plan_approval", False)),
        "max_step_extensions": int(prefs.get("max_step_extensions", 2)),
        # Long-term memory settings
        "user_memory_enabled":  bool(prefs.get("user_memory_enabled",  True)),
        "agent_scope_key":      prefs.get("agent_scope_key") or None,
        "agent_memory_enabled": bool(prefs.get("agent_memory_enabled", True)),
        # Skills mode (V3.1)
        "skills_mode": prefs.get("skills_mode") or "auto",
        # PreToolUse / PostToolUse hooks (B-053)
        "tool_hooks": prefs.get("tool_hooks") or [],
        # Shell safety (B-052)
        "shell_readonly": bool(prefs.get("shell_readonly", False)),
        # Environment context (V5.5)
        "user_timezone": prefs.get("user_timezone") or None,
        "user_location": prefs.get("user_location") or None,
        "user_language": prefs.get("user_language") or None,
        # PII redaction (S-03)
        "pii_redaction_enabled": bool(prefs.get("pii_redaction_enabled", False)),
        # Adaptable PEV runner (A-21) -- on by default, unlike most booleans here.
        "pev_lookahead_skip_enabled": bool(prefs.get("pev_lookahead_skip_enabled", True)),
        "api_key": _USER_SESSIONS.get(user_id, {}).get("api_key"),
    }


def set_user_session(
    user_id: str,
    provider: str,
    model: str,
    reasoning: str | None = None,  # V6.7: level string, or None for off
    # B-054: system_prompt deprecated at session level — per-conversation only
    system_prompt: str = "",   # accepted but ignored (backward compat)
    tools: list | None = None,
    fallback_chain: list | None = None,
    tool_approval_mode: str = ToolApprovalMode.AUTO.value,
    allowed_authorities: str = "read_only,mutating,destructive",
    agent_mode: str = AgentMode.AUTO.value,
    require_plan_approval: bool = False,
    max_step_extensions: int = 2,
    user_memory_enabled: bool = True,
    agent_scope_key: str | None = None,
    agent_memory_enabled: bool = True,
    skills_mode: str = "auto",
    tool_hooks: list | None = None,
    shell_readonly: bool = False,
    user_timezone: str | None = None,
    user_location: str | None = None,
    user_language: str | None = None,
    pii_redaction_enabled: bool = False,
    pev_lookahead_skip_enabled: bool = True,
):
    """
    Store all per-user configuration in memory.
    This is used by:
      - chat endpoint
      - AgentEngine
      - UserToolRegistry loader
    """
    _USER_SESSIONS[user_id] = {
        "provider": provider,
        "model": model,
        # B-054: system_prompt no longer stored in session (per-conversation only)
        "tools": tools or [],        # Always store list
        "reasoning": reasoning,
        "fallback_chain": fallback_chain or [],
        "tool_approval_mode": tool_approval_mode,
        "allowed_authorities": allowed_authorities,
        "agent_mode": agent_mode,
        "require_plan_approval": require_plan_approval,
        "max_step_extensions": max_step_extensions,
        # Long-term memory settings
        "user_memory_enabled":  user_memory_enabled,
        "agent_scope_key":      agent_scope_key or None,
        "agent_memory_enabled": agent_memory_enabled,
        # Skills mode (V3.1)
        "skills_mode": skills_mode or "auto",
        # PreToolUse / PostToolUse hooks (B-053)
        "tool_hooks": tool_hooks or [],
        # Shell safety (B-052)
        "shell_readonly": shell_readonly,
        # Environment context (V5.5)
        "user_timezone": user_timezone or None,
        "user_location": user_location or None,
        "user_language": user_language or None,
        # PII redaction (S-03)
        "pii_redaction_enabled": pii_redaction_enabled,
        # Adaptable PEV runner (A-21)
        "pev_lookahead_skip_enabled": pev_lookahead_skip_enabled,
        # Optionally store API key; for now assume stored elsewhere
        "api_key": _USER_SESSIONS.get(user_id, {}).get("api_key", None)
    }


def set_user_api_key(user_id: str, api_key: str):
    """
    Optional helper: store API key separately.
    Some systems pull API keys from Vault or DB.
    """
    if user_id not in _USER_SESSIONS:
        _USER_SESSIONS[user_id] = {}
    _USER_SESSIONS[user_id]["api_key"] = api_key


def get_user_session(user_id: str) -> dict[str, Any]:
    """
    Fetch user session. If not present, return a blank template.
    Never crash.
    """
    return _USER_SESSIONS.get(user_id, {
        "provider": "",
        "model": "",
        "reasoning": False,
        "system_prompt": None,
        "api_key": "",
        "tools": [],
        "fallback_chain": [],
        "tool_approval_mode": ToolApprovalMode.AUTO.value,
        "allowed_authorities": "read_only,mutating,destructive",
        "agent_mode": AgentMode.AUTO.value,
        "require_plan_approval": False,
        "max_step_extensions": 2,
        "user_memory_enabled":  True,
        "agent_scope_key":      None,
        "agent_memory_enabled": True,
        "skills_mode":          "auto",
        "tool_hooks":           [],
        "shell_readonly":       False,
        "user_timezone":        None,
        "user_location":        None,
        "user_language":        None,
        "pii_redaction_enabled": False,
        "pev_lookahead_skip_enabled": True,
    })


# ===========================================================================
# ── Execution ────────────────────────────────────────────────────────────────
# ===========================================================================

# How long an execution record is retained in the broker after completion.
# Default: 24 hours.  Override: EXECUTION_TTL_SECONDS=3600
EXECUTION_TTL_SECONDS: int = int(os.getenv("EXECUTION_TTL_SECONDS", str(60 * 60 * 24)))

# V6.59 Phase 5: run-time provider/model usability gate (core/providers/
# usability.py). The provider half (known, chat-ready, key configured) is always
# enforced; this switch controls only the MODEL half -- refusing a model that
# LiteLLM's dataset explicitly marks as a non-chat model or as lacking tool
# calling. Unknown models are never blocked either way. Turn off if the dataset
# is ever wrong about a model you need.
# Override: PROVIDER_GATE_MODEL_CHECK_ENABLED=false
PROVIDER_GATE_MODEL_CHECK_ENABLED: bool = os.getenv(
    "PROVIDER_GATE_MODEL_CHECK_ENABLED", "true"
).lower() not in ("false", "0", "")

# How often (seconds) the runner writes a heartbeat timestamp into the execution
# hash.  The watchdog uses this to detect stalled executions.
# Default: 30 s.  Override: EXECUTION_HEARTBEAT_SECONDS=15
EXECUTION_HEARTBEAT_SECONDS: int = int(os.getenv("EXECUTION_HEARTBEAT_SECONDS", "30"))


# ===========================================================================
# ── Authentication & Security ────────────────────────────────────────────────
# ===========================================================================

# The token-signing key is deliberately NOT a setting (V6.59 Phase 7). It used
# to be read from os.environ into a module constant here (default ""), which kept
# it in the process environment and made a missing value silently become an empty,
# forgeable key. It now lives only in the OS credential store and is obtained
# with core.auth.signing_key.get_signing_key(), which fails closed.

ALGORITHM: str = "HS256"

# Access token lifetime in minutes.  Default: 24 hours (1440 minutes).
# Override: TOKEN_EXPIRE_MINUTES=480
TOKEN_EXPIRE_MINUTES: int = int(os.getenv("TOKEN_EXPIRE_MINUTES", str(60 * 24)))

# Refresh token lifetime in days.  Default: 7 days.
# Override: REFRESH_TOKEN_EXPIRE_DAYS=30
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


# ===========================================================================
# ── Database ─────────────────────────────────────────────────────────────────
# ===========================================================================

# SQLAlchemy async database URL.  Defaults to local SQLite for development.
# Override: DATABASE_URL=postgresql+asyncpg://user:pass@localhost/eva_db
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./eva.db")


# ===========================================================================
# ── EVA Platform Identity ─────────────────────────────────────────────────────
# ===========================================================================

# Semantic version string injected into startup logs and the /health endpoint.
# Not used for logic — pure metadata.
# Override: EVA_VERSION=5.1.0-prod
EVA_VERSION: str = os.getenv("EVA_VERSION", "5.0.0-local")


# ===========================================================================
# ── Agent Engine & Streaming ─────────────────────────────────────────────────
# ===========================================================================

# Maximum ReAct loop iterations per execution.
# Default: 15 (raised 2026-07-27 from 8 -- observed live: a plan_execute
# task involving real tool exploration routinely needs more than 8 steps,
# hitting the ask-to-continue gate (core/execution/step_extension.py) and
# waiting up to STEP_EXTENSION_TIMEOUT_SECONDS with no one there to answer
# in an unattended run). Override: MAX_REACT_STEPS=20
MAX_REACT_STEPS: int = int(os.getenv("MAX_REACT_STEPS", "15"))

# Duplicate tool-call guard (V6.20, threshold raised V6.25, tiered V6.26) --
# how many consecutive identical (tool_name, arguments) calls are tolerated
# before the ReAct loop is aborted as a stuck loop. Tiered by the tool's
# ToolAuthority (core/tools/base.py) since the cost of a stuck loop scales
# with how risky repeating the call actually is -- a read-only re-check is
# cheap, a destructive repeat is not. "Consecutive" means the count resets
# the moment a genuinely different call happens in between; only an
# unbroken run of the exact same call counts against the limit.
# Override: DUPLICATE_TOOL_CALL_THRESHOLD_READ_ONLY=8
DUPLICATE_TOOL_CALL_THRESHOLD_READ_ONLY: int = int(
    os.getenv("DUPLICATE_TOOL_CALL_THRESHOLD_READ_ONLY", "5")
)
# Override: DUPLICATE_TOOL_CALL_THRESHOLD_MUTATING=4
DUPLICATE_TOOL_CALL_THRESHOLD_MUTATING: int = int(
    os.getenv("DUPLICATE_TOOL_CALL_THRESHOLD_MUTATING", "3")
)
# Override: DUPLICATE_TOOL_CALL_THRESHOLD_DESTRUCTIVE=3
DUPLICATE_TOOL_CALL_THRESHOLD_DESTRUCTIVE: int = int(
    os.getenv("DUPLICATE_TOOL_CALL_THRESHOLD_DESTRUCTIVE", "2")
)

# rag_ingest tool (Phase D) -- max chunks a single tool call will index.
# Embedding is local CPU work (ONNX, no network call) but sequential per
# chunk with no batching, so an unbounded input could still make one
# ReAct step's latency balloon. At the default 1000-char/200-overlap
# chunking, 100 chunks is roughly 80,000 characters (~15-20k words) --
# generous for a single document, bounded enough to keep worst-case tool
# latency predictable. Content over this limit fails clearly rather than
# silently truncating.
# Override: RAG_INGEST_MAX_CHUNKS=200
RAG_INGEST_MAX_CHUNKS: int = int(os.getenv("RAG_INGEST_MAX_CHUNKS", "100"))

# Phase B project-sync daemon -- max ReAct steps for the tiny per-project
# daemon whose only job is to delegate to the skill-runner subagent and
# relay its result. Small because it does nothing itself beyond that one
# delegate_to_agent call.
# Override: PROJECT_SYNC_DAEMON_MAX_STEPS=5
PROJECT_SYNC_DAEMON_MAX_STEPS: int = int(os.getenv("PROJECT_SYNC_DAEMON_MAX_STEPS", "3"))

# search_files / find_files (T-02) -- unlike single-file tools (read_file,
# edit_file), a recursive content/glob search is inherently unbounded, so
# unlike the rest of tools/default/file_tools.py this needs an explicit cap.
# Results beyond this are truncated with a clear message rather than
# silently returned in full or left to hang on a huge tree.
# Override: SEARCH_TOOL_MAX_RESULTS=500
SEARCH_TOOL_MAX_RESULTS: int = int(os.getenv("SEARCH_TOOL_MAX_RESULTS", "200"))

# V6.60: the result cap above limits what is RETURNED, not how long the walk
# takes -- a search that finds few matches in a huge tree used to run (and, being
# synchronous, freeze the whole server) until it had read every file. These bound
# the WORK. When any is hit the tool stops, returns what it has and says so
# (`truncated: true` + a message), instead of running on.
# Wall-clock budget for one search_files / find_files call (seconds).
# Override: SEARCH_TOOL_TIME_BUDGET_SECONDS=20
SEARCH_TOOL_TIME_BUDGET_SECONDS: float = float(os.getenv("SEARCH_TOOL_TIME_BUDGET_SECONDS", "10"))
# Most files one call will look at before stopping.
# Override: SEARCH_TOOL_MAX_FILES_VISITED=100000
SEARCH_TOOL_MAX_FILES_VISITED: int = int(os.getenv("SEARCH_TOOL_MAX_FILES_VISITED", "50000"))
# search_files only: files larger than this are not read (logs, dumps, databases).
# Override: SEARCH_TOOL_MAX_FILE_BYTES=5000000
SEARCH_TOOL_MAX_FILE_BYTES: int = int(os.getenv("SEARCH_TOOL_MAX_FILE_BYTES", str(2 * 1024 * 1024)))
# search_files only: only the first N characters of a line are matched, which also
# bounds the cost of a pathological regex on a minified one-line file.
# Override: SEARCH_TOOL_MAX_LINE_CHARS=50000
SEARCH_TOOL_MAX_LINE_CHARS: int = int(os.getenv("SEARCH_TOOL_MAX_LINE_CHARS", "20000"))
# Directory names never descended into (comma-separated). Only affects the walk BELOW
# the path the caller gave: pointing `path` straight at one of these still works.
# Override: SEARCH_TOOL_SKIP_DIRS=.git,node_modules
SEARCH_TOOL_SKIP_DIRS: frozenset = frozenset(
    d.strip() for d in os.getenv(
        "SEARCH_TOOL_SKIP_DIRS",
        ".git,node_modules,.venv,venv,__pycache__,.mypy_cache,.pytest_cache,.tox",
    ).split(",") if d.strip()
)

# V6.60: GET /projects/{id}/files lists a project's workspace RECURSIVELY (that is the
# feature -- the Files tab shows the whole tree, and for an external project that is
# the user's real codebase). The walk now runs off the event loop, so a huge tree no
# longer freezes the server; this is the ceiling on how many WORKSPACE files one
# response will carry (knowledge and uploaded files are never capped). Hitting it
# returns the first N in path order with truncated=true. Set high enough that ordinary
# projects never reach it.
# Override: PROJECT_FILES_MAX_WORKSPACE_ENTRIES=200000
PROJECT_FILES_MAX_WORKSPACE_ENTRIES: int = int(os.getenv("PROJECT_FILES_MAX_WORKSPACE_ENTRIES", "50000"))

# ===========================================================================
# ── Vision (M-07) ────────────────────────────────────────────────────────────
# ===========================================================================
# An MCP tool result image (e.g. a Playwright screenshot) or a chat-attached
# image is inlined as real image content for a CONFIRMED vision-capable model
# (core.providers.capability_registry -- supports_vision is True, never on
# missing/unknown data) instead of being stripped to a text placeholder.
# Global kill-switch, independent of any per-model capability check -- unlike
# the capability check, this can turn the feature off entirely regardless of
# what any model claims to support, e.g. while investigating a cost/privacy
# concern raised about a specific deployment.
# Default: True.  Override: EVA_VISION_ENABLED=false
EVA_VISION_ENABLED: bool = os.getenv("EVA_VISION_ENABLED", "true").lower() not in ("false", "0", "")

# Max size of a single image inlined as real content, before base64 encoding.
# Above this, the existing text-placeholder/omitted behaviour is used instead
# -- a large image is exactly the case a text-only fallback exists for
# (context bloat, provider payload limits), not a case to relax the cap on.
# Default: 5 MB.  Override: IMAGE_INLINE_MAX_BYTES=2000000
IMAGE_INLINE_MAX_BYTES: int = int(os.getenv("IMAGE_INLINE_MAX_BYTES", str(5 * 1024 * 1024)))

# Context-budget watchdog (V6.11) -- fraction of a model's max_context_tokens
# at which the ReAct loop proactively compresses accumulated tool-call
# history in place, before making any further LLM call. Only applies when
# the model/provider's max_context_tokens is known (capability_registry);
# unknown models get no proactive check, matching EVA's existing
# "don't guess when data doesn't exist" convention elsewhere.
# Default: 0.85.  Override: CONTEXT_WATCHDOG_TRIGGER_RATIO=0.75
CONTEXT_WATCHDOG_TRIGGER_RATIO: float = float(
    os.getenv("CONTEXT_WATCHDOG_TRIGGER_RATIO", "0.85")
)

# Fallback max_context_tokens used by the watchdog when capability_registry
# has no cached data for the (provider, model) pair -- confirmed empirically
# that this is NOT an edge case: providers/capability/ollama_cloud/ has no
# models_cache.json on disk at all in this deployment, so get_model()
# returns None for every ollama_cloud model, including gpt-oss:20b, the
# exact model involved in the reference incident (execution 9e029fc2).
# Silently disabling the watchdog whenever capability data is unknown would
# defeat it precisely where it's needed most. 8192 is a conservative,
# commonly-used Ollama default context window (many self-hosted Ollama
# deployments run with num_ctx=8192 unless explicitly raised) -- better to
# occasionally trigger early on a model with more real headroom than to
# never trigger on one that's about to overflow.
# Default: 8192.  Override: CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS=16384
CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS: int = int(
    os.getenv("CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS", "8192")
)

# Time allowed (seconds) before the VERY FIRST token arrives from a provider.
# Reasoning models (Claude extended-thinking, DeepSeek-R1, o3) can be silent
# for 60–90 s while thinking — 120 s gives adequate headroom.
# Default: 120 s.  Override: EVA_FIRST_CHUNK_TIMEOUT_S=60
EVA_FIRST_CHUNK_TIMEOUT_S: int = int(os.getenv("EVA_FIRST_CHUNK_TIMEOUT_S", "120"))

# Idle time (seconds) allowed BETWEEN consecutive tokens once streaming has
# started.  A 30 s gap in a live token stream almost certainly means a TCP
# stall — this fires the fallback chain.
# Default: 30 s.  Override: EVA_INTER_CHUNK_TIMEOUT_S=15
EVA_INTER_CHUNK_TIMEOUT_S: int = int(os.getenv("EVA_INTER_CHUNK_TIMEOUT_S", "30"))

# NOTE: the provider fallback chain's retry-before-failover constants
# (FALLBACK_RETRY_BASE_DELAY_S, FALLBACK_RETRY_MAX_DELAY_S,
# FALLBACK_MAX_RETRIES_*) live on the pydantic `Settings` class below, NOT
# here as bare os.getenv() constants like MAX_REACT_STEPS above — that
# style only picks up real OS/system environment variables. load_eva_env()
# (config/env_loader.py) now copies eva.env's general config lines into
# os.environ (Step 4), but only as a default beneath real OS variables; the
# pydantic class below reads eva.env directly and so does not depend on that.
# Confirmed empirically before choosing this class instead. See `settings.FALLBACK_*` below, which
# genuinely reads eva.env via pydantic's own env_file mechanism.


# ===========================================================================
# ── Worker Lifecycle ─────────────────────────────────────────────────────────
# ===========================================================================

# How often (seconds) the worker refreshes its liveness beacon and the
# worker:heartbeat key in the broker.
# Default: 15 s.  Override: WORKER_HEARTBEAT_INTERVAL_SECONDS=30
WORKER_HEARTBEAT_INTERVAL_SECONDS: int = int(
    os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "15")
)

# TTL (seconds) for the worker:heartbeat key in the broker.  Must be > WORKER_HEARTBEAT_INTERVAL_SECONDS.
# Default: 30 s.  Override: WORKER_HEARTBEAT_KEY_TTL_SECONDS=60
WORKER_HEARTBEAT_KEY_TTL_SECONDS: int = int(
    os.getenv("WORKER_HEARTBEAT_KEY_TTL_SECONDS", "30")
)

# TTL (seconds) for the worker liveness beacon (worker:alive:{worker_id}).
# Must be > 2 × WORKER_HEARTBEAT_INTERVAL_SECONDS so a single missed refresh
# does not falsely declare the worker dead.
# Default: 90 s.  Override: WORKER_LIVENESS_TTL_SECONDS=120
WORKER_LIVENESS_TTL: int = int(os.getenv("WORKER_LIVENESS_TTL_SECONDS", "90"))


# ===========================================================================
# ── Stuck-Execution Watchdog ─────────────────────────────────────────────────
# ===========================================================================

# How often (seconds) the watchdog scans all execution hashes.
# Default: 60 s.  Override: WATCHDOG_INTERVAL_SECONDS=120
WATCHDOG_INTERVAL_SECONDS: int = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "60"))

# Timeout for active-work states: RUNNING, PLANNING, VERIFYING.
# If the execution has not written a heartbeat within this window it is
# considered hung and will be ABORTed.
# Default: 900 s (15 min).  Override: WATCHDOG_RUNNING_TIMEOUT_SECONDS=600
WATCHDOG_RUNNING_TIMEOUT_SECONDS: int = int(
    os.getenv("WATCHDOG_RUNNING_TIMEOUT_SECONDS", "900")
)

# Timeout for a job stuck in PENDING (never reached RUNNING).
# Default: 1800 s (30 min).  Override: WATCHDOG_PENDING_TIMEOUT_SECONDS=600
WATCHDOG_PENDING_TIMEOUT_SECONDS: int = int(
    os.getenv("WATCHDOG_PENDING_TIMEOUT_SECONDS", "1800")
)

# Timeouts for the three sibling human-in-the-loop pause gates (see
# core/execution/gate_markers.py's own docstring, which lists all three
# together): AWAITING_APPROVAL, AWAITING_PLAN_APPROVAL, AWAITING_STEP_EXTENSION.
# Each has its own gate-level timeout too (TOOL_APPROVAL_TIMEOUT_SECONDS for
# the first) so these watchdog thresholds are deliberately much longer — a
# safety net for when the gate's own cancel mechanism also gets stuck, not
# the primary "how long do we wait for the user" control. Kept as three
# separate settings (not one shared constant) so each gate can be tuned
# independently later even though all three default to the same value today.

# AWAITING_APPROVAL (a tool call waiting on the user's allow/deny decision).
# Default: 14400 s (4 h).  Override: WATCHDOG_APPROVAL_TIMEOUT_SECONDS=7200
WATCHDOG_APPROVAL_TIMEOUT_SECONDS: int = int(
    os.getenv("WATCHDOG_APPROVAL_TIMEOUT_SECONDS", "14400")
)

# AWAITING_PLAN_APPROVAL (a generated plan waiting on approve/edit/reject).
# Default: 14400 s (4 h).  Override: WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS=7200
WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS: int = int(
    os.getenv("WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS", "14400")
)

# AWAITING_STEP_EXTENSION (V6.12's ReAct step-limit gate, waiting on the
# user's continue/stop decision). Was missing watchdog coverage entirely
# until V6.54; now its own independently-tunable setting rather than sharing
# WATCHDOG_APPROVAL_TIMEOUT_SECONDS.
# Default: 14400 s (4 h).  Override: WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS=7200
WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS: int = int(
    os.getenv("WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS", "14400")
)


# ===========================================================================
# ── Human-in-the-Loop Approval Gate ──────────────────────────────────────────
# ===========================================================================

# Maximum seconds the gate waits for user approve/reject before auto-cancelling.
# Default: 3600 s (1 hour).  Override: TOOL_APPROVAL_TIMEOUT_SECONDS=1800
TOOL_APPROVAL_TIMEOUT_SECONDS: int = int(
    os.getenv("TOOL_APPROVAL_TIMEOUT_SECONDS", "3600")
)

# Seconds between TTL-refresh heartbeats while the gate is blocking.
# Default: 60 s.  Override: APPROVAL_HEARTBEAT_INTERVAL_SECONDS=30
APPROVAL_HEARTBEAT_INTERVAL_SECONDS: int = int(
    os.getenv("APPROVAL_HEARTBEAT_INTERVAL_SECONDS", "60")
)

# TTL (seconds) for the per-conversation auto-approval key
# (ToolApprovalMode.PER_CONVERSATION).  7 days — long enough to span a typical
# user session without being permanent.
# Override: PER_CONV_APPROVAL_TTL_SECONDS=86400
PER_CONV_APPROVAL_TTL_SECONDS: int = int(
    os.getenv("PER_CONV_APPROVAL_TTL_SECONDS", str(7 * 24 * 3600))
)

# Maximum character length of an argument value shown in the approval UI
# preview.  Longer values are truncated with "…<truncated>".
# Default: 120.  Override: APPROVAL_ARGS_PREVIEW_MAX_LEN=200
APPROVAL_ARGS_PREVIEW_MAX_LEN: int = int(
    os.getenv("APPROVAL_ARGS_PREVIEW_MAX_LEN", "120")
)


# ===========================================================================
# ── PEV Runner ───────────────────────────────────────────────────────────────
# ===========================================================================

# Seconds to wait for the user to approve/reject a generated plan before
# auto-cancelling the execution.
# Default: 3600 s (1 hour).  Override: PLAN_APPROVAL_TIMEOUT_SECONDS=1800
PLAN_APPROVAL_TIMEOUT_SECONDS: int = int(
    os.getenv("PLAN_APPROVAL_TIMEOUT_SECONDS", "3600")
)

# Seconds between TTL-refresh heartbeats while waiting for plan approval.
# Default: 60 s.  Override: PLAN_APPROVAL_HEARTBEAT_SECONDS=30
PLAN_APPROVAL_HEARTBEAT_SECONDS: int = int(
    os.getenv("PLAN_APPROVAL_HEARTBEAT_SECONDS", "60")
)

# V6.12: seconds to wait for the user to continue/stop after the ReAct
# step limit is reached, before auto-stopping (clean completion, not an
# error/cancellation -- see core/execution/transitions.py). Matches
# PLAN_APPROVAL_TIMEOUT_SECONDS/TOOL_APPROVAL_TIMEOUT_SECONDS's 1-hour
# default rather than a short window -- consistent with EVA's other
# approval gates, which assume the user may be away from the screen
# rather than actively watching every turn.
# Default: 3600 s (1 hour).  Override: STEP_EXTENSION_TIMEOUT_SECONDS=1800
STEP_EXTENSION_TIMEOUT_SECONDS: int = int(
    os.getenv("STEP_EXTENSION_TIMEOUT_SECONDS", "3600")
)

# Seconds between TTL-refresh heartbeats while waiting for a step-extension decision.
# Default: 60 s.  Override: STEP_EXTENSION_HEARTBEAT_SECONDS=30
STEP_EXTENSION_HEARTBEAT_SECONDS: int = int(
    os.getenv("STEP_EXTENSION_HEARTBEAT_SECONDS", "60")
)

# Maximum number of adaptive re-planning attempts per execution.
# If verification keeps failing after this many replans, the runner accepts
# the failure and moves on rather than looping indefinitely.
# Default: 3.  Override: MAX_REPLAN_ATTEMPTS=5
MAX_REPLAN_ATTEMPTS: int = int(os.getenv("MAX_REPLAN_ATTEMPTS", "3"))

# UX-01 Phase 2: maximum number of times a rejected-with-feedback plan will
# be revised and re-shown for approval before a further rejection cancels
# the execution outright (same as rejecting with no feedback).
# Default: 2.  Override: PLAN_REVISION_MAX_ROUNDS=3
PLAN_REVISION_MAX_ROUNDS: int = int(os.getenv("PLAN_REVISION_MAX_ROUNDS", "2"))

# A-13 / A-14: what a plan step is told about the steps before it, and what the verifier is shown
# about the tools a step ran. Deliberately small -- one short line per tool call (tool, what it
# acted on, ok / ERROR) and the step's own text, never full arguments or results.
# Max characters of an earlier step's own text carried into later steps.
PEV_STEP_RECORD_TEXT_CHARS: int = int(os.getenv("PEV_STEP_RECORD_TEXT_CHARS", "1200"))
# Max tool-call lines kept per step (the most recent ones; older ones are counted, not listed).
PEV_STEP_RECORD_MAX_TOOL_LINES: int = int(os.getenv("PEV_STEP_RECORD_MAX_TOOL_LINES", "25"))
# Max characters of the "what it acted on" part of a tool-call line, and of an error message.
PEV_STEP_RECORD_TARGET_CHARS: int = int(os.getenv("PEV_STEP_RECORD_TARGET_CHARS", "90"))
PEV_STEP_RECORD_ERROR_CHARS: int = int(os.getenv("PEV_STEP_RECORD_ERROR_CHARS", "120"))
# Max characters of the whole "work already done" block added to a step's prompt. When the plan is
# long, older steps are cut down to a title and one line first, so the newest steps stay complete.
PEV_PRIOR_STEPS_MAX_CHARS: int = int(os.getenv("PEV_PRIOR_STEPS_MAX_CHARS", "6000"))
# A-20: the same "work already done" block, but for PlanVerifierEngine's one-time end-of-plan check
# -- larger than PEV_PRIOR_STEPS_MAX_CHARS because there is no next step's own prompt headroom to
# protect here (this call happens after every step has already finished), but still capped, not
# unlimited, so a pathological many-step plan can't blow up this one call's own prompt size.
PEV_PLAN_VERIFIER_TRAIL_MAX_CHARS: int = int(os.getenv("PEV_PLAN_VERIFIER_TRAIL_MAX_CHARS", "20000"))

# A-21: adaptable PEV runner -- deployer-level tuning knobs only. Whether the feature is used at
# all is a PER-USER session preference (pev_lookahead_skip_enabled, default True -- see
# hydrate_user_session_from_db()/set_user_session() below and api/session.py), matching
# require_plan_approval's own pattern, since the user asked for a frontend enable/disable toggle,
# not just a deployer env var.
#
# Hard ceiling on how many steps a single verification can skip at once, independent of whatever
# count the model itself reports -- a wrong or over-eager claim can only ever cost this many steps,
# never the entire rest of the plan.
# Default: 2.  Override: PEV_LOOKAHEAD_MAX_SKIP=3
PEV_LOOKAHEAD_MAX_SKIP: int = int(os.getenv("PEV_LOOKAHEAD_MAX_SKIP", "2"))
# Max characters of the upcoming-steps list (titles/descriptions only) added to the verifier's
# prompt when checking for a look-ahead skip. Small and separate from PEV_PRIOR_STEPS_MAX_CHARS --
# unexecuted steps have no tool evidence of their own to show, just their planned description.
# Default: 2000.  Override: PEV_LOOKAHEAD_STEPS_CHARS=3000
PEV_LOOKAHEAD_STEPS_CHARS: int = int(os.getenv("PEV_LOOKAHEAD_STEPS_CHARS", "2000"))


# ===========================================================================
# ── Tool Execution ───────────────────────────────────────────────────────────
# ===========================================================================

# Maximum wall-clock seconds any single tool invocation may run before it is
# cancelled and retried.
# Default: 300 s (5 min).  Override: TOOL_EXECUTION_TIMEOUT_SECONDS=60
TOOL_EXECUTION_TIMEOUT_SECONDS: int = int(
    os.getenv("TOOL_EXECUTION_TIMEOUT_SECONDS", "300")
)

# Number of retry attempts for transient tool failures before giving up.
# Default: 3.  Override: TOOL_MAX_RETRY_ATTEMPTS=1
TOOL_MAX_RETRY_ATTEMPTS: int = int(os.getenv("TOOL_MAX_RETRY_ATTEMPTS", "3"))

# Each retry attempt gets a progressively larger timeout window (attempt N
# gets TOOL_EXECUTION_TIMEOUT_SECONDS * 2^(N-1), capped at 4x the base --
# same exponential-backoff shape core/daemons/registry.py already uses for
# daemon retry scheduling). Attempt 1 is unchanged from before this existed.
# This is a backstop for a tool that's genuinely stuck, not the primary
# protection against slow-but-progressing work -- see
# execution_timeout_seconds on ToolBase for that.

# Base timeout (seconds) for delegate_to_agent specifically. A nested child
# AgentEngine already has its own protections against a genuinely hung or
# runaway run -- EVA_FIRST_CHUNK_TIMEOUT_S/EVA_INTER_CHUNK_TIMEOUT_S catch a
# dead LLM connection at the individual-call level, and the child's own
# max_steps ceiling bounds total work -- so the generic 300s tool timeout
# was cancelling and fully restarting legitimate multi-step delegation for
# no reason. This is a generous backstop, not the primary control.
# Default: 1800 s (30 min).  Override: DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS=900
DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS: int = int(
    os.getenv("DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS", "1800")
)


# ===========================================================================
# ── SSE & Event Streaming ────────────────────────────────────────────────────
# ===========================================================================

# How often (seconds) the SSE event feed sends a comment keepalive line.
# Default: 30 s.  Override: SSE_HEARTBEAT_SECONDS=15
SSE_HEARTBEAT_SECONDS: int = int(os.getenv("SSE_HEARTBEAT_SECONDS", "30"))

# Maximum seconds the SSE response generator will wait for an execution that
# has disappeared from the broker (e.g. it never started).
# Default: 30 s.  Override: SSE_STALE_POLL_SECONDS=10
SSE_STALE_POLL_SECONDS: int = int(os.getenv("SSE_STALE_POLL_SECONDS", "30"))


# ===========================================================================
# ── Shell Tool ───────────────────────────────────────────────────────────────
# ===========================================================================

# Default timeout (seconds) for individual shell commands.
# Users may supply a lower value; they cannot exceed this ceiling.
# Default: 30 s.  Override: SHELL_DEFAULT_TIMEOUT_SECONDS=60
SHELL_DEFAULT_TIMEOUT_SECONDS: int = int(
    os.getenv("SHELL_DEFAULT_TIMEOUT_SECONDS", "30")
)

# V6.15 (S-09): the comment above this setting has claimed "they cannot
# exceed this ceiling" since this file's early history, but nothing ever
# actually enforced it -- ShellArgs.timeout (tools/default/shell_tool.py)
# had no upper bound, so a tool call could request an arbitrarily large
# timeout, which (combined with cancellation only ever being checked
# between tool calls, never during one) meant the worst-case unstoppable
# window for a single shell command was effectively unbounded. This
# setting is now actually enforced via a Pydantic `le=` constraint on
# ShellArgs.timeout.
# Default: 300 s (5 min).  Override: SHELL_MAX_TIMEOUT_SECONDS=600
SHELL_MAX_TIMEOUT_SECONDS: int = int(
    os.getenv("SHELL_MAX_TIMEOUT_SECONDS", "300")
)

# Maximum stdout/stderr bytes captured from a single shell command.
# Prevents memory exhaustion from `cat largefile` style commands.
# Default: 524288 (512 KB).  Override: SHELL_MAX_OUTPUT_BYTES=1048576
SHELL_MAX_OUTPUT_BYTES: int = int(os.getenv("SHELL_MAX_OUTPUT_BYTES", str(512 * 1024)))

# TTL (seconds) for the shell-session key in the broker.  The session stores
# cwd and env vars so they persist across tool calls within a conversation.
# Default: 86400 s (24 hours).  Override: SHELL_SESSION_TTL_SECONDS=3600
SHELL_SESSION_TTL_SECONDS: int = int(os.getenv("SHELL_SESSION_TTL_SECONDS", "86400"))

# Maximum number of command strings retained in the shell session history.
# Default: 50.  Override: SHELL_SESSION_MAX_HISTORY=100
SHELL_SESSION_MAX_HISTORY: int = int(os.getenv("SHELL_SESSION_MAX_HISTORY", "50"))


# ===========================================================================
# ── Background Processes ─────────────────────────────────────────────────────
# ===========================================================================
# The background_process tool (tools/default/process_tools.py) starts a
# detached OS process (e.g. a dev server) that survives past the tool call
# returning -- unlike the shell tool, which always waits for the command to
# exit or kills it at SHELL_MAX_TIMEOUT_SECONDS. See core/db/models.py's
# BackgroundProcess docstring and core/shell/executor.py's start_background/
# stop_background/tail_background for the mechanics.

# Maximum lifetime (seconds) a background process may run before the daily
# cleanup sweep (core/tasks/cleanup.py) force-stops it, even if nobody ever
# called action="stop". Mirrors the same auto-terminate safety net Cursor's
# equivalent background-process feature uses -- a detached process with no
# tracked owner action is exactly the "invisible, unkillable orphan" risk
# flagged in the 2026-08 backend security audit, so this is a hard ceiling,
# not a suggestion.
# Default: 14400 s (4 hours).  Override: BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS=28800
BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS: int = int(
    os.getenv("BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS", "14400")
)

# Maximum stdout+stderr bytes read back by a single tail call. Independent
# from SHELL_MAX_OUTPUT_BYTES (that one bounds a single synchronous shell
# command's captured output; this one bounds one poll of a long-lived log
# file that may have grown far larger over the process's lifetime).
# Default: 65536 (64 KB).  Override: BACKGROUND_PROCESS_TAIL_MAX_BYTES=131072
BACKGROUND_PROCESS_TAIL_MAX_BYTES: int = int(
    os.getenv("BACKGROUND_PROCESS_TAIL_MAX_BYTES", str(64 * 1024))
)

# Grace period (seconds) between a graceful terminate of a background
# process tree and escalating to a force-kill. Deliberately mirrors
# core/shell/executor.py's _KILL_GRACE_SECONDS (5.0) for consistency, kept
# as its own named constant here since callers of stop_background() may
# want a different value than an in-flight synchronous shell command's own
# cancellation grace period.
# Default: 5.0 s.  Override: BACKGROUND_PROCESS_KILL_GRACE_SECONDS=10
BACKGROUND_PROCESS_KILL_GRACE_SECONDS: float = float(
    os.getenv("BACKGROUND_PROCESS_KILL_GRACE_SECONDS", "5.0")
)

# Grace period used ONLY by the app-quit graceful-shutdown endpoint
# (api/internal.py's POST /internal/shutdown), deliberately shorter than
# BACKGROUND_PROCESS_KILL_GRACE_SECONDS above. Electron's own stopBackend()
# falls back to a hard SIGKILL of the whole backend after a fixed ~6s
# timeout regardless of what's happening inside it -- since the processes
# killed here run concurrently (not sequentially), this bounds that step to
# ~2s worst case, leaving headroom for the ASGI lifespan shutdown block
# (broker/MCP pool teardown) that runs after it, comfortably inside
# Electron's own window. A live interactive Stop click (api/processes.py)
# does NOT use this -- it keeps the longer, more patient default above,
# since giving a single dev server time to flush is worth more there than
# shaving seconds off a quit nobody's timing.
# Default: 2.0 s.  Override: BACKGROUND_PROCESS_SHUTDOWN_KILL_GRACE_SECONDS=3
BACKGROUND_PROCESS_SHUTDOWN_KILL_GRACE_SECONDS: float = float(
    os.getenv("BACKGROUND_PROCESS_SHUTDOWN_KILL_GRACE_SECONDS", "2.0")
)

# How long a TERMINAL-status row (stopped/crashed/exited) is kept before the
# daily cleanup sweep purges it (and deletes its log file). Deliberately
# short -- hours, not days -- unlike execution_metrics/audit-log retention:
# a finished background process has no ongoing analytics/history value the
# way a completed conversation execution does, it's operational state that
# goes stale the moment the process it describes is gone. A still-"running"
# row is NEVER touched by this purge regardless of age (only by
# BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS above, which itself transitions a
# row to terminal before this purge would ever consider it).
# Default: 24 h.  Override: BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS=72
BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS: int = int(
    os.getenv("BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS", "24")
)


# ===========================================================================
# ── Hook Scripts ─────────────────────────────────────────────────────────────
# ===========================================================================

# Maximum file size (bytes) accepted for an uploaded hook script.
# Default: 1 MB.  Override: HOOK_SCRIPT_MAX_BYTES=524288
HOOK_SCRIPT_MAX_BYTES: int = int(
    os.getenv("HOOK_SCRIPT_MAX_BYTES", str(1 * 1024 * 1024))
)

# Timeout (seconds) for shell-script hook execution.
# Default: 30 s.  Override: HOOK_COMMAND_TIMEOUT_SECONDS=10
HOOK_COMMAND_TIMEOUT_SECONDS: int = int(
    os.getenv("HOOK_COMMAND_TIMEOUT_SECONDS", "30")
)

# Timeout (seconds) for HTTP hook calls.
# Default: 10 s.  Override: HOOK_HTTP_TIMEOUT_SECONDS=5
HOOK_HTTP_TIMEOUT_SECONDS: int = int(os.getenv("HOOK_HTTP_TIMEOUT_SECONDS", "10"))


# ===========================================================================
# ── Memory ───────────────────────────────────────────────────────────────────
# ===========================================================================

# Minimum number of messages required in a conversation before the memory
# extractor runs.  Skips trivial "hello" conversations.
# Default: 3.  Override: MEMORY_MIN_MESSAGES_FOR_EXTRACTION=5
MEMORY_MIN_MESSAGES_FOR_EXTRACTION: int = int(
    os.getenv("MEMORY_MIN_MESSAGES_FOR_EXTRACTION", "3")
)

# Maximum number of user facts the LLM may return in a single extraction call.
# Default: 5.  Override: MEMORY_MAX_FACTS_PER_EXTRACTION=10
MEMORY_MAX_FACTS_PER_EXTRACTION: int = int(
    os.getenv("MEMORY_MAX_FACTS_PER_EXTRACTION", "5")
)

# Maximum number of agent memory entries the LLM may return in a single
# extraction call.
# Default: 5.  Override: MEMORY_MAX_ENTRIES_PER_EXTRACTION=10
MEMORY_MAX_ENTRIES_PER_EXTRACTION: int = int(
    os.getenv("MEMORY_MAX_ENTRIES_PER_EXTRACTION", "5")
)

# TTL (seconds) for sensitive-memory access grants in the broker (B-033).
# Default: 3600 s (1 hour).  Override: MEMORY_SENSITIVE_GRANT_TTL_SECONDS=7200
MEMORY_SENSITIVE_GRANT_TTL_SECONDS: int = int(
    os.getenv("MEMORY_SENSITIVE_GRANT_TTL_SECONDS", "3600")
)

# A-04 Phase B: default TTL for newly-inserted "error"-type agent memory
# entries. Every other entry type (decision/success/artifact/note/state) and
# all user facts stay permanent (expires_at=null) -- "state" is already kept
# current by the Phase A supersedes rule, and the others are durable by
# design. "error" is the one type that realistically goes stale without ever
# being revisited (nothing re-triggers the same bug/fix conversation), so it
# gets a real expiry instead of accumulating forever.
# Default: 90 days.  Override: AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS=30
AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS: int = int(
    os.getenv("AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS", "90")
)


# ===========================================================================
# ── RAG ──────────────────────────────────────────────────────────────────────
# ===========================================================================

# TTL (seconds) for RAG ingestion job status keys in the broker (B-018).
# Default: 3600 s (1 hour).  Override: RAG_JOB_TTL_SECONDS=7200
RAG_JOB_TTL_SECONDS: int = int(os.getenv("RAG_JOB_TTL_SECONDS", "3600"))


# ===========================================================================
# ── MCP (Model Context Protocol) ─────────────────────────────────────────────
# ===========================================================================

# Timeout (seconds) for opening an MCP transport connection.
# Default: 60 s.  Override: MCP_CONNECT_TIMEOUT_SECONDS=30
MCP_CONNECT_TIMEOUT_SECONDS: float = float(
    os.getenv("MCP_CONNECT_TIMEOUT_SECONDS", "60.0")
)

# Timeout (seconds) for the MCP initialize handshake.
# Default: 60 s.  Override: MCP_INIT_TIMEOUT_SECONDS=30
MCP_INIT_TIMEOUT_SECONDS: float = float(
    os.getenv("MCP_INIT_TIMEOUT_SECONDS", "60.0")
)

# Timeout (seconds) for MCP tool list / tool call operations.
# Default: 60 s.  Override: MCP_CALL_TIMEOUT_SECONDS=30
MCP_CALL_TIMEOUT_SECONDS: float = float(
    os.getenv("MCP_CALL_TIMEOUT_SECONDS", "60.0")
)


# ===========================================================================
# ── HTTP Tool ────────────────────────────────────────────────────────────────
# ===========================================================================

# Maximum response body size (bytes) accepted by the http_get / http_post tools.
# Prevents memory exhaustion on large responses.
# Default: 10 MB.  Override: HTTP_TOOL_MAX_RESPONSE_BYTES=5242880
HTTP_TOOL_MAX_RESPONSE_BYTES: int = int(
    os.getenv("HTTP_TOOL_MAX_RESPONSE_BYTES", str(10 * 1024 * 1024))
)

# Maximum request body size (bytes) for http_post payloads.
# Default: 1 MB.  Override: HTTP_TOOL_MAX_PAYLOAD_BYTES=524288
HTTP_TOOL_MAX_PAYLOAD_BYTES: int = int(
    os.getenv("HTTP_TOOL_MAX_PAYLOAD_BYTES", str(1 * 1024 * 1024))
)

# Hard ceiling (seconds) for user-supplied HTTP request timeouts.
# Users may request any value; it is silently capped at this value.
# Default: 120 s.  Override: HTTP_TOOL_MAX_TIMEOUT_SECONDS=60
HTTP_TOOL_MAX_TIMEOUT_SECONDS: int = int(
    os.getenv("HTTP_TOOL_MAX_TIMEOUT_SECONDS", "120")
)


# ===========================================================================
# ── Multi-Agent Tools ────────────────────────────────────────────────────────
# ===========================================================================

# Maximum number of subagents that can run concurrently in parallel_delegate.
# Default: 5.  Override: MAX_PARALLEL_TASKS=10
MAX_PARALLEL_TASKS: int = int(os.getenv("MAX_PARALLEL_TASKS", "5"))

# Maximum number of phases the coordinate tool may plan.
# Default: 5.  Override: MAX_COORDINATE_PHASES=8
MAX_COORDINATE_PHASES: int = int(os.getenv("MAX_COORDINATE_PHASES", "5"))

# Maximum number of subagent tasks per phase in the coordinate tool.
# Default: 5.  Override: MAX_COORDINATE_TASKS_PER_PHASE=8
MAX_COORDINATE_TASKS_PER_PHASE: int = int(
    os.getenv("MAX_COORDINATE_TASKS_PER_PHASE", "5")
)

# Maximum number of DISTINCT dynamically-invented (not pre-registered) agent
# names the coordinator LLM may synthesize in one coordinate() call (A-05,
# gap report). Separate from MAX_COORDINATE_PHASES/TASKS_PER_PHASE -- those
# bound breadth (how many tasks total), this bounds identity-sprawl (how
# many never-before-seen personas get spun up from one hidden, unreviewed
# planning call). Default: 3.  Override: MAX_COORDINATE_DYNAMIC_AGENTS=5
MAX_COORDINATE_DYNAMIC_AGENTS: int = int(
    os.getenv("MAX_COORDINATE_DYNAMIC_AGENTS", "3")
)


# ===========================================================================
# ── Webhooks ─────────────────────────────────────────────────────────────────
# ===========================================================================

# Sliding-window duration (seconds) for the webhook rate limiter.
# Default: 60 s.  Override: WEBHOOK_RATE_LIMIT_WINDOW_SECONDS=120
WEBHOOK_RATE_LIMIT_WINDOW_SECONDS: int = int(
    os.getenv("WEBHOOK_RATE_LIMIT_WINDOW_SECONDS", "60")
)

# Maximum webhook calls allowed within WEBHOOK_RATE_LIMIT_WINDOW_SECONDS
# per token.  Default: 10.  Override: WEBHOOK_RATE_LIMIT_MAX_CALLS=20
WEBHOOK_RATE_LIMIT_MAX_CALLS: int = int(
    os.getenv("WEBHOOK_RATE_LIMIT_MAX_CALLS", "10")
)


# ===========================================================================
# ── Auth rate limiting (S-02) ────────────────────────────────────────────────
# ===========================================================================
#
# POST /auth/login, /auth/signup, /auth/refresh are the only genuinely
# unauthenticated endpoints; /chat and /execute already require a valid
# Bearer token and are left alone. The packaged app's entrypoint.py binds
# to 127.0.0.1 by default as of B2-1.5 (2026-08-18) -- these limiters are
# kept regardless, since EVA_HOST can still override that to expose the
# server wider, and dev runs (`uvicorn main:app`) are reachable however
# the caller invokes uvicorn.
#
# Login uses a dual-key (per-IP AND per-account) failure-counted limiter
# per OWASP's Authentication Cheat Sheet -- only failed attempts count, a
# successful login resets both counters, and the lockout is temporary
# (never permanent, which OWASP notes can itself become a DoS vector).
# Signup/refresh use a simpler all-traffic-counted, IP-only limiter (same
# shape as the existing webhook limiter) -- anti-spam/anti-hammering, not
# brute-force protection (refresh tokens are high-entropy, not guessable).

# Sliding-window duration (seconds) for failed login attempts, per IP and
# per account. Default: 900 s (15 min).
AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = int(
    os.getenv("AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900")
)

# Maximum failed login attempts allowed within the window (per IP, and
# separately per account) before a temporary lockout. Default: 5, within
# OWASP's commonly cited 3-10 range.
AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS: int = int(
    os.getenv("AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "5")
)

# Sliding-window duration (seconds) for the signup rate limiter (per IP).
# Default: 3600 s (1 h) -- signup abuse is about mass account creation
# over time, not a single burst.
AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS: int = int(
    os.getenv("AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS", "3600")
)

# Maximum signup calls allowed per IP within the window. Default: 5.
AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS: int = int(
    os.getenv("AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS", "5")
)

# Sliding-window duration (seconds) for the token-refresh rate limiter
# (per IP). Default: 60 s -- deliberately short and generous (see
# AUTH_REFRESH_RATE_LIMIT_MAX_CALLS below); this is an anti-hammering
# backstop, not brute-force protection.
AUTH_REFRESH_RATE_LIMIT_WINDOW_SECONDS: int = int(
    os.getenv("AUTH_REFRESH_RATE_LIMIT_WINDOW_SECONDS", "60")
)

# Maximum refresh calls allowed per IP within the window. Default: 30 --
# generous on purpose so legitimate multi-tab/multi-device usage never
# trips it.
AUTH_REFRESH_RATE_LIMIT_MAX_CALLS: int = int(
    os.getenv("AUTH_REFRESH_RATE_LIMIT_MAX_CALLS", "30")
)

# B2-1.6 (2026-08-18): POST /secrets/{provider} is authenticated (any
# valid JWT can call it) but previously had no rate limiting at all,
# despite triggering a live third-party provider-validation API call on
# every request -- a leaked/stolen token could be used to hammer it,
# racking up real cost/quota against the user's own provider account, not
# just server load. Keyed per-user (auth["user_id"]), not per-IP, since
# the endpoint already requires a valid token. Default: 10 calls / 5 min
# -- generous for legitimate key-rotation use, well below anything that
# would matter for provider quota/cost if abused.
SECRETS_RATE_LIMIT_WINDOW_SECONDS: int = int(
    os.getenv("SECRETS_RATE_LIMIT_WINDOW_SECONDS", "300")
)
SECRETS_RATE_LIMIT_MAX_CALLS: int = int(
    os.getenv("SECRETS_RATE_LIMIT_MAX_CALLS", "10")
)


# ===========================================================================
# ── Blackboard ───────────────────────────────────────────────────────────────
# ===========================================================================

# Default TTL (seconds) for the execution blackboard key in the broker.
# Extended on every write; explicitly deleted on execution completion.
# Default: 7200 s (2 hours).  Override: BLACKBOARD_TTL_SECONDS=3600
BLACKBOARD_TTL_SECONDS: int = int(os.getenv("BLACKBOARD_TTL_SECONDS", "7200"))

# Enable AES-GCM encryption of blackboard values at rest.
# Requires the 'cryptography' package.
# Default: false.  Override: BLACKBOARD_ENCRYPT=true
BLACKBOARD_ENCRYPT: bool = os.getenv("BLACKBOARD_ENCRYPT", "").lower() in (
    "1", "true", "yes"
)


# ===========================================================================
# ── Daemon Scheduler ─────────────────────────────────────────────────────────
# ===========================================================================

# How often (seconds) the daemon scheduler wakes up to check for due daemons.
# Default: 60 s.  Override: DAEMON_SCHEDULER_POLL_SECONDS=30
DAEMON_SCHEDULER_POLL_SECONDS: int = int(
    os.getenv("DAEMON_SCHEDULER_POLL_SECONDS", "60")
)


# ===========================================================================
# ── Wakeup Scheduler (P-19 Phase 1) ────────────────────────────────────────
# ===========================================================================

# How often (seconds) the wakeup scheduler polls scheduled_wakeups for rows
# that are due (wake_at elapsed) or whose watched execution has reached a
# terminal state. Shorter than the daemon scheduler's default since a
# schedule_wakeup(watch_execution_id=...) call resolves on this cadence, not
# instantly -- kept tight so "wake me when X finishes" doesn't feel laggy.
# Default: 30 s.  Override: WAKEUP_SCHEDULER_POLL_SECONDS=15
WAKEUP_SCHEDULER_POLL_SECONDS: int = int(
    os.getenv("WAKEUP_SCHEDULER_POLL_SECONDS", "30")
)

# Ceiling on how far in the future a single schedule_wakeup call may set
# wake_at -- a real bound, not advisory, so nothing schedules itself
# indefinitely far out and leaves orphaned state forever. Also used as the
# automatic fallback ceiling when only watch_execution_id is given (no
# explicit delay_seconds), so "wake me when X finishes" can never hang
# forever if X never reaches a terminal state.
# Default: 172800 s (48 hours).  Override: WAKEUP_MAX_DELAY_SECONDS=86400
WAKEUP_MAX_DELAY_SECONDS: int = int(
    os.getenv("WAKEUP_MAX_DELAY_SECONDS", "172800")
)

# Cap on how many still-pending scheduled_wakeups a single user may have at
# once. Mirrors the spirit of MAX_REACT_STEPS/PLAN_REVISION_MAX_ROUNDS -- a
# runaway agent shouldn't be able to schedule unbounded future work.
# Default: 15.  Override: WAKEUP_MAX_PENDING_PER_USER=25
WAKEUP_MAX_PENDING_PER_USER: int = int(
    os.getenv("WAKEUP_MAX_PENDING_PER_USER", "15")
)

# How long a resolved/cancelled scheduled_wakeups row is kept before the
# daily cleanup pass purges it. Deliberately much shorter than
# AUTH_AUDIT_LOG_RETENTION_DAYS -- unlike an auth audit log, a resolved
# wakeup row has no audit/security value: the reason text it carried is
# already durably preserved in the resume trigger message written to the
# conversation, and both the original and continuation execution's full
# outcome live in execution_metrics/messages. The row itself is only
# useful briefly, for correlating execution_id -> resumed_execution_id
# while debugging.
# Default: 7 days.  Override: SCHEDULED_WAKEUP_RETENTION_DAYS=3
SCHEDULED_WAKEUP_RETENTION_DAYS: int = int(
    os.getenv("SCHEDULED_WAKEUP_RETENTION_DAYS", "7")
)


# ===========================================================================
# ── Providers ────────────────────────────────────────────────────────────────
# ===========================================================================

# TTL (seconds) for non-forced provider capability refreshes.
# After this window, the next call to refresh_from_litellm() will re-fetch
# from the LiteLLM cost map.
# Default: 3600 s (1 hour).  Override: PROVIDER_CAPABILITY_REFRESH_TTL_SECONDS=7200
PROVIDER_CAPABILITY_REFRESH_TTL_SECONDS: int = int(
    os.getenv("PROVIDER_CAPABILITY_REFRESH_TTL_SECONDS", "3600")
)


# ===========================================================================
# ── Intent Router ────────────────────────────────────────────────────────────
# ===========================================================================

# Maximum tokens allowed in the LLM classifier response (Layer 2).
# The classifier only needs to return one word ("CONVERSATIONAL" or
# "PLAN_EXECUTE") -- but a "reasoning-mandatory" model (e.g. gpt-oss) can't
# be told to skip its chain-of-thought, and that reasoning consumes this
# same token budget before the final word is ever emitted. Confirmed
# empirically: 5 tokens fails on every prompt tested against gpt-oss
# (reasoning alone exceeds it every time, regardless of provider or
# reasoning_effort setting); 100 succeeded reliably across easy and hard
# classification prompts. For a model with no mandatory reasoning this
# cap is never actually reached -- the real cost is bounded by whatever
# the model naturally needs, not by raising the ceiling.
# Default: 100.  Override: INTENT_CLASSIFIER_MAX_TOKENS=150
INTENT_CLASSIFIER_MAX_TOKENS: int = int(
    os.getenv("INTENT_CLASSIFIER_MAX_TOKENS", "100")
)

# Temperature for the LLM intent classifier.  0.0 = deterministic.
# Default: 0.0.  Override: INTENT_CLASSIFIER_TEMPERATURE=0.1
INTENT_CLASSIFIER_TEMPERATURE: float = float(
    os.getenv("INTENT_CLASSIFIER_TEMPERATURE", "0.0")
)


# ===========================================================================
# ── Pydantic-Settings classes (provider / infrastructure sub-configs) ─────────
# ===========================================================================

class ChromaSettings(BaseSettings):
    """
    Configuration for ChromaDB embedded vector database.

    Override via environment variables or eva.env:
      CHROMA_PATH       – filesystem path for the persistent DB dir
                          (default: ~/eva_workspace/chroma_db)
      CHROMA_COLLECTION – collection name (default: eva_rag)
    """
    CHROMA_PATH: str = Field(
        default_factory=lambda: os.path.join(
            os.path.expanduser("~"), "eva_workspace", "chroma_db"
        ),
        env="CHROMA_PATH",
    )
    CHROMA_COLLECTION: str = Field("eva_rag", env="CHROMA_COLLECTION")

    class Config:
        env_file = str(_PROJECT_ROOT / "eva.env")
        case_sensitive = False
        extra = "ignore"


class MCPSettings(BaseSettings):
    """Configuration for Model Context Protocol (MCP) integration."""
    ENABLE_MCP: bool = Field(True, env="ENABLE_MCP")

    class Config:
        env_file = str(_PROJECT_ROOT / "eva.env")
        case_sensitive = False
        extra = "ignore"


class OllamaSettings(BaseSettings):
    """
    Configuration for the Ollama local LLM provider.

    Override via environment variables or eva.env:
      OLLAMA_HOST           – Base URL (default: http://localhost:11434)
      OLLAMA_DEFAULT_MODEL  – Default model name (default: llama3.2:latest)
    """
    OLLAMA_HOST:          str = Field("http://localhost:11434", env="OLLAMA_HOST")
    OLLAMA_DEFAULT_MODEL: str = Field("llama3.2:latest",       env="OLLAMA_DEFAULT_MODEL")

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / "eva.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class OllamaCloudSettings(BaseSettings):
    """
    Configuration for the Ollama Cloud LLM provider (https://ollama.com).

    Ollama Cloud is a subscription-based hosted service that exposes an
    OpenAI-compatible API.  Authentication uses a bearer API key provisioned
    at https://ollama.com/settings/keys.

    The Ollama Cloud API key is NOT eva.env-configurable -- it is not a
    setting at all: it is a provider secret kept
    in the OS credential store and resolved by providers/credentials.py (set
    it via POST /secrets/ollama_cloud or the EVA UI, never in eva.env or the
    process environment).

    Only OLLAMA_CLOUD_BASE_URL is genuinely eva.env-overridable:
      OLLAMA_CLOUD_BASE_URL – Override the API base URL (default: https://ollama.com/v1)
    """
    OLLAMA_CLOUD_BASE_URL: str = Field("https://ollama.com/v1", env="OLLAMA_CLOUD_BASE_URL")

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / "eva.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class Settings(BaseSettings):
    """
    Primary application settings — includes all nested settings.
    """
    chroma:        ChromaSettings       = ChromaSettings()
    mcp:           MCPSettings          = MCPSettings()
    ollama:        OllamaSettings       = OllamaSettings()
    ollama_cloud:  OllamaCloudSettings  = OllamaCloudSettings()

    # Provider resolver model-listing cache
    # Override via: PROVIDER_MODEL_CACHE_TTL_SECONDS=600
    PROVIDER_MODEL_CACHE_TTL_SECONDS: int = Field(
        300, env="PROVIDER_MODEL_CACHE_TTL_SECONDS"
    )
    # How many hours before the litellm.model_cost map is considered stale.
    # Override via: LITELLM_COST_MAP_REFRESH_HOURS=12
    LITELLM_COST_MAP_REFRESH_HOURS: int = Field(
        24, env="LITELLM_COST_MAP_REFRESH_HOURS"
    )

    # ── Provider fallback chain — retry-before-failover (V6.5 Phase 2) ────
    # Tier A errors (rate_limit / timeout / provider_down — see
    # core/error_handling.py's ERROR_POLICIES) retry the SAME provider this
    # many times, with exponential backoff, before AgentEngine falls back
    # to the next candidate in the chain. 0 disables retry for that error
    # type entirely (falls back to the next candidate immediately, same as
    # Tier B). Consumed via `settings.FALLBACK_*` (see core/error_handling.py
    # and core/agent_engine.py) rather than a top-level import, matching
    # PROVIDER_MODEL_CACHE_TTL_SECONDS above.

    # Base delay (seconds) before the first retry. Doubles each retry.
    # Override via: FALLBACK_RETRY_BASE_DELAY_S=1.0
    FALLBACK_RETRY_BASE_DELAY_S: float = Field(
        0.5, env="FALLBACK_RETRY_BASE_DELAY_S"
    )
    # Ceiling on the backoff delay regardless of retry count.
    # Override via: FALLBACK_RETRY_MAX_DELAY_S=15
    FALLBACK_RETRY_MAX_DELAY_S: float = Field(
        8.0, env="FALLBACK_RETRY_MAX_DELAY_S"
    )
    # Max same-provider retries for a rate_limit (429) response.
    # Override via: FALLBACK_MAX_RETRIES_RATE_LIMIT=3
    FALLBACK_MAX_RETRIES_RATE_LIMIT: int = Field(
        2, env="FALLBACK_MAX_RETRIES_RATE_LIMIT"
    )
    # Max same-provider retries for a stalled-connection timeout.
    # Override via: FALLBACK_MAX_RETRIES_TIMEOUT=2
    FALLBACK_MAX_RETRIES_TIMEOUT: int = Field(
        1, env="FALLBACK_MAX_RETRIES_TIMEOUT"
    )
    # Max same-provider retries for a 5xx / connection-refused response.
    # Override via: FALLBACK_MAX_RETRIES_PROVIDER_DOWN=3
    FALLBACK_MAX_RETRIES_PROVIDER_DOWN: int = Field(
        2, env="FALLBACK_MAX_RETRIES_PROVIDER_DOWN"
    )

    # ── Provider fallback chain — cross-execution cooldown (V6.5 Phase 4) ─
    # A (user, provider, model) that exhausts Tier A retries (rate_limit /
    # timeout / provider_down -- see ERROR_POLICIES) is put into cooldown:
    # skipped without being attempted by every execution that reaches it
    # until the cooldown expires. Only Tier A -- invalid_model/
    # content_policy/auth are structural or request-specific, not "this
    # provider is currently down", so cooling down on those would be wrong.
    # No real Redis involved -- backed by core/broker/local_broker.py's
    # in-memory LocalRedisAdapter, single-process, resets on server
    # restart.

    # Initial cooldown duration (seconds). Doubles on each consecutive
    # cooldown-triggering failure for the same (user, provider, model).
    # Override via: FALLBACK_COOLDOWN_BASE_SECONDS=60
    FALLBACK_COOLDOWN_BASE_SECONDS: float = Field(
        30.0, env="FALLBACK_COOLDOWN_BASE_SECONDS"
    )
    # Ceiling on the cooldown duration regardless of streak length.
    # Override via: FALLBACK_COOLDOWN_MAX_SECONDS=1800
    FALLBACK_COOLDOWN_MAX_SECONDS: float = Field(
        900.0, env="FALLBACK_COOLDOWN_MAX_SECONDS"
    )
    # How long the consecutive-failure streak counter survives before
    # resetting to a fresh start at the base cooldown duration. Longer than
    # the max cooldown itself so the streak isn't lost the instant a
    # cooldown expires -- a failure right after expiry should escalate,
    # not restart at the base duration.
    # Override via: FALLBACK_COOLDOWN_STREAK_TTL_SECONDS=7200
    FALLBACK_COOLDOWN_STREAK_TTL_SECONDS: int = Field(
        3600, env="FALLBACK_COOLDOWN_STREAK_TTL_SECONDS"
    )

    class Config:
        env_file = str(_PROJECT_ROOT / "eva.env")
        case_sensitive = False
        extra = "ignore"


# Module-level singleton for application import
settings = Settings()


# ===========================================================================
# ── Workspace & Storage ──────────────────────────────────────────────────────
# ===========================================================================

# Root directory for all user workspace files.
# Override: EVA_WORKSPACE_ROOT=/mnt/persistent/eva_workspace
WORKSPACE_ROOT: str = os.getenv(
    "EVA_WORKSPACE_ROOT",
    os.path.join(os.path.expanduser("~"), "eva_workspace"),
)

# Number of days a conversation scratch dir must be untouched before the daily
# cleanup task is allowed to delete it.  Applies only to
# ~/eva_workspace/{user_id}/conversations/{conv_id}/ subdirs — never to the
# main workspace, projects, rag, memory, skills, or hooks dirs.
# Override: EVA_WORKSPACE_TTL_DAYS=30
WORKSPACE_TTL_DAYS: int = int(os.getenv("EVA_WORKSPACE_TTL_DAYS", "90"))

# How often (seconds) the workspace cleanup background loop runs.
# Override: EVA_CLEANUP_INTERVAL_SECONDS=3600
WORKSPACE_CLEANUP_INTERVAL_SECONDS: int = int(
    os.getenv("EVA_CLEANUP_INTERVAL_SECONDS", "86400")
)

# B2-4.1 (2026-08-18): retry-with-backoff for each of the daily cleanup
# passes (workspace, auth retention, wakeup retention, background process,
# rate limiter keys). Previously a single failed pass just logged a
# WARNING and waited for the next scheduled run, up to
# WORKSPACE_CLEANUP_INTERVAL_SECONDS later (default 24h) -- a transient
# failure (e.g. a momentarily locked DB file) could go unnoticed for
# weeks with only a buried log line. Reuses the same exponential-backoff
# shape core/daemons/registry.py's daemon retry policy already
# established (delay * 2^(attempt-1), capped) rather than inventing a
# second retry idiom.
# Default: 3 attempts.  Override: CLEANUP_RETRY_MAX_ATTEMPTS=5
CLEANUP_RETRY_MAX_ATTEMPTS: int = int(
    os.getenv("CLEANUP_RETRY_MAX_ATTEMPTS", "3")
)
# Default: 60 s.  Override: CLEANUP_RETRY_BASE_DELAY_SECONDS=30
CLEANUP_RETRY_BASE_DELAY_SECONDS: int = int(
    os.getenv("CLEANUP_RETRY_BASE_DELAY_SECONDS", "60")
)
# Cap on the backoff delay -- deliberately much shorter than the daemon
# retry policy's 3600s cap, since these retries happen within a single
# cleanup cycle, not spread across days.
# Default: 300 s (5 min).  Override: CLEANUP_RETRY_MAX_DELAY_SECONDS=600
CLEANUP_RETRY_MAX_DELAY_SECONDS: int = int(
    os.getenv("CLEANUP_RETRY_MAX_DELAY_SECONDS", "300")
)

# DB-01: how many days auth_audit_logs rows (login/logout/failed-attempt
# history) are retained before the daily cleanup pass deletes them. This is
# a separate, longer-lived policy than auth_sessions itself (purged as soon
# as a session is dead -- see AuthRepo.purge_expired_sessions) since the
# audit log is the actual security record you'd want to check after the
# fact ("did someone try to get in while I was away"). No regulatory
# retention requirement applies to EVA; 180 days is a reasonable default
# in line with common security-log retention practice, not a fixed
# compliance number -- override freely for your own deployment.
# Override: AUTH_AUDIT_LOG_RETENTION_DAYS=90
AUTH_AUDIT_LOG_RETENTION_DAYS: int = int(
    os.getenv("AUTH_AUDIT_LOG_RETENTION_DAYS", "180")
)


# ===========================================================================
# ── Logging ──────────────────────────────────────────────────────────────────
# ===========================================================================

# Absolute directory for all server log files.
# In production / Electron packaging, the shell should set EVA_LOG_DIR to the
# OS user-data path (e.g. %APPDATA%\EVA\logs) before spawning the backend.
# This guarantees logs are written to a known writable location regardless of
# the process working directory at launch time.
# Dev fallback: <project_root>/logs/
LOG_DIR: str = os.getenv(
    "EVA_LOG_DIR",
    str(_PROJECT_ROOT / "logs"),
)

# ===========================================================================
# ── Execution runtime markers (B2-1.7/4.3) ──────────────────────────────────
# ===========================================================================

# Directory for file-based liveness markers written by the blocking
# approval/plan-approval/step-extension gates (core/execution/gate_markers.py).
# Deliberately NOT a DB table -- these gates only ever need to survive long
# enough to let a boot-time scan detect "this execution was paused when the
# process died" and fail it cleanly; a SQL row would blur the deliberate
# design boundary that SQLite only records completed executions (see
# core/db/models.py::ExecutionMetrics). One small file per currently-paused
# execution; deleted the moment the gate resolves or boot-reconciliation
# processes it -- never becomes durable history.
#
# main.js already passes EVA_CONFIG_DIR (Electron's app.getPath('userData'),
# e.g. %APPDATA%\EVA on Windows -- the same root EVA_LOG_DIR/DATABASE_URL are
# already derived from) to the packaged backend; reuse it here rather than
# adding a new Electron-side env var. Dev fallback: <project_root>/.runtime/executions/
EVA_CONFIG_DIR: Optional[str] = os.getenv("EVA_CONFIG_DIR")

EXECUTION_RUNTIME_DIR: str = os.getenv(
    "EVA_EXECUTION_RUNTIME_DIR",
    str(pathlib.Path(EVA_CONFIG_DIR) / "runtime" / "executions") if EVA_CONFIG_DIR
    else str(_PROJECT_ROOT / ".runtime" / "executions"),
)

# Per-file size cap (bytes) before a mid-day size rotation is triggered.
# Daily midnight rotation always happens regardless of size.
# Override: EVA_LOG_SERVER_MAX_BYTES / EVA_LOG_OBS_MAX_BYTES / EVA_LOG_ERROR_MAX_BYTES
LOG_SERVER_MAX_BYTES: int = int(os.getenv("EVA_LOG_SERVER_MAX_BYTES", str(50  * 1024 * 1024)))  # 50 MB
LOG_OBS_MAX_BYTES:    int = int(os.getenv("EVA_LOG_OBS_MAX_BYTES",    str(100 * 1024 * 1024)))  # 100 MB
LOG_ERROR_MAX_BYTES:  int = int(os.getenv("EVA_LOG_ERROR_MAX_BYTES",  str(10  * 1024 * 1024)))  # 10 MB

# Number of rotated files to retain per log stream (rollover events, not days).
# With hybrid rotation a "rollover" is either a midnight flip or a size overflow,
# so retention is measured in files rather than calendar days.
# Override: EVA_LOG_SERVER_BACKUP_COUNT / EVA_LOG_OBS_BACKUP_COUNT / EVA_LOG_ERROR_BACKUP_COUNT
LOG_SERVER_BACKUP_COUNT: int = int(os.getenv("EVA_LOG_SERVER_BACKUP_COUNT", "30"))
LOG_OBS_BACKUP_COUNT:    int = int(os.getenv("EVA_LOG_OBS_BACKUP_COUNT",    "60"))
LOG_ERROR_BACKUP_COUNT:  int = int(os.getenv("EVA_LOG_ERROR_BACKUP_COUNT",  "90"))

# Whether OBS_EVENTs propagate to the root logger (server.log) in addition to
# observability.log.  False = clean split (recommended for production).
# True = OBS_EVENTs appear in both files (useful for local debugging).
# Override: EVA_LOG_OBS_PROPAGATE=true
LOG_OBS_PROPAGATE: bool = os.getenv("EVA_LOG_OBS_PROPAGATE", "false").lower() in ("1", "true", "yes")
