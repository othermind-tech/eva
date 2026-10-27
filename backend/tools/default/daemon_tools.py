# tools/default/daemon_tools.py
#
# G-009: Cross-Daemon Signalling tool
#
# trigger_daemon — Consolidated (2026-07) from two separate tools
#                  (signal_daemon, await_daemon) into one tool with a
#                  `wait` parameter. The async-vs-sync split was a
#                  frequent source of tool-selection confusion — the
#                  choice is a timing decision, not a purpose decision,
#                  so it is better expressed as a boolean parameter than
#                  as two separately-named tools.
#
#                  wait=False (default): fire-and-forget. Returns
#                  execution_id immediately; daemon runs as a background
#                  asyncio task. Authority: MUTATING.
#
#                  wait=True: blocks until the daemon finishes (or times
#                  out) and returns its output directly. Analogous to
#                  delegate_to_agent but targets daemon agents instead of
#                  subagents. Authority: MUTATING (creates an execution
#                  regardless of whether the caller waits for it).
#
#                  chat_excluded=True — daemon invocation requires an
#                  execution context (execution_id, caller_agent_name)
#                  that conversational mode does not provide, and has no
#                  standalone meaning outside a multi-agent execution.

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from core.tools.base import ToolBase, ToolAuthority, ToolResult
from core.tools.registry import default_tool

logger = logging.getLogger(__name__)


class TriggerDaemonArgs(BaseModel):
    daemon_name: str = Field(
        description=(
            "Name of the daemon to trigger. Must match a configured daemon name "
            "exactly (case-sensitive). If you do not know the name, ask the user -- "
            "no tool lists daemons."
        )
    )
    message: Optional[str] = Field(
        default=None,
        description=(
            "Optional message / instruction to send as the daemon's activation prompt. "
            "If omitted the daemon runs with its own configured trigger_message."
        ),
    )
    wait: bool = Field(
        default=False,
        description=(
            "If False (default): fire-and-forget — returns execution_id immediately "
            "and the daemon runs in the background. Use this to wake a daemon (e.g. a "
            "monitor or notifier) without blocking your current task. "
            "If True: blocks until the daemon completes (or times out) and returns its "
            "output directly. Use this when you need the daemon's result as input for "
            "your current task, e.g. 'run the data-fetcher daemon and use its output "
            "to write a report'."
        ),
    )
    timeout_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
        description=(
            "Only used when wait=True. Maximum seconds to wait for the daemon to "
            "finish (5-300, default 60). If it does not complete within this window, "
            "an error is returned and the daemon continues running in the background."
        ),
    )


@default_tool(
    name="trigger_daemon",
    description=(
        "Trigger a named daemon agent to run. "
        "Set wait=False (default) to fire-and-forget — returns execution_id "
        "immediately, daemon runs in the background. To find out how it went, call "
        "get_daemon_config (its lifecycle_status) later, or call schedule_wakeup with "
        "watch_execution_id set to that execution_id to be resumed when it finishes. "
        "Set wait=True to block until the daemon finishes and get its output directly. "
        "[PEV/agent mode only — not available in conversational chat.]"
    ),
    args_schema=TriggerDaemonArgs,
    authority=ToolAuthority.MUTATING,
    category="general",
    chat_excluded=True,
    summary="Trigger a named daemon, either fire-and-forget or blocking on its result.",
)
class TriggerDaemonTool(ToolBase):
    """
    Triggers a daemon run, either as a background asyncio task (wait=False,
    returns immediately) or synchronously with a timeout (wait=True, blocks
    until the daemon finishes or the timeout elapses).
    """

    async def run(self, args: Dict[str, Any]) -> ToolResult:
        parsed          = TriggerDaemonArgs(**args)
        daemon_name     = parsed.daemon_name.strip()
        message         = parsed.message
        wait            = parsed.wait
        timeout_seconds = parsed.timeout_seconds

        if not daemon_name:
            return ToolResult(result={"error": "daemon_name must not be empty."})

        # ------------------------------------------------------------------
        # 1. Resolve execution context
        # ------------------------------------------------------------------
        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={"error": "trigger_daemon: no execution context."})

        user_id = getattr(execution, "user_id", "")
        caller  = getattr(execution, "caller_agent_name", "orchestrator")

        # ------------------------------------------------------------------
        # 2. Look up daemon config
        # ------------------------------------------------------------------
        from core.daemons.registry import daemon_registry
        daemon = await daemon_registry.get(user_id, daemon_name)
        if daemon is None:
            return ToolResult(result={
                "error": (
                    f"No daemon named '{daemon_name}' is configured for this user. "
                    "Ask the user for the exact name, or use get_daemon_config to check "
                    "a specific name."
                )
            })

        # ------------------------------------------------------------------
        # 3. Build activation message (inject caller identity if message given)
        # ------------------------------------------------------------------
        override_trigger: Optional[str] = None
        if message:
            verb = "invoked synchronously by" if wait else "signalled by"
            override_trigger = (
                f"You have been {verb} agent '{caller}'.\n"
                f"{'Task' if wait else 'Signal message'}: {message}"
            )

        # ------------------------------------------------------------------
        # 4. Pre-generate execution_id and mark daemon as running
        # ------------------------------------------------------------------
        execution_id = str(uuid.uuid4())
        try:
            await daemon_registry.set_lifecycle_status(
                user_id, daemon_name, "running", execution_id=execution_id
            )
        except Exception as exc:
            logger.warning("trigger_daemon: could not update lifecycle status: %s", exc)

        # ------------------------------------------------------------------
        # 5a. wait=False — fire-and-forget background task
        # ------------------------------------------------------------------
        from core.daemons.runner import run_daemon as _run_daemon_core

        if not wait:
            async def _run_and_record() -> None:
                result    = await _run_daemon_core(
                    daemon=daemon,
                    execution_id=execution_id,
                    override_trigger=override_trigger,
                )
                success   = result.get("success", False)
                error_msg = result.get("error", "")
                try:
                    if success:
                        await daemon_registry.record_success(
                            user_id=daemon.user_id, name=daemon.name
                        )
                    else:
                        await daemon_registry.record_failure(
                            user_id=daemon.user_id, name=daemon.name, error=error_msg
                        )
                except Exception as exc:
                    logger.error("trigger_daemon: lifecycle update failed: %s", exc)

            asyncio.create_task(_run_and_record(), name=f"trigger-daemon-{daemon_name}")

            logger.info(
                "trigger_daemon: triggered (wait=False)",
                extra={"daemon_name": daemon_name, "caller": caller, "execution_id": execution_id},
            )

            return ToolResult(result={
                "triggered":    True,
                "daemon_name":  daemon_name,
                "execution_id": execution_id,
                "note": (
                    f"Daemon '{daemon_name}' has been signalled. To find out how it went, "
                    "call get_daemon_config later, or call schedule_wakeup with "
                    f"watch_execution_id='{execution_id}' to be resumed when it finishes."
                ),
            })

        # ------------------------------------------------------------------
        # 5b. wait=True — run synchronously with timeout
        # ------------------------------------------------------------------
        try:
            result = await asyncio.wait_for(
                _run_daemon_core(
                    daemon=daemon,
                    execution_id=execution_id,
                    override_trigger=override_trigger,
                ),
                timeout=float(timeout_seconds),
            )
        except asyncio.TimeoutError:
            # Daemon is still running in the background — record as a soft failure.
            try:
                await daemon_registry.record_failure(
                    user_id=user_id,
                    name=daemon_name,
                    error=f"trigger_daemon (wait=True) timed out after {timeout_seconds}s",
                )
            except Exception:
                pass
            return ToolResult(result={
                "daemon_name":  daemon_name,
                "execution_id": execution_id,
                "success":      False,
                "error": (
                    f"Daemon '{daemon_name}' did not finish within "
                    f"{timeout_seconds}s. It may still be running. Call get_daemon_config to "
                    f"check on it, or call schedule_wakeup with watch_execution_id='{execution_id}' "
                    "to be resumed when it finishes."
                ),
            })

        success   = result.get("success", False)
        error_msg = result.get("error", "")
        try:
            if success:
                await daemon_registry.record_success(user_id=daemon.user_id, name=daemon.name)
            else:
                await daemon_registry.record_failure(
                    user_id=daemon.user_id, name=daemon.name, error=error_msg
                )
        except Exception as exc:
            logger.error("trigger_daemon: lifecycle update failed: %s", exc)

        logger.info(
            "trigger_daemon: completed (wait=True)",
            extra={
                "daemon_name":  daemon_name,
                "execution_id": execution_id,
                "success":      success,
            },
        )

        return ToolResult(result={
            "daemon_name":  daemon_name,
            "execution_id": execution_id,
            "success":      success,
            "output":       result.get("output", ""),
            "error":        error_msg,
        })
