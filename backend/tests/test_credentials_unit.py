"""
V6.59 Phase 2 -- providers/credentials.py, the single provider-key resolver.

What these pin down:
  * one store: capability (listing/validation) and chat handlers resolve keys
    from the same place, with no os.environ fallback anywhere;
  * every handler is handed an explicit key -- chat no longer works "by
    accident" through an SDK reading its own environment variable;
  * a key saved through POST /secrets is live immediately and is not copied
    into the process environment.
"""
import os
import re
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from providers import credentials
from providers.credentials import MemoryBackend, ProviderKeyError

BACKEND = Path(__file__).resolve().parent.parent


class StoreTests(unittest.TestCase):
    def test_set_get_delete_roundtrip(self):
        self.assertIsNone(credentials.get_api_key("groq"))
        name = credentials.set_api_key("groq", "gsk-1")
        self.assertEqual(name, "GROQ_API_KEY")
        self.assertEqual(credentials.get_api_key("groq"), "gsk-1")
        self.assertTrue(credentials.delete_api_key("groq"))
        self.assertIsNone(credentials.get_api_key("groq"))
        self.assertFalse(credentials.delete_api_key("groq"))

    def test_write_invalidates_cache_immediately(self):
        self.assertIsNone(credentials.get_api_key("openai"))   # caches the miss
        credentials.set_api_key("openai", "sk-new")
        self.assertEqual(credentials.get_api_key("openai"), "sk-new")
        credentials.set_api_key("openai", "sk-newer")
        self.assertEqual(credentials.get_api_key("openai"), "sk-newer")

    def test_cache_expires(self):
        backend = MemoryBackend({"GROQ_API_KEY": "old"})
        credentials.set_backend(backend)
        self.assertEqual(credentials.get_api_key("groq"), "old")
        backend.set("GROQ_API_KEY", "changed-out-of-band")
        self.assertEqual(credentials.get_api_key("groq"), "old")          # cached
        real = time.monotonic
        with patch("providers.credentials.time.monotonic", lambda: real() + 3600):
            self.assertEqual(credentials.get_api_key("groq"), "changed-out-of-band")

    def test_backend_failure_means_not_configured_and_is_not_cached(self):
        class Flaky(MemoryBackend):
            fail = True

            def get(self, name):
                if self.fail:
                    raise RuntimeError("store locked")
                return super().get(name)

        flaky = Flaky({"GROQ_API_KEY": "k"})
        credentials.set_backend(flaky)
        self.assertIsNone(credentials.get_api_key("groq"))
        self.assertFalse(credentials.is_configured("groq"))
        flaky.fail = False
        self.assertEqual(credentials.get_api_key("groq"), "k")   # recovered, not stuck on the failure

    def test_keyless_and_unknown_providers(self):
        self.assertIsNone(credentials.get_api_key("ollama"))
        self.assertIsNone(credentials.get_api_key("nope"))
        with self.assertRaises(ValueError):
            credentials.set_api_key("ollama", "x")

    def test_alias_shares_entry(self):
        credentials.set_api_key("together", "tg")
        self.assertEqual(credentials.get_api_key("together_ai"), "tg")


class ResolutionTests(unittest.TestCase):
    def test_explicit_wins_over_stored(self):
        credentials.set_api_key("openai", "stored")
        self.assertEqual(credentials.resolve_api_key("openai", "explicit"), "explicit")

    def test_stored_used_when_no_explicit(self):
        credentials.set_api_key("openai", "stored")
        self.assertEqual(credentials.resolve_api_key("openai"), "stored")

    def test_environment_is_never_consulted(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "from-env", "DEEPSEEK_API_KEY": "x"}):
            self.assertEqual(credentials.resolve_api_key("openai"), "")
            self.assertFalse(credentials.is_configured("openai"))
            self.assertFalse(credentials.is_configured("deepseek"))

    def test_ollama_resolves_to_host_not_key(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://h:1"}):
            self.assertEqual(credentials.resolve_api_key("ollama"), "http://h:1")
        self.assertTrue(credentials.is_configured("ollama"))

    def test_missing_returns_empty_string_never_none(self):
        self.assertEqual(credentials.resolve_api_key("groq"), "")
        self.assertEqual(credentials.resolve_api_key("not_a_provider"), "")

    def test_require_raises_with_actionable_message(self):
        with self.assertRaises(ProviderKeyError) as cm:
            credentials.require_api_key("groq")
        self.assertIn("API Keys", str(cm.exception))
        credentials.set_api_key("groq", "k")
        self.assertEqual(credentials.require_api_key("groq"), "k")

    def test_provider_key_error_is_the_capability_one(self):
        from providers.capability.base import ProviderKeyError as FromBase
        self.assertIs(FromBase, ProviderKeyError)


class HandlersGetExplicitKeysTests(unittest.TestCase):
    """The Group-A gap: handlers must hold the stored key, not rely on SDK env fallback."""

    def _make(self, provider, model="m"):
        from core.factory import HandlerFactory
        from core.tools.user_registry import UserToolRegistry
        return HandlerFactory.create_handler(provider, {
            "api_key": None,                      # what the worker passes today
            "model": model,
            "tools": UserToolRegistry(default_tools=False, enabled_tools=[]),
        })

    def test_openai_handler(self):
        credentials.set_api_key("openai", "sk-stored")
        self.assertEqual(self._make("openai").client.api_key, "sk-stored")

    def test_groq_handler(self):
        credentials.set_api_key("groq", "gsk-stored")
        self.assertEqual(self._make("groq").client.api_key, "gsk-stored")

    def test_anthropic_handler(self):
        credentials.set_api_key("anthropic", "ant-stored")
        self.assertEqual(self._make("anthropic").api_key, "ant-stored")

    def test_ollama_cloud_handler(self):
        credentials.set_api_key("ollama_cloud", "oc-stored")
        self.assertEqual(self._make("ollama_cloud").api_key, "oc-stored")

    def test_gateway_handler(self):
        credentials.set_api_key("deepseek", "ds-stored")
        h = self._make("deepseek", "deepseek-chat")
        self.assertEqual(h.api_key, "ds-stored")
        self.assertEqual(h.model, "deepseek/deepseek-chat")

    def test_explicit_key_still_wins(self):
        from core.factory import HandlerFactory
        from core.tools.user_registry import UserToolRegistry
        credentials.set_api_key("openai", "sk-stored")
        h = HandlerFactory.create_handler("openai", {
            "api_key": "sk-explicit", "model": "m",
            "tools": UserToolRegistry(default_tools=False, enabled_tools=[]),
        })
        self.assertEqual(h.client.api_key, "sk-explicit")

    def test_environment_key_is_not_picked_up_by_sdk(self):
        """Empty store + keys in os.environ: neither SDK may end up with the
        env key (the old accidental path let each SDK read OPENAI_API_KEY /
        GROQ_API_KEY itself). OpenAI refuses to build a keyless client; Groq
        builds one with an empty key."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env", "GROQ_API_KEY": "gsk-env"}):
            with self.assertRaises(Exception):
                self._make("openai")
            self.assertEqual(self._make("groq").client.api_key, "")

    def test_missing_key_for_anthropic_fails_clearly(self):
        with self.assertRaises(ValueError) as cm:
            self._make("anthropic")
        self.assertIn("not configured", str(cm.exception))


class CapabilityModulesTests(unittest.TestCase):
    def test_each_native_module_reads_the_store(self):
        import importlib
        for prov in ("anthropic", "openai", "groq", "ollama_cloud"):
            mod = importlib.import_module(f"providers.capability.{prov}.provider_api")
            self.assertFalse(mod.is_available(), prov)
            with self.assertRaises(ProviderKeyError):
                mod._resolve_key(None)
            credentials.set_api_key(prov, f"{prov}-key")
            self.assertTrue(mod.is_available(), prov)
            self.assertEqual(mod._resolve_key(None), f"{prov}-key")
            self.assertEqual(mod._resolve_key("override"), "override")

    def test_env_var_does_not_satisfy_capability_modules(self):
        from providers.capability.groq import provider_api
        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk-env"}):
            self.assertFalse(provider_api.is_available())


class SecretsApiTests(unittest.TestCase):
    def test_write_secret_goes_to_store_not_environment(self):
        from api import secrets
        os.environ.pop("GROQ_API_KEY", None)
        name = secrets._write_secret("groq", "gsk-written")
        self.assertEqual(name, "GROQ_API_KEY")
        self.assertEqual(credentials.get_api_key("groq"), "gsk-written")
        self.assertNotIn("GROQ_API_KEY", os.environ)

    def test_alias_written_to_canonical_entry(self):
        from api import secrets
        secrets._write_secret("together", "tg")
        self.assertEqual(credentials.get_api_key("together_ai"), "tg")


class NoEnvKeyReadersTests(unittest.TestCase):
    """Source-level guard: application code must not read provider keys from
    the process environment (only the env loader / settings declarations may
    mention the names)."""

    _SCAN = ("api", "core", "providers", "tools", "utils")
    _ALLOWED = {"providers/registry.py", "providers/credentials.py"}
    _PATTERN = re.compile(r"os\.(getenv|environ)[^\n]*(_API_KEY|ENV_KEY|AWS_ACCESS_KEY_ID)")

    def test_no_provider_key_env_reads(self):
        offenders = []
        for top in self._SCAN:
            for path in (BACKEND / top).rglob("*.py"):
                rel = path.relative_to(BACKEND).as_posix()
                if rel in self._ALLOWED:
                    continue
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if self._PATTERN.search(line):
                        offenders.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual(offenders, [], "provider key read from os.environ:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
