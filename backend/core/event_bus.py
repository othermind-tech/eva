# core/event_bus.py
#
# Synchronous, in-process event bus.
#
# Design rules:
#   - No external dependencies (stdlib only).
#   - publish() NEVER raises — handler exceptions are swallowed and logged.
#   - subscribe() is safe to call at any point (including at import time).
#   - Thread-safe for reads; subscribe() calls at import time are effectively
#     single-threaded (Python GIL + module import lock).
#   - Handlers are called in subscription order.
#   - Handlers must be synchronous callables (no async handlers).
#
# Usage:
#   from core.event_bus import event_bus
#   from core.events import ExecutionStarted
#
#   # Subscribe (typically at module load):
#   event_bus.subscribe(ExecutionStarted, my_handler)
#
#   # Publish (in business logic):
#   event_bus.publish(ExecutionStarted(execution_id=..., ...))

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class EventBus:
    """
    Minimal synchronous, in-process pub/sub event bus.

    - One bus instance per process (use the module-level `event_bus` singleton).
    - Handlers are registered per concrete event type (exact type match — no subclass walk).
    - publish() dispatches synchronously and swallows all handler exceptions.
    """

    def __init__(self) -> None:
        # Maps event_class → [handler, ...]
        self._handlers: Dict[type, List[Callable[[Any], None]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(
        self,
        event_type: Type[T],
        handler: Callable[[T], None],
    ) -> None:
        """
        Register `handler` to be called whenever an event of `event_type` is published.

        Parameters
        ----------
        event_type:
            The concrete event class (e.g. ExecutionStarted).
        handler:
            A synchronous callable that accepts a single event instance.
            Must never raise — exceptions are caught by publish().

        Notes
        -----
        - Calling subscribe() multiple times with the same (type, handler) pair
          will result in the handler being called multiple times per publish.
        - There is intentionally no unsubscribe() — handlers live for the process lifetime.
        """
        self._handlers[event_type].append(handler)
        log.debug(
            "EventBus subscribed",
            extra={
                "event_type": event_type.__name__,
                "handler": getattr(handler, "__qualname__", repr(handler)),
            },
        )

    def subscribe_all(
        self,
        event_types: tuple,
        handler: Callable[[Any], None],
    ) -> None:
        """
        Convenience method: subscribe `handler` to multiple event types at once.

        Parameters
        ----------
        event_types:
            A tuple of concrete event classes (e.g. from core.events.ALL_EVENT_TYPES).
        handler:
            Called with each matching event instance.
        """
        for event_type in event_types:
            self.subscribe(event_type, handler)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(self, event: Any) -> None:
        """
        Dispatch `event` to all registered handlers for its exact type.

        - Never raises.
        - Swallows and logs handler exceptions so that a bad subscriber
          never disrupts business logic.
        - Handlers are called in the order they were subscribed.

        Parameters
        ----------
        event:
            Any event instance (typically a dataclass from core.events).
        """
        handlers = self._handlers.get(type(event))
        if not handlers:
            return

        event_type_name = type(event).__name__
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.warning(
                    "EventBus handler raised an exception (swallowed)",
                    extra={
                        "event_type": event_type_name,
                        "handler": getattr(handler, "__qualname__", repr(handler)),
                    },
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Introspection (testing / diagnostics)
    # ------------------------------------------------------------------

    def subscriber_count(self, event_type: type) -> int:
        """Return the number of handlers subscribed to `event_type`."""
        return len(self._handlers.get(event_type, []))

    def registered_event_types(self) -> List[type]:
        """Return a list of event types that have at least one subscriber."""
        return [et for et, handlers in self._handlers.items() if handlers]

    def reset(self) -> None:
        """
        Remove all subscriptions.
        Intended for use in unit tests only — do NOT call in production code.
        """
        self._handlers.clear()
        log.debug("EventBus reset: all subscriptions cleared.")


# ---------------------------------------------------------------------------
# Module-level singleton — import and use this everywhere
# ---------------------------------------------------------------------------

event_bus = EventBus()
