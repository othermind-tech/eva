from typing import Protocol, Dict, Any


class ObservabilityHook(Protocol):
    """
    Side-channel observability hook.
    Must never raise.
    Must never affect control flow.
    """

    def emit(self, event: str, payload: Dict[str, Any]) -> None:
        ...
