from observability.base import ObservabilityHook


class NullObservabilityHook:
    def emit(self, event: str, payload: dict) -> None:
        return
