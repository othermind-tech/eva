# tests/test_ollama_cloud_capabilities_endpoint_unit.py
"""
Follow-up to A-17 (2026-09-22): GET /providers/{provider}/models/{model_id}/capabilities
had its own pre-existing shortcut (api/providers.py, old resolution "0") that
returned a hardcoded source="unknown" response for BOTH "ollama" and
"ollama_cloud" unconditionally, before ever checking the static registry.
Once A-17 wired Ollama Cloud into that registry (capability_registry.py),
this endpoint kept reporting "unknown" for it anyway -- a display-layer
regression relative to what the registry actually knows, discovered live
while verifying A-17 against the real restarted server.

Fix: local Ollama keeps the unconditional optimistic default (the registry
genuinely never has data for it, by design). Ollama Cloud now checks the
registry FIRST, and only falls back to the same optimistic default when the
registry genuinely has nothing for that specific model -- so the reasoning
support guarantee (never falsely report unknown) is preserved either way.

Calls core.execution.planner... no -- api.providers.get_model_capabilities
directly as a plain async function (auth is a Depends() default, which plain
calls just skip -- no TestClient/HTTP layer needed to exercise the real
resolution logic).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from api.providers import get_model_capabilities
from core.providers.capability_registry import ModelCapability


class TestOllamaCloudCapabilitiesEndpoint(unittest.IsolatedAsyncioTestCase):

    async def test_local_ollama_still_gets_the_unconditional_optimistic_default(self):
        # Must NOT even consult the registry -- it's meaningless for local
        # Ollama by design (A-17 never populates it).
        with patch("api.providers.capability_registry.get_model") as mock_get:
            out = await get_model_capabilities(provider="ollama", model_id="llama3.1", auth={"user_id": "u"})
        mock_get.assert_not_called()
        self.assertEqual(out.source, "unknown")
        self.assertTrue(out.supports_reasoning)
        self.assertIsNone(out.max_context_tokens)

    async def test_ollama_cloud_with_real_registry_data_reports_it_not_unknown(self):
        cap = ModelCapability(
            provider="ollama_cloud", model="gpt-oss:120b",
            max_context_tokens=131072, max_output_tokens=131072,
            source="merged",
        )
        with patch("api.providers.capability_registry.get_model", return_value=cap):
            out = await get_model_capabilities(provider="ollama_cloud", model_id="gpt-oss:120b", auth={"user_id": "u"})
        self.assertEqual(out.source, "registry")
        self.assertEqual(out.max_context_tokens, 131072)

    async def test_ollama_cloud_with_no_registry_data_falls_back_to_the_optimistic_default(self):
        # A model LiteLLM doesn't track (yet, or ever) -- must not regress to
        # a flat supports_reasoning=None; same guarantee local Ollama gets.
        with patch("api.providers.capability_registry.get_model", return_value=None), \
             patch("api.providers.resolver") as mock_resolver:
            mock_resolver.resolve.return_value.supported = True
            out = await get_model_capabilities(provider="ollama_cloud", model_id="some-new-model", auth={"user_id": "u"})
        self.assertEqual(out.source, "unknown")
        self.assertTrue(out.supports_reasoning)
        self.assertIsNone(out.max_context_tokens)

    async def test_ollama_cloud_fallback_does_not_reach_the_generic_litellm_lookup(self):
        # The generic LiteLLM fallback (_capabilities_from_litellm) keys off
        # "{provider}/{model}", which never matches LiteLLM's "-cloud"-suffixed
        # shape for this provider -- confirm it's never even called, so a
        # mismatch there can't silently produce a different, wrong response.
        with patch("api.providers.capability_registry.get_model", return_value=None), \
             patch("api.providers._capabilities_from_litellm") as mock_ll:
            await get_model_capabilities(provider="ollama_cloud", model_id="untracked-model", auth={"user_id": "u"})
        mock_ll.assert_not_called()

    async def test_other_providers_are_unaffected(self):
        cap = ModelCapability(provider="openai", model="gpt-4o", max_context_tokens=128000, source="registry")
        with patch("api.providers.capability_registry.get_model", return_value=cap):
            out = await get_model_capabilities(provider="openai", model_id="gpt-4o", auth={"user_id": "u"})
        self.assertEqual(out.source, "registry")
        self.assertEqual(out.max_context_tokens, 128000)


if __name__ == "__main__":
    unittest.main()
