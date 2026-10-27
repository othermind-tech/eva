from fastapi import APIRouter, HTTPException, Depends
from logging import getLogger
from api.schemas.responses import CancelExecutionResponse
from core.execution import execution_registry
from core.auth.dependencies import get_current_auth  

logger = getLogger(__name__)
router = APIRouter()


@router.post("/cancel", response_model=CancelExecutionResponse)
async def cancel_execution(conversation_id: str, auth=Depends(get_current_auth)):
    """
    Cancels the latest RUNNING execution for a conversation.
    """
    user_id = auth["user_id"]
    logger.info(
        "Cancel execution requested",
        extra={
            "conversation_id": conversation_id,
            "user_id": user_id,
        },
    )
    execution = execution_registry.get_latest(conversation_id)
    if not execution:
        logger.warning(
            "Cancel failed - no execution found",
            extra={"conversation_id": conversation_id},
        )
        raise HTTPException(
            status_code=404,
            detail="No execution found for the given conversation_id",
        )
    
    if execution.user_id != user_id:
        logger.warning(
            "Cancel failed - unauthorized",
            extra={
                "conversation_id": conversation_id,
                "user_id": user_id,
                "execution_user_id": execution.user_id,
            },
        )
        raise HTTPException(
            status_code=403,
            detail="Not authorized to cancel this execution",
        )
    
    logger.debug(
        "Cancelling execution",
        extra={"conversation_id": conversation_id, "user_id": user_id},
    )
    success = await execution_registry.cancel_latest(conversation_id)

    if not success:
        logger.warning(
            "Cancel failed - no running execution",
            extra={"conversation_id": conversation_id},
        )
        raise HTTPException(
            status_code=404,
            detail="No running execution to cancel",
        )

    logger.info(
        "Execution cancelled successfully",
        extra={
            "conversation_id": conversation_id,
            "user_id": user_id,
        },
    )
    return {
        "conversation_id": conversation_id,
        "status": "cancelled"
    }
