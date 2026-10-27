# tests/test_session_credential_gate_unit.py
"""
Unit tests for V6.5 Phase 1b: POST /set-session credential validation gate.

Moves API-key-presence checking out of AgentEngine/the fallback loop and
into session-config save time, per design discussion: a primary provider
with no resolvable API key is a hard-reject (424); fallback_chain entries
with no resolvable API key are dropped (not the whole request rejected),
with one warning per dropped entry. This is what lets AgentEngine's
fallback loop stay entirely unaware of "was a key ever configured" -- see
core/agent_engine.py's _gen_with_fallback and core/error_handling.py's AUTH
policy (skip_same_provider), which only ever handles a credential that was
valid at save time but got rejected since (revoked, expired, etc).

Mocked throughout -- no live server, no real DB, no real API calls.

Run with:
    cd <project_root>
    python tests/test_session_credential_gate_unit.py
    (or: pytest tests/test_session_credential_gate_unit.py -v)
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


class TestPrimaryCredentialGate(unittest.IsolatedAsyncioTestCase):
    async def test_primary_with_no_key_is_rejected_424(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest
        from fastapi import HTTPException

        body = SessionRequest(provider="openai", model="gpt-4")

        with patch("api.session.is_provider_key_configured", return_value=False) as mock_check, \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()) as mock_upsert, \
             patch("api.session.set_user_session") as mock_set_session:
            with self.assertRaises(HTTPException) as ctx:
                await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(ctx.exception.status_code, 424)
        mock_check.assert_any_call("openai")
        mock_upsert.assert_not_called()
        mock_set_session.assert_not_called()

    async def test_primary_with_configured_key_is_accepted(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest

        body = SessionRequest(provider="ollama_cloud", model="gpt-oss:20b")

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()) as mock_upsert, \
             patch("api.session.set_user_session") as mock_set_session:
            result = await set_session(body, auth={"user_id": "u1"})

        mock_upsert.assert_called_once()
        mock_set_session.assert_called_once()
        self.assertEqual(result["provider"], "ollama_cloud")
        self.assertEqual(result["warnings"], [])


class TestFallbackChainCredentialGate(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_entry_with_no_key_is_dropped_not_rejected(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        body = SessionRequest(
            provider="ollama_cloud",
            model="gpt-oss:20b",
            fallback_chain=[
                FallbackChainEntry(provider="groq", model="llama-3.1-70b"),   # configured
                FallbackChainEntry(provider="openai", model="gpt-4"),        # NOT configured
            ],
        )

        def _fake_configured(provider, explicit_key=None):
            return provider != "openai"

        with patch("api.session.is_provider_key_configured", side_effect=_fake_configured), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session") as mock_set_session:
            result = await set_session(body, auth={"user_id": "u1"})

        # Only the configured entry survives.
        self.assertEqual(len(result["fallback_chain"]), 1)
        self.assertEqual(result["fallback_chain"][0]["provider"], "groq")

        # The rest of the request still saved -- not rejected outright.
        mock_set_session.assert_called_once()
        saved_fallback_chain = mock_set_session.call_args.kwargs["fallback_chain"]
        self.assertEqual(len(saved_fallback_chain), 1)

        # A warning explains the drop.
        self.assertTrue(any("openai" in w for w in result["warnings"]))

    async def test_all_fallback_entries_configured_no_warnings(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        body = SessionRequest(
            provider="ollama_cloud",
            model="gpt-oss:20b",
            fallback_chain=[FallbackChainEntry(provider="groq", model="llama-3.1-70b")],
        )

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(len(result["fallback_chain"]), 1)
        self.assertEqual(result["warnings"], [])

    async def test_fallback_entry_explicit_api_key_overrides_env_check(self):
        """An entry with its own api_key should be checked via that
        explicit key, not just the provider's env var -- confirms the
        is_provider_key_configured(provider, explicit_key=...) call shape."""
        from api.session import set_session
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        body = SessionRequest(
            provider="ollama_cloud",
            model="gpt-oss:20b",
            fallback_chain=[FallbackChainEntry(provider="openai", model="gpt-4", api_key="sk-explicit")],
        )

        with patch("api.session.is_provider_key_configured") as mock_check, \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            mock_check.return_value = True
            await set_session(body, auth={"user_id": "u1"})

        # Called once for the primary, once for the fallback entry, and the
        # fallback call must carry the entry's own explicit key.
        fallback_calls = [
            c for c in mock_check.call_args_list
            if c.args and c.args[0] == "openai"
        ]
        self.assertEqual(len(fallback_calls), 1)
        self.assertEqual(fallback_calls[0].kwargs.get("explicit_key"), "sk-explicit")


class TestFallbackChainSchemaCap(unittest.TestCase):
    """V6.5 Phase 6: fallback_chain is capped at 5 entries at the schema
    level (api/schemas/requests.py) -- FastAPI rejects an over-length
    request with 422 before set_session ever runs."""

    def test_five_entries_accepted(self):
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        SessionRequest(
            provider="openai",
            model="gpt-4",
            fallback_chain=[
                FallbackChainEntry(provider="groq", model=f"m{i}") for i in range(5)
            ],
        )  # no raise

    def test_six_entries_rejected(self):
        from pydantic import ValidationError
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        with self.assertRaises(ValidationError):
            SessionRequest(
                provider="openai",
                model="gpt-4",
                fallback_chain=[
                    FallbackChainEntry(provider="groq", model=f"m{i}") for i in range(6)
                ],
            )


class TestFallbackChainDedup(unittest.IsolatedAsyncioTestCase):
    """V6.5 Phase 6: duplicate (provider, model) candidates -- whether
    against the primary or an earlier fallback entry -- are dropped with a
    warning rather than saved, since a duplicate can never produce a
    different outcome than the candidate ahead of it already tried."""

    async def test_entry_duplicating_primary_is_dropped(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        body = SessionRequest(
            provider="openai",
            model="gpt-4",
            fallback_chain=[
                FallbackChainEntry(provider="openai", model="gpt-4"),  # == primary
                FallbackChainEntry(provider="groq", model="llama-3.1-70b"),
            ],
        )

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session") as mock_set_session:
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(len(result["fallback_chain"]), 1)
        self.assertEqual(result["fallback_chain"][0]["provider"], "groq")
        self.assertTrue(any("duplicates" in w for w in result["warnings"]))
        saved_fallback_chain = mock_set_session.call_args.kwargs["fallback_chain"]
        self.assertEqual(len(saved_fallback_chain), 1)

    async def test_duplicate_within_chain_keeps_first_occurrence(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        body = SessionRequest(
            provider="ollama_cloud",
            model="gpt-oss:20b",
            fallback_chain=[
                FallbackChainEntry(provider="groq", model="llama-3.1-70b", api_key="first"),
                FallbackChainEntry(provider="groq", model="llama-3.1-70b", api_key="second"),
            ],
        )

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(len(result["fallback_chain"]), 1)
        self.assertEqual(result["fallback_chain"][0]["api_key"], "first")
        self.assertTrue(any("duplicates" in w for w in result["warnings"]))

    async def test_dedup_and_credential_gate_combine(self):
        """A chain with both a duplicate and a no-credential entry should
        drop both, with one distinct warning each, and keep the rest."""
        from api.session import set_session
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        body = SessionRequest(
            provider="ollama_cloud",
            model="gpt-oss:20b",
            fallback_chain=[
                FallbackChainEntry(provider="groq", model="llama-3.1-70b"),   # kept
                FallbackChainEntry(provider="groq", model="llama-3.1-70b"),   # dup of above
                FallbackChainEntry(provider="openai", model="gpt-4"),        # no key
            ],
        )

        def _fake_configured(provider, explicit_key=None):
            return provider != "openai"

        with patch("api.session.is_provider_key_configured", side_effect=_fake_configured), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(len(result["fallback_chain"]), 1)
        self.assertEqual(result["fallback_chain"][0]["provider"], "groq")
        self.assertEqual(len(result["warnings"]), 2)
        self.assertTrue(any("duplicates" in w for w in result["warnings"]))
        self.assertTrue(any("no API key configured" in w for w in result["warnings"]))

    async def test_no_duplicates_no_warnings(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest, FallbackChainEntry

        body = SessionRequest(
            provider="ollama_cloud",
            model="gpt-oss:20b",
            fallback_chain=[
                FallbackChainEntry(provider="groq", model="llama-3.1-70b"),
                FallbackChainEntry(provider="openai", model="gpt-4o"),
            ],
        )

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(len(result["fallback_chain"]), 2)
        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
