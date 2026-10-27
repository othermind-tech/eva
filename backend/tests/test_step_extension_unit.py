# tests/test_step_extension_unit.py
"""
Unit tests for V6.12: the ReAct step-limit "ask user to continue" gate.

Covers:
  - core.execution.step_extension.gate_step_extension() /
    resolve_step_extension() — mirrors core.execution.pev_runner's
    _gate_plan_approval()/resolve_plan_approval() pattern; mocked broker
    client throughout (no live Redis).
  - core.execution.transitions — AWAITING_STEP_EXTENSION's allowed FSM
    transitions.

Mocked throughout — no live server, no real Redis, no real API calls.

Run with:
    cd <project_root>
    python tests/test_step_extension_unit.py
    (or: pytest tests/test_step_extension_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from core.broker import local_broker as _local_broker  # the singleton; patch.object avoids the module-vs-instance ambiguity of the string path (resolves differently on py3.10 vs 3.11)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTransitions(unittest.TestCase):
    def test_running_can_transition_to_awaiting_step_extension(self):
        from core.execution.models import ExecutionStatus
        from core.execution.transitions import ALLOWED_TRANSITIONS
        self.assertIn(
            ExecutionStatus.AWAITING_STEP_EXTENSION,
            ALLOWED_TRANSITIONS[ExecutionStatus.RUNNING],
        )

    def test_awaiting_step_extension_can_continue_to_running(self):
        from core.execution.models import ExecutionStatus
        from core.execution.transitions import ALLOWED_TRANSITIONS
        self.assertIn(
            ExecutionStatus.RUNNING,
            ALLOWED_TRANSITIONS[ExecutionStatus.AWAITING_STEP_EXTENSION],
        )

    def test_awaiting_step_extension_can_stop_to_completed_not_cancelled(self):
        """Deliberate design choice: stopping at the step limit is a clean
        completion (work already done is kept), not a rejection -- must
        NOT be able to transition to CANCELLED, unlike the plan/tool
        approval gates' reject path."""
        from core.execution.models import ExecutionStatus
        from core.execution.transitions import ALLOWED_TRANSITIONS
        allowed = ALLOWED_TRANSITIONS[ExecutionStatus.AWAITING_STEP_EXTENSION]
        self.assertIn(ExecutionStatus.COMPLETED, allowed)
        self.assertNotIn(ExecutionStatus.CANCELLED, allowed)

    def test_awaiting_step_extension_has_watchdog_abort_failsafe(self):
        from core.execution.models import ExecutionStatus
        from core.execution.transitions import ALLOWED_TRANSITIONS
        self.assertIn(
            ExecutionStatus.ABORTED,
            ALLOWED_TRANSITIONS[ExecutionStatus.AWAITING_STEP_EXTENSION],
        )


class TestGateStepExtension(unittest.IsolatedAsyncioTestCase):
    def _fake_execution(self):
        from core.execution.models import ExecutionStatus
        return SimpleNamespace(
            execution_id="exec-1", status=ExecutionStatus.RUNNING,
            conversation_id="conv-1", user_id="user-1",
        )

    def _mock_client(self, blpop_result):
        client = AsyncMock()
        client.setex = AsyncMock()
        client.hset = AsyncMock()
        client.hdel = AsyncMock()
        client.expire = AsyncMock()
        client.delete = AsyncMock()
        client.blpop = AsyncMock(return_value=blpop_result)
        return client

    async def test_continue_decision_returns_true(self):
        from core.execution.step_extension import gate_step_extension

        client = self._mock_client(blpop_result=["key", "1"])
        execution = self._fake_execution()

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
            result = await gate_step_extension(
                execution, steps_used=8, max_steps=8, extensions_used=0, extensions_max=2,
                timeout_seconds=5,
            )

        self.assertTrue(result)

    async def test_stop_decision_returns_false(self):
        from core.execution.step_extension import gate_step_extension

        client = self._mock_client(blpop_result=["key", "0"])
        execution = self._fake_execution()

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
            result = await gate_step_extension(
                execution, steps_used=8, max_steps=8, extensions_used=0, extensions_max=2,
                timeout_seconds=5,
            )

        self.assertFalse(result)

    async def test_continue_restores_running_in_the_broker(self):
        """After "continue" the broker must say RUNNING again -- /status and the UI read it,
        and the engine only resets the in-memory status."""
        from core.execution.step_extension import gate_step_extension

        client = self._mock_client(blpop_result=["key", "1"])
        execution = self._fake_execution()

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
            mock_registry.mark_execution_resumed = AsyncMock()
            result = await gate_step_extension(
                execution, steps_used=8, max_steps=8, extensions_used=0, extensions_max=2,
                timeout_seconds=5,
            )

        self.assertTrue(result)
        mock_registry.mark_execution_resumed.assert_awaited_once_with("exec-1")

    async def test_stop_and_timeout_do_not_claim_running(self):
        from core.execution.step_extension import gate_step_extension

        for blpop_result in (["key", "0"], None):
            client = self._mock_client(blpop_result=blpop_result)
            with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
                mock_registry.connect = AsyncMock(return_value=client)
                mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
                mock_registry.mark_execution_resumed = AsyncMock()
                result = await gate_step_extension(
                    self._fake_execution(), steps_used=8, max_steps=8, extensions_used=0,
                    extensions_max=2, timeout_seconds=5,
                )
            self.assertFalse(result)
            mock_registry.mark_execution_resumed.assert_not_awaited()

    async def test_a_failing_broker_restore_never_breaks_the_continue(self):
        from core.execution.step_extension import gate_step_extension

        client = self._mock_client(blpop_result=["key", "1"])
        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
            mock_registry.mark_execution_resumed = AsyncMock(side_effect=RuntimeError("broker down"))
            result = await gate_step_extension(
                self._fake_execution(), steps_used=8, max_steps=8, extensions_used=0,
                extensions_max=2, timeout_seconds=5,
            )
        self.assertTrue(result)

    async def test_timeout_returns_false_not_true(self):
        """blpop timing out (no user response) must resolve to False (stop)
        -- the auto-stop safety net that avoids Cline's known task-lockout
        bug (#3480): the execution always terminates, never hangs forever."""
        from core.execution.step_extension import gate_step_extension

        client = self._mock_client(blpop_result=None)  # blpop returns None on timeout
        execution = self._fake_execution()

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
            result = await gate_step_extension(
                execution, steps_used=8, max_steps=8, extensions_used=0, extensions_max=2,
                timeout_seconds=5,
            )

        self.assertFalse(result)

    async def test_exception_during_wait_returns_false(self):
        from core.execution.step_extension import gate_step_extension

        client = self._mock_client(blpop_result=None)
        client.blpop = AsyncMock(side_effect=RuntimeError("connection lost"))
        execution = self._fake_execution()

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
            result = await gate_step_extension(
                execution, steps_used=8, max_steps=8, extensions_used=0, extensions_max=2,
                timeout_seconds=5,
            )

        self.assertFalse(result)

    async def test_pushes_step_limit_reached_sse_event(self):
        from core.execution.step_extension import gate_step_extension

        client = self._mock_client(blpop_result=["key", "1"])
        execution = self._fake_execution()

        pushed = {}
        async def _fake_append(payload):
            pushed.update(payload)

        fake_buffer = SimpleNamespace(append=_fake_append)

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry, \
             patch.object(_local_broker, "get_shadow_buffer", return_value=fake_buffer):
            mock_registry.connect = AsyncMock(return_value=client)
            mock_registry.mark_execution_awaiting_step_extension = AsyncMock()
            await gate_step_extension(
                execution, steps_used=8, max_steps=8, extensions_used=1, extensions_max=2,
                timeout_seconds=5,
            )

        self.assertEqual(pushed.get("type"), "step_limit_reached")
        self.assertEqual(pushed.get("steps_used"), 8)
        self.assertEqual(pushed.get("extensions_used"), 1)
        self.assertEqual(pushed.get("extensions_max"), 2)


class TestResolveStepExtension(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_token_returns_false(self):
        from core.execution.step_extension import resolve_step_extension

        client = AsyncMock()
        client.exists = AsyncMock(return_value=False)

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            result = await resolve_step_extension("unknown-token", continue_=True)

        self.assertFalse(result)

    async def test_known_token_pushes_correct_decision_value(self):
        from core.execution.step_extension import resolve_step_extension

        client = AsyncMock()
        client.exists = AsyncMock(return_value=True)
        client.rpush = AsyncMock()
        client.expire = AsyncMock()
        client.delete = AsyncMock()

        with patch("core.execution.step_extension.redis_execution_registry") as mock_registry:
            mock_registry.connect = AsyncMock(return_value=client)
            result = await resolve_step_extension("known-token", continue_=True)

        self.assertTrue(result)
        client.rpush.assert_called_once()
        args = client.rpush.call_args[0]
        self.assertEqual(args[1], "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
