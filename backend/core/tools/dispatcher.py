# core/tools/dispatcher.py

import asyncio
import json
from typing import Dict, Any, Optional
import logging, time
from logging import getLogger
from pydantic import ValidationError

from core.tools.base import ToolResult, ToolAuthority
from core.memory.token_counter import TokenCounter
from core.types import (
    ToolExecutionResult,
    ToolErrorType,
    ToolExecutionStatus,
)
from core.event_bus import event_bus
from core.events import (
    ToolCalled,
    ToolResult as ToolResultEvent,
    ToolPermissionDenied,
)
from core.tools.tool_adapter import build_toolbase_subclass_from_manifest
from core.tools.approval import gate_approval, is_conversation_approved
from core.hooks import HookRunner
from config.settings import (
    get_user_session,
    ToolApprovalMode,
    TOOL_EXECUTION_TIMEOUT_SECONDS,
    TOOL_MAX_RETRY_ATTEMPTS,
)

# Kept for backward compatibility: AgentEngine reads
# `getattr(dispatcher, "observability", None)` and falls back to
# NullObservabilityHook when the attribute is absent.  Removing this import
# would make the attribute-lookup return None (harmless), but we leave the
# import so existing call sites that pass `observability=` still type-check.
from observability.null import NullObservabilityHook
from observability.base import ObservabilityHook


logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# Context-budget watchdog (V6.11) -- Layer 1: dynamic per-call output cap
# ---------------------------------------------------------------------------
# Generalizes core/shell/executor.py's static SHELL_MAX_OUTPUT_BYTES
# truncation (a fixed byte cap, shell-only) into a dispatcher-level cap
# applied to EVERY tool's result, sized dynamically from how much context
# budget actually remains for this execution rather than a fixed constant.
# This is the safety net for a single oversized tool result (a huge
# web_search page, a large shell dump) blowing the whole context window in
# one shot -- the separate, complementary problem of many individually-
# reasonable tool results accumulating over a long ReAct loop is handled by
# the proactive history-compression watchdog in agent_engine.py.
#
# Deliberately scoped to post-hoc truncation of the materialized result,
# not live mid-execution cancellation of the underlying tool call. True
# streaming cancellation would need per-tool progress instrumentation most
# tools don't have (they buffer-then-return); the existing shell truncation
# precedent already establishes that capping the returned text is an
# accepted, sufficient mitigation for this codebase.

def _cap_tool_output(raw_output: Any, max_tokens: int, model_name: str) -> Any:
    """
    Truncate a tool's raw result if it would exceed max_tokens against the
    given model's tokenizer. Returns raw_output unchanged when under budget
    or when max_tokens/raw_output make capping inapplicable (empty output).

    Non-string results (dict/list/etc.) are JSON-serialized before sizing
    and, if over budget, replaced with a truncated string -- the model only
    ever consumes tool results as text in its context regardless of the
    original Python type, so this matches what actually counts against the
    budget. Structure is not preserved on truncation, same trade-off
    core/shell/executor.py's existing byte-cap already makes.
    """
    if raw_output is None or max_tokens <= 0:
        return raw_output

    is_str = isinstance(raw_output, str)
    text = raw_output if is_str else _safe_json_dumps(raw_output)

    tokens = TokenCounter.estimate(text, model_name)
    if tokens <= max_tokens:
        return raw_output

    # Approximate proportional cut -- a safety net, not an exact tokenizer
    # boundary. Good enough to land close to max_tokens without a per-call
    # binary search; being slightly under is fine, slightly over defeats
    # the point.
    ratio = (max_tokens / tokens) if tokens else 1.0
    cut_at = max(0, int(len(text) * ratio))
    marker = f"\n[...truncated by EVA — tool result exceeded remaining context budget ({max_tokens} tokens)]"

    return text[:cut_at] + marker


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
# Approval gate helper
# ---------------------------------------------------------------------------

async def _should_require_approval(
    tool_cls,
    execution_obj,
    user_id: str,
    conversation_id: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Determine whether the Human-in-the-Loop gate should fire for this tool call.

    Returns True  → gate required; dispatcher must pause and await user decision.
    Returns False → proceed immediately (auto-approve).

    Decision matrix:
    ┌──────────────────────┬───────────────────────────────────────┐
    │ tool_approval_mode   │ behaviour                             │
    ├──────────────────────┼───────────────────────────────────────┤
    │ auto (default)       │ never gate                            │
    │ always_ask           │ gate every mutating/destructive call  │
    │ per_conversation     │ gate first call in conversation only  │
    └──────────────────────┴───────────────────────────────────────┘
    READ_ONLY tools never trigger the gate regardless of mode.

    B2-1.1 (2026-08-18): one exception to the mode matrix above, checked
    FIRST and unconditionally -- a MUTATING/DESTRUCTIVE tool whose
    path_arg_name argument resolves outside the caller's workspace root
    always gates, even under tool_approval_mode="auto". This is deliberately
    the only forced case: resolve_path_for_user() (utils/workspace.py) is
    intentionally unrestricted (EVA manages files anywhere on the user's
    machine, not just a sandbox) and tool_approval_mode="auto" is
    intentionally ungated by default -- combined, an LLM turn (including one
    steered by prompt injection from fetched/RAG content) could otherwise
    write/overwrite/delete an arbitrary out-of-workspace file with zero
    human awareness.
    """
    # READ_ONLY tools are never gated.
    authority = getattr(tool_cls, "authority", ToolAuthority.READ_ONLY)
    if authority == ToolAuthority.READ_ONLY:
        return False

    # No execution context → can't look up user; skip gate.
    if execution_obj is None or not user_id:
        return False

    # B2-1.1: out-of-workspace destructive/mutating call -- forced gate,
    # independent of tool_approval_mode.
    path_arg_name = getattr(tool_cls, "path_arg_name", None)
    if path_arg_name and arguments:
        target_path = arguments.get(path_arg_name)
        if target_path:
            from utils.workspace import is_path_in_workspace
            in_workspace = is_path_in_workspace(
                user_id,
                target_path,
                conversation_id=conversation_id,
                project_workspace=getattr(execution_obj, "project_workspace", None),
            )
            if not in_workspace:
                return True

    session = get_user_session(user_id)
    mode = session.get("tool_approval_mode", ToolApprovalMode.AUTO.value)

    if mode == ToolApprovalMode.AUTO.value:
        return False

    if mode == ToolApprovalMode.ALWAYS_ASK.value:
        return True

    if mode == ToolApprovalMode.PER_CONVERSATION.value:
        # If the user already approved once in this conversation, auto-proceed.
        return not await is_conversation_approved(conversation_id)

    # Unknown mode → safe default: do not gate.
    return False


class ToolDispatcher:
    """
    Executes a user-specific tool.

    Responsibilities:
    - Validate arguments
    - Enforce allowed_authorities session policy
    - Check Human-in-the-Loop approval gate (mutating/destructive tools)
    - Execute tool with per-invocation timeout
    - Classify failures
    - Retry transient failures deterministically
    """

    MAX_ATTEMPTS = TOOL_MAX_RETRY_ATTEMPTS

    @staticmethod
    async def execute_tool(
        call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_classes: Dict[str, Any],  # user-specific tool dict
        execution_metadata: Dict[str, Any] | None = None,
        observability: ObservabilityHook = NullObservabilityHook(),  # kept for compat
        dynamic_registry: Optional[Any] = None,
        max_result_tokens: Optional[int] = None,
        model_name: Optional[str] = None,
    ) -> ToolExecutionResult:
        """
        Executes a tool from the user-specific tool_classes mapping.

        max_result_tokens, model_name (V6.11): when both are provided, the
        tool's raw result is capped to max_result_tokens (estimated against
        model_name's tokenizer) before being returned -- see
        _cap_tool_output() above. Caller (core/agent_engine.py) computes
        max_result_tokens from remaining context headroom per step; None
        (the default) disables capping entirely, e.g. for callers that
        don't track a context budget.

        Returns:
            ToolExecutionResult (Pydantic)
        Raises:
            TypeError / RuntimeError ONLY for dispatcher misuse.
            All tool-level failures are captured in ToolExecutionResult.

        Events published:
            ToolCalled              — once per attempt (including retries)
            ToolResultEvent         — once per resolved outcome (success or failure)
            ToolPermissionDenied    — instead of ToolResultEvent on PermissionError
            ToolApprovalRequested   — when the gate fires (approval pending)
            ToolApprovalResolved    — when the gate resolves (approved/rejected)
        """
        # ----------------------------------------------------------------
        # Extract execution context from metadata
        # ----------------------------------------------------------------
        execution_obj    = (execution_metadata or {}).get("execution")
        execution_id: str = getattr(execution_obj, "execution_id", "") or ""
        user_id: str      = getattr(execution_obj, "user_id", "") or ""
        conversation_id: str = getattr(execution_obj, "conversation_id", "") or ""

        # --- HARD CONTRACT CHECK ---
        if not isinstance(arguments, dict):
            raise TypeError(
                f"ToolDispatcher expected dict arguments for tool '{tool_name}', "
                f"got {type(arguments).__name__}"
            )

        # ----------------------------------------------------------------
        # 1. Fetch tool class (user-specific, no global registry)
        # ----------------------------------------------------------------
        tool_cls = tool_classes.get(tool_name)

        # Fallback: Try dynamic registry
        if not tool_cls and dynamic_registry:
            dynamic_manifest = dynamic_registry.get(tool_name)
            if dynamic_manifest:
                logger.info(
                    "Tool dynamically resolved",
                    extra={"tool_name": tool_name, "source": "dynamic_registry"},
                )
                tool_cls = build_toolbase_subclass_from_manifest(dynamic_manifest)
                tool_classes[tool_name] = tool_cls

        # Still not found — check whether this is a progressive-disclosure miss:
        # the tool exists in the full registry but was never promoted via
        # describe_tool before being called.  This happens when the model reads
        # the tool name from the Available Tools manifest and calls it directly
        # without the required describe_tool activation step.
        #
        # Strategy: auto-promote the tool AND EXECUTE THE CALL. Progressive
        # disclosure is a token-saving device (schemas are shown on demand), not a
        # permission boundary -- the tool is already in this user's registry, so
        # authority policy, PreToolUse hooks and the approval gate below still
        # apply exactly as for any other call. Previously this branch returned a
        # "not_yet_active" stub and made the model repeat the call on its next
        # turn; live runs showed that costing one step per first-used tool and,
        # in one run, eating the final edit_file call just before the step limit.
        # Bad arguments (the model had not seen the schema) come back as a normal
        # validation failure, which carries the schema errors.
        _auto_promoted = False
        if not tool_cls:
            _exec_obj_miss = (execution_metadata or {}).get("execution")
            _user_reg_miss = getattr(_exec_obj_miss, "user_registry", None)
            if (
                _user_reg_miss is not None
                and getattr(_user_reg_miss, "_is_progressive", False)
                and tool_name in _user_reg_miss.all_tools()
            ):
                _user_reg_miss.promote(tool_name)
                tool_cls = _user_reg_miss.all_tools()[tool_name]
                tool_classes[tool_name] = tool_cls
                _auto_promoted = True
                logger.warning(
                    "Progressive gate: tool called without describe_tool — auto-promoted, executing",
                    extra={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "execution_id": execution_id,
                    },
                )
            else:
                # Genuinely not available for this user (not in registry at all)
                logger.error(
                    "Tool not found",
                    extra={"tool_name": tool_name},
                )
                raise RuntimeError(f"Tool '{tool_name}' not available for this user.")

        # ----------------------------------------------------------------
        # 2. Validate args via the tool's args_schema (Pydantic)
        # ----------------------------------------------------------------
        schema = getattr(tool_cls, "args_schema", None)
        if not schema:
            logger.error(
                "Tool schema not defined",
                extra={"tool_name": tool_name},
            )
            raise RuntimeError(f"Tool '{tool_name}' does not define args_schema.")

        # ----------------------------
        # ARGUMENT VALIDATION
        # ----------------------------
        try:
            parsed_args = schema.parse_obj(arguments)
        except ValidationError as ve:
            logger.warning(
                "Tool argument validation failed",
                extra={
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "error": str(ve),
                },
            )
            event_bus.publish(
                ToolResultEvent(
                    execution_id=execution_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    success=False,
                    latency_ms=0,
                    retry_count=0,
                    error_type="validation",
                )
            )
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILURE,
                error_type=ToolErrorType.VALIDATION,
                error_message=str(ve),
                retryable=False,
                metadata={
                    "tool": tool_name,
                    "call_id": call_id,
                },
            )

        # ----------------------------------------------------------------
        # 2b. Allowed-authority enforcement
        #     Check whether the user's session permits tools of this
        #     authority level.  Runs after arg validation but before the
        #     approval gate so the tool never executes without permission.
        # ----------------------------------------------------------------
        if user_id:
            session = get_user_session(user_id)
            allowed_authorities_str = session.get(
                "allowed_authorities", "read_only,mutating,destructive"
            )
            allowed_set = set(allowed_authorities_str.split(","))
            tool_authority_value = getattr(
                tool_cls, "authority", ToolAuthority.READ_ONLY
            ).value  # e.g. "read_only", "mutating", "destructive"

            if tool_authority_value not in allowed_set:
                logger.warning(
                    "Tool authority not permitted by session policy",
                    extra={
                        "tool_name": tool_name,
                        "tool_authority": tool_authority_value,
                        "allowed_authorities": allowed_authorities_str,
                        "user_id": user_id,
                    },
                )
                event_bus.publish(
                    ToolPermissionDenied(
                        execution_id=execution_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        error=(
                            f"Tool authority '{tool_authority_value}' is not permitted. "
                            f"Allowed: {allowed_authorities_str}"
                        ),
                    )
                )
                return ToolExecutionResult(
                    status=ToolExecutionStatus.FAILURE,
                    error_type=ToolErrorType.PERMISSION,
                    error_message=(
                        f"Tool '{tool_name}' requires '{tool_authority_value}' authority, "
                        f"which is not enabled for this user. "
                        f"Allowed authorities: {allowed_authorities_str}"
                    ),
                    retryable=False,
                    metadata={"tool": tool_name, "call_id": call_id},
                )

        # ----------------------------------------------------------------
        # 2b-ii. Progressive Disclosure Hallucination Gate (V5.0 Sprint 2)
        #     When progressive disclosure is active, tools are meant to be
        #     promoted via describe_tool() before they run. A tool that resolved
        #     here without having been promoted (typically an MCP tool found through
        #     the dynamic registry) is promoted now so later turns carry its full
        #     schema, and the call proceeds. It used to return a stub and ask the
        #     model to retry; see the note on the first gate above for why that was
        #     dropped. Logged at WARNING so manifest quality stays trackable.
        # ----------------------------------------------------------------
        if tool_name != "describe_tool":
            _exec_obj = (execution_metadata or {}).get("execution")
            _user_reg = getattr(_exec_obj, "user_registry", None)
            if _user_reg is not None and getattr(_user_reg, "_is_progressive", False):
                if tool_name not in getattr(_user_reg, "_promoted_tools", set()):
                    logger.warning(
                        "Hallucination gate: tool called without prior promotion — auto-promoted, executing",
                        extra={
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "execution_id": execution_id,
                        },
                    )
                    _user_reg.promote(tool_name)
                    _auto_promoted = True

        # ----------------------------------------------------------------
        # 2c. PreToolUse hook layer (B-053)
        #     Runs AFTER authority enforcement, BEFORE the HitL gate.
        #     Hooks can: allow (pass through), deny (block), or mutate_args.
        #     A denial here short-circuits the entire call — HitL never fires.
        # ----------------------------------------------------------------
        if user_id:
            session = get_user_session(user_id)
            hook_definitions = session.get("tool_hooks") or []
            if hook_definitions:
                hook_response = await HookRunner.run_pre(
                    tool_name=tool_name,
                    tool_input=parsed_args.dict(),
                    hook_definitions=hook_definitions,
                    user_id=user_id,
                    session_id=conversation_id,
                    workspace_root=session.get("workspace_root", ""),
                    execution_obj=execution_obj,
                    tool_use_id=call_id,
                )
                if hook_response.blocked:
                    denial_reason = hook_response.reason
                    logger.warning(
                        "PreToolUse hook denied tool call",
                        extra={
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "reason": denial_reason,
                        },
                    )
                    event_bus.publish(
                        ToolPermissionDenied(
                            execution_id=execution_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            error=denial_reason or "Blocked by hook policy.",
                        )
                    )
                    return ToolExecutionResult(
                        status=ToolExecutionStatus.FAILURE,
                        error_type=ToolErrorType.PERMISSION,
                        error_message=denial_reason or "Tool blocked by hook policy.",
                        retryable=False,
                        metadata={"tool": tool_name, "call_id": call_id},
                    )
                # Re-parse mutated args so the tool's schema validation is
                # applied to whatever the hook produced.
                if hook_response.updated_input:
                    try:
                        parsed_args = schema.parse_obj(hook_response.updated_input)
                    except ValidationError as ve:
                        logger.warning(
                            "Hook-mutated args failed schema validation",
                            extra={"tool_name": tool_name, "error": str(ve)},
                        )
                        return ToolExecutionResult(
                            status=ToolExecutionStatus.FAILURE,
                            error_type=ToolErrorType.VALIDATION,
                            error_message=f"Hook mutation produced invalid args: {ve}",
                            retryable=False,
                            metadata={"tool": tool_name, "call_id": call_id},
                        )

        # ----------------------------------------------------------------
        # 3. Human-in-the-Loop approval gate
        #    Runs AFTER argument validation (so args_preview is accurate)
        #    but BEFORE the retry loop (so we never execute without approval).
        # ----------------------------------------------------------------
        if await _should_require_approval(tool_cls, execution_obj, user_id, conversation_id, arguments):
            authority_value = getattr(
                getattr(tool_cls, "authority", ToolAuthority.READ_ONLY),
                "value",
                "mutating",
            )
            logger.info(
                "Approval gate triggered",
                extra={
                    "tool_name": tool_name,
                    "authority": authority_value,
                    "execution_id": execution_id,
                },
            )
            approved = await gate_approval(
                execution=execution_obj,
                call_id=call_id,
                tool_name=tool_name,
                authority=authority_value,
                arguments=arguments,
            )
            if not approved:
                # gate_approval already transitioned execution → CANCELLED.
                # Return a clean PERMISSION failure; the caller can surface it.
                return ToolExecutionResult(
                    status=ToolExecutionStatus.FAILURE,
                    error_type=ToolErrorType.PERMISSION,
                    error_message="Tool execution was rejected by the user.",
                    retryable=False,
                    metadata={"tool": tool_name, "call_id": call_id},
                )

        # ----------------------------
        # RETRY LOOP
        # ----------------------------
        last_result: ToolExecutionResult | None = None

        # Per-tool timeout override (tool-aware timeout): a tool that wraps
        # its own bounded, independently-protected work (e.g. delegate_to_agent's
        # nested AgentEngine) can declare a larger base via
        # ToolBase.execution_timeout_seconds instead of inheriting the
        # generic default meant for simple, flat tool calls.
        base_timeout = getattr(tool_cls, "execution_timeout_seconds", None) or TOOL_EXECUTION_TIMEOUT_SECONDS

        # Retry continuity: a plain dict shared across every attempt of THIS
        # call only (scoped to this execute_tool() invocation, never persisted
        # or shared across unrelated calls). Tools that track their own
        # progress as they go (see DelegateToAgentTool) can use this to avoid
        # repeating work lost when an earlier attempt was cancelled on
        # timeout -- cheaper and safer than replaying a cancelled attempt's
        # raw, possibly mid-generation transcript.
        retry_continuity: Dict[str, Any] = {}

        for attempt in range(1, ToolDispatcher.MAX_ATTEMPTS + 1):
            attempt_start = time.monotonic()

            # ---- ToolCalled: one event per attempt ----
            event_bus.publish(
                ToolCalled(
                    execution_id=execution_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    attempt=attempt,
                    retry_count=attempt - 1,
                )
            )

            logger.debug(
                "Tool execution attempt",
                extra={
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "attempt": attempt,
                },
            )

            tool_instance = tool_cls()

            # -----------------------------------------
            # ATTACH EXECUTION CONTEXT TO TOOL INSTANCE
            # -----------------------------------------
            if execution_metadata and "execution" in execution_metadata:
                tool_instance.execution = execution_metadata["execution"]
            tool_instance.retry_continuity = retry_continuity

            # Progressive timeout: attempt N gets base_timeout * 2^(N-1),
            # capped at 4x base (same exponential-backoff shape as
            # core/daemons/registry.py's retry scheduling). Attempt 1 is
            # exactly base_timeout -- no regression for the common case where
            # a tool never times out.
            attempt_timeout = min(base_timeout * (2 ** (attempt - 1)), base_timeout * 4)

            try:
                # Wrap tool execution in asyncio.wait_for() so no single tool
                # invocation can run indefinitely.  TimeoutError is retryable
                # and is caught by the existing TimeoutError handler below.
                try:
                    raw_result = await asyncio.wait_for(
                        tool_instance.run(parsed_args.dict()),
                        timeout=attempt_timeout,
                    )
                except asyncio.TimeoutError:
                    raise TimeoutError(
                        f"Tool '{tool_name}' exceeded the {attempt_timeout}s "
                        f"execution timeout (attempt {attempt}/{ToolDispatcher.MAX_ATTEMPTS})."
                    )

                latency_ms = int((time.monotonic() - attempt_start) * 1000)
                logger.debug(
                    "Tool executed successfully",
                    extra={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "attempt": attempt,
                        "latency_ms": latency_ms,
                    },
                )

                # ---- ToolResultEvent: success ----
                event_bus.publish(
                    ToolResultEvent(
                        execution_id=execution_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        success=True,
                        latency_ms=latency_ms,
                        retry_count=attempt - 1,
                        error_type=None,
                    )
                )

                # ----------------------------
                # PostToolUse hook layer (B-053)
                # Runs after successful execution, before result is returned to LLM.
                # Hooks can mutate or redact the result.
                # ----------------------------
                raw_output = (
                    raw_result.result
                    if hasattr(raw_result, "result")
                    else raw_result
                )
                if user_id:
                    _session = get_user_session(user_id)
                    _hook_defs = _session.get("tool_hooks") or []
                    if _hook_defs:
                        raw_output = await HookRunner.run_post(
                            tool_name=tool_name,
                            tool_input=parsed_args.dict(),
                            tool_response=raw_output,
                            hook_definitions=_hook_defs,
                            user_id=user_id,
                            session_id=conversation_id,
                            workspace_root=_session.get("workspace_root", ""),
                            execution_obj=execution_obj,
                            tool_use_id=call_id,
                        )

                # ----------------------------
                # Context-budget cap (V6.11) -- last step before the result
                # ever reaches the model, so hooks above still see the full,
                # untruncated data.
                # ----------------------------
                if max_result_tokens is not None and model_name:
                    capped_output = _cap_tool_output(raw_output, max_result_tokens, model_name)
                    if capped_output is not raw_output:
                        logger.warning(
                            "Tool result truncated by context-budget watchdog",
                            extra={
                                "tool_name": tool_name,
                                "call_id": call_id,
                                "max_result_tokens": max_result_tokens,
                            },
                        )
                    raw_output = capped_output

                # ----------------------------
                # SUCCESS
                # ----------------------------
                _success_metadata = {
                    "tool": tool_name,
                    "call_id": call_id,
                    "attempt": attempt,
                }
                if _auto_promoted:
                    # Not "auto_promoted": core/agent_engine.py used that key to mean
                    # "this result was only a stub". This call really executed.
                    _success_metadata["auto_activated"] = True
                return ToolExecutionResult(
                    status=ToolExecutionStatus.SUCCESS,
                    output=raw_output,
                    metadata=_success_metadata,
                )

            except PermissionError as e:
                latency_ms = int((time.monotonic() - attempt_start) * 1000)
                logger.warning(
                    "Tool permission error",
                    extra={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "attempt": attempt,
                    },
                    exc_info=True,
                )

                # ---- ToolPermissionDenied: distinct event for permission failures ----
                event_bus.publish(
                    ToolPermissionDenied(
                        execution_id=execution_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        error=str(e),
                    )
                )

                return ToolExecutionResult(
                    status=ToolExecutionStatus.FAILURE,
                    error_type=ToolErrorType.PERMISSION,
                    error_message=str(e),
                    retryable=False,
                    metadata={"tool": tool_name, "call_id": call_id},
                )

            except TimeoutError as e:
                latency_ms = int((time.monotonic() - attempt_start) * 1000)
                logger.warning(
                    "Tool timeout",
                    extra={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "attempt": attempt,
                        "max_attempts": ToolDispatcher.MAX_ATTEMPTS,
                    },
                    exc_info=True,
                )

                last_result = ToolExecutionResult(
                    status=ToolExecutionStatus.FAILURE,
                    error_type=ToolErrorType.TRANSIENT,
                    error_message=str(e),
                    retryable=True,
                    metadata={
                        "tool": tool_name,
                        "call_id": call_id,
                        "attempt": attempt,
                    },
                )

                # ---- ToolResultEvent: transient failure (may be retried) ----
                event_bus.publish(
                    ToolResultEvent(
                        execution_id=execution_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        success=False,
                        latency_ms=latency_ms,
                        retry_count=attempt,
                        error_type="timeout",
                    )
                )

            except Exception as e:
                latency_ms = int((time.monotonic() - attempt_start) * 1000)
                logger.exception("Tool execution failed")

                # ---- ToolResultEvent: internal error (non-retryable) ----
                event_bus.publish(
                    ToolResultEvent(
                        execution_id=execution_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        success=False,
                        latency_ms=latency_ms,
                        retry_count=attempt - 1,
                        error_type="internal",
                    )
                )

                return ToolExecutionResult(
                    status=ToolExecutionStatus.FAILURE,
                    error_type=ToolErrorType.INTERNAL_BUG,
                    error_message=str(e),
                    retryable=False,
                    metadata={"tool": tool_name, "call_id": call_id},
                )

            # ----------------------------
            # RETRY DECISION
            # ----------------------------
            if not last_result.retryable:
                return last_result

            if attempt >= ToolDispatcher.MAX_ATTEMPTS:
                logger.error(
                    "Tool failed after max attempts",
                    extra={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "max_attempts": ToolDispatcher.MAX_ATTEMPTS,
                    },
                )

                # ---- ToolResultEvent: final failure after exhausting retries ----
                event_bus.publish(
                    ToolResultEvent(
                        execution_id=execution_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        success=False,
                        latency_ms=0,
                        retry_count=ToolDispatcher.MAX_ATTEMPTS,
                        error_type=str(last_result.error_type)
                        if last_result.error_type
                        else "unknown",
                    )
                )
                return last_result

            # otherwise → retry silently (ToolCalled will fire on next iteration)

        logger.debug(
            "Tool execution loop completed",
            extra={
                "tool_name": tool_name,
                "call_id": call_id,
            },
        )

        # Should never reach here
        return last_result
