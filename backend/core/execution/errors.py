class ExecutionStateError(RuntimeError):
    pass


class IllegalStateTransition(ExecutionStateError):
    def __init__(self, execution_id, from_state, to_state):
        super().__init__(
            f"Illegal execution state transition: "
            f"{execution_id} {from_state} → {to_state}"
        )
