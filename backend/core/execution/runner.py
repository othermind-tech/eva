import time, asyncio, json
from logging import getLogger
from typing import Any, Callable, Awaitable

from core import execution
from core.stream_buffer import BufferedStream
from core.execution.models import ExecutionStatus
from core.types import ErrorChunk
from core.execution.redis_registry import redis_execution_registry
from core.blackboard import ExecutionBlackboard
from config.settings import EXECUTION_HEARTBEAT_SECONDS
from core.execution.state_machine import ExecutionStateMachine
from core.event_bus import event_bus
from core.events import (
    ExecutionStarted,
    ExecutionCompleted,
    ExecutionFailed,
    ExecutionCancelled,
)

logger = getLogger(__name__)


async def run_execution(
    *,
    execution: Any,                              # ConversationExecution — avoid circular import
    agent: Any,                                  # AgentEngine instance
    messages: list[dict],                        # conversation message history
    finalizer: Callable[..., Awaitable[None]],   # async callback on completion
    preamble: list | None = None,                # optional pre-execution chunks
) -> None:
    def transition(new_status):
        ExecutionStateMachine.assert_transition_allowed(
            execution.execution_id,
            execution.status,
            new_status,
        )
        execution.status = new_status
        logger.debug(
            "Execution status transition",
            extra={
                "execution_id": execution.execution_id,
                "new_status": new_status,
            },
        )

    start_monotonic = time.monotonic()
    execution.started_at = time.time()

    logger.info(
        "Execution starting",
        extra={
            "execution_id": execution.execution_id,
            "conversation_id": execution.conversation_id,
            "user_id": execution.user_id,
        },
    )

    # ---------------------------
    # Transition → RUNNING
    # ---------------------------
    transition(ExecutionStatus.RUNNING)

    await redis_execution_registry.mark_execution_running(
        execution.execution_id
    )

    await redis_execution_registry.update_execution(
        execution.execution_id,
        started_at=execution.started_at,
    )

    event_bus.publish(
        ExecutionStarted(
            execution_id=execution.execution_id,
            conversation_id=execution.conversation_id,
            user_id=execution.user_id,
        )
    )

    from core.broker import local_broker as _broker
    shadow = _broker.get_shadow_buffer(execution.execution_id)
    # G-003: expose shadow to tool instances (e.g. delegate_to_agent)
    # so they can emit subagent chunks into the parent's SSE stream.
    execution.shadow = shadow
    blackboard = None
    last_heartbeat = time.monotonic()
    last_cancel_check = time.monotonic()
    _CANCEL_CHECK_SECONDS = 2   # poll broker for external cancel every 2 s

    try:
        raw_stream = agent.call(messages, stream=True)
        buffered = BufferedStream(raw_stream)
        execution.buffer = buffered

        # G-004: per-execution shared blackboard for multi-agent coordination.
        client = await redis_execution_registry.connect()
        blackboard = ExecutionBlackboard(
            redis=client,
            execution_id=execution.execution_id,
            # ttl_seconds uses BLACKBOARD_TTL_SECONDS default from settings
        )
        execution.blackboard = blackboard

        # Eager clear: wipe any leftover data from a previous run with the
        # same execution_id (e.g. worker crash + retry) before writing
        # preamble chunks.  Safe because each execution gets a unique UUID key.
        logger.debug(
            "run_execution — eager stream clear",
            extra={"execution_id": execution.execution_id},
        )
        await shadow.clear()
        # Write preamble chunks (e.g. StatusChunk for summarisation) so
        # the SSE endpoint delivers them before the first AI text arrives.
        for pc in (preamble or []):
            await shadow.append(pc.__dict__)

        # ---------------------------
        # STREAM LOOP
        # ---------------------------
        async for chunk in buffered.stream_and_buffer():
            await shadow.append(chunk.__dict__)

            # Heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= EXECUTION_HEARTBEAT_SECONDS:
                await redis_execution_registry.heartbeat(
                    execution.execution_id
                )
                last_heartbeat = now

            # Cancel / abort check every 2 s.
            # The cancel API and the watchdog both signal via broker status updates.
            # We poll here so the runner coroutine stops promptly without waiting
            # for asyncio.CancelledError (which may never arrive when the LLM
            # call is deep inside a third-party SDK await).
            if now - last_cancel_check >= _CANCEL_CHECK_SECONDS:
                last_cancel_check = now
                try:
                    _client = await redis_execution_registry.connect()
                    _broker_status = await _client.hget(
                        f"execution:{execution.execution_id}", "status"
                    )
                    if _broker_status in ("cancelled", "aborted"):
                        execution.cancel_token.cancelled = True
                        logger.info(
                            "External cancel/abort detected via broker — stopping execution",
                            extra={
                                "execution_id": execution.execution_id,
                                "broker_status": _broker_status,
                            },
                        )
                except Exception as _poll_exc:
                    logger.debug("Cancel-check poll failed (will retry): %s", _poll_exc)

            # Cancellation check (in-memory flag; set locally or via broker poll above)
            if execution.cancel_token.cancelled:
                transition(ExecutionStatus.CANCELLED)
                execution.completed_at = time.time()

                # V6.15.3: recover whatever usage (and text) was accumulated
                # before cancellation landed, BEFORE buffered.clear() wipes
                # it. Without this, finalizer() was never called on the
                # cancel path at all, so execution_metrics recorded null
                # token counts for a cancelled run even when real tokens had
                # already been spent (e.g. the LLM turn that decided to call
                # the tool that then got cancelled). Persisted to the broker
                # (not appended to `shadow`) because shadow.clear() right
                # below would silently drop anything appended there -- the
                # SSE endpoint's per-connection chunk index doesn't reset
                # when the underlying list is cleared.
                _partial_final = await buffered.aggregated(require_ended=False)
                await finalizer(_partial_final)
                _partial_usage = _partial_final.get("usage")
                if _partial_usage:
                    try:
                        await redis_execution_registry.update_execution(
                            execution.execution_id,
                            usage_json=json.dumps(_partial_usage),
                        )
                    except Exception as _usage_persist_exc:
                        logger.debug(
                            "Failed to persist partial usage on cancel (non-fatal): %s",
                            _usage_persist_exc,
                        )

                buffered.clear()
                await shadow.clear()
                if blackboard:  # blackboard may be None if its constructor raised
                    await blackboard.delete()

                await redis_execution_registry.mark_execution_cancelled(
                    execution.execution_id,
                    reason="user",
                )

                event_bus.publish(
                    ExecutionCancelled(
                        execution_id=execution.execution_id,
                        duration_ms=int(
                            (time.monotonic() - start_monotonic) * 1000
                        ),
                        cancel_reason="user",
                    )
                )
                return

            await asyncio.sleep(0)

        # ---------------------------
        # STREAM ENDED — COMPLETED or FAILED?
        # ---------------------------
        # A provider/fallback failure that AgentEngine has nothing left to
        # retry or fall over into is *yielded* as a terminal ErrorChunk, not
        # raised -- by design, so the SSE stream always finishes cleanly via
        # the status-driven finish/done framing in api/eva_response.py,
        # rather than the HTTP connection dying mid-response. That design is
        # unaffected by the check below: the ErrorChunk itself was already
        # streamed to the frontend as a normal `event: error` chunk earlier
        # in this same loop. But because it's yielded rather than raised,
        # this is the only place that can tell "produced a real answer"
        # apart from "stream ended with nothing but an error to show for
        # it" -- aggregated() already captures whichever ErrorChunk (if
        # any) was last in the buffer, so a genuinely successful stream
        # (which never yields one) is unaffected.
        final = await buffered.aggregated()
        await finalizer(final)

        final_error = final.get("error")
        if final_error:
            error_message = final_error.get("message") or "Execution ended in an unrecoverable error."
            logger.warning(
                "run_execution — stream ended in an unrecoverable ErrorChunk, marking FAILED",
                extra={
                    "execution_id": execution.execution_id,
                    "error_type": final_error.get("error_type"),
                    "provider": final_error.get("provider"),
                },
            )
            transition(ExecutionStatus.FAILED)
            execution.error = {"message": error_message}
            execution.completed_at = time.time()

            # Deliberately NOT clearing buffer/shadow here (unlike the
            # except-Exception branch below) -- the ErrorChunk content is
            # already correctly buffered and must stay visible so the SSE
            # endpoint's drain-then-finish sequence can still deliver it.
            if blackboard:
                await blackboard.delete()

            await redis_execution_registry.mark_execution_failed(
                execution.execution_id,
                error=error_message,
            )

            event_bus.publish(
                ExecutionFailed(
                    execution_id=execution.execution_id,
                    duration_ms=int(
                        (time.monotonic() - start_monotonic) * 1000
                    ),
                    error_type=final_error.get("error_type") or "unknown",
                    error_message=error_message,
                    project_id=execution.parent_project_id,
                )
            )
            try:
                from core.events_publisher import publish_user_event
                await publish_user_event(
                    user_id=execution.user_id,
                    event="execution.failed",
                    execution_id=execution.execution_id,
                    summary=f"Execution failed: {final_error.get('error_type') or 'unknown'}",
                )
            except Exception:
                pass
            return

        # P-19 Phase 1: same fix as core/execution/pev_runner.py's identical
        # "all steps streamed" finalize path -- a schedule_wakeup call
        # leaves execution.status at AWAITING_WAKEUP and the ReAct loop
        # ends normally (not an error), reaching this same unconditional
        # "mark completed" code. Without this check it silently overwrites
        # AWAITING_WAKEUP within milliseconds (COMPLETED is a legal target
        # from AWAITING_WAKEUP -- it has to be, for WakeupScheduler's own
        # later resolution -- so this doesn't raise, it just defeats the
        # point of a distinct paused state). Leave both alone here --
        # WakeupScheduler._resolve() does the real transition once the
        # wakeup actually resolves.
        if execution.status == ExecutionStatus.AWAITING_WAKEUP:
            # P-19 Phase 3: let the frontend's live task-tray know this
            # execution just paused, without it having to poll
            # GET /executions/running to notice.
            try:
                from core.events_publisher import publish_user_event
                await publish_user_event(
                    user_id=execution.user_id,
                    event="execution.awaiting_wakeup",
                    execution_id=execution.execution_id,
                    summary="Execution paused, waiting to resume.",
                )
            except Exception:
                pass
            return

        transition(ExecutionStatus.COMPLETED)
        execution.completed_at = time.time()

        await redis_execution_registry.mark_execution_finished(
            execution.execution_id
        )

        event_bus.publish(
            ExecutionCompleted(
                execution_id=execution.execution_id,
                duration_ms=int(
                    (time.monotonic() - start_monotonic) * 1000
                ),
                project_id=execution.parent_project_id,
            )
        )
        # B-051: publish user event so GET /events/stream can forward it.
        try:
            from core.events_publisher import publish_user_event
            await publish_user_event(
                user_id=execution.user_id,
                event="execution.completed",
                execution_id=execution.execution_id,
                summary="Execution completed successfully.",
            )
        except Exception:
            pass
        # G-004: clean up blackboard on normal completion (L6)
        if blackboard:
            await blackboard.delete()

    except asyncio.CancelledError:
        # Guard: the watchdog may have already set status→ABORTED in the broker
        # AND called mark_execution_aborted().  The in-process transition() will
        # raise IllegalStateTransition if the execution is already terminal.
        # We catch that here so cleanup still runs cleanly.
        try:
            transition(ExecutionStatus.ABORTED)
        except Exception:
            pass  # already in a terminal state — safe to continue cleanup
        execution.completed_at = time.time()

        if execution.buffer:
            execution.buffer.clear()
        await shadow.clear()
        if blackboard:
            await blackboard.delete()

        # mark_execution_aborted is idempotent — safe to call even if the
        # watchdog already set ABORTED via the broker.
        await redis_execution_registry.mark_execution_aborted(
            execution.execution_id,
            reason="task_cancelled",
        )

        event_bus.publish(
            ExecutionCancelled(
                execution_id=execution.execution_id,
                duration_ms=int(
                    (time.monotonic() - start_monotonic) * 1000
                ),
                cancel_reason="task_cancelled",
            )
        )

    except Exception as exc:
        logger.error(
            "run_execution EXCEPTION — marking FAILED",
            exc_info=exc,
            extra={
                "execution_id": execution.execution_id,
                "exception_type": type(exc).__name__,
                "exception_msg": str(exc)[:500],
                "elapsed_ms": int((time.monotonic() - start_monotonic) * 1000),
            },
        )
        transition(ExecutionStatus.FAILED)
        execution.error = {"message": str(exc)}
        execution.completed_at = time.time()

        if execution.buffer:
            execution.buffer.clear()
        await shadow.clear()
        if blackboard:
            await blackboard.delete()

        await redis_execution_registry.mark_execution_failed(
            execution.execution_id,
            error=str(exc),
        )

        event_bus.publish(
            ExecutionFailed(
                execution_id=execution.execution_id,
                duration_ms=int(
                    (time.monotonic() - start_monotonic) * 1000
                ),
                error_type=type(exc).__name__,
                error_message=str(exc),
                project_id=execution.parent_project_id,
            )
        )
        # B-051: publish failure event so GET /events/stream can forward it.
        try:
            from core.events_publisher import publish_user_event
            await publish_user_event(
                user_id=execution.user_id,
                event="execution.failed",
                execution_id=execution.execution_id,
                summary=f"Execution failed: {type(exc).__name__}",
            )
        except Exception:
            pass
