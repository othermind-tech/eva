# tests/test_appclose_cancellation_unit.py
#
# Unit tests for the app-close task-cancellation backend work (Option A,
# 2026-07-21).
#
# Two previously-undiscovered gaps, found while reviewing the app-close
# plan against the actual code (not assumed from the plan's text):
#
#   1. GET /executions/running didn't exist -- neither ExecutionRegistry nor
#      the broker could answer "what's running right now for user X",
#      across every conversation/daemon, not just the one currently open in
#      the UI.
#   2. Daemon cancellation was cosmetic. POST /daemons/{name}/cancel only
#      flipped a broker status string; nothing ever read it back into the
#      actual cancel_token the daemon's AgentEngine checks, because
#      run_daemon() never registered its ConversationExecution with
#      ExecutionRegistry and had no equivalent of worker.py's
#      _watch_for_external_cancellation() background watcher. A running
#      daemon was therefore uncancellable in practice -- "Stop and Quit"
#      would have reported success while the task kept running to natural
#      completion (or process death).
#
# Fix: daemons now register through ExecutionRegistry.create_execution_record()
# (same as conversational executions) and run the same background cancel
# watcher worker.py uses -- so POST /cancel?conversation_id=<daemon.id>
# works identically for daemons and chat, with no frontend special-casing.
#
# Run with:
#     cd <project_root>
#     pytest tests/test_appclose_cancellation_unit.py -v

from __future__ import annotations

import pytest

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from core.broker import local_broker as _local_broker  # the singleton; patch.object avoids the module-vs-instance ambiguity of the string path (resolves differently on py3.10 vs 3.11)


# ===========================================================================
# ExecutionRegistry.create_execution_record() -- pre-generated execution_id
# ===========================================================================

class TestCreateExecutionRecordPreGeneratedId(unittest.IsolatedAsyncioTestCase):

    async def test_uses_supplied_execution_id_instead_of_generating_one(self):
        from core.execution.registry import ExecutionRegistry

        reg = ExecutionRegistry()
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            execution = await reg.create_execution_record(
                conversation_id="conv-1", user_id="u1", session_id="s1",
                execution_mode="daemon", execution_id="fixed-id-123",
            )

        self.assertEqual(execution.execution_id, "fixed-id-123")
        mock_broker.create_execution.assert_awaited_once()
        self.assertEqual(mock_broker.create_execution.call_args.kwargs["execution_id"], "fixed-id-123")

    async def test_generates_uuid_when_execution_id_omitted(self):
        """Regression guard: existing conversational callers pass nothing."""
        from core.execution.registry import ExecutionRegistry

        reg = ExecutionRegistry()
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            execution = await reg.create_execution_record(
                conversation_id="conv-2", user_id="u1", session_id="s1",
            )

        self.assertTrue(execution.execution_id)
        self.assertNotEqual(execution.execution_id, "fixed-id-123")

    async def test_execution_mode_is_retained_on_the_object(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ConversationExecution

        reg = ExecutionRegistry()
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            execution = await reg.create_execution_record(
                conversation_id="conv-3", user_id="u1", session_id="s1",
                execution_mode="daemon",
            )

        self.assertIsInstance(execution, ConversationExecution)
        self.assertEqual(execution.execution_mode, "daemon")

    async def test_initial_message_forwarded_to_broker(self):
        from core.execution.registry import ExecutionRegistry

        reg = ExecutionRegistry()
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            await reg.create_execution_record(
                conversation_id="conv-4", user_id="u1", session_id="s1",
                initial_message="hello trigger",
            )

        self.assertEqual(
            mock_broker.create_execution.call_args.kwargs["initial_message"],
            "hello trigger",
        )


# ===========================================================================
# ExecutionRegistry.list_running_for_user() -- app-close's actual data source
# ===========================================================================

class TestListRunningForUser(unittest.IsolatedAsyncioTestCase):

    async def _register(self, reg, *, conversation_id, user_id, status, mode="conversational"):
        from core.execution.registry import ExecutionRegistry  # noqa: F401 (typing aid)
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            execution = await reg.create_execution_record(
                conversation_id=conversation_id, user_id=user_id, session_id="s1",
                execution_mode=mode,
            )
        execution.status = status
        return execution

    async def test_returns_only_non_terminal_executions_for_the_given_user(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ExecutionStatus

        reg = ExecutionRegistry()
        running = await self._register(reg, conversation_id="c1", user_id="u1", status=ExecutionStatus.RUNNING)
        await self._register(reg, conversation_id="c1", user_id="u1", status=ExecutionStatus.COMPLETED)
        awaiting = await self._register(
            reg, conversation_id="c2", user_id="u1", status=ExecutionStatus.AWAITING_APPROVAL, mode="daemon",
        )
        await self._register(reg, conversation_id="c3", user_id="u2", status=ExecutionStatus.RUNNING)

        result = reg.list_running_for_user("u1")

        result_ids = {e.execution_id for e in result}
        self.assertEqual(result_ids, {running.execution_id, awaiting.execution_id})

    async def test_excludes_cancelled_and_aborted_and_failed(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ExecutionStatus

        reg = ExecutionRegistry()
        for status in (ExecutionStatus.CANCELLED, ExecutionStatus.ABORTED, ExecutionStatus.FAILED):
            await self._register(reg, conversation_id=f"c-{status.value}", user_id="u1", status=status)

        self.assertEqual(reg.list_running_for_user("u1"), [])

    async def test_no_running_executions_returns_empty_list(self):
        from core.execution.registry import ExecutionRegistry

        reg = ExecutionRegistry()
        self.assertEqual(reg.list_running_for_user("nobody-home"), [])


# ===========================================================================
# cancel_latest()'s PENDING/broker-only fallback branch -- found live by the
# frontend team (2026-07-21): cancelling an execution that the worker hasn't
# yet locally marked RUNNING correctly cancelled it in the broker, but never
# updated the local ConversationExecution's .status/.cancel_token -- so
# list_running_for_user() (GET /executions/running) reported it as still
# live forever, confirmed stuck across three real polls ~30s apart.
# ===========================================================================

class TestCancelLatestFallbackBranchUpdatesLocalState(unittest.IsolatedAsyncioTestCase):

    async def test_cancelling_a_pending_execution_removes_it_from_running_list(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ExecutionStatus

        reg = ExecutionRegistry()
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            execution = await reg.create_execution_record(
                conversation_id="conv-pending-1", user_id="u1", session_id="s1",
            )
        # Left at PENDING -- simulates the real race: the worker hasn't yet
        # flipped this object's local .status to RUNNING when cancel arrives.
        self.assertEqual(execution.status, ExecutionStatus.PENDING)

        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.connect = AsyncMock(return_value=AsyncMock(
                hget=AsyncMock(return_value="pending"),
            ))
            mock_broker.mark_execution_cancelled = AsyncMock()

            cancelled = await reg.cancel_latest("conv-pending-1")

        self.assertTrue(cancelled)
        self.assertEqual(execution.status, ExecutionStatus.CANCELLED)
        self.assertTrue(execution.cancel_token.cancelled)

        # The actual regression: GET /executions/running must not keep
        # reporting this execution as live after a successful cancel.
        self.assertEqual(reg.list_running_for_user("u1"), [])

    async def test_broker_check_failure_still_falls_through_to_local_update(self):
        """If the defensive broker-status pre-check itself fails (network/etc),
        the existing code already proceeds with the cancel anyway -- confirm
        the local-state fix applies on that path too, not just the happy path."""
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ExecutionStatus

        reg = ExecutionRegistry()
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            execution = await reg.create_execution_record(
                conversation_id="conv-pending-2", user_id="u1", session_id="s1",
            )

        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.connect = AsyncMock(side_effect=RuntimeError("broker unreachable"))
            mock_broker.mark_execution_cancelled = AsyncMock()

            cancelled = await reg.cancel_latest("conv-pending-2")

        self.assertTrue(cancelled)
        self.assertEqual(execution.status, ExecutionStatus.CANCELLED)
        self.assertEqual(reg.list_running_for_user("u1"), [])


# ===========================================================================
# Per-conversation history trim -- daemons can re-append to the same
# conversation_id (daemon.id) every run_interval_seconds indefinitely.
# ===========================================================================

class TestExecutionHistoryTrim(unittest.IsolatedAsyncioTestCase):

    async def test_terminal_entries_trimmed_past_cap(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ExecutionStatus

        reg = ExecutionRegistry()
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            for _ in range(60):
                execution = await reg.create_execution_record(
                    conversation_id="daemon-conv", user_id="u1", session_id="",
                    execution_mode="daemon",
                )
                execution.status = ExecutionStatus.COMPLETED

        bucket = reg._executions["daemon-conv"]
        self.assertLessEqual(len(bucket), 50)

    async def test_live_entry_is_never_trimmed_even_if_oldest(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ExecutionStatus

        reg = ExecutionRegistry()
        with patch("core.execution.registry.redis_execution_registry") as mock_broker:
            mock_broker.create_execution = AsyncMock()
            first = await reg.create_execution_record(
                conversation_id="daemon-conv-2", user_id="u1", session_id="",
                execution_mode="daemon",
            )
            first.status = ExecutionStatus.RUNNING  # deliberately left "running" -- oldest entry

            for _ in range(60):
                execution = await reg.create_execution_record(
                    conversation_id="daemon-conv-2", user_id="u1", session_id="",
                    execution_mode="daemon",
                )
                execution.status = ExecutionStatus.COMPLETED

        bucket = reg._executions["daemon-conv-2"]
        self.assertIn(first, bucket, "a live (non-terminal) entry must never be trimmed, regardless of age")


# ===========================================================================
# The actual bug fix: daemon cancellation must reach a real, running
# cancel_token via the same POST /cancel?conversation_id= mechanism used
# for chat -- not just flip a cosmetic broker status field.
# ===========================================================================

@pytest.mark.usefixtures("stored_provider_keys")
class TestDaemonCancellationReachesRealToken(unittest.IsolatedAsyncioTestCase):

    async def test_cancel_latest_flips_the_running_daemons_actual_cancel_token(self):
        """
        End-to-end through the real ExecutionRegistry + real LocalBroker
        singletons (both process-local, in-memory, safe to exercise for
        real in a test) -- only the daemon's own heavy plumbing (journal,
        DB metrics, blackboard, MCP sync, AgentEngine/provider) is mocked.

        Proves the full chain: execution_registry.cancel_latest(daemon.id)
        (exactly what POST /cancel?conversation_id=<daemon.id> calls) ->
        broker status flips to "cancelled" -> run_daemon()'s own background
        watcher (added by this fix) polls the broker and sets
        cancel_token.cancelled=True on the SAME object AgentEngine was
        constructed with -- while the fake agent is still "mid-tool-call"
        (blocked in asyncio.sleep), not just at a boundary.
        """
        import core.execution.worker as worker_mod
        from core.daemons.runner import run_daemon
        from core.execution import execution_registry

        daemon_id = "daemon-cancel-e2e-1"
        user_id = "user-cancel-e2e-1"

        mock_daemon = MagicMock()
        mock_daemon.id = daemon_id
        mock_daemon.user_id = user_id
        mock_daemon.name = "cancel-e2e-daemon"
        mock_daemon.description = "d"
        mock_daemon.model = "gpt-x"
        mock_daemon.provider = "openai"
        mock_daemon.trigger_message = None
        mock_daemon.system_prompt = None
        mock_daemon.project_id = None
        mock_daemon.tools = []
        mock_daemon.max_steps = 5

        agent_call_started = asyncio.Event()

        class _FakeAgent:
            async def call(self, messages, stream=True):
                agent_call_started.set()
                await asyncio.sleep(5)  # stands in for an in-flight tool/LLM call
                yield MagicMock(type="text", content="unreachable")

        mock_metrics_db = AsyncMock()

        with patch("core.daemons.runner.ConversationJournal"), \
             patch("core.db.session.AsyncSessionLocal") as mock_session_local, \
             patch("core.db.repository.ExecutionMetricsRepo") as mock_metrics_repo, \
             patch("core.blackboard.ExecutionBlackboard"), \
             patch.object(_local_broker, "connect", new=AsyncMock()), \
             patch("config.settings.get_user_session", return_value={}), \
             patch("core.mcp.manager.MCPManager") as mock_mcp_mgr, \
             patch("core.agent_engine.AgentEngine", return_value=_FakeAgent()), \
             patch("core.events_publisher.publish_user_event", new=AsyncMock()), \
             patch.object(worker_mod, "_CANCEL_WATCH_POLL_SECONDS", 0.05):

            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_metrics_db)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_metrics_repo.upsert = AsyncMock()
            mock_metrics_repo.purge_old_daemon_runs = AsyncMock()
            mock_mcp_mgr.return_value.register_tools_for_user = AsyncMock()

            daemon_task = asyncio.ensure_future(run_daemon(daemon=mock_daemon, execution_id="exec-cancel-e2e-1"))

            await asyncio.wait_for(agent_call_started.wait(), timeout=2)
            # A brief extra beat so the daemon's own execution object is
            # fully registered before we try to cancel it.
            await asyncio.sleep(0.05)

            live = execution_registry.get_latest(daemon_id)
            self.assertIsNotNone(live, "run_daemon() must register its execution in ExecutionRegistry")
            self.assertFalse(live.cancel_token.cancelled)

            cancelled = await execution_registry.cancel_latest(daemon_id)
            self.assertTrue(cancelled, "cancel_latest() must find and cancel the running daemon execution")

            # Give the background watcher (polling every 0.05s) a few
            # intervals to observe the broker's now-cancelled status.
            for _ in range(20):
                if live.cancel_token.cancelled:
                    break
                await asyncio.sleep(0.05)

            self.assertTrue(
                live.cancel_token.cancelled,
                "the daemon's background cancel watcher must flip the REAL cancel_token "
                "the running AgentEngine was constructed with -- this is the bug: "
                "previously nothing ever read the broker's cancelled status back "
                "into a daemon's execution object",
            )

            daemon_task.cancel()
            try:
                await daemon_task
            except asyncio.CancelledError:
                pass

    async def test_registration_failure_falls_back_to_unregistered_standalone_run(self):
        """
        Broker/registry issues must stay non-fatal to daemon execution
        (pre-existing guarantee) -- the daemon still runs even if it can't
        be registered, just isn't listable/cancellable for that one run.
        """
        from core.daemons.runner import run_daemon

        mock_daemon = MagicMock()
        mock_daemon.id = "daemon-fallback-1"
        mock_daemon.user_id = "user-fallback-1"
        mock_daemon.name = "fallback-daemon"
        mock_daemon.description = "d"
        mock_daemon.model = "gpt-x"
        mock_daemon.provider = "openai"
        mock_daemon.trigger_message = None
        mock_daemon.system_prompt = None
        mock_daemon.project_id = None
        mock_daemon.tools = []
        mock_daemon.max_steps = 5

        class _FakeAgent:
            async def call(self, messages, stream=True):
                yield MagicMock(type="text", content="ran anyway")

        mock_metrics_db = AsyncMock()

        with patch("core.daemons.runner.ConversationJournal"), \
             patch("core.execution.execution_registry.create_execution_record",
                   new=AsyncMock(side_effect=RuntimeError("broker unreachable"))), \
             patch("core.db.session.AsyncSessionLocal") as mock_session_local, \
             patch("core.db.repository.ExecutionMetricsRepo") as mock_metrics_repo, \
             patch("core.blackboard.ExecutionBlackboard"), \
             patch.object(_local_broker, "connect", new=AsyncMock()), \
             patch("config.settings.get_user_session", return_value={}), \
             patch("core.mcp.manager.MCPManager") as mock_mcp_mgr, \
             patch("core.agent_engine.AgentEngine", return_value=_FakeAgent()), \
             patch("core.events_publisher.publish_user_event", new=AsyncMock()):

            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_metrics_db)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_metrics_repo.upsert = AsyncMock()
            mock_metrics_repo.purge_old_daemon_runs = AsyncMock()
            mock_mcp_mgr.return_value.register_tools_for_user = AsyncMock()

            result = await run_daemon(daemon=mock_daemon, execution_id="exec-fallback-1")

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "ran anyway")


# ===========================================================================
# GET /executions/running
# ===========================================================================

class TestListRunningExecutionsEndpoint(unittest.IsolatedAsyncioTestCase):

    async def test_returns_summaries_for_current_user_only(self):
        from api.executions import list_running_executions
        from core.execution.models import ConversationExecution, ExecutionStatus

        e1 = ConversationExecution(
            execution_id="e1", conversation_id="c1", user_id="u1",
            session_id="s1", status=ExecutionStatus.RUNNING, buffer=None,
            execution_mode="conversational",
        )
        e2 = ConversationExecution(
            execution_id="e2", conversation_id="daemon-1", user_id="u1",
            session_id="", status=ExecutionStatus.AWAITING_APPROVAL, buffer=None,
            execution_mode="daemon",
        )

        with patch("core.execution.execution_registry.list_running_for_user", return_value=[e1, e2]):
            result = await list_running_executions(auth={"user_id": "u1"})

        self.assertEqual(len(result), 2)
        by_id = {r.execution_id: r for r in result}
        self.assertEqual(by_id["e1"].conversation_id, "c1")
        self.assertEqual(by_id["e1"].execution_mode, "conversational")
        self.assertEqual(by_id["e2"].execution_mode, "daemon")
        self.assertEqual(by_id["e2"].status, "awaiting_approval")

    async def test_empty_when_nothing_running(self):
        from api.executions import list_running_executions

        with patch("core.execution.execution_registry.list_running_for_user", return_value=[]):
            result = await list_running_executions(auth={"user_id": "u1"})

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
