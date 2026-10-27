"""
V6.59 Phase 4b -- save-time validation of every provider choice.

A provider can be chosen in four places: the session's primary, the session's
fallback chain, a sub-agent's override and a daemon's override. Each must be
(1) a provider EVA knows, (2) chat-ready, (3) configured -- checked when the
choice is SAVED, not discovered later as a silent skip or a mid-turn failure.
"Inherit from the session" (no provider) is always valid.
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException

from api import _provider_validation as pv
from providers import credentials


def _http(exc: HTTPException):
    return exc.status_code, exc.detail


class CheckProviderUsableTests(unittest.TestCase):
    def test_matrix(self):
        credentials.set_api_key("groq", "k")
        credentials.set_api_key("azure", "k")        # a stored key does not make azure usable
        self.assertIsNone(pv.check_provider_usable("groq"))
        self.assertIsNone(pv.check_provider_usable("ollama"))
        self.assertEqual(pv.check_provider_usable("nope").reason, pv.REASON_UNKNOWN)
        self.assertEqual(pv.check_provider_usable("azure").reason, pv.REASON_NOT_CHAT_READY)
        self.assertEqual(pv.check_provider_usable("openai").reason, pv.REASON_NOT_CONFIGURED)

    def test_status_codes(self):
        self.assertEqual(pv.check_provider_usable("nope").status_code, 422)
        self.assertEqual(pv.check_provider_usable("azure").status_code, 422)
        self.assertEqual(pv.check_provider_usable("openai").status_code, 424)

    def test_alias_resolves(self):
        credentials.set_api_key("together_ai", "k")
        self.assertIsNone(pv.check_provider_usable("together"))


class ValidateOverrideTests(unittest.TestCase):
    def test_empty_inherits(self):
        for blank in (None, "", "   "):
            self.assertIsNone(pv.validate_provider_override(blank, "any-model"))
            self.assertIsNone(pv.validate_provider_override(blank, None))

    def test_provider_requires_a_model(self):
        credentials.set_api_key("groq", "k")
        for model in (None, "", "  "):
            with self.assertRaises(HTTPException) as cm:
                pv.validate_provider_override("groq", model)
            code, detail = _http(cm.exception)
            self.assertEqual(code, 422)
            self.assertEqual(detail["reason"], "model_required_with_provider")
            self.assertEqual(detail["field"], "model")

    def test_configured_returns_canonical_name(self):
        credentials.set_api_key("together_ai", "k")
        self.assertEqual(pv.validate_provider_override("Together", "m"), "together_ai")

    def test_unknown_not_ready_unconfigured(self):
        cases = [("nope", 422, pv.REASON_UNKNOWN), ("bedrock", 422, pv.REASON_NOT_CHAT_READY),
                 ("openai", 424, pv.REASON_NOT_CONFIGURED)]
        for provider, status, reason in cases:
            with self.assertRaises(HTTPException) as cm:
                pv.validate_provider_override(provider, "m")
            code, detail = _http(cm.exception)
            self.assertEqual((code, detail["reason"], detail["code"], detail["field"]),
                             (status, reason, pv.ERROR_CODE, "provider"), provider)
            self.assertTrue(detail["message"])

    def test_unchanged_provider_is_accepted_even_if_key_since_removed(self):
        # openai has no key stored, but the agent already uses it: editing the
        # description must not be blocked.
        self.assertEqual(pv.validate_provider_override("openai", "m", existing_provider="OpenAI"), "openai")

    def test_changed_provider_is_still_validated(self):
        with self.assertRaises(HTTPException):
            pv.validate_provider_override("openai", "m", existing_provider="groq")


def _agent_body(**kw):
    from api.agents import SubagentConfigRequest
    return SubagentConfigRequest(name="helper", **kw)


def _daemon_body(**kw):
    from api.daemons import DaemonConfigRequest
    return DaemonConfigRequest(name="nightly", **kw)


class AgentEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, fn_name, body, existing=None):
        import api.agents as agents
        reg = MagicMock()
        reg.get = AsyncMock(return_value=existing)
        reg.upsert = AsyncMock(return_value=MagicMock())
        with patch.object(agents, "subagent_registry", reg), \
             patch.object(agents, "_to_response", lambda row: "ok"):
            if fn_name == "upsert_agent":
                out = await agents.upsert_agent(body, auth={"user_id": "u"})
            else:
                out = await agents.update_agent("helper", body, auth={"user_id": "u"})
        return out, reg

    async def test_create_with_usable_provider_stores_canonical(self):
        credentials.set_api_key("together_ai", "k")
        _, reg = await self._call("upsert_agent", _agent_body(provider="together", model="m"))
        self.assertEqual(reg.upsert.await_args.args[1]["provider"], "together_ai")

    async def test_create_inherit_stores_none(self):
        _, reg = await self._call("upsert_agent", _agent_body(model="only-a-model"))
        self.assertIsNone(reg.upsert.await_args.args[1]["provider"])

    async def test_create_rejects_bad_provider_and_stores_nothing(self):
        for provider, status in (("nope", 422), ("azure", 422), ("openai", 424)):
            with self.assertRaises(HTTPException) as cm:
                await self._call("upsert_agent", _agent_body(provider=provider, model="m"))
            self.assertEqual(cm.exception.status_code, status, provider)

    async def test_update_rejects_bad_provider(self):
        existing = MagicMock(provider=None, is_default=False, tools=None)
        with self.assertRaises(HTTPException) as cm:
            await self._call("update_agent", _agent_body(provider="openai", model="m"), existing)
        self.assertEqual(cm.exception.status_code, 424)

    async def test_update_unchanged_provider_is_not_blocked(self):
        existing = MagicMock(provider="openai", is_default=False, tools=None)
        _, reg = await self._call("update_agent", _agent_body(provider="openai", model="m", description="new"), existing)
        self.assertEqual(reg.upsert.await_args.args[1]["provider"], "openai")

    async def test_provider_without_model_rejected(self):
        credentials.set_api_key("groq", "k")
        with self.assertRaises(HTTPException) as cm:
            await self._call("upsert_agent", _agent_body(provider="groq"))
        self.assertEqual(cm.exception.detail["reason"], "model_required_with_provider")


class DaemonEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, fn_name, body, existing=None):
        import api.daemons as daemons
        reg = MagicMock()
        reg.get = AsyncMock(return_value=existing)
        row = MagicMock(id="d1", name="nightly", description=None, model=None, provider=None)
        reg.upsert = AsyncMock(return_value=row)
        with patch.object(daemons, "daemon_registry", reg), \
             patch.object(daemons, "_to_response", lambda r: "ok"), \
             patch.object(daemons.ConversationJournal, "write_daemon_meta", MagicMock()):
            if fn_name == "upsert_daemon":
                out = await daemons.upsert_daemon(body, auth={"user_id": "u"})
            else:
                out = await daemons.update_daemon("nightly", body, auth={"user_id": "u"})
        return out, reg

    async def test_create_usable(self):
        credentials.set_api_key("groq", "k")
        _, reg = await self._call("upsert_daemon", _daemon_body(provider="groq", model="m", enabled=False))
        self.assertEqual(reg.upsert.await_args.args[1]["provider"], "groq")

    async def test_create_rejects_unusable(self):
        for provider, status in (("nope", 422), ("vertex_ai", 422), ("anthropic", 424)):
            with self.assertRaises(HTTPException) as cm:
                await self._call("upsert_daemon", _daemon_body(provider=provider, model="m", enabled=False))
            self.assertEqual(cm.exception.status_code, status, provider)

    async def test_update_rejects_unusable(self):
        existing = MagicMock(provider=None, project_id=None)
        with self.assertRaises(HTTPException) as cm:
            await self._call("update_daemon", _daemon_body(provider="anthropic", model="m", enabled=False), existing)
        self.assertEqual(cm.exception.status_code, 424)

    async def test_update_unchanged_provider_not_blocked(self):
        existing = MagicMock(provider="anthropic", project_id=None)
        _, reg = await self._call("update_daemon", _daemon_body(provider="anthropic", model="m", enabled=False), existing)
        self.assertEqual(reg.upsert.await_args.args[1]["provider"], "anthropic")

    async def test_inherit_allowed(self):
        _, reg = await self._call("upsert_daemon", _daemon_body(enabled=False))
        self.assertIsNone(reg.upsert.await_args.args[1]["provider"])


def _db():
    mock_db = AsyncMock()
    local = MagicMock()
    local.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    local.return_value.__aexit__ = AsyncMock(return_value=False)
    return local


class SessionEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def _set(self, **kw):
        from api.session import set_session
        from api.schemas.requests import SessionRequest
        body = SessionRequest(**kw)
        with patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _db()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session") as stored:
            result = await set_session(body, auth={"user_id": "u1"})
        return result, stored

    async def test_primary_unknown_and_not_ready_are_422_structured(self):
        for provider in ("nope", "azure"):
            credentials.set_api_key("azure", "k")
            with self.assertRaises(HTTPException) as cm:
                await self._set(provider=provider, model="m")
            self.assertEqual(cm.exception.status_code, 422, provider)
            self.assertEqual(cm.exception.detail["code"], pv.ERROR_CODE)
            self.assertEqual(cm.exception.detail["field"], "provider")

    async def test_primary_unconfigured_keeps_the_established_424_string(self):
        with self.assertRaises(HTTPException) as cm:
            await self._set(provider="openai", model="gpt-4")
        self.assertEqual(cm.exception.status_code, 424)
        self.assertIsInstance(cm.exception.detail, str)

    async def test_primary_alias_is_stored_canonical(self):
        credentials.set_api_key("together_ai", "k")
        result, stored = await self._set(provider="together", model="m")
        self.assertEqual(result["provider"], "together_ai")
        self.assertEqual(stored.call_args.kwargs.get("provider", stored.call_args.args[1] if len(stored.call_args.args) > 1 else None), "together_ai")

    async def test_fallback_unknown_or_not_ready_rejects_whole_request_with_index(self):
        from api.schemas.requests import FallbackChainEntry
        credentials.set_api_key("ollama_cloud", "k")
        credentials.set_api_key("groq", "k")
        for bad in ("nope", "bedrock"):
            with self.assertRaises(HTTPException) as cm:
                await self._set(provider="ollama_cloud", model="m", fallback_chain=[
                    FallbackChainEntry(provider="groq", model="a"),
                    FallbackChainEntry(provider=bad, model="b"),
                ])
            self.assertEqual(cm.exception.status_code, 422, bad)
            self.assertEqual(cm.exception.detail["field"], "fallback_chain[1].provider")

    async def test_fallback_unconfigured_is_still_dropped_with_warning(self):
        from api.schemas.requests import FallbackChainEntry
        credentials.set_api_key("ollama_cloud", "k")
        credentials.set_api_key("groq", "k")
        result, _ = await self._set(provider="ollama_cloud", model="m", fallback_chain=[
            FallbackChainEntry(provider="groq", model="a"),
            FallbackChainEntry(provider="openai", model="b"),       # no key
        ])
        self.assertEqual([e["provider"] for e in result["fallback_chain"]], ["groq"])
        self.assertTrue(any("openai" in w for w in result["warnings"]))

    async def test_fallback_alias_canonicalised_and_deduplicated(self):
        from api.schemas.requests import FallbackChainEntry
        credentials.set_api_key("ollama_cloud", "k")
        credentials.set_api_key("together_ai", "k")
        result, _ = await self._set(provider="ollama_cloud", model="m", fallback_chain=[
            FallbackChainEntry(provider="together", model="x"),
            FallbackChainEntry(provider="together_ai", model="x"),   # same thing, other spelling
        ])
        self.assertEqual([(e["provider"], e["model"]) for e in result["fallback_chain"]], [("together_ai", "x")])


if __name__ == "__main__":
    unittest.main()
