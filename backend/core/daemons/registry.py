# core/daemons/registry.py
#
# G-008: DaemonRegistry
#
# Stateless async DB-backed registry for per-user daemon configurations.
# Mirrors the pattern established by core/agents/registry.py (SubagentRegistry).

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from sqlalchemy import select, and_, or_, delete

from core.db.session import AsyncSessionLocal
from core.db.models import DaemonConfig

logger = logging.getLogger(__name__)


def sync_daemon_name(project_id: str) -> str:
    """
    The per-project sync daemon's name is fully deterministic from project_id
    (itself stable/immutable once a project is created) -- no DB column
    needed to look it up later. Shared by api/projects.py's create_project()
    (to name the row it creates), sync_project() (to address it), and
    core/daemons/runner.py's B-094 hook (to recognize and exclude it, since
    it already writes to a project's .eva files directly via skill-runner). Lives here,
    not in api/projects.py, so core/daemons/runner.py (core layer) doesn't
    have to import from the api layer to use it.
    """
    return f"project-sync-{project_id[:8]}"


class DaemonRegistry:
    """Async DB-backed registry for per-user daemon configurations."""

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, user_id: str, name: str) -> Optional[DaemonConfig]:
        """Return the DaemonConfig row for (user_id, name), or None."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(
                        DaemonConfig.user_id == user_id,
                        DaemonConfig.name == name,
                    )
                )
            )
            return result.scalar_one_or_none()

    async def list(
        self,
        user_id: str,
        project_id: Optional[str] = None,
    ) -> List[DaemonConfig]:
        """Return daemon configs for a user, ordered by name.

        Pass project_id to filter to daemons belonging to a specific project.
        Omit to return all daemons regardless of project.
        """
        async with AsyncSessionLocal() as db:
            q = select(DaemonConfig).where(DaemonConfig.user_id == user_id)
            if project_id is not None:
                q = q.where(DaemonConfig.project_id == project_id)
            result = await db.execute(q.order_by(DaemonConfig.name))
            return list(result.scalars().all())

    async def list_subscribed(
        self, event_type: str, project_id: Optional[str] = None
    ) -> List[DaemonConfig]:
        """
        Return all enabled daemons that have ``event_type`` in their
        ``event_subscriptions`` JSON list, scoped to the project the
        triggering execution belongs to.

        P-05b fix (2026-07-27): this previously had no project filter at
        all, so any daemon anywhere in the account -- regardless of which
        project it lives in -- fired on every execution completing in any
        *other* project. Scoping rule, matching the existing
        pause_for_project()/resume_for_project() convention below: a
        daemon with no project_id is global and fires on any triggering
        execution; a project-scoped daemon only fires when the triggering
        execution's own project_id matches its own. If the triggering
        execution itself has no project_id (project_id=None here), only
        global daemons match -- a project-scoped daemon has no reason to
        react to activity outside every project it's tied to.

        Filtering by event_type is done in Python after a broad DB query
        (SQLite JSON array containment is not reliably indexable without
        JSON1); project scoping is done in SQL since it's a plain column.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(
                        DaemonConfig.enabled == True,
                        DaemonConfig.event_subscriptions != None,
                        or_(
                            DaemonConfig.project_id == None,
                            DaemonConfig.project_id == project_id,
                        ),
                    )
                )
            )
            rows = list(result.scalars().all())
        return [
            r for r in rows
            if event_type in (r.event_subscriptions or [])
        ]

    async def list_due(self) -> List[DaemonConfig]:
        """
        Return all enabled daemons whose next_run_at is in the past.
        Used by the scheduler to find daemons ready to fire.
        """
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(
                        DaemonConfig.enabled == True,
                        DaemonConfig.run_interval_seconds != None,
                        DaemonConfig.next_run_at != None,
                        DaemonConfig.next_run_at <= now,
                    )
                )
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def upsert(self, user_id: str, data: dict) -> DaemonConfig:
        """
        Create or replace a daemon config for (user_id, data["name"]).

        data keys: name, description, kind, system_prompt, trigger_message,
                   model, provider, tools, max_steps,
                   run_interval_seconds, enabled, event_subscriptions

        kind ("standard" | "system" | "supervisor") -- this method trusts
        whatever the caller passes; rejecting a client-supplied "system"
        is the API layer's job (api/daemons.py), not this repo-level method
        -- internal callers like api/projects.py's _ensure_sync_daemon()
        legitimately need to pass "system" here directly.
        """
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("Daemon name must not be empty.")

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(
                        DaemonConfig.user_id == user_id,
                        DaemonConfig.name == name,
                    )
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.kind                = data.get("kind",                existing.kind)
                existing.description         = data.get("description",         existing.description)
                existing.system_prompt       = data.get("system_prompt",       existing.system_prompt)
                existing.trigger_message     = data.get("trigger_message",     existing.trigger_message)
                existing.model               = data.get("model",               existing.model)
                existing.provider            = data.get("provider",            existing.provider)
                existing.tools               = data.get("tools",               existing.tools)
                existing.max_steps           = data.get("max_steps",           existing.max_steps)
                existing.run_interval_seconds = data.get("run_interval_seconds", existing.run_interval_seconds)
                existing.enabled             = data.get("enabled",             existing.enabled)
                existing.project_id          = data.get("project_id",          existing.project_id)
                existing.event_subscriptions = data.get("event_subscriptions", existing.event_subscriptions)
                # Retry policy (only updated if explicitly provided)
                if "retry_on_failure" in data:
                    existing.retry_on_failure = data["retry_on_failure"]
                if "max_retries" in data:
                    existing.max_retries = data["max_retries"]
                if "retry_delay_seconds" in data:
                    existing.retry_delay_seconds = data["retry_delay_seconds"]
                # Recalculate next_run_at if interval changed
                if (
                    "run_interval_seconds" in data
                    and existing.run_interval_seconds
                    and existing.enabled
                ):
                    existing.next_run_at = (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=existing.run_interval_seconds)
                    )
                row = existing
            else:
                interval = data.get("run_interval_seconds")
                next_run = (
                    datetime.now(timezone.utc) + timedelta(seconds=interval)
                    if interval else None
                )
                row = DaemonConfig(
                    user_id=user_id,
                    name=name,
                    kind=data.get("kind", "standard"),
                    description=data.get("description"),
                    system_prompt=data.get("system_prompt"),
                    trigger_message=data.get("trigger_message"),
                    model=data.get("model"),
                    provider=data.get("provider"),
                    tools=data.get("tools"),
                    max_steps=data.get("max_steps", 5),
                    run_interval_seconds=interval,
                    enabled=data.get("enabled", True),
                    next_run_at=next_run,
                    retry_on_failure=data.get("retry_on_failure", True),
                    max_retries=data.get("max_retries", 3),
                    retry_delay_seconds=data.get("retry_delay_seconds", 60),
                    lifecycle_status="idle",
                    failure_count=0,
                    project_id=data.get("project_id"),
                    event_subscriptions=data.get("event_subscriptions"),
                )
                db.add(row)

            await db.commit()
            await db.refresh(row)
            logger.info("Daemon config upserted", extra={"user_id": user_id, "daemon_name": name})
            return row

    async def mark_run_complete(
        self,
        user_id: str,
        name: str,
        success: bool,
    ) -> None:
        """
        Update last_run_at and schedule next_run_at after a daemon run.
        Delegates to record_success / record_failure for lifecycle tracking.
        """
        if success:
            await self.record_success(user_id, name)
        else:
            await self.record_failure(user_id, name, error="")

    async def record_success(self, user_id: str, name: str) -> None:
        """
        Called when a daemon run completes successfully.
        Resets failure count, sets lifecycle_status='idle', reschedules.
        """
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(DaemonConfig.user_id == user_id, DaemonConfig.name == name)
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return

            row.last_run_at      = now
            row.failure_count    = 0
            row.last_error       = None
            row.current_execution_id = None
            # Don't overwrite a deliberate "paused" status or a project-pause.
            # "project_paused" is set by pause_for_project() when a project is
            # paused/archived; only resume_for_project() should clear it.
            if row.lifecycle_status not in ("paused", "project_paused"):
                # Defensive: if the daemon ran while its project was paused (e.g.
                # a race between pause_for_project and an in-flight run), restore
                # the cascade state rather than surfacing a misleading "idle" status
                # when the scheduler can't fire this daemon anyway.
                if not row.enabled and row.project_id:
                    row.lifecycle_status = "project_paused"
                else:
                    row.lifecycle_status = "idle"
            if row.run_interval_seconds:
                row.next_run_at = now + timedelta(seconds=row.run_interval_seconds)
            else:
                row.next_run_at = None

            await db.commit()
            logger.info(
                "Daemon run succeeded — failure count reset",
                extra={"user_id": user_id, "daemon_name": name},
            )

    async def record_failure(
        self,
        user_id: str,
        name: str,
        error: str,
    ) -> None:
        """
        Called when a daemon run fails.
        Increments failure_count.  Schedules a retry with exponential backoff
        if retry_on_failure=True and failure_count < max_retries.
        Permanently disables the daemon if max_retries is exhausted.
        """
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(DaemonConfig.user_id == user_id, DaemonConfig.name == name)
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return

            row.last_run_at      = now
            row.last_error       = error or "Unknown error"
            row.last_failure_at  = now
            row.current_execution_id = None
            row.failure_count    = (row.failure_count or 0) + 1

            max_retries    = row.max_retries or 3
            retry_on_fail  = row.retry_on_failure if row.retry_on_failure is not None else True
            delay_base     = row.retry_delay_seconds or 60

            if retry_on_fail and row.failure_count <= max_retries:
                # Exponential back-off: delay * 2^(attempt-1), capped at 1 hour
                backoff = min(delay_base * (2 ** (row.failure_count - 1)), 3600)

                # Defensive: if daemon is project-paused (enabled=False by cascade),
                # don't schedule it for retry — the scheduler won't fire it anyway
                # and "retrying" would be a misleading status.  Restore project_paused
                # so the daemon stays in the correct visible state until the project
                # is resumed.
                if not row.enabled and row.project_id:
                    row.next_run_at      = None
                    row.lifecycle_status = "project_paused"
                    logger.warning(
                        "Daemon run failed while project-paused — retry suppressed",
                        extra={"user_id": user_id, "daemon_name": name, "error": error},
                    )
                else:
                    row.next_run_at      = now + timedelta(seconds=backoff)
                    row.lifecycle_status = "retrying"
                    logger.warning(
                        "Daemon run failed — retry %d/%d scheduled in %ds",
                        row.failure_count, max_retries, backoff,
                        extra={"user_id": user_id, "daemon_name": name, "error": error},
                    )
            else:
                # Max retries exhausted (or retry disabled) — permanently disable
                row.next_run_at      = None
                row.enabled          = False
                row.lifecycle_status = "permanently_failed"
                logger.error(
                    "Daemon permanently failed after %d attempts",
                    row.failure_count,
                    extra={"user_id": user_id, "daemon_name": name, "error": error},
                )

            await db.commit()

    async def set_lifecycle_status(
        self,
        user_id: str,
        name: str,
        status: str,
        execution_id: Optional[str] = None,
    ) -> None:
        """
        Set lifecycle_status (and optionally current_execution_id) directly.
        Used by the runner/trigger to reflect mid-lifecycle transitions
        (idle → running → idle/retrying/permanently_failed).
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(DaemonConfig.user_id == user_id, DaemonConfig.name == name)
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.lifecycle_status = status
            if execution_id is not None:
                row.current_execution_id = execution_id
            await db.commit()

    async def pause_for_project(self, user_id: str, project_id: str) -> int:
        """
        Pause all active/idle/retrying daemons belonging to a project.

        Called when a project transitions to paused or archived.
        Sets lifecycle_status='project_paused' and enabled=False so the
        scheduler stops firing them.

        Deliberately skips daemons already in 'paused' or 'permanently_failed'
        or 'project_paused' states — those are under explicit control and must
        not be touched.

        Returns the number of daemons affected.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(
                        DaemonConfig.user_id == user_id,
                        DaemonConfig.project_id == project_id,
                        DaemonConfig.lifecycle_status.notin_(
                            ["paused", "permanently_failed", "project_paused"]
                        ),
                    )
                )
            )
            rows = list(result.scalars().all())
            for row in rows:
                row.enabled = False
                row.lifecycle_status = "project_paused"
            await db.commit()
        count = len(rows)
        if count:
            logger.info(
                "Daemons paused due to project pause/archive",
                extra={"user_id": user_id, "project_id": project_id, "count": count},
            )
        return count

    async def resume_for_project(self, user_id: str, project_id: str) -> int:
        """
        Resume daemons that were paused because their project was paused.

        Called when a project transitions back to active.
        Only restores daemons with lifecycle_status='project_paused' —
        daemons the user manually paused ('paused') are NOT touched.

        Recalculates next_run_at from now so daemons fire at the correct
        next interval rather than immediately (which could mass-trigger after
        a long pause).

        Returns the number of daemons resumed.
        """
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DaemonConfig).where(
                    and_(
                        DaemonConfig.user_id == user_id,
                        DaemonConfig.project_id == project_id,
                        DaemonConfig.lifecycle_status == "project_paused",
                    )
                )
            )
            rows = list(result.scalars().all())
            for row in rows:
                row.enabled = True
                row.lifecycle_status = "idle"
                # Recalculate next_run_at from now so the daemon fires at the
                # correct next interval, not immediately after a long pause.
                if row.run_interval_seconds:
                    row.next_run_at = now + timedelta(seconds=row.run_interval_seconds)
            await db.commit()
        count = len(rows)
        if count:
            logger.info(
                "Daemons resumed due to project activation",
                extra={"user_id": user_id, "project_id": project_id, "count": count},
            )
        return count

    async def delete(self, user_id: str, name: str) -> bool:
        """Delete the daemon config for (user_id, name). Returns True if deleted."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(DaemonConfig).where(
                    and_(
                        DaemonConfig.user_id == user_id,
                        DaemonConfig.name == name,
                    )
                )
            )
            await db.commit()
            deleted = result.rowcount > 0
            if deleted:
                logger.info("Daemon config deleted", extra={"user_id": user_id, "daemon_name": name})
            return deleted


# Module-level singleton
daemon_registry = DaemonRegistry()
