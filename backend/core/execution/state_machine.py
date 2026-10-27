from logging import getLogger

from core.execution.transitions import (
    TERMINAL_STATES,
    ALLOWED_TRANSITIONS,
)
from core.execution.errors import IllegalStateTransition

logger = getLogger(__name__)


class ExecutionStateMachine:
    @staticmethod
    def assert_transition_allowed(
        execution_id: str,
        from_state,
        to_state,
    ):
        if from_state == to_state:
            return  # idempotent

        if from_state in TERMINAL_STATES:
            logger.warning(
                "Invalid state transition from terminal state",
                extra={
                    "execution_id": execution_id,
                    "from_state": from_state,
                    "to_state": to_state,
                },
            )
            raise IllegalStateTransition(
                execution_id, from_state, to_state
            )

        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            logger.warning(
                "Invalid state transition",
                extra={
                    "execution_id": execution_id,
                    "from_state": from_state,
                    "to_state": to_state,
                    "allowed_transitions": list(allowed),
                },
            )
            raise IllegalStateTransition(
                execution_id, from_state, to_state
            )
