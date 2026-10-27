# tests/test_mcp_last_synced_at_unit.py
"""
Unit tests for B1-2.3 (2026-08-19): MCPToolMeta/MCPResourceMeta/MCPPromptMeta
.last_synced_at.

Investigation found this wasn't quite "never populated" as the audit
originally framed it -- the current sync flow (core/mcp/manager.py) always
deletes all rows for a config then reinserts, so the column's insert-time
default already produces a correct timestamp on every real sync. Two real
gaps existed instead:

  1. MCPRepo.upsert_*_meta()'s UPDATE branch (unreachable today since sync
     always deletes first, but the reachable path a future incremental-
     upsert refactor would use) never refreshed the column -- no onupdate=
     was set, and none of the update dicts include the field. Fixed by
     adding onupdate= to all three columns in core/db/models.py.
  2. The value was never surfaced in any API response -- GET /mcp/{id}/tools,
     /resources, /prompts, and POST /mcp/{id}/sync all hand-build response
     dicts that omitted it. Fixed in api/mcp.py.

This file covers gap 1 (real DB, both branches) and gap 2 (API response
shape). Follows the established _DbHarnessMixin in-memory SQLite pattern
from tests/test_background_process_unit.py.

Run with:
    cd <project_root>
    pytest tests/test_mcp_last_synced_at_unit.py -v
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_engine_and_session():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from core.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, Session


class _DbHarnessMixin:
    """Shared in-memory SQLite setup -- same shape as test_background_process_unit.py."""

    async def asyncSetUp(self):
        self.engine, self.Session = _make_engine_and_session()
        async with self.engine.begin() as conn:
            from core.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _make_config(self, db, config_id="cfg-1"):
        from core.db.models import MCPConfig
        cfg = MCPConfig(
            id=config_id, user_id="user-1", name="test-server",
            transport="stdio", command="echo", enabled=True,
        )
        db.add(cfg)
        await db.commit()
        return cfg


# ===========================================================================
# Gap 1 -- onupdate refresh, real DB, both upsert branches
# ===========================================================================

class TestToolMetaLastSyncedAtRefresh(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    async def test_insert_branch_sets_last_synced_at(self):
        from core.db.repository import MCPRepo

        async with self.Session() as db:
            await self._make_config(db)
            t = await MCPRepo.upsert_tool_meta(db, "cfg-1", {
                "name": "search", "description": "d", "input_schema": {}, "output_schema": None,
            })
            self.assertIsNotNone(t.last_synced_at)

    async def test_update_branch_refreshes_last_synced_at(self):
        """The previously-dead branch: upsert_tool_meta() called twice for
        the SAME (config_id, name) WITHOUT a delete in between must now
        refresh the timestamp on the second call, via onupdate=."""
        from core.db.repository import MCPRepo

        async with self.Session() as db:
            await self._make_config(db)
            t1 = await MCPRepo.upsert_tool_meta(db, "cfg-1", {
                "name": "search", "description": "d1", "input_schema": {}, "output_schema": None,
            })
            first_id = t1.id
            first_synced_at = t1.last_synced_at

            await asyncio.sleep(0.05)

            t2 = await MCPRepo.upsert_tool_meta(db, "cfg-1", {
                "name": "search", "description": "d2-changed", "input_schema": {}, "output_schema": None,
            })

            self.assertEqual(t2.id, first_id, "must be the same row (update, not insert)")
            self.assertGreater(t2.last_synced_at, first_synced_at)

    async def test_delete_then_reinsert_pattern_still_produces_fresh_timestamps(self):
        """Regression guard for the ACTUAL current sync strategy (delete all,
        then reinsert) -- confirms it keeps working after the onupdate= change."""
        from core.db.repository import MCPRepo

        async with self.Session() as db:
            await self._make_config(db)

            await MCPRepo.delete_tools_for_config(db, "cfg-1")
            t1 = await MCPRepo.upsert_tool_meta(db, "cfg-1", {
                "name": "search", "description": "d", "input_schema": {}, "output_schema": None,
            })
            first_id, first_synced_at = t1.id, t1.last_synced_at

            await asyncio.sleep(0.05)

            await MCPRepo.delete_tools_for_config(db, "cfg-1")
            t2 = await MCPRepo.upsert_tool_meta(db, "cfg-1", {
                "name": "search", "description": "d", "input_schema": {}, "output_schema": None,
            })

            self.assertNotEqual(t2.id, first_id, "delete+reinsert produces a new row id")
            self.assertGreater(t2.last_synced_at, first_synced_at)


class TestResourceMetaLastSyncedAtRefresh(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    async def test_update_branch_refreshes_last_synced_at(self):
        from core.db.repository import MCPRepo

        async with self.Session() as db:
            await self._make_config(db)
            r1 = await MCPRepo.upsert_resource_meta(db, "cfg-1", {
                "uri": "file:///a.txt", "name": "a", "description": "d1", "mime_type": "text/plain",
            })
            first_synced_at = r1.last_synced_at

            await asyncio.sleep(0.05)

            r2 = await MCPRepo.upsert_resource_meta(db, "cfg-1", {
                "uri": "file:///a.txt", "name": "a", "description": "d2", "mime_type": "text/plain",
            })
            self.assertEqual(r2.id, r1.id)
            self.assertGreater(r2.last_synced_at, first_synced_at)


class TestPromptMetaLastSyncedAtRefresh(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    async def test_update_branch_refreshes_last_synced_at(self):
        from core.db.repository import MCPRepo

        async with self.Session() as db:
            await self._make_config(db)
            p1 = await MCPRepo.upsert_prompt_meta(db, "cfg-1", {
                "name": "greet", "description": "d1", "arguments": None,
            })
            first_synced_at = p1.last_synced_at

            await asyncio.sleep(0.05)

            p2 = await MCPRepo.upsert_prompt_meta(db, "cfg-1", {
                "name": "greet", "description": "d2", "arguments": None,
            })
            self.assertEqual(p2.id, p1.id)
            self.assertGreater(p2.last_synced_at, first_synced_at)


# ===========================================================================
# Gap 2 -- API response exposure
# ===========================================================================

def _make_tool(name="search", last_synced_at=None):
    t = MagicMock()
    t.name = name
    t.description = "d"
    t.input_schema = {}
    t.output_schema = None
    t.last_synced_at = last_synced_at
    return t


def _make_resource(uri="file:///a.txt", last_synced_at=None):
    r = MagicMock()
    r.uri = uri
    r.name = "a"
    r.description = "d"
    r.mime_type = "text/plain"
    r.last_synced_at = last_synced_at
    return r


def _make_prompt(name="greet", last_synced_at=None):
    p = MagicMock()
    p.name = name
    p.description = "d"
    p.arguments = None
    p.last_synced_at = last_synced_at
    return p


def _make_cfg(config_id="cfg-1", enabled=True):
    cfg = MagicMock()
    cfg.id = config_id
    cfg.enabled = enabled
    return cfg


class TestAPIResponsesIncludeLastSyncedAt(unittest.IsolatedAsyncioTestCase):

    async def test_list_tools_endpoint_includes_last_synced_at(self):
        import api.mcp as mcp_api
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc)
        cfg = _make_cfg()

        with patch.object(mcp_api.MCPRepo, "get_config", new=AsyncMock(return_value=cfg)), \
             patch.object(mcp_api.MCPRepo, "list_tools_for_config",
                          new=AsyncMock(return_value=[_make_tool(last_synced_at=ts)])), \
             patch.object(mcp_api, "AsyncSessionLocal") as mock_session_local:
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_db
            mock_session_local.return_value.__aexit__.return_value = None

            result = await mcp_api.list_mcp_tools("cfg-1", auth={"user_id": "u1"})

        self.assertEqual(len(result), 1)
        self.assertIn("last_synced_at", result[0])
        self.assertEqual(result[0]["last_synced_at"], ts)

    async def test_list_resources_endpoint_includes_last_synced_at(self):
        import api.mcp as mcp_api
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc)
        cfg = _make_cfg()

        with patch.object(mcp_api.MCPRepo, "get_config", new=AsyncMock(return_value=cfg)), \
             patch.object(mcp_api.MCPManager, "sync_resources_for_config",
                          new=AsyncMock(return_value=[_make_resource(last_synced_at=ts)])), \
             patch.object(mcp_api, "AsyncSessionLocal") as mock_session_local:
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_db
            mock_session_local.return_value.__aexit__.return_value = None

            result = await mcp_api.list_mcp_resources("cfg-1", auth={"user_id": "u1"})

        self.assertEqual(len(result), 1)
        self.assertIn("last_synced_at", result[0])
        self.assertEqual(result[0]["last_synced_at"], ts)

    async def test_list_prompts_endpoint_includes_last_synced_at(self):
        import api.mcp as mcp_api
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc)
        cfg = _make_cfg()

        with patch.object(mcp_api.MCPRepo, "get_config", new=AsyncMock(return_value=cfg)), \
             patch.object(mcp_api.MCPManager, "sync_prompts_for_config",
                          new=AsyncMock(return_value=[_make_prompt(last_synced_at=ts)])), \
             patch.object(mcp_api, "AsyncSessionLocal") as mock_session_local:
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_db
            mock_session_local.return_value.__aexit__.return_value = None

            result = await mcp_api.list_mcp_prompts("cfg-1", auth={"user_id": "u1"})

        self.assertEqual(len(result), 1)
        self.assertIn("last_synced_at", result[0])
        self.assertEqual(result[0]["last_synced_at"], ts)

    async def test_sync_endpoint_includes_last_synced_at_in_all_three_arrays(self):
        import api.mcp as mcp_api
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc)
        cfg = _make_cfg()

        with patch.object(mcp_api.MCPRepo, "get_config", new=AsyncMock(return_value=cfg)), \
             patch.object(mcp_api.MCPManager, "sync_tools_for_config",
                          new=AsyncMock(return_value=[_make_tool(last_synced_at=ts)])), \
             patch.object(mcp_api.MCPManager, "sync_resources_for_config",
                          new=AsyncMock(return_value=[_make_resource(last_synced_at=ts)])), \
             patch.object(mcp_api.MCPManager, "sync_prompts_for_config",
                          new=AsyncMock(return_value=[_make_prompt(last_synced_at=ts)])), \
             patch.object(mcp_api, "AsyncSessionLocal") as mock_session_local:
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_db
            mock_session_local.return_value.__aexit__.return_value = None

            result = await mcp_api.sync_mcp_tools("cfg-1", auth={"user_id": "u1"})

        self.assertEqual(result["tools"][0]["last_synced_at"], ts)
        self.assertEqual(result["resources"][0]["last_synced_at"], ts)
        self.assertEqual(result["prompts"][0]["last_synced_at"], ts)


if __name__ == "__main__":
    unittest.main()
