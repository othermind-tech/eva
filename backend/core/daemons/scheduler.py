# core/daemons/scheduler.py
#
# G-008: Daemon Scheduler
#
# A lightweight async background loop that:
#   1. Wakes up every POLL_INTERVAL_SECONDS (default 60)
#   2. Queries all enabled daemons whose next_run_at <= now
#   3. Fires each due daemon as a background asyncio task
#   4. Updates last_run_at / next_run_at in the DB after each run
#
# The scheduler is started inside the FastAPI lifespan context manager
# (main.py) as a background asyncio task.  It is cancelled cleanly on
# shutdown.
#
# Design notes:
#   - Each daemon run is an independent asyncio task — slow daemons don't
#     block the poll loop or each other.
#   - run_daemon() obtains the LocalBroker adapter internally for the
#     blackboard and inter-agent messaging.
#   - If a daemon raises an unhandled exception, the error is logged and
#     the daemon is rescheduled normally (it doesn't permanently disable it).

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.settings import DAEMON_SCHEDULER_POLL_SECONDS

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = DAEMON_SCHEDULER_POLL_SECONDS


class DaemonScheduler:
    """
    Background scheduler that fires daemon agents on their configured intervals.

    Usage (in main.py lifespan):
        scheduler = DaemonScheduler()
        task = asyncio.create_task(scheduler.run())
        yield
        task.cancel()
    """

    def __init__(self) -> None:
        pass

    async def run(self) -> None:
        """Main scheduler loop — runs until cancelled."""
        logger.info("[DaemonScheduler] Started (poll interval: %ds)", POLL_INTERVAL_SECONDS)

        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                await self._poll()
            except asyncio.CancelledError:
                logger.info("[DaemonScheduler] Shutting down.")
                break
            except Exception as exc:
                # Never let the poll loop crash permanently
                logger.error("[DaemonScheduler] Poll error (continuing): %s", exc)

    async def _poll(self) -> None:
        """Find and fire all daemons that are due to run."""
        from core.daemons.registry import daemon_registry

        try:
            due = await daemon_registry.list_due()
        except Exception as exc:
            logger.error("[DaemonScheduler] list_due failed: %s", exc)
            return

        if not due:
            return

        logger.info("[DaemonScheduler] %d daemon(s) due to run", len(due))

        for daemon in due:
            asyncio.create_task(
                self._fire(daemon),
                name=f"daemon-{daemon.name}-{daemon.user_id[:8]}",
            )

    async def _fire(self, daemon: Any) -> None:
        """Execute one daemon run and update its schedule."""
        from core.daemons.runner import run_daemon
        from core.daemons.registry import daemon_registry

        logger.info("[DaemonScheduler] Firing daemon: %s (user=%s)", daemon.name, daemon.user_id)

        try:
            result = await run_daemon(daemon=daemon)
            success = result.get("success", False)
        except Exception as exc:
            logger.exception(
                "[DaemonScheduler] Unhandled exception in daemon run: %s", exc
            )
            success = False

        try:
            await daemon_registry.mark_run_complete(
                user_id=daemon.user_id,
                name=daemon.name,
                success=success,
            )
        except Exception as exc:
            logger.error(
                "[DaemonScheduler] Failed to update daemon schedule: daemon=%s err=%s",
                daemon.name, exc,
            )
