# tests/test_daemon_event_scoping_unit.py
"""
Unit tests for the P-05b project-scoping fix (2026-07-27).

Before this fix, core/daemons/registry.py's list_subscribed() had no
project_id filter at all -- any enabled daemon subscribed to an event
type fired on every matching execution completing anywhere in the
account, regardless of which project either the daemon or the triggering
execution belonged to. Found during a gap-tracker verification sweep
(P-05), not by a user report.

Fix: ExecutionCompleted/ExecutionFailed (core/events.py) now carry
project_id, threaded through from the triggering execution at all 5
publish sites (core/execution/{pev_runner,runner}.py), through main.py's
event handler, into list_subscribed()'s new project_id parameter and SQL
filter. Scoping rule: a daemon with no project_id is global and matches
any trigger; a project-scoped daemon only matches a trigger from the same
project.

Live-DB round trip (real AsyncSessionLocal), matching this codebase's
existing convention for DaemonRegistry tests (see
test_frontend_gap_fixes_unit.py's TestEventSubscriptionsRegistryRoundTrip).

Run with:
    cd <project_root>
    pytest tests/test_daemon_event_scoping_unit.py -v
"""

from __future__ import annotations

import unittest


# ===========================================================================
# core/events.py -- ExecutionCompleted/ExecutionFailed carry project_id
# ===========================================================================

class TestExecutionEventsCarryProjectId(unittest.TestCase):
    """These are what main.py's handler reads project_id off of -- if the
    field disappears or its default changes, the fix silently breaks."""

    def test_execution_completed_has_project_id_field(self):
        from core.events import ExecutionCompleted
        e = ExecutionCompleted(execution_id="e1", duration_ms=100)
        self.assertIsNone(e.project_id)  # default -- unlinked execution
        e2 = ExecutionCompleted(execution_id="e1", duration_ms=100, project_id="proj-A")
        self.assertEqual(e2.project_id, "proj-A")

    def test_execution_failed_has_project_id_field(self):
        from core.events import ExecutionFailed
        e = ExecutionFailed(execution_id="e1", duration_ms=100, error_type="ValueError")
        self.assertIsNone(e.project_id)
        e2 = ExecutionFailed(
            execution_id="e1", duration_ms=100, error_type="ValueError", project_id="proj-A"
        )
        self.assertEqual(e2.project_id, "proj-A")


# ===========================================================================
# core/daemons/registry.py's list_subscribed() -- the actual fix
# ===========================================================================

class TestListSubscribedProjectScoping(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from core.daemons.registry import daemon_registry
        self.registry = daemon_registry
        self.user_id = "pytest-event-scope-user"
        self._created: list[tuple[str, str]] = []  # (user_id, name) to clean up

    async def asyncTearDown(self):
        for user_id, name in self._created:
            try:
                await self.registry.delete(user_id, name)
            except Exception:
                pass

    async def _make_daemon(self, name, project_id=None, enabled=True):
        row = await self.registry.upsert(self.user_id, {
            "name": name,
            "event_subscriptions": ["execution.completed"],
            "enabled": enabled,
            "project_id": project_id,
        })
        self._created.append((self.user_id, name))
        return row

    async def test_global_daemon_fires_regardless_of_triggering_project(self):
        """A daemon with no project_id is not scoped -- it must match no
        matter which project (or no project) the triggering execution
        belongs to."""
        await self._make_daemon("pytest-global-daemon", project_id=None)

        for triggering_project_id in (None, "pytest-proj-A", "pytest-proj-B"):
            with self.subTest(triggering_project_id=triggering_project_id):
                matched = await self.registry.list_subscribed(
                    "execution.completed", project_id=triggering_project_id
                )
                self.assertIn("pytest-global-daemon", [d.name for d in matched])

    async def test_project_scoped_daemon_fires_only_for_same_project(self):
        """The actual bug this fix closes: a daemon scoped to project A
        must not fire when the triggering execution belongs to project B,
        or to no project at all."""
        await self._make_daemon("pytest-proja-daemon", project_id="pytest-proj-A")

        matched_same = await self.registry.list_subscribed(
            "execution.completed", project_id="pytest-proj-A"
        )
        self.assertIn("pytest-proja-daemon", [d.name for d in matched_same])

        matched_other = await self.registry.list_subscribed(
            "execution.completed", project_id="pytest-proj-B"
        )
        self.assertNotIn("pytest-proja-daemon", [d.name for d in matched_other])

        matched_unlinked = await self.registry.list_subscribed(
            "execution.completed", project_id=None
        )
        self.assertNotIn("pytest-proja-daemon", [d.name for d in matched_unlinked])

    async def test_two_project_scoped_daemons_do_not_cross_fire(self):
        """Direct regression test for the reported scenario: a daemon in
        project A and a daemon in project B must each only ever see their
        own project's triggers, never the other's."""
        await self._make_daemon("pytest-proja-daemon2", project_id="pytest-proj-A")
        await self._make_daemon("pytest-projb-daemon2", project_id="pytest-proj-B")

        matched_a = await self.registry.list_subscribed(
            "execution.completed", project_id="pytest-proj-A"
        )
        names_a = [d.name for d in matched_a]
        self.assertIn("pytest-proja-daemon2", names_a)
        self.assertNotIn("pytest-projb-daemon2", names_a)

        matched_b = await self.registry.list_subscribed(
            "execution.completed", project_id="pytest-proj-B"
        )
        names_b = [d.name for d in matched_b]
        self.assertIn("pytest-projb-daemon2", names_b)
        self.assertNotIn("pytest-proja-daemon2", names_b)

    async def test_disabled_daemon_still_excluded_regardless_of_project_match(self):
        """Regression guard: the pre-existing enabled filter must keep
        working correctly alongside the new project filter, not be
        silently defeated by it."""
        await self._make_daemon(
            "pytest-disabled-daemon", project_id="pytest-proj-A", enabled=False
        )

        matched = await self.registry.list_subscribed(
            "execution.completed", project_id="pytest-proj-A"
        )
        self.assertNotIn("pytest-disabled-daemon", [d.name for d in matched])

    async def test_event_type_not_subscribed_still_excluded(self):
        """Regression guard: a daemon subscribed to a different event type
        must not match, independent of project scoping."""
        await self._make_daemon("pytest-wrongevent-daemon", project_id="pytest-proj-A")
        # _make_daemon only subscribes to "execution.completed"
        matched = await self.registry.list_subscribed(
            "execution.failed", project_id="pytest-proj-A"
        )
        self.assertNotIn("pytest-wrongevent-daemon", [d.name for d in matched])


if __name__ == "__main__":
    unittest.main(verbosity=2)
