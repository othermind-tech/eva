# observability/logger.py
#
# Structured-logging subscriber for EVA's typed event bus.
#
# Two responsibilities:
#
# 1. LoggingObservabilityHook (LEGACY)
#    Kept for backward compatibility: AgentEngine still uses the old
#    observability.emit(event_str, payload_dict) protocol.  Until that
#    migration is complete this hook converts those calls into log lines.
#
# 2. Event bus handlers (NEW)
#    One handler per typed event from core.events.
#    Registered automatically when this module is imported via
#    `register_with_bus()` → called at the bottom of this file.
#
# All handlers:
#   - Emit a single logger.info("OBS_EVENT", extra={...}) line.
#   - NEVER raise — guaranteed by the "try/except pass" contract.
#   - Use a flat `extra` dict so that log formatters / sinks can index fields.

import logging

from core.event_bus import event_bus
from core.events import (
    ALL_EVENT_TYPES,
    ExecutionCancelled,
    ExecutionCompleted,
    ExecutionFailed,
    ExecutionStarted,
    FallbackTriggered,
    ProviderFallbackTriggered,
    StepCompleted,
    ToolCalled,
    ToolPermissionDenied,
    ToolResult,
)

logger = logging.getLogger("eva.observability")


# ===========================================================================
# 1. LEGACY hook — keeps AgentEngine working without modification
# ===========================================================================

class LoggingObservabilityHook:
    """
    Emits legacy observability.emit() calls as structured log lines.

    This class exists solely for backward compatibility with AgentEngine,
    which calls `self.observability.emit(event_str, payload_dict)`.
    New code must use `event_bus.publish(TypedEvent(...))` instead.
    """

    def emit(self, event: str, payload: dict) -> None:
        try:
            logger.info(
                "OBS_EVENT",
                extra={
                    "event": event,
                    **payload,
                },
            )
        except Exception:
            # Observability must NEVER break execution
            pass


# ===========================================================================
# 2. Typed event bus handlers
# ===========================================================================

def _on_execution_started(e: ExecutionStarted) -> None:
    try:
        logger.info(
            "OBS_EVENT",
            extra={
                "event":           "execution.started",
                "execution_id":    e.execution_id,
                "conversation_id": e.conversation_id,
                "user_id":         e.user_id,
                "ts":              e.timestamp,
            },
        )
    except Exception:
        pass


def _on_execution_completed(e: ExecutionCompleted) -> None:
    try:
        logger.info(
            "OBS_EVENT",
            extra={
                "event":        "execution.completed",
                "execution_id": e.execution_id,
                "duration_ms":  e.duration_ms,
                "status":       "success",
                "ts":           e.timestamp,
            },
        )
    except Exception:
        pass


def _on_execution_failed(e: ExecutionFailed) -> None:
    try:
        logger.info(
            "OBS_EVENT",
            extra={
                "event":        "execution.failed",
                "execution_id": e.execution_id,
                "duration_ms":  e.duration_ms,
                "error_type":   e.error_type,
                "status":       "failed",
                "ts":           e.timestamp,
            },
        )
    except Exception:
        pass


def _on_execution_cancelled(e: ExecutionCancelled) -> None:
    try:
        logger.info(
            "OBS_EVENT",
            extra={
                "event":         "execution.cancelled",
                "execution_id":  e.execution_id,
                "duration_ms":   e.duration_ms,
                "cancel_reason": e.cancel_reason,
                "status":        "cancelled",
                "ts":            e.timestamp,
            },
        )
    except Exception:
        pass


def _on_tool_called(e: ToolCalled) -> None:
    try:
        logger.info(
            "OBS_EVENT",
            extra={
                "event":        "tool.called",
                "execution_id": e.execution_id,
                "call_id":      e.call_id,
                "tool_name":    e.tool_name,
                "attempt":      e.attempt,
                "retry_count":  e.retry_count,
                "ts":           e.timestamp,
            },
        )
    except Exception:
        pass


def _on_tool_result(e: ToolResult) -> None:
    try:
        logger.info(
            "OBS_EVENT",
            extra={
                "event":        "tool.result",
                "execution_id": e.execution_id,
                "call_id":      e.call_id,
                "tool_name":    e.tool_name,
                "success":      e.success,
                "latency_ms":   e.latency_ms,
                "retry_count":  e.retry_count,
                "error_type":   e.error_type,
                "ts":           e.timestamp,
            },
        )
    except Exception:
        pass


def _on_tool_permission_denied(e: ToolPermissionDenied) -> None:
    try:
        logger.warning(
            "OBS_EVENT",
            extra={
                "event":        "tool.permission_denied",
                "execution_id": e.execution_id,
                "call_id":      e.call_id,
                "tool_name":    e.tool_name,
                "error":        e.error,
                "ts":           e.timestamp,
            },
        )
    except Exception:
        pass


def _on_step_completed(e: StepCompleted) -> None:
    try:
        logger.info(
            "OBS_EVENT",
            extra={
                "event":        "agent.step_completed",
                "execution_id": e.execution_id,
                "session_id":   e.session_id,
                "step_index":   e.step_index,
                "ts":           e.timestamp,
            },
        )
    except Exception:
        pass


def _on_fallback_triggered(e: FallbackTriggered) -> None:
    try:
        logger.info(
            "OBS_EVENT",
            extra={
                "event":        "agent.fallback_triggered",
                "execution_id": e.execution_id,
                "session_id":   e.session_id,
                "step_index":   e.step_index,
                "reason":       e.reason,
                "ts":           e.timestamp,
            },
        )
    except Exception:
        pass


def _on_provider_fallback_triggered(e: ProviderFallbackTriggered) -> None:
    try:
        logger.warning(
            "OBS_EVENT",
            extra={
                "event":             "provider.fallback_triggered",
                "execution_id":      e.execution_id,
                "session_id":        e.session_id,
                "failed_provider":   e.failed_provider,
                "failed_model":      e.failed_model,
                "failure_reason":    e.failure_reason,
                "fallback_provider": e.fallback_provider,
                "fallback_model":    e.fallback_model,
                "fallback_index":    e.fallback_index,
                "ts":                e.timestamp,
            },
        )
    except Exception:
        pass


# ===========================================================================
# 3. Registration
# ===========================================================================

def register_with_bus(bus=event_bus) -> None:
    """
    Subscribe all logging handlers to the given event bus.

    Called automatically at module import (bottom of this file).
    May also be called explicitly with a custom bus in tests.
    """
    bus.subscribe(ExecutionStarted,          _on_execution_started)
    bus.subscribe(ExecutionCompleted,        _on_execution_completed)
    bus.subscribe(ExecutionFailed,           _on_execution_failed)
    bus.subscribe(ExecutionCancelled,        _on_execution_cancelled)
    bus.subscribe(ToolCalled,                _on_tool_called)
    bus.subscribe(ToolResult,                _on_tool_result)
    bus.subscribe(ToolPermissionDenied,      _on_tool_permission_denied)
    bus.subscribe(StepCompleted,             _on_step_completed)
    bus.subscribe(FallbackTriggered,         _on_fallback_triggered)
    bus.subscribe(ProviderFallbackTriggered, _on_provider_fallback_triggered)


# Auto-register when this module is first imported.
# main.py already imports config.logging (which sets up handlers) before
# any business-logic imports, so the logging system is ready at this point.
register_with_bus()
