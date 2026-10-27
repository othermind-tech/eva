# core/broker/exceptions.py
"""Exceptions raised by the LocalBroker."""


class BrokerNotStarted(RuntimeError):
    """Raised when LocalBroker is used before start() is called."""


class ApprovalGateNotFound(KeyError):
    """Raised when resolving an unknown approval token."""


class ApprovalGateExpired(RuntimeError):
    """Raised when an approval gate has already been resolved or timed out."""
