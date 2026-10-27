# tests/test_frontend_gap_fixes_unit.py
"""
Unit tests for three backend gaps identified during frontend integration
(2026-07-13 frontend-mismatch review):

  #4 — DaemonExecutionSummary missing model/provider/token fields
  #5 — event_subscriptions never round-trips (request schema, registry
       upsert write path, response schema)
  #6 — tool_args/tool_result/is_error lost on conversation reload
       (worker.py write path, MessageResponse read path)

Run with:
    cd <project_root>
    pytest tests/test_frontend_gap_fixes_unit.py -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch


# ===========================================================================
# #4 — Daemon execution history: model/provider/token fields
# ===========================================================================

class TestDaemonExecutionSummaryMetricsFields(unittest.TestCase):

    def test_summary_schema_has_model_and_token_fields(self):
        from api.daemons import DaemonExecutionSummary
        fields = DaemonExecutionSummary.model_fields
        for f in ("model_used", "provider_used", "prompt_tokens",
                  "completion_tokens", "reasoning_tokens"):
            self.assertIn(f, fields)

    def test_summary_accepts_metrics_values(self):
        from api.daemons import DaemonExecutionSummary
        s = DaemonExecutionSummary(
            execution_id="e1", status="completed",
            model_used="gpt-oss:20b", provider_used="ollama_cloud",
            prompt_tokens=100, completion_tokens=50, reasoning_tokens=0,
        )
        self.assertEqual(s.model_used, "gpt-oss:20b")
        self.assertEqual(s.provider_used, "ollama_cloud")
        self.assertEqual(s.prompt_tokens, 100)

    def test_summary_metrics_fields_optional(self):
        """Rows predating this fix (or with NULL columns) must not 422."""
        from api.daemons import DaemonExecutionSummary
        s = DaemonExecutionSummary(execution_id="e1", status="completed")
        self.assertIsNone(s.model_used)
        self.assertIsNone(s.prompt_tokens)


class TestListDaemonExecutionsCopiesMetrics(unittest.IsolatedAsyncioTestCase):

    async def test_construction_loop_copies_model_and_tokens(self):
        """
        list_daemon_executions() must copy model_used/provider_used/
        prompt_tokens/completion_tokens/reasoning_tokens from the
        ExecutionMetrics row onto the response -- previously these were
        silently dropped even though the row already carried them.
        """
        class _FakeRow:
            execution_id = "exec-1"
            status = "completed"
            triggered_by = "daemon"
            project_id = None
            termination_reason = "success"
            error_message = None
            duration_ms = 1234
            created_at = datetime.now(timezone.utc)
            completed_at = datetime.now(timezone.utc)
            model_used = "gpt-oss:20b"
            provider_used = "ollama_cloud"
            prompt_tokens = 500
            completion_tokens = 80
            reasoning_tokens = 10

        with patch("api.daemons.daemon_registry") as mock_reg, \
             patch("api.daemons.ExecutionMetricsRepo") as mock_repo, \
             patch("api.daemons.AsyncSessionLocal") as mock_session_factory:
            mock_reg.get = AsyncMock(return_value=object())  # daemon exists
            mock_repo.list_for_daemon = AsyncMock(return_value=[_FakeRow()])

            mock_db = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            from api.daemons import list_daemon_executions
            result = await list_daemon_executions(
                name="d1", limit=20, auth={"user_id": "user-1"},
            )

        self.assertEqual(len(result), 1)
        summary = result[0]
        self.assertEqual(summary.model_used, "gpt-oss:20b")
        self.assertEqual(summary.provider_used, "ollama_cloud")
        self.assertEqual(summary.prompt_tokens, 500)
        self.assertEqual(summary.completion_tokens, 80)
        self.assertEqual(summary.reasoning_tokens, 10)


# ===========================================================================
# #5 — event_subscriptions round trip (request -> registry -> response)
# ===========================================================================

class TestEventSubscriptionsRequestResponseSchema(unittest.TestCase):

    def test_request_accepts_event_subscriptions(self):
        from api.daemons import DaemonConfigRequest
        req = DaemonConfigRequest(name="d1", event_subscriptions=["execution.completed"])
        self.assertEqual(req.event_subscriptions, ["execution.completed"])

    def test_request_defaults_to_none(self):
        from api.daemons import DaemonConfigRequest
        req = DaemonConfigRequest(name="d1")
        self.assertIsNone(req.event_subscriptions)

    def test_response_has_event_subscriptions_field(self):
        from api.daemons import DaemonConfigResponse
        self.assertIn("event_subscriptions", DaemonConfigResponse.model_fields)

    def test_to_response_copies_event_subscriptions(self):
        """_to_response() must read event_subscriptions off the DB row."""
        from api.daemons import _to_response

        class _FakeRow:
            id = "id1"
            name = "d1"
            description = None
            system_prompt = None
            trigger_message = None
            model = None
            provider = None
            tools = None
            max_steps = 5
            run_interval_seconds = None
            enabled = True
            last_run_at = None
            next_run_at = None
            created_at = datetime.now(timezone.utc)
            updated_at = None
            event_subscriptions = ["execution.completed", "execution.failed"]

        resp = _to_response(_FakeRow())
        self.assertEqual(resp.event_subscriptions, ["execution.completed", "execution.failed"])


class TestEventSubscriptionsWritePath(unittest.TestCase):
    """
    The original frontend-dev report assumed event_subscriptions was
    'already accepted/persisted on create/update' and only the response
    was missing it. That was incorrect: prior to this fix, the field
    was unreachable through the API on *both* create and update -- these
    tests pin the write-path fix (registry.upsert()) directly via source
    inspection, matching this file's existing convention for pinning
    structural fixes without a live DB.
    """

    def test_registry_upsert_updates_existing_row(self):
        src = Path("core/daemons/registry.py").read_text(encoding="utf-8")
        idx = src.find("if existing:")
        self.assertGreater(idx, 0)
        next_block = src[idx:idx + 1200]
        self.assertIn("existing.event_subscriptions", next_block,
                      "registry.upsert() must write event_subscriptions on update")

    def test_registry_upsert_sets_on_create(self):
        src = Path("core/daemons/registry.py").read_text(encoding="utf-8")
        idx = src.find("row = DaemonConfig(")
        self.assertGreater(idx, 0)
        ctor_block = src[idx:idx + 1300]
        self.assertIn('event_subscriptions=data.get("event_subscriptions")', ctor_block,
                      "registry.upsert() must set event_subscriptions on create")


class TestEventSubscriptionsRegistryRoundTrip(unittest.IsolatedAsyncioTestCase):

    async def test_upsert_persists_and_preserves_event_subscriptions(self):
        """
        Live-DB round trip (real AsyncSessionLocal, matching this codebase's
        convention of exercising DaemonRegistry against the real dev DB in
        other daemon tests): create with subscriptions, update without the
        key present (must be preserved, not wiped), then explicit None
        (must clear) -- covers the exact "silently wiping data" concern
        raised in the originating report.
        """
        from core.daemons.registry import daemon_registry

        user_id = "pytest-event-sub-user"
        name = "pytest-evsub-daemon"
        try:
            row1 = await daemon_registry.upsert(user_id, {
                "name": name,
                "event_subscriptions": ["execution.completed"],
            })
            self.assertEqual(row1.event_subscriptions, ["execution.completed"])

            row2 = await daemon_registry.upsert(user_id, {
                "name": name,
                "description": "no event_subscriptions key in this payload",
            })
            self.assertEqual(row2.event_subscriptions, ["execution.completed"])

            row3 = await daemon_registry.upsert(user_id, {
                "name": name,
                "event_subscriptions": None,
            })
            self.assertIsNone(row3.event_subscriptions)
        finally:
            await daemon_registry.delete(user_id, name)


# ===========================================================================
# #6 — tool_args / tool_result / is_error
# ===========================================================================

class TestMessageResponseToolFields(unittest.TestCase):

    def test_schema_has_new_fields(self):
        from api.schemas.responses import MessageResponse
        fields = MessageResponse.model_fields
        for f in ("tool_args", "tool_result", "is_error"):
            self.assertIn(f, fields)

    def test_is_error_true_on_explicit_failure(self):
        from api.schemas.responses import MessageResponse
        m = MessageResponse(
            id="1", conversation_id="c", role="tool", content="{}", type="tool_result",
            tool_name="shell", tool_result={"ok": False, "error": "boom"},
            created_at=datetime.now(timezone.utc),
        )
        self.assertTrue(m.is_error)

    def test_is_error_false_on_explicit_success(self):
        from api.schemas.responses import MessageResponse
        m = MessageResponse(
            id="1", conversation_id="c", role="tool", content="{}", type="tool_result",
            tool_name="web_search", tool_result={"ok": True, "result": {}},
            created_at=datetime.now(timezone.utc),
        )
        self.assertFalse(m.is_error)

    def test_is_error_none_for_non_tool_roles(self):
        from api.schemas.responses import MessageResponse
        m = MessageResponse(
            id="1", conversation_id="c", role="assistant", content="hi", type=None,
            tool_name=None, created_at=datetime.now(timezone.utc),
        )
        self.assertIsNone(m.is_error)

    def test_tool_args_round_trips(self):
        from api.schemas.responses import MessageResponse
        m = MessageResponse(
            id="1", conversation_id="c", role="assistant", content=None, type="tool_call",
            tool_name=None, tool_args={"query": "today"},
            created_at=datetime.now(timezone.utc),
        )
        self.assertEqual(m.tool_args, {"query": "today"})


class TestWorkerPassesToolArgsAndResult(unittest.TestCase):
    """
    Source-inspection pin (matching this file's existing convention, e.g.
    TestB082MultiActiveProjects) confirming _save_tool_step() actually
    passes tool_args=/tool_result= through to DBRepo.add_message() -- the
    root cause was that add_message() already accepted both params but
    the only caller never passed them.
    """

    def test_tool_call_message_passes_tool_args(self):
        src = Path("core/execution/worker.py").read_text(encoding="utf-8")
        idx = src.find("_save_tool_step")
        self.assertGreater(idx, 0)
        section = src[idx:idx + 3000]
        self.assertIn("tool_args=tool_calls[0].arguments", section)

    def test_tool_call_message_passes_tool_name(self):
        """
        V6.14.2: the tool_call row previously carried tool_args but not
        tool_name, even though the paired tool_result row always had it --
        a reload-path view resolving a tool_call message on its own (or a
        call that never got a matching result, e.g. cancelled mid-call) had
        nothing but "unknown" to show for the tool name. Also silently
        broke conversation export's "Calling tool: {tool_name or
        'unknown'}" line, which already expected this field.

        (tool_call_id was part of this same fix originally, but DB-10
        phase 1 (2026-09-15) removed messages.tool_call_id entirely --
        confirmed zero consumers ever read it, including this reload path.)
        """
        src = Path("core/execution/worker.py").read_text(encoding="utf-8")
        idx = src.find("_save_tool_step")
        self.assertGreater(idx, 0)
        section = src[idx:idx + 3000]
        self.assertIn("tool_name=tool_calls[0].tool_name", section)

    def test_tool_result_message_passes_tool_result(self):
        src = Path("core/execution/worker.py").read_text(encoding="utf-8")
        idx = src.find("_save_tool_step")
        self.assertGreater(idx, 0)
        section = src[idx:idx + 4500]
        self.assertIn('tool_result=result if isinstance(result, dict) else {"output": str(result)}', section)


class TestToolFieldsFullRoundTrip(unittest.IsolatedAsyncioTestCase):

    async def test_db_write_then_reload_preserves_tool_fields(self):
        """
        End-to-end: write via DBRepo.add_message() the same way worker.py's
        _save_tool_step() now does, then read back through a fresh session
        (simulating a real conversation reload) and validate through the
        actual MessageResponse the read endpoint uses.
        """
        from core.db.session import AsyncSessionLocal
        from core.db.repository import DBRepo
        from core.db.models import Message, Conversation
        from api.schemas.responses import MessageResponse
        from sqlalchemy import select, delete as sa_delete

        async with AsyncSessionLocal() as db:
            convo = await DBRepo.create_conversation(
                db, user_id="pytest-tool-fields-user", session_id="s1",
            )
            conversation_id = convo.id

            await DBRepo.add_message(
                db=db, conversation_id=conversation_id, role="assistant", content=None,
                mtype="tool_call", tool_args={"query": "today date"},
                tool_name="web_search",
            )
            await DBRepo.add_message(
                db=db, conversation_id=conversation_id, role="tool", content="{}",
                mtype="tool_result", tool_name="web_search",
                tool_result={"ok": True, "result": {"x": 1}},
            )
            await DBRepo.add_message(
                db=db, conversation_id=conversation_id, role="tool", content="{}",
                mtype="tool_result", tool_name="shell",
                tool_result={"ok": False, "error": "command not found"},
            )

        try:
            async with AsyncSessionLocal() as db2:
                result = await db2.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.sequence)
                )
                rows = result.scalars().all()
                reloaded = [MessageResponse.model_validate(r) for r in rows]

            self.assertEqual(len(reloaded), 3)
            call_msg, ok_msg, err_msg = reloaded

            self.assertEqual(call_msg.tool_args, {"query": "today date"})
            self.assertIsNone(call_msg.is_error)
            # V6.14.2: tool_call rows now carry tool_name too, matching the
            # paired tool_result row -- no more "unknown" tool name on the
            # reload path.
            self.assertEqual(call_msg.tool_name, "web_search")

            self.assertEqual(ok_msg.tool_result, {"ok": True, "result": {"x": 1}})
            self.assertFalse(ok_msg.is_error)

            self.assertEqual(err_msg.tool_result, {"ok": False, "error": "command not found"})
            self.assertTrue(err_msg.is_error)
        finally:
            async with AsyncSessionLocal() as db3:
                await db3.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
                await db3.execute(sa_delete(Conversation).where(Conversation.id == conversation_id))
                await db3.commit()


if __name__ == "__main__":
    unittest.main()
