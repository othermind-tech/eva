# tests/test_provider_config_guard_unit.py
"""
Unit tests for the 2026-07-27 hardcoded-default-elimination cleanup.

Companion to tests/test_no_default_provider_unit.py (which covers the
original V6.4 Phase D call sites). This file covers what V6.4 explicitly
deferred: config.settings.DEFAULT_SESSION itself, and every place that
seeds or displays a session from it. A live server.log trace showed
DEFAULT_SESSION's old "groq"/"openai/gpt-oss-20b" values firing real,
billed API calls for a brand-new user's first message(s), before their
first POST /set-session call landed -- these tests lock in the fix.

Mocked, no live server, no real API calls.

Run with:
    cd <project_root>
    pytest tests/test_provider_config_guard_unit.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException


class TestDefaultSessionNoGuess(unittest.TestCase):
    """Regression guard: nobody should re-add a real provider/model here."""

    def test_provider_and_model_are_empty(self):
        from config.settings import DEFAULT_SESSION

        self.assertEqual(DEFAULT_SESSION["provider"], "")
        self.assertEqual(DEFAULT_SESSION["model"], "")


class TestBuildPrefsDictNoFallback(unittest.TestCase):
    """core/auth/hydration.py: provider/model come straight from the DB row,
    no default_session fallback (those columns are NOT NULL -- a fallback
    could only ever mask a data integrity bug)."""

    def test_uses_prefs_values_directly(self):
        from core.auth.hydration import build_prefs_dict

        prefs = MagicMock()
        prefs.provider = "anthropic"
        prefs.model = "claude-opus-4-8"
        prefs.reasoning = None
        prefs.tools = []
        prefs.fallback_chain = []
        prefs.tool_approval_mode = "auto"
        prefs.allowed_authorities = "read_only,mutating,destructive"
        prefs.agent_mode = "auto"
        prefs.require_plan_approval = False
        prefs.user_memory_enabled = True
        prefs.agent_scope_key = None
        prefs.agent_memory_enabled = True
        prefs.skills_mode = "auto"
        prefs.tool_hooks = []
        prefs.shell_readonly = False
        prefs.user_timezone = None
        prefs.user_location = None
        prefs.user_language = None

        result = build_prefs_dict(prefs)

        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["model"], "claude-opus-4-8")


class TestChatEndpointGuard(unittest.IsolatedAsyncioTestCase):
    """api/chat.py: the primary, synchronous fix for the actual bug."""

    async def test_unconfigured_session_raises_424_before_any_db_work(self):
        from api.chat import chat_endpoint
        from api.schemas.requests import ChatRequest

        request = ChatRequest(message="Hey there!", conversation_id=None)
        auth = {"user_id": "u1", "session_id": "s1"}

        with patch("api.chat.get_user_session", return_value={"provider": "", "model": ""}), \
             patch("api.chat.DBRepoModule") as mock_db_module:
            with self.assertRaises(HTTPException) as ctx:
                await chat_endpoint(request, auth=auth, db=MagicMock())

        self.assertEqual(ctx.exception.status_code, 424)
        self.assertEqual(ctx.exception.detail["error_code"], "provider_not_configured")
        # Guard fires before any conversation/DB lookup is attempted.
        mock_db_module.DBRepo.get_conversation.assert_not_called()

    async def test_configured_session_does_not_trip_the_guard(self):
        from api.chat import chat_endpoint
        from api.schemas.requests import ChatRequest

        request = ChatRequest(message="Hey there!", conversation_id=None)
        auth = {"user_id": "u1", "session_id": "s1"}

        # Marker exception thrown by the next real DB call the endpoint
        # makes for a new conversation -- proves execution got *past* the
        # guard without asserting on the rest of this large function.
        class _ReachedPastGuard(Exception):
            pass

        with patch("api.chat.get_user_session",
                    return_value={"provider": "ollama_cloud", "model": "gpt-oss:20b"}), \
             patch("api.chat.DBRepoModule") as mock_db_module, \
             patch("api.chat.ProjectRepo"):
            mock_db_module.DBRepo.get_conversation = AsyncMock(side_effect=_ReachedPastGuard)
            mock_db_module.DBRepo.create_conversation = AsyncMock(side_effect=_ReachedPastGuard)
            try:
                await chat_endpoint(request, auth=auth, db=MagicMock())
            except HTTPException as exc:
                self.assertNotEqual(
                    getattr(exc, "detail", None) and exc.detail.get("error_code")
                    if isinstance(exc.detail, dict) else None,
                    "provider_not_configured",
                )
            except _ReachedPastGuard:
                pass  # expected -- confirms the guard let it through


class TestWorkerProcessExecutionGuard(unittest.IsolatedAsyncioTestCase):
    """core/execution/worker.py: defensive backstop for executions that
    reach processing without going through api/chat.py's check.

    Isolation fix (found during the Q-01 worker.py test-harness
    investigation, 2026-08-17): this test never used to patch
    AsyncSessionLocal, so _process_execution's "Hydrate User Preferences
    from DB" phase silently queried the real dev eva.db for user "u1" --
    it only ever passed because that user happened to have no
    UserPreference row there. Now uses a real in-memory SQLite DB instead,
    matching the discipline established in
    tests/test_worker_process_execution_unit.py."""

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        import core.db.session as _dbs
        import core.execution.worker as worker_mod
        self._orig_dbs_session_local = _dbs.AsyncSessionLocal
        self._orig_worker_session_local = worker_mod.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session
        worker_mod.AsyncSessionLocal = self.Session

    async def asyncTearDown(self):
        import core.db.session as _dbs
        import core.execution.worker as worker_mod
        _dbs.AsyncSessionLocal = self._orig_dbs_session_local
        worker_mod.AsyncSessionLocal = self._orig_worker_session_local
        await self.engine.dispose()

    async def test_unconfigured_session_fails_cleanly_without_building_agent(self):
        from core.execution.worker import ExecutionWorker

        mock_client = MagicMock()
        mock_client.hgetall = AsyncMock(return_value={
            "conversation_id": "c1",
            "user_id": "u1",
            "session_id": "s1",
            "execution_mode": "conversational",
        })

        with patch("core.execution.worker.redis_execution_registry") as mock_registry, \
             patch("core.execution.worker.get_user_session",
                   return_value={"provider": "", "model": ""}), \
             patch("core.execution.worker.publish_user_event", new=AsyncMock()) as mock_publish, \
             patch("core.execution.worker.AgentEngine") as mock_agent_engine:
            mock_registry.connect = AsyncMock(return_value=mock_client)
            mock_registry.mark_execution_failed = AsyncMock()

            worker = ExecutionWorker()
            await worker._process_execution("exec-1")

        mock_agent_engine.assert_not_called()
        mock_registry.mark_execution_failed.assert_awaited_once()
        failed_call_args = mock_registry.mark_execution_failed.await_args
        self.assertIn("provider/model", failed_call_args.args[1])
        mock_publish.assert_awaited_once()


class TestSessionEndpointsNoFallback(unittest.IsolatedAsyncioTestCase):
    """api/session.py: GET /session shows null honestly; PATCH
    /session/permissions refuses to be the first thing that ever writes a
    UserPreference row."""

    async def test_get_session_returns_null_when_unconfigured(self):
        from api.session import get_session

        with patch("api.session.get_user_session", return_value={"provider": "", "model": ""}):
            result = await get_session(auth={"user_id": "u1"})

        self.assertIsNone(result.provider)
        self.assertIsNone(result.model)

    async def test_get_session_returns_real_values_when_configured(self):
        from api.session import get_session

        with patch("api.session.get_user_session",
                   return_value={"provider": "anthropic", "model": "claude-opus-4-8"}):
            result = await get_session(auth={"user_id": "u1"})

        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(result.model, "claude-opus-4-8")

    async def test_patch_permissions_rejects_when_unconfigured(self):
        from api.session import update_permissions, PermissionsUpdateRequest

        body = PermissionsUpdateRequest(tool_approval_mode="always_ask")

        with patch("api.session.get_user_session", return_value={"provider": "", "model": ""}):
            with self.assertRaises(HTTPException) as ctx:
                await update_permissions(body, auth={"user_id": "u1"})

        self.assertEqual(ctx.exception.status_code, 424)
        self.assertEqual(ctx.exception.detail["error_code"], "provider_not_configured")


if __name__ == "__main__":
    unittest.main(verbosity=2)
