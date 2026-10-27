"""
V6.59 Phase 1 -- the single provider registry (providers/registry.py).

The bug class these tests pin down: "configured" and "can chat" used to be
decided in six independent tables, so a provider could hold a stored key and
appear in the UI yet be refused by the tool builder when a chat began
(deepseek: ValueError "Unknown provider"). Every table is now derived from the
registry; these tests fail if any of them drifts or a listed provider has no
working chat path.
"""
import os
import unittest
from unittest.mock import patch

from providers import registry
from providers.capability.base import PROVIDER_KEY_MAP, is_provider_key_configured
from core.providers.resolver import resolver, _NATIVE_PROVIDERS, _PROVIDER_ALIASES
from core.tools.builder import build_provider_tools


class _Tool:
    description = "d"

    class args_schema:
        @staticmethod
        def model_json_schema():
            return {"title": "T", "type": "object", "properties": {}}


class RegistryDerivedTablesTests(unittest.TestCase):
    def test_key_map_is_the_registry(self):
        self.assertEqual(PROVIDER_KEY_MAP, registry.key_map())

    def test_resolver_native_set_is_the_registry(self):
        self.assertEqual(_NATIVE_PROVIDERS, registry.native_names())

    def test_every_gateway_prefix_is_in_the_resolver_alias_table(self):
        for s in registry.PROVIDERS.values():
            if s.litellm_prefix:
                self.assertEqual(_PROVIDER_ALIASES[s.name], s.litellm_prefix)

    def test_curated_list_is_the_registry(self):
        from api.providers import _CURATED_PROVIDERS
        self.assertEqual(
            [e["provider"] for e in _CURATED_PROVIDERS],
            [s.name for s in registry.curated_specs()],
        )

    def test_env_keys_unique(self):
        keys = [s.env_key for s in registry.PROVIDERS.values() if s.env_key]
        self.assertEqual(len(keys), len(set(keys)))


class ChatReadyContractTests(unittest.TestCase):
    """Every provider the UI can offer must actually be able to start a chat."""

    def test_every_curated_provider_is_chat_ready(self):
        for s in registry.curated_specs():
            self.assertTrue(s.chat_ready, f"{s.name} is curated but not chat-ready")

    def test_every_chat_ready_provider_builds_tools(self):
        for name, s in registry.PROVIDERS.items():
            if not s.chat_ready:
                continue
            out = build_provider_tools(name, {"t": _Tool})
            self.assertEqual(len(out), 1, name)

    def test_every_chat_ready_provider_creates_a_handler(self):
        from core.factory import HandlerFactory
        from core.tools.user_registry import UserToolRegistry
        for name, s in registry.PROVIDERS.items():
            if not s.chat_ready:
                continue
            handler = HandlerFactory.create_handler(name, {
                "api_key": "k", "model": "m",
                "tools": UserToolRegistry(default_tools=False, enabled_tools=[]),
            })
            self.assertIsNotNone(handler, name)

    def test_deepseek_regression(self):
        """The reported failure: configured deepseek could not start a chat."""
        out = build_provider_tools("deepseek", {"t": _Tool})
        self.assertEqual(out[0]["type"], "function")

    def test_gateway_handler_gets_prefixed_model(self):
        from core.factory import HandlerFactory
        from core.tools.user_registry import UserToolRegistry
        from providers.handlers.litellm_handler import LiteLLMHandler
        h = HandlerFactory.create_handler("deepseek", {
            "api_key": "k", "model": "deepseek-chat",
            "tools": UserToolRegistry(default_tools=False, enabled_tools=[]),
        })
        self.assertIsInstance(h, LiteLLMHandler)
        self.assertEqual(h.model, "deepseek/deepseek-chat")

    def test_together_alias_resolves_to_canonical(self):
        r = resolver.resolve("together", "meta-llama/x")
        self.assertEqual(r.provider, "together_ai")
        self.assertEqual(r.prefixed_model, "together_ai/meta-llama/x")

    def test_prefix_not_doubled(self):
        r = resolver.resolve("mistral", "mistral/codestral-latest")
        self.assertEqual(r.prefixed_model, "mistral/codestral-latest")

    def test_anthropic_uses_anthropic_schema(self):
        out = build_provider_tools("anthropic", {"t": _Tool})
        self.assertIn("input_schema", out[0])
        self.assertNotIn("title", out[0]["input_schema"])


class NotChatReadyTests(unittest.TestCase):
    def test_legacy_providers_refused_with_reason(self):
        for name in ("azure", "bedrock", "vertex_ai", "perplexity"):
            with self.assertRaises(ValueError) as cm:
                build_provider_tools(name, {"t": _Tool})
            self.assertIn("not chat-ready", str(cm.exception), name)

    def test_unknown_provider_refused(self):
        with self.assertRaises(ValueError) as cm:
            build_provider_tools("not_a_provider", {"t": _Tool})
        self.assertIn("Unknown provider", str(cm.exception))

    def test_resolver_flags_legacy_not_ready(self):
        r = resolver.resolve("azure", "gpt-4o")
        self.assertTrue(r.supported)
        self.assertFalse(r.chat_ready)
        self.assertTrue(r.not_ready_reason)

    def test_resolver_rejects_unregistered_litellm_provider(self):
        # LiteLLM knows "replicate"; EVA has not verified it -> unsupported.
        self.assertFalse(resolver.resolve("replicate", "x").supported)

    def test_legacy_not_curated(self):
        names = {s.name for s in registry.curated_specs()}
        for gone in ("azure", "bedrock", "cohere", "perplexity", "vertex_ai"):
            self.assertNotIn(gone, names)

    def test_legacy_keys_still_manageable(self):
        # A stored Azure key must stay listable/deletable.
        self.assertIn("azure", PROVIDER_KEY_MAP)


class ConfiguredTests(unittest.TestCase):
    def test_unknown_provider_is_never_configured(self):
        self.assertFalse(is_provider_key_configured("not_a_provider"))

    def test_ollama_always_configured(self):
        self.assertTrue(is_provider_key_configured("ollama"))

    def test_gateway_configured_via_credential_store(self):
        from providers import credentials
        self.assertFalse(is_provider_key_configured("deepseek"))
        credentials.set_api_key("deepseek", "sk-x")
        self.assertTrue(is_provider_key_configured("deepseek"))
        credentials.delete_api_key("deepseek")
        self.assertFalse(is_provider_key_configured("deepseek"))

    def test_env_var_alone_does_not_configure(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-x"}):
            self.assertFalse(is_provider_key_configured("deepseek"))

    def test_alias_configured(self):
        from providers import credentials
        credentials.set_api_key("together_ai", "k")
        self.assertTrue(is_provider_key_configured("together"))


class ModelListingTests(unittest.TestCase):
    def test_listing_ids_are_bare_and_chat_only(self):
        models = resolver._list_from_litellm_sdk("mistral")
        self.assertTrue(models)
        for m in models:
            self.assertFalse(m.model_id.startswith("mistral/"), m.model_id)

    def test_unregistered_provider_lists_nothing(self):
        self.assertEqual(resolver._list_from_litellm_sdk("replicate"), [])

    def test_usability_flags(self):
        from api.providers import _model_usability
        tools, usable = _model_usability("deepseek", "deepseek-chat")
        self.assertTrue(usable)
        self.assertTrue(tools)
        # unknown model -> unknown tools, still usable (do not hide new models)
        self.assertEqual(_model_usability("deepseek", "brand-new-model-xyz"), (None, True))


if __name__ == "__main__":
    unittest.main()
