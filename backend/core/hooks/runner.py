# core/hooks/runner.py
"""
HookRunner — unified hook dispatch engine (B-064).

Supports three hook types:
  "builtin"  — built-in Python handler (B-053 handlers, backward compatible)
  "command"  — user-supplied shell script at ~/eva_workspace/{user_id}/hooks/
  "http"     — POST JSON payload to an external URL

Blocking semantics:
  event="pre" / event="prompt_submit"  → always blocking (await before proceeding)
  event="post" / "session_start" / "stop" → non-blocking by default;
      hook.async_=True makes them fire-and-forget

All subprocess calls use asyncio.to_thread — the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from config.settings import HOOK_COMMAND_TIMEOUT_SECONDS, HOOK_HTTP_TIMEOUT_SECONDS
from .schemas import HookPayload, HookResponse, HookEventName

logger = logging.getLogger(__name__)

_DEFAULT_COMMAND_TIMEOUT = HOOK_COMMAND_TIMEOUT_SECONDS
_DEFAULT_HTTP_TIMEOUT    = HOOK_HTTP_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Built-in handler bridge (delegates to core/tools/hooks.py functions)
# ---------------------------------------------------------------------------

def _run_builtin_pre(
    hook: Dict[str, Any],
    payload: HookPayload,
    execution_obj: Any,
) -> HookResponse:
    """Run a type="builtin" pre-hook and translate result to HookResponse."""
    from core.tools.hooks import _PRE_HANDLERS

    handler_name = hook.get("handler", "")
    handler_fn = _PRE_HANDLERS.get(handler_name)
    if handler_fn is None:
        logger.warning("[HookRunner] Unknown builtin pre handler '%s' — skipping", handler_name)
        return HookResponse.allow()

    args = payload.tool_input or {}
    config = hook.get("config") or {}
    result = handler_fn(payload.tool_name or "", args, config, execution_obj)

    if result.get("action") == "deny":
        return HookResponse.block(result.get("reason", "Blocked by built-in hook."))

    # Handler may have mutated args — surface as updated_input
    mutated = result.get("args")
    if mutated and mutated != args:
        return HookResponse(decision="allow", updated_input=mutated)
    return HookResponse.allow()


def _run_builtin_post(
    hook: Dict[str, Any],
    payload: HookPayload,
    execution_obj: Any,
) -> HookResponse:
    """Run a type="builtin" post-hook and translate result to HookResponse."""
    from core.tools.hooks import _POST_HANDLERS

    handler_name = hook.get("handler", "")
    handler_fn = _POST_HANDLERS.get(handler_name)
    if handler_fn is None:
        logger.warning("[HookRunner] Unknown builtin post handler '%s' — skipping", handler_name)
        return HookResponse.allow()

    tool_response = payload.tool_response or {}
    # A real tool's own result is whatever shape that tool returns natively
    # (e.g. read_file: {"content": ...}; shell: {"cmd": [...], "stdout": ...,
    # "exit_code": ...}) -- it is NOT pre-wrapped as {"result": <value>} by
    # the dispatcher (core/tools/dispatcher.py passes ToolResult.result
    # straight through). Unconditionally reading tool_response["result"]
    # therefore returned None for every real tool call -- confirmed live
    # 2026-09-16 while testing S-03's redact_pii hook: it never fired on the
    # shell tool's output because of exactly this. redact_env_vars (built on
    # the same contract) has the identical gap and, per its own docstring
    # example (`{"tool": "read_file", ...}`), was never actually exercised
    # against read_file's real {"content": ...} shape either.
    # Fix: only unwrap a "result" key if the tool_response actually has one
    # (a caller that already wrapped it that way); otherwise hand handlers
    # the whole native dict so they can reach the real content.
    if isinstance(tool_response, dict) and "result" in tool_response:
        result_val = tool_response["result"]
    else:
        result_val = tool_response

    config = hook.get("config") or {}
    hook_result = handler_fn(payload.tool_name or "", result_val, config, execution_obj)
    mutated = hook_result.get("result")

    if mutated != result_val:
        # Surface the mutated result via additional_context so callers can pick it up
        return HookResponse(decision="allow", updated_input={"result": mutated})
    return HookResponse.allow()


# ---------------------------------------------------------------------------
# Command hook (subprocess)
# ---------------------------------------------------------------------------

def _subprocess_run_hook(
    script_path: str,
    payload_json: str,
    timeout: int,
) -> Tuple[int, str, str]:
    """
    Synchronous subprocess runner — called via asyncio.to_thread.
    Returns (exit_code, stdout, stderr).
    """
    try:
        from utils.child_env import child_environment
        proc = subprocess.run(
            [script_path],
            input=payload_json,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_environment(),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        logger.warning("[HookRunner] command hook timed out after %ds: %s", timeout, script_path)
        return 1, "", f"Hook timed out after {timeout}s."
    except FileNotFoundError:
        return 1, "", f"Hook script not found: {script_path}"
    except Exception as exc:
        return 1, "", f"Hook script error: {exc}"


async def _run_command_hook(
    hook: Dict[str, Any],
    payload: HookPayload,
    user_id: str,
) -> HookResponse:
    from utils.workspace import get_user_hooks_dir
    hooks_dir = get_user_hooks_dir(user_id)
    script_name = hook.get("command", "")
    script_path = str(hooks_dir / script_name)
    timeout = int(hook.get("timeout") or _DEFAULT_COMMAND_TIMEOUT)
    payload_json = payload.to_json()

    exit_code, stdout, stderr = await asyncio.to_thread(
        _subprocess_run_hook, script_path, payload_json, timeout
    )

    if stderr:
        logger.debug(
            "[HookRunner] command hook stderr: %s",
            stderr[:500],
            extra={"script": script_name, "user_id": user_id},
        )

    response = HookResponse.from_stdout(stdout, exit_code, stderr)
    logger.info(
        "[HookRunner] command hook result",
        extra={
            "script": script_name,
            "user_id": user_id,
            "decision": response.decision,
            "exit_code": exit_code,
        },
    )
    return response


# ---------------------------------------------------------------------------
# HTTP hook
# ---------------------------------------------------------------------------

async def _run_http_hook(
    hook: Dict[str, Any],
    payload: HookPayload,
) -> HookResponse:
    url = hook.get("url", "")
    headers = hook.get("headers") or {}
    timeout = int(hook.get("timeout") or _DEFAULT_HTTP_TIMEOUT)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                content=payload.to_json(),
                headers={"Content-Type": "application/json", **headers},
            )
        if resp.status_code >= 400:
            logger.warning(
                "[HookRunner] http hook returned %d — treating as allow",
                resp.status_code,
                extra={"url": url},
            )
            return HookResponse.allow()

        response = HookResponse.from_stdout(resp.text, 0)
        logger.info(
            "[HookRunner] http hook result",
            extra={"url": url, "decision": response.decision},
        )
        return response

    except ImportError:
        logger.error("[HookRunner] httpx not installed — http hooks disabled")
        return HookResponse.allow()
    except Exception as exc:
        logger.warning(
            "[HookRunner] http hook error — treating as allow: %s", exc,
            extra={"url": url},
        )
        return HookResponse.allow()


# ---------------------------------------------------------------------------
# HookRunner — main dispatcher
# ---------------------------------------------------------------------------

class HookRunner:
    """
    Unified async hook dispatcher.

    Usage
    -----
    Pre-tool (blocking):
        response = await HookRunner.run_pre(
            tool_name="shell",
            tool_input={"command": "ls"},
            hook_definitions=session["tool_hooks"],
            user_id=user_id,
            session_id=conversation_id,
            workspace_root=workspace,
            execution_obj=execution,
        )
        if response.blocked:
            return error(response.reason)
        if response.updated_input:
            args = response.updated_input   # args were rewritten by hook

    Post-tool (non-blocking):
        await HookRunner.run_post(
            tool_name="shell",
            tool_input={"command": "ls"},
            tool_response={"result": "file.txt"},
            hook_definitions=session["tool_hooks"],
            user_id=user_id,
            session_id=conversation_id,
            workspace_root=workspace,
            execution_obj=execution,
        )

    Lifecycle events (always non-blocking):
        await HookRunner.fire_event(
            event="session_start",
            hook_definitions=session["tool_hooks"],
            user_id=user_id,
            session_id=session_id,
            workspace_root=workspace,
        )
    """

    @classmethod
    async def run_pre(
        cls,
        tool_name: str,
        tool_input: Dict[str, Any],
        hook_definitions: List[Dict[str, Any]],
        user_id: str,
        session_id: str,
        workspace_root: str,
        execution_obj: Any = None,
        tool_use_id: Optional[str] = None,
    ) -> HookResponse:
        """
        Run all matching PreToolUse hooks sequentially (blocking).
        First block decision short-circuits — remaining hooks are skipped.
        Returns the final HookResponse (allow/block/warn).
        """
        payload = HookPayload(
            hook_event_name=HookEventName.PRE_TOOL_USE.value,
            session_id=session_id,
            user_id=user_id,
            cwd=workspace_root,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=tool_use_id,
        )

        current_input = dict(tool_input)
        accumulated_warnings: List[str] = []

        for hook in hook_definitions:
            if hook.get("event", "pre") != "pre":
                continue
            target = hook.get("tool", "*")
            if target != "*" and target != tool_name:
                continue

            payload.tool_input = current_input
            response = await cls._dispatch(hook, payload, user_id, execution_obj)

            if response.blocked:
                return response

            if response.warned:
                # warn = proceed but surface the warning (B-077 Bug 2)
                warn_msg = response.reason or f"Hook warning on tool '{tool_name}'"
                logger.warning(
                    "[HookRunner] hook warning on PreToolUse: %s",
                    warn_msg,
                    extra={"tool_name": tool_name, "user_id": user_id},
                )
                accumulated_warnings.append(warn_msg)
                # Write warn to blackboard if execution context is available
                blackboard = getattr(execution_obj, "blackboard", None)
                if blackboard is not None:
                    try:
                        import asyncio as _asyncio
                        _asyncio.create_task(
                            blackboard.write(
                                slot="hook_warnings",
                                content=warn_msg,
                                written_by="hook_runner",
                                mode="append",
                                sensitive=False,
                            )
                        )
                    except Exception:
                        pass  # blackboard write failure is non-fatal

            if response.updated_input:
                current_input = response.updated_input

        # Return allow with any accumulated input mutations
        if current_input != tool_input:
            return HookResponse(decision="allow", updated_input=current_input)
        return HookResponse.allow()

    @classmethod
    async def run_post(
        cls,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_response: Any,
        hook_definitions: List[Dict[str, Any]],
        user_id: str,
        session_id: str,
        workspace_root: str,
        execution_obj: Any = None,
        tool_use_id: Optional[str] = None,
    ) -> Any:
        """
        Run all matching PostToolUse hooks.
        Non-blocking hooks (async_=True) are fire-and-forget.
        Returns the (possibly mutated) tool_response.
        """
        if not isinstance(tool_response, dict):
            tool_response_dict = {"result": tool_response}
        else:
            tool_response_dict = tool_response

        payload = HookPayload(
            hook_event_name=HookEventName.POST_TOOL_USE.value,
            session_id=session_id,
            user_id=user_id,
            cwd=workspace_root,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=tool_use_id,
            tool_response=tool_response_dict,
        )

        current_response = tool_response
        for hook in hook_definitions:
            if hook.get("event", "pre") != "post":
                continue
            target = hook.get("tool", "*")
            if target != "*" and target != tool_name:
                continue

            is_async = bool(hook.get("async_", False))
            if is_async:
                asyncio.create_task(cls._dispatch(hook, payload, user_id, execution_obj))
                continue

            response = await cls._dispatch(hook, payload, user_id, execution_obj)
            # PostToolUse: updated_input carries the mutated result
            if response.updated_input and "result" in response.updated_input:
                current_response = response.updated_input["result"]

        return current_response

    @classmethod
    async def fire_event(
        cls,
        event: str,
        hook_definitions: List[Dict[str, Any]],
        user_id: str,
        session_id: str,
        workspace_root: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Fire a lifecycle event (session_start, stop) — always non-blocking.
        Matching hooks with async_=False are still awaited but failures
        are swallowed so they never block the caller.
        """
        event_name = HookEventName.from_hook_event(event)
        payload = HookPayload(
            hook_event_name=event_name.value,
            session_id=session_id,
            user_id=user_id,
            cwd=workspace_root,
            **(extra or {}),
        )

        for hook in hook_definitions:
            if hook.get("event") != event:
                continue
            is_async = bool(hook.get("async_", False))
            if is_async:
                asyncio.create_task(
                    cls._safe_dispatch(hook, payload, user_id, None)
                )
            else:
                await cls._safe_dispatch(hook, payload, user_id, None)

    @classmethod
    async def fire_prompt_submit(
        cls,
        prompt: str,
        hook_definitions: List[Dict[str, Any]],
        user_id: str,
        session_id: str,
        workspace_root: str,
    ) -> HookResponse:
        """
        Fire UserPromptSubmit hooks — blocking (can deny prompt; cannot modify it).

        Option A (Claude Code alignment): prompt modification via updated_input is
        intentionally NOT supported.  Hooks can only BLOCK or ALLOW a prompt.
        Any updated_input in a script's response is silently discarded here —
        the caller always receives the original prompt unchanged.

        Hooks may include additional_context in their response; the caller is
        responsible for deciding whether to surface it to the execution.
        """
        payload = HookPayload(
            hook_event_name=HookEventName.PROMPT_SUBMIT.value,
            session_id=session_id,
            user_id=user_id,
            cwd=workspace_root,
            prompt=prompt,
        )

        for hook in hook_definitions:
            if hook.get("event") != "prompt_submit":
                continue
            response = await cls._dispatch(hook, payload, user_id, None)
            if response.blocked:
                # Return block with reason but NEVER updated_input (Option A)
                return HookResponse.block(response.reason)
            if response.warned:
                logger.warning(
                    "[HookRunner] UserPromptSubmit hook warning: %s",
                    response.reason or "hook returned warn",
                    extra={"user_id": user_id},
                )

        return HookResponse.allow()

    @classmethod
    async def fire_model_call(
        cls,
        hook_definitions: List[Dict[str, Any]],
        user_id: str,
        session_id: str,
        workspace_root: str,
        model: str,
        provider: str,
        turn_number: int,
        token_estimate: Optional[int] = None,
        execution_id: Optional[str] = None,
    ) -> None:
        """
        Fire ModelCall hooks — always non-blocking (EVA extension, B-076 Gap 6).

        Fires before each LLM API call inside the ReAct loop.  Hooks cannot
        block or mutate the LLM call — they are observation-only.  Use cases:
          - Audit logging of every LLM call with model + token context
          - Per-model call rate limiting / cost projection
          - Injecting telemetry to external systems

        This event type has no Claude Code equivalent — it is EVA-specific.
        """
        payload = HookPayload(
            hook_event_name=HookEventName.MODEL_CALL.value,
            session_id=session_id,
            user_id=user_id,
            cwd=workspace_root,
            model=model,
            provider=provider,
            turn_number=turn_number,
            token_estimate=token_estimate,
            execution_id=execution_id,
        )

        for hook in hook_definitions:
            if hook.get("event") != "model_call":
                continue
            is_async = bool(hook.get("async_", False))
            if is_async:
                asyncio.create_task(
                    cls._safe_dispatch(hook, payload, user_id, None)
                )
            else:
                await cls._safe_dispatch(hook, payload, user_id, None)

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    @classmethod
    async def _dispatch(
        cls,
        hook: Dict[str, Any],
        payload: HookPayload,
        user_id: str,
        execution_obj: Any,
    ) -> HookResponse:
        hook_type = hook.get("type", "builtin")
        event = hook.get("event", "pre")

        try:
            if hook_type == "builtin":
                if event == "pre":
                    return _run_builtin_pre(hook, payload, execution_obj)
                else:
                    return _run_builtin_post(hook, payload, execution_obj)

            elif hook_type == "command":
                return await _run_command_hook(hook, payload, user_id)

            elif hook_type == "http":
                return await _run_http_hook(hook, payload)

            else:
                logger.warning("[HookRunner] Unknown hook type '%s' — skipping", hook_type)
                return HookResponse.allow()

        except Exception as exc:
            logger.exception(
                "[HookRunner] Hook dispatch error — allowing: %s", exc,
                extra={"hook_type": hook_type, "user_id": user_id},
            )
            return HookResponse.allow()

    @classmethod
    async def _safe_dispatch(
        cls,
        hook: Dict[str, Any],
        payload: HookPayload,
        user_id: str,
        execution_obj: Any,
    ) -> HookResponse:
        """Dispatch with full exception swallowing — for fire-and-forget events."""
        try:
            return await cls._dispatch(hook, payload, user_id, execution_obj)
        except Exception as exc:
            logger.warning("[HookRunner] lifecycle hook error (swallowed): %s", exc)
            return HookResponse.allow()
