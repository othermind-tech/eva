"""
V6.59 Phase 4 -- DELETE /secrets/{provider}: remove a leaked or abandoned key.

Pins down: the key really leaves the credential store and the provider stops
being configured at once; deletion is idempotent; what in the caller's own
configuration still points at the provider is reported (not silently changed);
cached model lists fetched with the old key are dropped; the endpoint is
rate-limited like its POST sibling; the key value never appears in a response.
"""
import os
import sys
import unittest
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import credentials
from providers.capability.base import is_provider_key_configured


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    """Returns agent rows on the first execute(), daemon rows on the second."""

    def __init__(self, agents, daemons):
        self._batches = [agents, daemons]

    async def execute(self, _stmt):
        return _FakeResult(self._batches.pop(0))


def _fake_session_factory(agents=(), daemons=()):
    @asynccontextmanager
    async def _cm():
        yield _FakeDb(list(agents), list(daemons))
    return _cm


class DeleteSecretTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import api.secrets as secrets_mod
        self.m = secrets_mod
        self.m._secrets_rate_limiter = type(secrets_mod._secrets_rate_limiter)(window_seconds=60, max_calls=100)
        self.auth = {"user_id": "u-delete"}

    async def _delete(self, provider, session=None, agents=(), daemons=()):
        session = session or {"provider": "", "fallback_chain": []}
        with patch("config.settings.get_user_session", return_value=session), \
             patch("core.db.session.AsyncSessionLocal", _fake_session_factory(agents, daemons)):
            return await self.m.delete_provider_secret(provider=provider, auth=self.auth)

    async def test_deletes_stored_key_and_unconfigures_provider(self):
        credentials.set_api_key("groq", "gsk-leaked")
        self.assertTrue(is_provider_key_configured("groq"))
        r = await self._delete("groq")
        self.assertTrue(r.deleted)
        self.assertFalse(r.configured)
        self.assertEqual(r.key_name, "GROQ_API_KEY")
        self.assertIsNone(credentials.get_api_key("groq"))
        self.assertFalse(is_provider_key_configured("groq"))

    async def test_response_never_contains_the_key(self):
        credentials.set_api_key("groq", "gsk-leaked-VALUE")
        r = await self._delete("groq")
        self.assertNotIn("gsk-leaked-VALUE", r.model_dump_json())

    async def test_idempotent_when_no_key_stored(self):
        r = await self._delete("openai")
        self.assertFalse(r.deleted)
        self.assertFalse(r.configured)
        self.assertIn("nothing to remove", r.message)

    async def test_alias_deletes_canonical_entry(self):
        credentials.set_api_key("together_ai", "tg")
        r = await self._delete("together")
        self.assertEqual(r.provider, "together_ai")
        self.assertTrue(r.deleted)
        self.assertIsNone(credentials.get_api_key("together_ai"))

    async def test_only_the_named_provider_is_touched(self):
        credentials.set_api_key("groq", "g")
        credentials.set_api_key("openai", "o")
        await self._delete("groq")
        self.assertEqual(credentials.get_api_key("openai"), "o")

    async def test_ollama_and_unknown_rejected(self):
        for name in ("ollama", "not_a_provider"):
            with self.assertRaises(Exception) as cm:
                await self._delete(name)
            self.assertEqual(cm.exception.status_code, 400, name)

    async def test_dependents_reported_not_changed(self):
        credentials.set_api_key("groq", "g")
        session = {"provider": "groq", "fallback_chain": [
            {"provider": "groq", "model": "a"}, {"provider": "openai", "model": "b"}, {"provider": "groq", "model": "c"}]}
        r = await self._delete("groq", session=session,
                               agents=[("researcher", "groq"), ("writer", "openai")],
                               daemons=[("nightly", "GROQ")])
        self.assertTrue(r.in_use.session_primary)
        self.assertEqual(r.in_use.fallback_entries, 2)
        self.assertEqual(r.in_use.agents, ["researcher"])
        self.assertEqual(r.in_use.daemons, ["nightly"])
        self.assertEqual(len(r.warnings), 4)
        self.assertIn("2 fallback entries use Groq", " ".join(r.warnings))
        self.assertEqual(session["provider"], "groq")            # configuration untouched
        self.assertEqual(len(session["fallback_chain"]), 3)

    async def test_no_dependents_no_warnings(self):
        credentials.set_api_key("groq", "g")
        r = await self._delete("groq", session={"provider": "openai", "fallback_chain": []},
                               agents=[("a", "openai")], daemons=[])
        self.assertFalse(r.in_use.session_primary)
        self.assertEqual(r.warnings, [])

    async def test_cached_model_list_is_invalidated(self):
        from core.providers import resolver as resolver_mod
        credentials.set_api_key("groq", "g")
        resolver_mod._model_cache["groq:"] = (0, ["stale"])
        resolver_mod._model_cache["openai:"] = (0, ["keep"])
        await self._delete("groq")
        self.assertNotIn("groq:", resolver_mod._model_cache)
        self.assertIn("openai:", resolver_mod._model_cache)
        resolver_mod._model_cache.pop("openai:", None)

    async def test_credential_store_failure_is_a_503_not_a_false_success(self):
        with patch.object(credentials, "delete_api_key", side_effect=RuntimeError("locked")):
            with self.assertRaises(Exception) as cm:
                await self._delete("groq")
        self.assertEqual(cm.exception.status_code, 503)

    async def test_rate_limited_per_user(self):
        self.m._secrets_rate_limiter = type(self.m._secrets_rate_limiter)(window_seconds=60, max_calls=2)
        await self._delete("groq")
        await self._delete("groq")
        with self.assertRaises(Exception) as cm:
            await self._delete("groq")
        self.assertEqual(cm.exception.status_code, 429)

    async def test_route_is_registered_as_delete(self):
        methods = {(r.path, m) for r in self.m.router.routes for m in r.methods}
        self.assertIn(("/secrets/{provider}", "DELETE"), methods)


if __name__ == "__main__":
    unittest.main()
