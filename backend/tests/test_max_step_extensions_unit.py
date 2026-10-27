# tests/test_max_step_extensions_unit.py
"""
Unit tests for the max_step_extensions session field (V6.12 plumbing).

Mirrors the require_plan_approval session-plumbing coverage in
tests/test_apis.py (section 14f) and tests/test_session_credential_gate_unit.py's
style, but as a pure mocked unit test -- no live server, no real DB.

Covers:
  - Default value (2) when the field is unset on the request.
  - Round-trip through POST /session (set_session) -> GET /session (get_session).
  - Persistence to the DB upsert dict and load-back via
    hydrate_user_session_from_db.

Run with:
    cd <project_root>
    python -m pytest tests/test_max_step_extensions_unit.py -v
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mock_db_context():
    mock_db = AsyncMock()
    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session_local


class TestMaxStepExtensionsDefault(unittest.TestCase):
    """Schema-level default: omitting the field entirely yields 2."""

    def test_default_value_on_request_schema(self):
        from api.schemas.requests import SessionRequest

        body = SessionRequest(provider="openai", model="gpt-4")
        self.assertEqual(body.max_step_extensions, 2)

    def test_default_value_on_response_schema(self):
        from api.schemas.responses import SessionUpdateResponse

        # Build with only the required fields; max_step_extensions should
        # default to 2 exactly like require_plan_approval defaults to False.
        resp = SessionUpdateResponse(
            status="ok",
            provider="openai",
            model="gpt-4",
            reasoning=None,
            tools_enabled=[],
            system_prompt="",
            fallback_chain=[],
            tool_approval_mode="auto",
            allowed_authorities="read_only,mutating,destructive",
            agent_mode="auto",
        )
        self.assertEqual(resp.max_step_extensions, 2)
        self.assertEqual(resp.require_plan_approval, False)

    def test_default_value_in_blank_session_template(self):
        from config.settings import get_user_session

        session = get_user_session("nonexistent-user-xyz")
        self.assertEqual(session["max_step_extensions"], 2)


class TestMaxStepExtensionsSetSession(unittest.IsolatedAsyncioTestCase):
    """POST /session (set_session) plumbing."""

    async def test_custom_value_is_saved_and_returned(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest

        body = SessionRequest(provider="ollama_cloud", model="gpt-oss:20b", max_step_extensions=5)

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()) as mock_upsert, \
             patch("api.session.set_user_session") as mock_set_session:
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(result["max_step_extensions"], 5)

        # DB upsert dict includes the field.
        upsert_kwargs = mock_upsert.call_args.args[2]
        self.assertEqual(upsert_kwargs["max_step_extensions"], 5)

        # In-memory session write includes the field.
        self.assertEqual(mock_set_session.call_args.kwargs["max_step_extensions"], 5)

    async def test_default_value_flows_through_when_unset(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest

        body = SessionRequest(provider="ollama_cloud", model="gpt-oss:20b")

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()) as mock_upsert, \
             patch("api.session.set_user_session") as mock_set_session:
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(result["max_step_extensions"], 2)
        self.assertEqual(mock_upsert.call_args.args[2]["max_step_extensions"], 2)
        self.assertEqual(mock_set_session.call_args.kwargs["max_step_extensions"], 2)


class TestMaxStepExtensionsGetSession(unittest.IsolatedAsyncioTestCase):
    """GET /session (get_session) round-trip through the in-memory session store."""

    async def test_get_session_reflects_stored_value(self):
        from api.session import get_session
        from config.settings import set_user_session

        set_user_session(
            "u-round-trip",
            provider="openai",
            model="gpt-4",
            max_step_extensions=7,
        )

        result = await get_session(auth={"user_id": "u-round-trip"})
        self.assertEqual(result.max_step_extensions, 7)

    async def test_get_session_defaults_when_absent_from_stored_session(self):
        from api.session import get_session
        from config.settings import _USER_SESSIONS  # noqa: PLC0415

        # Simulate a legacy in-memory session dict saved before this field
        # existed -- must fall back to 2, not KeyError.
        _USER_SESSIONS["u-legacy"] = {
            "provider": "openai",
            "model": "gpt-4",
        }
        try:
            result = await get_session(auth={"user_id": "u-legacy"})
            self.assertEqual(result.max_step_extensions, 2)
        finally:
            del _USER_SESSIONS["u-legacy"]


class TestMaxStepExtensionsDbHydration(unittest.TestCase):
    """hydrate_user_session_from_db: DB prefs -> in-memory session."""

    def test_hydrate_loads_persisted_value(self):
        from config.settings import hydrate_user_session_from_db, _USER_SESSIONS  # noqa: PLC0415

        prefs = {
            "provider": "openai",
            "model": "gpt-4",
            "max_step_extensions": 4,
        }
        hydrate_user_session_from_db("u-hydrate", prefs)
        try:
            self.assertEqual(_USER_SESSIONS["u-hydrate"]["max_step_extensions"], 4)
        finally:
            del _USER_SESSIONS["u-hydrate"]

    def test_hydrate_defaults_when_missing_from_db_row(self):
        from config.settings import hydrate_user_session_from_db, _USER_SESSIONS  # noqa: PLC0415

        prefs = {
            "provider": "openai",
            "model": "gpt-4",
            # max_step_extensions intentionally absent -- simulates a row
            # written before the column existed / server_default applied.
        }
        hydrate_user_session_from_db("u-hydrate-default", prefs)
        try:
            self.assertEqual(_USER_SESSIONS["u-hydrate-default"]["max_step_extensions"], 2)
        finally:
            del _USER_SESSIONS["u-hydrate-default"]


class TestMaxStepExtensionsDbModel(unittest.TestCase):
    """core/db/models.py column definition."""

    def test_column_exists_with_correct_default(self):
        from core.db.models import UserPreference

        col = UserPreference.__table__.columns["max_step_extensions"]
        self.assertFalse(col.nullable)
        self.assertEqual(col.default.arg, 2)


class TestMaxStepExtensionsPermissionsPatchOmission(unittest.IsolatedAsyncioTestCase):
    """PATCH /session/permissions intentionally does NOT touch this field --
    it only has a SQLAlchemy-level default, same as require_plan_approval,
    so per the rule documented at api/session.py's update_permissions it is
    safe to omit from that scoped upsert."""

    async def test_permissions_patch_upsert_omits_max_step_extensions(self):
        from api.session import update_permissions, PermissionsUpdateRequest
        from config.settings import set_user_session

        set_user_session(
            "u-perms",
            provider="openai",
            model="gpt-4",
            max_step_extensions=9,
        )

        body = PermissionsUpdateRequest(tool_approval_mode="auto")

        with patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()) as mock_upsert:
            await update_permissions(body, auth={"user_id": "u-perms"})

        upsert_dict = mock_upsert.call_args.args[2]
        self.assertNotIn("max_step_extensions", upsert_dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
