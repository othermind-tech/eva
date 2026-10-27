# tests/test_gate_markers_unit.py
"""
Unit tests for B2-1.7/4.3: file-based liveness markers for EVA's blocking
approval/plan-approval/step-extension gates, and boot-time reconciliation.

Background: gate_approval()/gate_step_extension()/_gate_plan_approval()
all block on an in-memory broker wait inside the same in-flight asyncio
task. None of that survives a real process restart -- the coroutine
itself is destroyed, not just the data. core/execution/gate_markers.py
writes a small file per currently-paused execution (NOT a DB row --
SQLite here only ever records completed executions, see the module's own
docstring) so a boot-time scan can detect "this execution was paused when
the process died" and fail it cleanly instead of leaving it permanently
stuck in an unresolvable "awaiting_*" state.

Covers:
  - gate_markers.py: write/delete/scan, atomic write, corrupt-marker
    handling, directory auto-creation.
  - Integration: gate_approval()/gate_step_extension() actually write a
    marker on pause and delete it on resolution (both decision and
    timeout paths).
  - Boot reconciliation: LocalBroker._recover_from_gate_markers() writes
    exactly one terminal ExecutionMetrics row per orphaned marker and
    deletes the marker; malformed markers are skipped, not fatal.

Run with:
    cd <project_root>
    pytest tests/test_gate_markers_unit.py -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


# ===========================================================================
# gate_markers.py — pure module tests
# ===========================================================================

class TestWriteReadDeleteMarker(unittest.TestCase):

    def test_write_creates_expected_file_and_content(self):
        from core.execution.gate_markers import write_gate_marker, _marker_path

        write_gate_marker(
            execution_id="exec-1", conversation_id="conv-1", user_id="user-1",
            gate_kind="approval", token="tok-1", tool_name="write_file", authority="mutating",
        )
        path = _marker_path("exec-1")
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["execution_id"], "exec-1")
        self.assertEqual(data["conversation_id"], "conv-1")
        self.assertEqual(data["user_id"], "user-1")
        self.assertEqual(data["gate_kind"], "approval")
        self.assertEqual(data["token"], "tok-1")
        self.assertEqual(data["tool_name"], "write_file")
        self.assertEqual(data["authority"], "mutating")
        self.assertIn("written_at", data)

    def test_write_is_atomic_no_leftover_tmp_file(self):
        from core.execution.gate_markers import write_gate_marker, _marker_path

        write_gate_marker(
            execution_id="exec-atomic", conversation_id="c", user_id="u",
            gate_kind="approval", token="t",
        )
        tmp_path = _marker_path("exec-atomic").with_suffix(".tmp")
        self.assertFalse(tmp_path.exists())

    def test_write_rejects_unknown_gate_kind(self):
        from core.execution.gate_markers import write_gate_marker

        with self.assertRaises(ValueError):
            write_gate_marker(
                execution_id="exec-1", conversation_id="c", user_id="u",
                gate_kind="not_a_real_kind", token="t",
            )

    def test_write_creates_directory_if_missing(self):
        from core.execution.gate_markers import write_gate_marker, _runtime_dir

        d = _runtime_dir()
        self.assertTrue(d.is_dir())

    def test_delete_removes_the_file(self):
        from core.execution.gate_markers import write_gate_marker, delete_gate_marker, _marker_path

        write_gate_marker(
            execution_id="exec-del", conversation_id="c", user_id="u",
            gate_kind="approval", token="t",
        )
        self.assertTrue(_marker_path("exec-del").exists())
        delete_gate_marker("exec-del")
        self.assertFalse(_marker_path("exec-del").exists())

    def test_delete_is_a_noop_when_marker_missing(self):
        from core.execution.gate_markers import delete_gate_marker
        # Must not raise.
        delete_gate_marker("never-existed")


class TestScanOrphanedMarkers(unittest.TestCase):

    def test_scan_returns_all_present_markers(self):
        from core.execution.gate_markers import write_gate_marker, scan_orphaned_markers

        write_gate_marker(execution_id="e1", conversation_id="c", user_id="u",
                           gate_kind="approval", token="t1")
        write_gate_marker(execution_id="e2", conversation_id="c", user_id="u",
                           gate_kind="step_extension", token="t2")

        found = scan_orphaned_markers()
        ids = {m["execution_id"] for m in found}
        self.assertEqual(ids, {"e1", "e2"})

    def test_scan_returns_empty_list_when_no_markers(self):
        from core.execution.gate_markers import scan_orphaned_markers
        self.assertEqual(scan_orphaned_markers(), [])

    def test_scan_removes_and_skips_corrupt_marker(self):
        from core.execution.gate_markers import _runtime_dir, scan_orphaned_markers

        d = _runtime_dir()
        bad_path = d / "corrupt-exec.json"
        bad_path.write_text("{not valid json", encoding="utf-8")

        found = scan_orphaned_markers()
        self.assertEqual(found, [])
        self.assertFalse(bad_path.exists())


# ===========================================================================
# Integration — real gate functions actually write/delete markers
# ===========================================================================

def _mock_broker_client(blpop_result):
    client = AsyncMock()
    client.setex = AsyncMock()
    client.hset = AsyncMock()
    client.hdel = AsyncMock()
    client.expire = AsyncMock()
    client.delete = AsyncMock()
    client.blpop = AsyncMock(return_value=blpop_result)
    return client


class TestGateApprovalWritesAndDeletesMarker(unittest.IsolatedAsyncioTestCase):

    async def test_marker_exists_during_wait_and_gone_after_timeout(self):
        from core.tools.approval import gate_approval
        from core.execution.gate_markers import _marker_path

        execution = SimpleNamespace(
            execution_id="exec-appr-1", conversation_id="conv-1", user_id="user-1",
            status=None, cancel_token=None,
        )
        client = _mock_broker_client(blpop_result=None)  # timeout

        marker_seen_during_wait = {}

        async def _fake_blpop(*args, **kwargs):
            # Snapshot marker state mid-wait, before the gate resolves.
            marker_seen_during_wait["exists"] = _marker_path("exec-appr-1").exists()
            return None

        client.blpop = _fake_blpop

        with patch("core.tools.approval.redis_execution_registry.connect",
                   new=AsyncMock(return_value=client)), \
             patch("core.tools.approval.redis_execution_registry.mark_execution_awaiting_approval",
                   new=AsyncMock()), \
             patch("core.tools.approval.redis_execution_registry.mark_execution_cancelled",
                   new=AsyncMock()), \
             patch("core.tools.approval._push_approval_event_to_stream", new=AsyncMock()), \
             patch("core.tools.approval.ExecutionStateMachine.assert_transition_allowed"), \
             patch("core.tools.approval.event_bus.publish"):
            result = await gate_approval(
                execution=execution, call_id="call-1", tool_name="write_file",
                authority="mutating", arguments={}, timeout_seconds=1,
            )

        self.assertFalse(result)
        self.assertTrue(marker_seen_during_wait["exists"], "marker must exist while gate is waiting")
        self.assertFalse(_marker_path("exec-appr-1").exists(), "marker must be deleted after resolution")


class TestGateStepExtensionWritesAndDeletesMarker(unittest.IsolatedAsyncioTestCase):

    async def test_marker_exists_during_wait_and_gone_after_decision(self):
        from core.execution.step_extension import gate_step_extension
        from core.execution.gate_markers import _marker_path
        from core.execution.models import ExecutionStatus

        execution = SimpleNamespace(
            execution_id="exec-step-1", conversation_id="conv-1", user_id="user-1",
            status=ExecutionStatus.RUNNING,
        )

        marker_seen_during_wait = {}

        async def _fake_blpop(*args, **kwargs):
            marker_seen_during_wait["exists"] = _marker_path("exec-step-1").exists()
            return ["key", "1"]

        client = _mock_broker_client(blpop_result=None)
        client.blpop = _fake_blpop

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
            result = await gate_step_extension(
                execution, steps_used=8, max_steps=8, extensions_used=0, extensions_max=2,
                timeout_seconds=5,
            )

        self.assertTrue(result)
        self.assertTrue(marker_seen_during_wait["exists"])
        self.assertFalse(_marker_path("exec-step-1").exists())


# ===========================================================================
# Boot-time reconciliation — LocalBroker._recover_from_gate_markers()
# ===========================================================================

def _make_engine_and_session():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from core.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, Session


class TestBootReconciliationFromGateMarkers(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.engine, self.Session = _make_engine_and_session()
        async with self.engine.begin() as conn:
            from core.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        import core.db.session as _dbs
        self._orig_session_local = _dbs.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session

    async def asyncTearDown(self):
        import core.db.session as _dbs
        _dbs.AsyncSessionLocal = self._orig_session_local
        await self.engine.dispose()

    async def _seed_user_and_conversation(self, user_id="user-1", conv_id="conv-1"):
        from core.db.models import User, Conversation
        async with self.Session() as db:
            db.add(User(id=user_id, email=f"{user_id}@test.local", username=user_id, password_hash="x"))
            db.add(Conversation(id=conv_id, user_id=user_id))
            await db.commit()

    async def test_recovers_one_orphaned_marker_into_terminal_metrics_row(self):
        from core.broker.local_broker import LocalBroker
        from core.execution.gate_markers import write_gate_marker
        from core.db.repository import ExecutionMetricsRepo

        await self._seed_user_and_conversation()
        write_gate_marker(
            execution_id="exec-orphan-1", conversation_id="conv-1", user_id="user-1",
            gate_kind="approval", token="tok-1", tool_name="write_file", authority="mutating",
        )

        broker = LocalBroker()
        recovered = await broker._recover_from_gate_markers()

        self.assertEqual(recovered, ["exec-orphan-1"])
        async with self.Session() as db:
            row = await ExecutionMetricsRepo.get(db, "exec-orphan-1")
        self.assertIsNotNone(row)
        self.assertEqual(row.status, "aborted")
        self.assertEqual(row.termination_reason, "process_restart")
        self.assertIn("write_file", row.error_message)

    async def test_marker_deleted_after_recovery(self):
        from core.broker.local_broker import LocalBroker
        from core.execution.gate_markers import write_gate_marker, _marker_path

        await self._seed_user_and_conversation()
        write_gate_marker(
            execution_id="exec-orphan-2", conversation_id="conv-1", user_id="user-1",
            gate_kind="step_extension", token="tok-2",
        )

        broker = LocalBroker()
        await broker._recover_from_gate_markers()

        self.assertFalse(_marker_path("exec-orphan-2").exists())

    async def test_no_markers_returns_empty_list(self):
        from core.broker.local_broker import LocalBroker

        broker = LocalBroker()
        recovered = await broker._recover_from_gate_markers()
        self.assertEqual(recovered, [])

    async def test_malformed_marker_is_skipped_not_fatal(self):
        """A marker missing a required field (conversation_id/user_id) must
        be skipped and removed, not crash the whole reconciliation pass --
        one bad marker must never block recovery of the others."""
        from core.broker.local_broker import LocalBroker
        from core.execution.gate_markers import write_gate_marker, _runtime_dir, _marker_path

        await self._seed_user_and_conversation()
        # Well-formed marker.
        write_gate_marker(
            execution_id="exec-good", conversation_id="conv-1", user_id="user-1",
            gate_kind="approval", token="tok-good",
        )
        # Malformed: missing conversation_id/user_id (write directly, bypassing
        # write_gate_marker's required kwargs, to simulate a corrupted marker).
        bad_path = _runtime_dir() / "exec-bad.json"
        bad_path.write_text(json.dumps({"execution_id": "exec-bad", "gate_kind": "approval"}), encoding="utf-8")

        broker = LocalBroker()
        recovered = await broker._recover_from_gate_markers()

        self.assertEqual(recovered, ["exec-good"])
        self.assertFalse(_marker_path("exec-good").exists())
        self.assertFalse(bad_path.exists(), "malformed marker must still be removed")

    async def test_multiple_orphaned_markers_all_recovered(self):
        from core.broker.local_broker import LocalBroker
        from core.execution.gate_markers import write_gate_marker
        from core.db.repository import ExecutionMetricsRepo

        await self._seed_user_and_conversation()
        for i in range(3):
            write_gate_marker(
                execution_id=f"exec-multi-{i}", conversation_id="conv-1", user_id="user-1",
                gate_kind="plan_approval", token=f"tok-{i}",
            )

        broker = LocalBroker()
        recovered = await broker._recover_from_gate_markers()

        self.assertEqual(set(recovered), {f"exec-multi-{i}" for i in range(3)})
        async with self.Session() as db:
            for i in range(3):
                row = await ExecutionMetricsRepo.get(db, f"exec-multi-{i}")
                self.assertEqual(row.status, "aborted")


if __name__ == "__main__":
    unittest.main()
