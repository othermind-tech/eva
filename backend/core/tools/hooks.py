# core/tools/hooks.py
"""
B-053 — PreToolUse / PostToolUse Hook System.

Hooks are a programmatic intercept layer that fires before and after every
tool call inside the ToolDispatcher.  They are distinct from the Human-in-the-
Loop (HitL) approval gate:

    Agent wants to call a tool
            ↓
    [Layer 1: PreToolUse Hook]   ← this file (run_pre_hooks)
      → Can: allow, deny, mutate_args
      → If denied: HitL never fires; LLM receives an error result
            ↓
    [Layer 2: HitL Approval Gate]   ← core/tools/approval.py (unchanged)
      → Fires per tool_approval_mode
      → Sees post-hook (potentially mutated) arguments
            ↓
    [Tool Executes]   ← unchanged
            ↓
    [Layer 3: PostToolUse Hook]   ← this file (run_post_hooks)
      → Can: mutate result before the LLM sees it
            ↓
    Result returned to LLM

Hook definitions are stored in the user's session under the key "tool_hooks"
as a JSON list of HookDefinition objects (see api/schemas/requests.py):

    [
      {"tool": "*",         "event": "pre",  "handler": "audit_log"},
      {"tool": "shell",     "event": "pre",  "handler": "path_guard",
       "config": {"allowed_prefixes": ["/home/user/projects"]}},
      {"tool": "read_file", "event": "post", "handler": "redact_env_vars"},
    ]

Built-in handlers
─────────────────
  audit_log       Pre + Post  Log the tool name and arguments / result to the
                              application logger.  Useful for compliance trails.

  deny_always     Pre         Unconditionally deny the tool call and return
                              a denial message to the LLM.  Useful for
                              blacklisting specific tools without removing them
                              from the tool set.

  path_guard      Pre         Reject any file/shell tool call whose first
                              path-like argument is outside the configured
                              allowed_prefixes list.  Prevents path traversal
                              outside the project workspace.

  redact_env_vars Post        Scan every string in the result (recursing into
                              dicts/lists -- e.g. shell's {"stdout": ...}) for
                              patterns that look like environment-variable
                              assignments (KEY=value) and replace the value
                              with "***". Prevents leaking secrets that tools
                              may echo.

  redact_pii      Post        Scan every string in the result (recursing into
                              dicts/lists) for PII (email, phone, credit card,
                              SSN, bank/passport/license numbers) using an
                              offline NLP engine (Microsoft Presidio) and
                              replace each match with a placeholder like
                              "<EMAIL_ADDRESS>".
                              Config: {"entities": [...]} to override the
                              default entity list (core/security/
                              pii_redactor.py's DEFAULT_PII_ENTITIES).
                              S-03.
"""

import logging
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

# PreToolUse result:
#   {"action": "allow", "args": <dict>}          — proceed (args may be mutated)
#   {"action": "deny",  "reason": <str>}          — block the tool call
PreHookResult = Dict[str, Any]

# PostToolUse result:
#   {"result": <Any>}    — result (may be mutated)
PostHookResult = Dict[str, Any]


# ---------------------------------------------------------------------------
# Built-in handler implementations
# ---------------------------------------------------------------------------

def _handler_audit_log_pre(
    tool_name: str,
    args: Dict[str, Any],
    config: Optional[Dict[str, Any]],
    execution_obj: Any,
) -> PreHookResult:
    """Log tool call before execution."""
    user_id = getattr(execution_obj, "user_id", "unknown")
    execution_id = getattr(execution_obj, "execution_id", "unknown")
    logger.info(
        "[HOOK:audit_log] pre-tool",
        extra={
            "hook_event": "pre",
            "tool_name": tool_name,
            "user_id": user_id,
            "execution_id": execution_id,
            "args_keys": list(args.keys()),
        },
    )
    return {"action": "allow", "args": args}


def _handler_audit_log_post(
    tool_name: str,
    result: Any,
    config: Optional[Dict[str, Any]],
    execution_obj: Any,
) -> PostHookResult:
    """Log tool result after execution."""
    user_id = getattr(execution_obj, "user_id", "unknown")
    execution_id = getattr(execution_obj, "execution_id", "unknown")
    result_preview = str(result)[:200] if result is not None else "<none>"
    logger.info(
        "[HOOK:audit_log] post-tool",
        extra={
            "hook_event": "post",
            "tool_name": tool_name,
            "user_id": user_id,
            "execution_id": execution_id,
            "result_preview": result_preview,
        },
    )
    return {"result": result}


def _handler_deny_always_pre(
    tool_name: str,
    args: Dict[str, Any],
    config: Optional[Dict[str, Any]],
    execution_obj: Any,
) -> PreHookResult:
    """Unconditionally deny the tool call."""
    reason = (config or {}).get(
        "reason",
        f"Tool '{tool_name}' is blocked by a deny_always hook policy.",
    )
    logger.warning(
        "[HOOK:deny_always] blocking tool call",
        extra={"tool_name": tool_name},
    )
    return {"action": "deny", "reason": reason}


def _handler_path_guard_pre(
    tool_name: str,
    args: Dict[str, Any],
    config: Optional[Dict[str, Any]],
    execution_obj: Any,
) -> PreHookResult:
    """
    Reject tool calls whose first path-like argument is outside the configured
    allowed_prefixes list.

    Config keys:
        allowed_prefixes : List[str]  — list of allowed absolute path prefixes.
                           Empty list means "deny all absolute paths".
    """
    allowed_prefixes: List[str] = (config or {}).get("allowed_prefixes", [])

    # Collect all string argument values that look like paths (start with / or ~
    # or drive letter on Windows).
    def _looks_like_path(v: str) -> bool:
        return (
            v.startswith("/")
            or v.startswith("~")
            or (len(v) >= 3 and v[1] == ":" and v[2] in ("/", "\\"))
        )

    path_args = [v for v in args.values() if isinstance(v, str) and _looks_like_path(v)]

    for path in path_args:
        expanded = os.path.abspath(os.path.expanduser(path))
        if not any(expanded.startswith(prefix) for prefix in allowed_prefixes):
            logger.warning(
                "[HOOK:path_guard] path outside allowed prefixes — blocking",
                extra={
                    "tool_name": tool_name,
                    "path": path,
                    "allowed_prefixes": allowed_prefixes,
                },
            )
            return {
                "action": "deny",
                "reason": (
                    f"Path '{path}' is outside the allowed workspace prefixes. "
                    f"Allowed: {allowed_prefixes}"
                ),
            }

    return {"action": "allow", "args": args}


# Matches patterns like:  KEY=value  or  KEY="value"  or  SOME_VAR=secret123
_ENV_VAR_RE = re.compile(
    r'(?<![A-Z0-9_])([A-Z][A-Z0-9_]{2,})(=)([^\s\n"\']{1,200})',
    re.MULTILINE,
)


def _walk_and_redact_strings(value: Any, redact_fn) -> Any:
    """
    Recursively apply redact_fn(str) -> str to every string reachable from
    *value*, rebuilding dicts/lists with the same shape and passing every
    other type (int, float, bool, None) through unchanged.

    Real EVA tools return their own native result shape -- read_file:
    {"content": ...}; shell: {"cmd": [...], "stdout": ..., "exit_code": ...}
    -- not a single flat string. A redaction handler that only checked
    `isinstance(result, str)` (the original design of both handlers below)
    silently never redacted anything for these tools: the top-level value is
    a dict, so the whole check short-circuited to "pass through unchanged"
    before ever looking at the string fields nested inside it. Confirmed
    live 2026-09-16 -- see core/hooks/runner.py's _run_builtin_post for the
    matching fix on the extraction side of this same bug.
    """
    if isinstance(value, str):
        return redact_fn(value)
    if isinstance(value, dict):
        return {k: _walk_and_redact_strings(v, redact_fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_and_redact_strings(v, redact_fn) for v in value]
    return value


def _handler_redact_env_vars_post(
    tool_name: str,
    result: Any,
    config: Optional[Dict[str, Any]],
    execution_obj: Any,
) -> PostHookResult:
    """
    Scan every string reachable from the result (recursing into dicts/lists,
    e.g. shell's {"stdout": ..., "cmd": [...]}) for environment-variable-like
    assignments and redact the value portion with '***'.
    """
    _count = 0

    def _redact_str(s: str) -> str:
        nonlocal _count
        redacted, n = _ENV_VAR_RE.subn(r"\1\2***", s)
        _count += n
        return redacted

    redacted_result = _walk_and_redact_strings(result, _redact_str)
    if _count > 0:
        logger.info(
            "[HOOK:redact_env_vars] redacted %d env-var value(s) from result",
            _count,
            extra={"tool_name": tool_name},
        )
    return {"result": redacted_result}


def _handler_redact_pii_post(
    tool_name: str,
    result: Any,
    config: Optional[Dict[str, Any]],
    execution_obj: Any,
) -> PostHookResult:
    """
    Scan every string reachable from the result (recursing into dicts/lists,
    e.g. shell's {"stdout": ..., "cmd": [...]}) for PII (S-03) using an
    offline NLP engine (Microsoft Presidio) and replace each match with a
    placeholder like "<EMAIL_ADDRESS>".

    Config keys:
        entities : List[str] — overrides the default entity allowlist
                   (core/security/pii_redactor.py's DEFAULT_PII_ENTITIES).
    """
    from core.security.pii_redactor import redact_pii  # noqa: PLC0415

    cfg = config or {}
    entities = cfg.get("entities")
    _total_count = 0

    def _redact_str(s: str) -> str:
        nonlocal _total_count
        redacted, n = redact_pii(s, entities=entities)
        _total_count += n
        return redacted

    redacted_result = _walk_and_redact_strings(result, _redact_str)
    if _total_count > 0:
        logger.info(
            "[HOOK:redact_pii] redacted %d PII entit(y/ies) from result",
            _total_count,
            extra={"tool_name": tool_name},
        )
    return {"result": redacted_result}


# ---------------------------------------------------------------------------
# Rate-limit handler — in-memory, per-user, per-tool, time-windowed (B-076)
# ---------------------------------------------------------------------------

# Structure: {(user_id, tool_name, window_start): call_count}
# window_start is the integer epoch second aligned to window_seconds
_RATE_LIMIT_STORE: Dict[tuple, int] = defaultdict(int)

# Remove stale windows older than 2× the max configured window to bound memory.
_RATE_LIMIT_MAX_AGE_SECONDS = 3600  # 1 hour; cleaned on each check
_rate_limit_last_gc: float = 0.0


def _rate_limit_gc(now: float) -> None:
    """Evict entries older than _RATE_LIMIT_MAX_AGE_SECONDS (called infrequently)."""
    global _rate_limit_last_gc
    if now - _rate_limit_last_gc < 60:  # only clean at most once per minute
        return
    _rate_limit_last_gc = now
    cutoff = now - _RATE_LIMIT_MAX_AGE_SECONDS
    stale_keys = [k for k in _RATE_LIMIT_STORE if k[2] < cutoff]
    for k in stale_keys:
        del _RATE_LIMIT_STORE[k]


def _handler_rate_limit_pre(
    tool_name: str,
    args: Dict[str, Any],
    config: Optional[Dict[str, Any]],
    execution_obj: Any,
) -> PreHookResult:
    """
    Reject tool calls that exceed a configurable rate limit.

    Config keys:
        window_seconds : int  — sliding window length in seconds (default 60).
        max_calls      : int  — maximum calls allowed per window (default 10).

    Keyed by (user_id, tool_name, window_start) so limits are per-user,
    per-tool, per time-window.  Uses an in-memory store — limits reset on
    server restart (intentional; no Redis dependency).
    """
    cfg = config or {}
    window_seconds = int(cfg.get("window_seconds", 60))
    max_calls = int(cfg.get("max_calls", 10))

    user_id = getattr(execution_obj, "user_id", "unknown")
    now = time.monotonic()

    # Periodic GC — use wall-clock for age checks (monotonic works here too)
    _rate_limit_gc(now)

    window_start = int(now // window_seconds) * window_seconds
    key = (user_id, tool_name, window_start)
    _RATE_LIMIT_STORE[key] += 1
    call_count = _RATE_LIMIT_STORE[key]

    if call_count > max_calls:
        remaining = window_start + window_seconds - now
        logger.warning(
            "[HOOK:rate_limit] rate limit exceeded",
            extra={
                "tool_name": tool_name,
                "user_id": user_id,
                "call_count": call_count,
                "max_calls": max_calls,
                "window_seconds": window_seconds,
                "retry_after_seconds": round(remaining, 1),
            },
        )
        return {
            "action": "deny",
            "reason": (
                f"Rate limit exceeded for tool '{tool_name}': "
                f"{call_count} calls in the last {window_seconds}s "
                f"(max {max_calls}). "
                f"Retry in {round(remaining, 0):.0f}s."
            ),
        }

    logger.debug(
        "[HOOK:rate_limit] call counted",
        extra={
            "tool_name": tool_name,
            "user_id": user_id,
            "call_count": call_count,
            "max_calls": max_calls,
        },
    )
    return {"action": "allow", "args": args}


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_PRE_HANDLERS: Dict[str, Any] = {
    "audit_log":    _handler_audit_log_pre,
    "deny_always":  _handler_deny_always_pre,
    "path_guard":   _handler_path_guard_pre,
    "rate_limit":   _handler_rate_limit_pre,
}

_POST_HANDLERS: Dict[str, Any] = {
    "audit_log":      _handler_audit_log_post,
    "redact_env_vars": _handler_redact_env_vars_post,
    "redact_pii":      _handler_redact_pii_post,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_pre_hooks(
    tool_name: str,
    args: Dict[str, Any],
    hook_definitions: List[Dict[str, Any]],
    execution_obj: Any,
) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """
    Run all matching PreToolUse hooks for *tool_name*.

    Parameters
    ----------
    tool_name        : Name of the tool about to be called.
    args             : Current arguments dict (may be mutated by hooks).
    hook_definitions : List of hook dicts from the user session ("tool_hooks").
    execution_obj    : ConversationExecution — passed to every handler.

    Returns
    -------
    (action, final_args, denial_reason)

    action        : "allow" if all hooks pass, "deny" if any hook blocked.
    final_args    : The (possibly mutated) arguments dict.
    denial_reason : Set when action == "deny"; the human-readable block reason.
    """
    current_args = dict(args)  # shallow copy so we don't mutate the caller's dict

    for hook in hook_definitions:
        event = hook.get("event", "pre")
        if event != "pre":
            continue

        target_tool = hook.get("tool", "*")
        if target_tool != "*" and target_tool != tool_name:
            continue

        handler_name = hook.get("handler", "")
        handler_fn = _PRE_HANDLERS.get(handler_name)

        if handler_fn is None:
            logger.warning(
                "[HOOK] Unknown pre-hook handler '%s' — skipping",
                handler_name,
                extra={"tool_name": tool_name},
            )
            continue

        config = hook.get("config") or {}
        result = handler_fn(tool_name, current_args, config, execution_obj)

        if result.get("action") == "deny":
            return "deny", current_args, result.get("reason", "Blocked by hook policy.")

        # Handler may return mutated args
        if "args" in result:
            current_args = result["args"]

    return "allow", current_args, None


def run_post_hooks(
    tool_name: str,
    result: Any,
    hook_definitions: List[Dict[str, Any]],
    execution_obj: Any,
) -> Any:
    """
    Run all matching PostToolUse hooks for *tool_name*.

    Parameters
    ----------
    tool_name        : Name of the tool that just ran.
    result           : The raw tool output (string, dict, list, etc.).
    hook_definitions : List of hook dicts from the user session ("tool_hooks").
    execution_obj    : ConversationExecution — passed to every handler.

    Returns
    -------
    The (possibly mutated) result that will be returned to the LLM.
    """
    current_result = result

    for hook in hook_definitions:
        event = hook.get("event", "post")
        if event != "post":
            continue

        target_tool = hook.get("tool", "*")
        if target_tool != "*" and target_tool != tool_name:
            continue

        handler_name = hook.get("handler", "")
        handler_fn = _POST_HANDLERS.get(handler_name)

        if handler_fn is None:
            logger.warning(
                "[HOOK] Unknown post-hook handler '%s' — skipping",
                handler_name,
                extra={"tool_name": tool_name},
            )
            continue

        config = hook.get("config") or {}
        hook_result = handler_fn(tool_name, current_result, config, execution_obj)
        current_result = hook_result.get("result", current_result)

    return current_result
