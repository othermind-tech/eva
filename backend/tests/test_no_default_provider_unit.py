# tests/test_no_default_provider_unit.py
"""
Unit tests for V6.4 Phase D: removal of hardcoded provider/model fallback
defaults from the 6 remaining call sites (title_generator, prompts.enhance,
daemons/runner, coordinate_tool, parallel_delegate_tool, delegate_tool).

Each test confirms: when session (and any config override) has no
provider/model configured, the code returns/raises a clean, explicit
"not configured" result instead of guessing a hardcoded provider/model and
attempting a call with it. Mocked, no live server, no real API calls.

Run with:
    cd <project_root>
    python tests/test_no_default_provider_unit.py
    (or: pytest tests/test_no_default_provider_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from core.broker import local_broker as _local_broker  # the singleton; patch.object avoids the module-vs-instance ambiguity of the string path (resolves differently on py3.10 vs 3.11)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTitleGeneratorNoDefault(unittest.IsolatedAsyncioTestCase):
    async def test_no_provider_skips_without_calling_handler(self):
        from core.execution.title_generator import generate_and_save

        mock_convo = MagicMock(title=None)
        mock_db = AsyncMock()

        mock_msg = MagicMock(role="user", content="hello", type=None)

        with patch("core.execution.title_generator.AsyncSessionLocal") as mock_session_local, \
             patch("core.execution.title_generator.DBRepo") as mock_repo, \
             patch("utils.handler_utils.create_provider_handler") as mock_cph:
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_repo.get_conversation = AsyncMock(return_value=mock_convo)
            mock_repo.list_messages_for_conversation = AsyncMock(return_value=[mock_msg])

            await generate_and_save(
                conversation_id="c1",
                user_id="u1",
                session={},  # no provider/model configured
            )

        mock_cph.assert_not_called()


class TestPromptsEnhanceNoDefault(unittest.IsolatedAsyncioTestCase):
    async def test_no_provider_raises_424_without_calling_handler(self):
        from api.prompts import enhance_prompt, PromptEnhanceRequest
        from fastapi import HTTPException

        body = PromptEnhanceRequest(raw_prompt="You are a helpful assistant.")

        with patch("api.prompts.get_user_session", return_value={}), \
             patch("utils.handler_utils.create_provider_handler") as mock_cph:
            with self.assertRaises(HTTPException) as ctx:
                await enhance_prompt(body, auth={"user_id": "u1"})

        self.assertEqual(ctx.exception.status_code, 424)
        mock_cph.assert_not_called()


class TestDaemonRunnerNoDefault(unittest.IsolatedAsyncioTestCase):
    async def test_no_provider_finalises_as_failed_without_building_agent(self):
        from core.daemons.runner import run_daemon

        mock_daemon = MagicMock()
        mock_daemon.id = "daemon-1"
        mock_daemon.user_id = "u1"
        mock_daemon.name = "test-daemon"
        mock_daemon.description = "desc"
        mock_daemon.model = None
        mock_daemon.provider = None
        mock_daemon.trigger_message = None
        mock_daemon.system_prompt = None
        mock_daemon.project_id = None
        mock_daemon.tools = []
        mock_daemon.max_steps = 5

        mock_metrics_db = AsyncMock()

        with patch("core.daemons.runner.ConversationJournal") as mock_journal, \
             patch("core.execution.redis_registry.redis_execution_registry") as mock_redis_reg, \
             patch("core.db.session.AsyncSessionLocal") as mock_session_local, \
             patch("core.db.repository.ExecutionMetricsRepo") as mock_metrics_repo, \
             patch("core.blackboard.ExecutionBlackboard"), \
             patch.object(_local_broker, "connect", new=AsyncMock()), \
             patch("config.settings.get_user_session", return_value={}), \
             patch("core.agent_engine.AgentEngine") as mock_agent_engine, \
             patch("core.events_publisher.publish_user_event", new=AsyncMock()):

            mock_redis_reg.create_execution = AsyncMock()
            mock_redis_reg.mark_execution_running = AsyncMock()
            mock_redis_reg.mark_execution_finished = AsyncMock()
            mock_redis_reg.mark_execution_failed = AsyncMock()
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_metrics_db)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_metrics_repo.upsert = AsyncMock()
            mock_metrics_repo.purge_old_daemon_runs = AsyncMock()

            result = await run_daemon(daemon=mock_daemon, execution_id="exec-1")

        mock_agent_engine.assert_not_called()
        self.assertFalse(result["success"])
        self.assertIn("no provider/model configured", result["error"])
        self.assertEqual(result["failure_class"], "permanent")


class TestCoordinateToolNoDefault(unittest.IsolatedAsyncioTestCase):
    async def test_no_provider_returns_error_result(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool = DefaultToolRegistry.all()["coordinate"]()
        execution = MagicMock()
        execution.user_id = "u1"
        tool.execution = execution

        with patch("config.settings.get_user_session", return_value={}):
            result = await tool.run({"goal": "do something"})

        self.assertIn("error", result.result)
        self.assertIn("provider/model", result.result["error"])


class TestParallelDelegateToolNoDefault(unittest.IsolatedAsyncioTestCase):
    async def test_no_provider_returns_error_result(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool = DefaultToolRegistry.all()["parallel_delegate"]()
        execution = MagicMock()
        execution.user_id = "u1"
        tool.execution = execution

        with patch("config.settings.get_user_session", return_value={}):
            result = await tool.run({
                "tasks": [{"agent_name": "a", "task": "do x"}],
            })

        self.assertIn("error", result.result)
        self.assertIn("provider/model", result.result["error"])


class TestDelegateToAgentToolNoDefault(unittest.IsolatedAsyncioTestCase):
    async def test_no_provider_and_no_cfg_override_returns_error_result(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool = DefaultToolRegistry.all()["delegate_to_agent"]()
        execution = MagicMock()
        execution.user_id = "u1"
        execution.execution_id = "e1"
        execution.conversation_id = "c1"
        execution.parent_project_id = None
        tool.execution = execution

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)  # no registered cfg -> dynamic

        with patch("config.settings.get_user_session", return_value={}), \
             patch("core.agents.registry.subagent_registry", mock_registry):
            result = await tool.run({"agent_name": "helper", "task": "do x"})

        self.assertIn("error", result.result)
        self.assertIn("provider/model", result.result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
