# tests/test_daemon_kind_unit.py
"""
Unit tests for DaemonConfig.kind (2026-08-18, Phase 1 of the background-
process-supervision plan).

Background: the only daemon-behavior distinction that existed before this
was a fragile name-string comparison in core/daemons/runner.py
(daemon.name != sync_daemon_name(daemon.project_id)) special-casing the
project's auto-created sync daemon. This adds a formal `kind` column
("standard" | "system" | "supervisor") and replaces that name-coupled
check with a real type check.

Live-DB round trip (real AsyncSessionLocal) for the DaemonRegistry tests,
matching this codebase's existing convention for DaemonRegistry tests --
see test_daemon_event_scoping_unit.py, which documents the same choice
(DaemonRegistry has no injectable-session constructor, so an isolated
in-memory-SQLite harness isn't available the way it is for repo-level
classes tested via core.db.repository directly).

Run with:
    cd <project_root>
    pytest tests/test_daemon_kind_unit.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# ===========================================================================
# core/db/models.py -- DaemonConfig.kind column
# ===========================================================================

class TestDaemonConfigKindColumn(unittest.TestCase):

    def test_kind_column_exists_with_standard_default(self):
        from core.db.models import DaemonConfig
        col = DaemonConfig.__table__.c.get("kind")
        self.assertIsNotNone(col)
        self.assertFalse(col.nullable)
        self.assertEqual(col.default.arg, "standard")


# ===========================================================================
# core/daemons/registry.py -- DaemonRegistry.upsert() kind handling
# ===========================================================================

class TestDaemonRegistryKindPersistence(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from core.daemons.registry import daemon_registry
        self.registry = daemon_registry
        self.user_id = "pytest-daemon-kind-user"
        self._created: list[tuple[str, str]] = []

    async def asyncTearDown(self):
        for user_id, name in self._created:
            try:
                await self.registry.delete(user_id, name)
            except Exception:
                pass

    async def _make(self, name, **overrides):
        data = {"name": name, **overrides}
        row = await self.registry.upsert(self.user_id, data)
        self._created.append((self.user_id, name))
        return row

    async def test_create_without_kind_defaults_to_standard(self):
        row = await self._make("pytest-kind-default")
        self.assertEqual(row.kind, "standard")

    async def test_create_with_explicit_supervisor_kind_persists(self):
        row = await self._make("pytest-kind-supervisor", kind="supervisor")
        self.assertEqual(row.kind, "supervisor")

        fetched = await self.registry.get(self.user_id, "pytest-kind-supervisor")
        self.assertEqual(fetched.kind, "supervisor")

    async def test_create_with_system_kind_persists(self):
        """The registry trusts its caller -- rejecting a client-supplied
        'system' is api/daemons.py's job (enforced by the Pydantic Literal
        type on DaemonConfigRequest), not this repo-level method's. Internal
        callers like _ensure_sync_daemon() legitimately need this to work."""
        row = await self._make("pytest-kind-system", kind="system")
        self.assertEqual(row.kind, "system")

    async def test_update_omitting_kind_leaves_existing_kind_unchanged(self):
        await self._make("pytest-kind-update-omit", kind="supervisor")
        updated = await self.registry.upsert(self.user_id, {
            "name": "pytest-kind-update-omit",
            "description": "changed description, no kind key at all",
        })
        self.assertEqual(updated.kind, "supervisor")

    async def test_update_explicitly_changing_kind_updates_it(self):
        await self._make("pytest-kind-update-explicit", kind="standard")
        updated = await self.registry.upsert(self.user_id, {
            "name": "pytest-kind-update-explicit",
            "kind": "supervisor",
        })
        self.assertEqual(updated.kind, "supervisor")


# ===========================================================================
# api/daemons.py -- DaemonConfigRequest rejects client-supplied "system"
# ===========================================================================

class TestDaemonConfigRequestKindValidation(unittest.TestCase):

    def test_standard_and_supervisor_accepted(self):
        from api.daemons import DaemonConfigRequest
        for k in ("standard", "supervisor"):
            with self.subTest(kind=k):
                req = DaemonConfigRequest(name="x", system_prompt="p", kind=k)
                self.assertEqual(req.kind, k)

    def test_omitted_kind_defaults_to_standard(self):
        from api.daemons import DaemonConfigRequest
        req = DaemonConfigRequest(name="x", system_prompt="p")
        self.assertEqual(req.kind, "standard")

    def test_system_kind_rejected_by_schema(self):
        import pydantic
        from api.daemons import DaemonConfigRequest
        with self.assertRaises(pydantic.ValidationError):
            DaemonConfigRequest(name="x", system_prompt="p", kind="system")

    def test_response_model_allows_system_for_read_side(self):
        """The response type is a plain str (not the request's Literal) --
        an existing 'system' daemon must still be representable when read
        back, even though it can never be set through the request body."""
        from api.daemons import DaemonConfigResponse
        from datetime import datetime, timezone
        resp = DaemonConfigResponse(
            id="d1", name="project-sync-abc", kind="system",
            description=None, system_prompt="p", trigger_message=None,
            model=None, provider=None, tools=None, max_steps=3,
            run_interval_seconds=None, enabled=True,
            last_run_at=None, next_run_at=None,
            created_at=datetime.now(timezone.utc), updated_at=None,
            lifecycle_status="idle", current_execution_id=None,
            retry_on_failure=True, max_retries=3, retry_delay_seconds=60,
            failure_count=0, last_error=None, last_failure_at=None,
        )
        self.assertEqual(resp.kind, "system")


# ===========================================================================
# api/projects.py -- _ensure_sync_daemon() sets kind="system"
# ===========================================================================

class TestEnsureSyncDaemonSetsSystemKind(unittest.IsolatedAsyncioTestCase):

    async def test_ensure_sync_daemon_passes_kind_system(self):
        from api.projects import _ensure_sync_daemon
        from core.daemons.registry import daemon_registry

        fake_project = MagicMock()
        fake_project.id = "proj-kind-test"
        fake_project.name = "Kind Test Project"

        with patch.object(daemon_registry, "upsert", new=AsyncMock()) as mock_upsert:
            await _ensure_sync_daemon(fake_project, "u1")

        mock_upsert.assert_called_once()
        _, call_data = mock_upsert.call_args.args
        self.assertEqual(call_data.get("kind"), "system")


# ===========================================================================
# core/daemons/runner.py -- kind-based artifact-updater exclusion
# ===========================================================================

class TestRunnerArtifactUpdaterKindExclusion(unittest.TestCase):
    """Source-level check, matching the existing convention for this exact
    hook (TestDaemonRunnerArtifactUpdaterWiring in test_features.py) -- a
    full functional test would need to mock run_daemon()'s entire
    AgentEngine/broker/DB call chain, disproportionate for verifying which
    condition gates a fire-and-forget asyncio.create_task() call."""

    @staticmethod
    def _hook_section() -> str:
        from pathlib import Path
        src = Path("core/daemons/runner.py").read_text(encoding="utf-8")
        idx = src.find("B-094")
        assert idx > 0, "B-094 hook marker not found in runner.py"
        return src[idx:idx + 2000]

    def test_exclusion_is_kind_based_not_name_based(self):
        section = self._hook_section()
        self.assertIn('getattr(daemon, "kind", "standard") != "system"', section)
        # A comment mentioning sync_daemon_name() for context (explaining
        # what this replaced) is fine -- the old check/import must be gone.
        self.assertNotIn("daemon.name != sync_daemon_name", section)
        self.assertNotIn("from core.daemons.registry import sync_daemon_name", section)
        self.assertNotIn("daemon.name !=", section)


# ===========================================================================
# api/daemons.py -- clone endpoint never propagates "system" kind
# ===========================================================================

class TestCloneDaemonKindHandling(unittest.IsolatedAsyncioTestCase):

    async def _run_clone(self, source_kind: str):
        from api.daemons import clone_daemon, DaemonCloneRequest
        from core.daemons.registry import daemon_registry

        source = MagicMock()
        source.kind = source_kind
        source.description = "d"
        source.system_prompt = "p"
        source.trigger_message = None
        source.model = None
        source.provider = None
        source.tools = None
        source.max_steps = 5
        source.run_interval_seconds = None
        source.retry_on_failure = True
        source.max_retries = 3
        source.retry_delay_seconds = 60
        source.project_id = None

        from datetime import datetime, timezone
        result_row = MagicMock()
        result_row.id = "cloned-id"
        result_row.name = "cloned-name"
        result_row.kind = "standard" if source_kind == "system" else source_kind
        result_row.description = None
        result_row.system_prompt = "p"
        result_row.trigger_message = None
        result_row.model = None
        result_row.provider = None
        result_row.tools = None
        result_row.max_steps = 5
        result_row.run_interval_seconds = None
        result_row.enabled = False
        result_row.last_run_at = None
        result_row.next_run_at = None
        result_row.created_at = datetime.now(timezone.utc)
        result_row.updated_at = None
        result_row.lifecycle_status = "idle"
        result_row.current_execution_id = None
        result_row.retry_on_failure = True
        result_row.max_retries = 3
        result_row.retry_delay_seconds = 60
        result_row.failure_count = 0
        result_row.last_error = None
        result_row.last_failure_at = None
        result_row.project_id = None
        result_row.event_subscriptions = None

        with patch.object(daemon_registry, "get", new=AsyncMock(side_effect=[source, None])), \
             patch.object(daemon_registry, "upsert", new=AsyncMock(return_value=result_row)) as mock_upsert, \
             patch("api.daemons.ConversationJournal.write_daemon_meta"):
            await clone_daemon(
                "source-name",
                DaemonCloneRequest(new_name="cloned-name"),
                auth={"user_id": "u1"},
            )
        return mock_upsert.call_args.args[1]  # (user_id, data_dict)

    async def test_cloning_system_daemon_demotes_to_standard(self):
        data = await self._run_clone("system")
        self.assertEqual(data["kind"], "standard")

    async def test_cloning_supervisor_daemon_preserves_kind(self):
        data = await self._run_clone("supervisor")
        self.assertEqual(data["kind"], "supervisor")

    async def test_cloning_standard_daemon_preserves_kind(self):
        data = await self._run_clone("standard")
        self.assertEqual(data["kind"], "standard")


# ===========================================================================
# api/daemons.py -- upsert/update endpoints never let a client silently
# downgrade an existing "system" daemon's kind
# ===========================================================================

class TestUpdateEndpointsPreserveSystemKind(unittest.IsolatedAsyncioTestCase):
    """
    DaemonConfigRequest.kind has a Pydantic default ("standard") -- so
    body.model_dump() ALWAYS includes a "kind" key, even when the caller
    never mentioned kind. Since the request schema can't even represent
    "system", saving any change to an existing system-kind daemon through
    these endpoints would otherwise silently reset it to "standard" on
    every single save. Found by tracing the request lifecycle, not live --
    exactly the kind of thing worth a regression test.
    """

    async def test_upsert_daemon_preserves_system_kind(self):
        from api.daemons import upsert_daemon, DaemonConfigRequest
        from core.daemons.registry import daemon_registry

        existing_system_daemon = MagicMock()
        existing_system_daemon.kind = "system"
        existing_system_daemon.project_id = None

        result_row = MagicMock()
        result_row.id = "d1"

        with patch.object(daemon_registry, "get", new=AsyncMock(return_value=existing_system_daemon)), \
             patch.object(daemon_registry, "upsert", new=AsyncMock(return_value=result_row)) as mock_upsert, \
             patch("api.daemons.ConversationJournal.write_daemon_meta"), \
             patch("api.daemons._to_response", return_value=MagicMock()):
            body = DaemonConfigRequest(name="project-sync-abc", system_prompt="p")  # kind defaults to "standard"
            await upsert_daemon(body, auth={"user_id": "u1"})

        _, call_data = mock_upsert.call_args.args
        self.assertNotIn("kind", call_data, "kind must be stripped, not sent as 'standard', for an existing system daemon")

    async def test_update_daemon_preserves_system_kind(self):
        from api.daemons import update_daemon, DaemonConfigRequest
        from core.daemons.registry import daemon_registry

        existing_system_daemon = MagicMock()
        existing_system_daemon.kind = "system"
        existing_system_daemon.project_id = None

        result_row = MagicMock()
        result_row.id = "d1"

        with patch.object(daemon_registry, "get", new=AsyncMock(return_value=existing_system_daemon)), \
             patch.object(daemon_registry, "upsert", new=AsyncMock(return_value=result_row)) as mock_upsert, \
             patch("api.daemons.ConversationJournal.write_daemon_meta"), \
             patch("api.daemons._to_response", return_value=MagicMock()):
            body = DaemonConfigRequest(name="project-sync-abc", system_prompt="new prompt")
            await update_daemon("project-sync-abc", body, auth={"user_id": "u1"})

        _, call_data = mock_upsert.call_args.args
        self.assertNotIn("kind", call_data)

    async def test_upsert_daemon_for_new_daemon_still_sets_kind_normally(self):
        """Sanity check the fix doesn't over-apply: a brand-new daemon (no
        existing row) must still get its requested kind through untouched."""
        from api.daemons import upsert_daemon, DaemonConfigRequest
        from core.daemons.registry import daemon_registry

        result_row = MagicMock()
        result_row.id = "d2"

        with patch.object(daemon_registry, "get", new=AsyncMock(return_value=None)), \
             patch.object(daemon_registry, "upsert", new=AsyncMock(return_value=result_row)) as mock_upsert, \
             patch("api.daemons.ConversationJournal.write_daemon_meta"), \
             patch("api.daemons._to_response", return_value=MagicMock()):
            body = DaemonConfigRequest(name="brand-new", system_prompt="p", kind="supervisor")
            await upsert_daemon(body, auth={"user_id": "u1"})

        _, call_data = mock_upsert.call_args.args
        self.assertEqual(call_data.get("kind"), "supervisor")


if __name__ == "__main__":
    unittest.main()
