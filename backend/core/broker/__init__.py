# core/broker/__init__.py
"""
LocalBroker — single-process, Redis-free execution infrastructure.

Import the singleton directly:
    from core.broker import local_broker
"""
from core.broker.local_broker import local_broker

__all__ = ["local_broker"]
