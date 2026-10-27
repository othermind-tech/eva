# core/tools/executor.py

from typing import Dict, Any
from logging import getLogger

from core.types import ToolExecutionResult, ToolExecutionStatus

logger = getLogger(__name__)


async def execute_tool_safely(
    *,
    dispatcher,
    call_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_classes: Dict[str, Any],
    execution,
    dynamic_registry=None,
    max_result_tokens=None,
    model_name=None,
) -> Dict[str, Any]:
    """
    Execute a tool and normalize the result into:
    { "ok": bool, "result" | "error": ... }

    max_result_tokens, model_name (V6.11): passed straight through to
    ToolDispatcher.execute_tool()'s context-budget cap -- see there for
    details. Both default to None (capping disabled) for any caller that
    doesn't track a context budget.

    NEVER raises — AgentEngine controls flow.
    """
    logger.debug(
        "Executing tool",
        extra={
            "call_id": call_id,
            "tool_name": tool_name,
            "argument_keys": list(arguments.keys()) if arguments else [],
        },
    )

    try:
        tool_result = await dispatcher.execute_tool(
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments or {},
            tool_classes=tool_classes,
            execution_metadata={"execution":execution},
            dynamic_registry=dynamic_registry,
            max_result_tokens=max_result_tokens,
            model_name=model_name,
        )
        if isinstance(tool_result, ToolExecutionResult):

            if tool_result.status == ToolExecutionStatus.SUCCESS:
                logger.debug(
                    "Tool executed successfully",
                    extra={"call_id": call_id, "tool_name": tool_name},
                )
                return {
                    "ok": True,
                    "result": tool_result.output,
                    "metadata": tool_result.metadata,
                }

            # Failure case
            logger.warning(
                "Tool execution failed",
                extra={
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "error_type": tool_result.error_type,
                    "retryable": tool_result.retryable,
                },
            )
            return {
                "ok": False,
                "error": {
                    "type": tool_result.error_type,
                    "message": tool_result.error_message,
                    "retryable": tool_result.retryable,
                },
            }

    except Exception as exc:
        logger.exception(
            "Tool execution threw exception",
            extra={"call_id": call_id, "tool_name": tool_name},
        )
        return {
            "ok": False,
            "error": str(exc),
        }
