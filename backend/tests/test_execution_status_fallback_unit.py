# tests/test_execution_status_fallback_unit.py
"""
Unit tests for GET /executions/{id}/status's DB fallback (2026-08-19).

Background: this endpoint previously read ONLY the in-memory broker hash
(LocalRedisAdapter, in-process only) -- a real process restart wipes that
registry entirely, so it 404'd for every execution that predated the
current process, including ones boot-time reconciliation (B2-1.7/4.3)
correctly marked "aborted" in the durable execution_metrics table. Found
live: GET /executions correctly showed a recovered execution as aborted
while GET /executions/{id}/status 404'd for the exact same id.

Fix: fall back to ExecutionMetricsRepo when the in-memory hash misses,
instead of 404ing unconditionally.

Run with:
    cd <project_root>
    pytest tests/test_execution_status_fallback_unit.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch


def _make_engine_and_session():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from core.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, Session


class _DbHarnessMixin:
    async def asyncSetUp(self):
        self.engine, self.Session = _make_engine_and_session()
        async with self.engine.begin() as conn:
            from core.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        import core.db.session as _dbs
        self._orig_session_local = _dbs.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session

        import api.executions as _execapi
        self._orig_execapi_session_local = _execapi.AsyncSessionLocal
        _execapi.AsyncSessionLocal = self.Session

    async def asyncTearDown(self):
        import core.db.session as _dbs
        _dbs.AsyncSessionLocal = self._orig_session_local
        import api.executions as _execapi
        _execapi.AsyncSessionLocal = self._orig_execapi_session_local
        await self.engine.dispose()

    async def _seed_user_and_conversation(self, user_id="user-1", conv_id="conv-1"):
        from core.db.models import User, Conversation
        async with self.Session() as db:
            db.add(User(id=user_id, email=f"{user_id}@test.local", username=user_id, password_hash="x"))
            db.add(Conversation(id=conv_id, user_id=user_id))
            await db.commit()


class TestInMemoryHitPathUnchanged(unittest.IsolatedAsyncioTestCase):
    """Regression: when the in-memory registry has the execution, behavior
    is byte-for-byte the same as before -- no DB round-trip taken at all."""

    async def test_returns_status_from_memory_when_present(self):
        import api.executions as execapi

        client = AsyncMock()
        client.hgetall = AsyncMock(return_value={
            "status": "running", "conversation_id": "conv-mem", "user_id": "user-1",
        })

        with patch.object(execapi.redis_execution_registry, "connect", new=AsyncMock(return_value=client)), \
             patch.object(execapi, "ExecutionMetricsRepo") as mock_repo:
            result = await execapi.get_execution_status("exec-mem-1", auth={"user_id": "user-1"})

        self.assertEqual(result.status, "running")
        self.assertEqual(result.conversation_id, "conv-mem")
        mock_repo.get.assert_not_called()

    async def test_forbidden_when_memory_row_belongs_to_another_user(self):
        import api.executions as execapi
        from fastapi import HTTPException

        client = AsyncMock()
        client.hgetall = AsyncMock(return_value={
            "status": "running", "conversation_id": "conv-mem", "user_id": "someone-else",
        })

        with patch.object(execapi.redis_execution_registry, "connect", new=AsyncMock(return_value=client)):
            with self.assertRaises(HTTPException) as ctx:
                await execapi.get_execution_status("exec-mem-1", auth={"user_id": "user-1"})

        self.assertEqual(ctx.exception.status_code, 403)


class TestDbFallbackPath(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    async def test_falls_back_to_db_when_memory_registry_empty(self):
        import api.executions as execapi
        from core.db.repository import ExecutionMetricsRepo

        await self._seed_user_and_conversation()
        async with self.Session() as db:
            await ExecutionMetricsRepo.upsert(db, {
                "execution_id": "exec-restarted-1",
                "conversation_id": "conv-1",
                "user_id": "user-1",
                "status": "aborted",
                "termination_reason": "process_restart",
            })

        client = AsyncMock()
        client.hgetall = AsyncMock(return_value={})  # empty -- wiped by restart

        with patch.object(execapi.redis_execution_registry, "connect", new=AsyncMock(return_value=client)):
            result = await execapi.get_execution_status("exec-restarted-1", auth={"user_id": "user-1"})

        self.assertEqual(result.status, "aborted")
        self.assertEqual(result.conversation_id, "conv-1")

    async def test_404_when_neither_memory_nor_db_has_it(self):
        import api.executions as execapi
        from fastapi import HTTPException

        client = AsyncMock()
        client.hgetall = AsyncMock(return_value={})

        with patch.object(execapi.redis_execution_registry, "connect", new=AsyncMock(return_value=client)):
            with self.assertRaises(HTTPException) as ctx:
                await execapi.get_execution_status("never-existed", auth={"user_id": "user-1"})

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_forbidden_when_db_row_belongs_to_another_user(self):
        import api.executions as execapi
        from core.db.repository import ExecutionMetricsRepo
        from fastapi import HTTPException

        await self._seed_user_and_conversation(user_id="owner-1", conv_id="conv-1")
        async with self.Session() as db:
            await ExecutionMetricsRepo.upsert(db, {
                "execution_id": "exec-restarted-2",
                "conversation_id": "conv-1",
                "user_id": "owner-1",
                "status": "completed",
            })

        client = AsyncMock()
        client.hgetall = AsyncMock(return_value={})

        with patch.object(execapi.redis_execution_registry, "connect", new=AsyncMock(return_value=client)):
            with self.assertRaises(HTTPException) as ctx:
                await execapi.get_execution_status("exec-restarted-2", auth={"user_id": "not-the-owner"})

        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
