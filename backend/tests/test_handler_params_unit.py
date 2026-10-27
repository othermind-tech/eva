# tests/test_handler_params_unit.py
"""
Unit tests for V6.4 max_tokens/temperature support across all 6 provider
handlers -- mocked SDK clients, no real API calls, no live server.

Strategy: mock each handler's underlying SDK call (create/chat/acompletion)
to capture the kwargs it was invoked with, then drain the handler's
create_message() generator. Response-shape parsing after the call may raise
(the mock doesn't return a realistic response object) -- that's fine, each
handler's own try/except converts it to an ErrorChunk, which we ignore.
Only the captured REQUEST kwargs are asserted on.

Run with:
    cd <project_root>
    python tests/test_handler_params_unit.py
    (or: pytest tests/test_handler_params_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _drain(agen):
    """Consume an async generator fully, ignoring whatever it yields."""
    async for _ in agen:
        pass


class TestGroqHandlerParams(unittest.IsolatedAsyncioTestCase):
    def _make_handler(self, model="llama-3.3-70b-versatile", **extra_config):
        from providers.handlers.groq_handler import GroqHandler
        with patch("providers.handlers.groq_handler.AsyncGroq"):
            handler = GroqHandler({"api_key": "fake", "model": model, **extra_config})
        return handler

    async def test_default_no_params_omits_both(self):
        handler = self._make_handler()
        mock_create = AsyncMock(side_effect=RuntimeError("stop after capturing call"))
        handler.client.chat.completions.create = mock_create
        await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_create.call_args.kwargs
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("max_completion_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)

    async def test_max_tokens_and_temperature_forwarded(self):
        handler = self._make_handler(max_tokens=200, temperature=0.0)
        mock_create = AsyncMock(side_effect=RuntimeError("stop"))
        handler.client.chat.completions.create = mock_create
        await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_create.call_args.kwargs
        # Groq's current preferred name
        self.assertEqual(kwargs.get("max_completion_tokens"), 200)
        self.assertNotIn("max_tokens", kwargs)
        self.assertEqual(kwargs.get("temperature"), 0.0)


class TestOpenAIHandlerParams(unittest.IsolatedAsyncioTestCase):
    def _make_handler(self, model="gpt-4o", **extra_config):
        from providers.handlers.openai_handler import OpenAIHandler
        with patch("providers.handlers.openai_handler.AsyncOpenAI"):
            handler = OpenAIHandler({"api_key": "fake", "model": model, **extra_config})
        return handler

    async def test_default_no_params_omits_both(self):
        handler = self._make_handler()
        mock_create = AsyncMock(side_effect=RuntimeError("stop"))
        handler.client.chat.completions.create = mock_create
        await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_create.call_args.kwargs
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("max_completion_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)

    async def test_standard_model_uses_max_tokens(self):
        handler = self._make_handler(model="gpt-4o", max_tokens=200, temperature=0.0)
        mock_create = AsyncMock(side_effect=RuntimeError("stop"))
        handler.client.chat.completions.create = mock_create
        await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs.get("max_tokens"), 200)
        self.assertEqual(kwargs.get("temperature"), 0.0)

    async def test_reasoning_model_uses_max_completion_tokens_and_omits_temperature(self):
        handler = self._make_handler(model="o1", max_tokens=200, temperature=0.0)
        mock_create = AsyncMock(side_effect=RuntimeError("stop"))
        handler.client.chat.completions.create = mock_create
        await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs.get("max_completion_tokens"), 200)
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn(
            "temperature", kwargs,
            "o1 should not receive a custom temperature — capability check must omit it",
        )


class TestAnthropicHandlerParams(unittest.IsolatedAsyncioTestCase):
    def _make_handler(self, model="claude-3-5-sonnet-20241022", **extra_config):
        from providers.handlers.anthropic_handler import AnthropicHandler
        handler = AnthropicHandler({"api_key": "fake", "model": model, **extra_config})
        return handler

    async def test_default_max_tokens_is_the_constructor_default(self):
        """max_tokens is REQUIRED by the Anthropic API -- must always be
        present, even with no explicit config, unlike every other handler."""
        handler = self._make_handler()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.messages.create.call_args.kwargs
        self.assertIn("max_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)

    async def test_explicit_max_tokens_overrides_default(self):
        handler = self._make_handler(max_tokens=200)
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.messages.create.call_args.kwargs
        self.assertEqual(kwargs.get("max_tokens"), 200)

    async def test_older_model_gets_requested_temperature(self):
        handler = self._make_handler(model="claude-3-5-sonnet-20241022", temperature=0.0)
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.messages.create.call_args.kwargs
        self.assertEqual(kwargs.get("temperature"), 0.0)

    async def test_opus_4_8_temperature_silently_omitted(self):
        """The core regression this whole module exists to prevent."""
        handler = self._make_handler(model="claude-opus-4-8", temperature=0.0)
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.messages.create.call_args.kwargs
        self.assertNotIn(
            "temperature", kwargs,
            "Opus 4.8 would 400-error if temperature were sent — must be omitted",
        )
        # max_tokens is unaffected by the temperature restriction
        self.assertIn("max_tokens", kwargs)

    async def test_token_limit_param_is_never_max_completion_tokens(self):
        """Anthropic's native API has no such parameter at all."""
        handler = self._make_handler(model="claude-opus-4-8", max_tokens=200)
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.messages.create.call_args.kwargs
        self.assertNotIn("max_completion_tokens", kwargs)
        self.assertEqual(kwargs.get("max_tokens"), 200)

    async def test_system_prompt_gets_cache_control_breakpoint(self):
        """V6.8: the system prompt is re-sent unchanged on every turn of a
        single execution's ReAct tool-calling loop (core/agent_engine.py).
        Structuring it as a one-block content list with a cache_control
        marker lets Anthropic reuse the cached prefix across those turns
        instead of reprocessing it at full token cost every time."""
        handler = self._make_handler(model="claude-opus-4-8")
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message(
                "You are EVA, a helpful agent.",
                [{"role": "user", "content": "hi"}],
                stream=False,
            ))
        kwargs = mock_client.messages.create.call_args.kwargs
        system = kwargs.get("system")
        self.assertIsInstance(system, list, "system must be a content-block list, not a bare string, for cache_control to attach")
        self.assertEqual(len(system), 1)
        block = system[0]
        self.assertEqual(block.get("type"), "text")
        self.assertEqual(block.get("text"), "You are EVA, a helpful agent.")
        self.assertEqual(block.get("cache_control"), {"type": "ephemeral"})

    async def test_empty_system_prompt_omits_system_key_entirely(self):
        """Guard against wrapping an empty/falsy system_prompt in a
        cache_control block -- Anthropic's API rejects an empty system
        block, and there's nothing worth caching anyway."""
        handler = self._make_handler(model="claude-opus-4-8")
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.messages.create.call_args.kwargs
        self.assertNotIn("system", kwargs)


class TestOllamaHandlerParams(unittest.IsolatedAsyncioTestCase):
    def _make_handler(self, **extra_config):
        from providers.handlers.ollama_handler import OllamaHandler
        return OllamaHandler({"api_key": "", "model": "llama3.2", **extra_config})

    async def test_default_no_params_sends_no_options(self):
        handler = self._make_handler()
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.chat.call_args.kwargs
        self.assertIsNone(kwargs.get("options"))

    async def test_params_nested_under_options_not_top_level(self):
        handler = self._make_handler(max_tokens=200, temperature=0.0)
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.chat.call_args.kwargs
        self.assertNotIn("max_tokens", kwargs, "must not be a top-level key for Ollama")
        self.assertNotIn("temperature", kwargs, "must not be a top-level key for Ollama")
        options = kwargs.get("options")
        self.assertIsNotNone(options)
        self.assertEqual(options.get("num_predict"), 200)
        self.assertEqual(options.get("temperature"), 0.0)


class TestOllamaCloudHandlerParams(unittest.IsolatedAsyncioTestCase):
    def _make_handler(self, **extra_config):
        from providers.handlers.ollama_cloud_handler import OllamaCloudHandler
        return OllamaCloudHandler({"api_key": "fake", "model": "gpt-oss:20b", **extra_config})

    async def test_default_no_params_omits_both(self):
        handler = self._make_handler()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)

    async def test_params_forwarded_top_level_openai_style(self):
        handler = self._make_handler(max_tokens=200, temperature=0.0)
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("stop"))
        with patch.object(handler, "_get_client", return_value=mock_client):
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs.get("max_tokens"), 200)
        self.assertEqual(kwargs.get("temperature"), 0.0)


class TestOllamaCloudHandlerReasoningExtraction(unittest.IsolatedAsyncioTestCase):
    """
    Regression coverage for the bug found investigating why VerifierEngine/
    IntentRouter/PlannerEngine could get an empty response from gpt-oss:20b
    on Ollama Cloud: the handler never read the structured `message.reasoning`
    / `delta.reasoning` field gpt-oss actually returns its chain-of-thought
    in -- only <think>-tag parsing (a different model's convention) existed.
    Confirmed live against the real API before fixing; these tests lock in
    the fix with mocked SDK response objects (SimpleNamespace, not MagicMock,
    so attribute absence/None is exact -- a MagicMock's default child-mock
    attributes would defeat the `getattr(msg, "reasoning", None)` checks
    being tested here).
    """

    def _make_handler(self, **extra_config):
        from providers.handlers.ollama_cloud_handler import OllamaCloudHandler
        return OllamaCloudHandler({"api_key": "fake", "model": "gpt-oss:20b", **extra_config})

    async def test_non_streaming_reasoning_field_yields_reasoning_chunk(self):
        from types import SimpleNamespace
        from core.types import ReasoningChunk, TextChunk

        handler = self._make_handler()
        msg = SimpleNamespace(
            content="", reasoning="The user wants X, so the answer is Y.",
            tool_calls=None,
        )
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        response = SimpleNamespace(choices=[choice], usage=None)
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        chunks = []
        with patch.object(handler, "_get_client", return_value=mock_client):
            async for c in handler.create_message(
                "", [{"role": "user", "content": "hi"}], stream=False, reasoning="medium",
            ):
                chunks.append(c)

        reasoning_chunks = [c for c in chunks if isinstance(c, ReasoningChunk)]
        self.assertEqual(len(reasoning_chunks), 1)
        self.assertEqual(reasoning_chunks[0].content, "The user wants X, so the answer is Y.")
        text_chunks = [c for c in chunks if isinstance(c, TextChunk)]
        self.assertEqual(text_chunks[0].content, "")

    async def test_non_streaming_reasoning_suppressed_when_disabled(self):
        from types import SimpleNamespace
        from core.types import ReasoningChunk

        handler = self._make_handler()
        msg = SimpleNamespace(content="", reasoning="hidden thought", tool_calls=None)
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        response = SimpleNamespace(choices=[choice], usage=None)
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        chunks = []
        with patch.object(handler, "_get_client", return_value=mock_client):
            async for c in handler.create_message(
                "", [{"role": "user", "content": "hi"}], stream=False, reasoning=None,
            ):
                chunks.append(c)

        # Model still reasoned internally (the field is present), but the
        # caller asked for reasoning off -- must not surface it.
        self.assertEqual([c for c in chunks if isinstance(c, ReasoningChunk)], [])

    async def test_non_streaming_think_tags_still_work(self):
        """Regression guard: the pre-existing <think> tag convention (a
        different model's shape, e.g. qwen3) must keep working unchanged."""
        from types import SimpleNamespace
        from core.types import ReasoningChunk, TextChunk

        handler = self._make_handler()
        msg = SimpleNamespace(
            content="<think>pondering</think>final answer",
            reasoning=None,  # this model doesn't use the structured field
            tool_calls=None,
        )
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        response = SimpleNamespace(choices=[choice], usage=None)
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        chunks = []
        with patch.object(handler, "_get_client", return_value=mock_client):
            async for c in handler.create_message(
                "", [{"role": "user", "content": "hi"}], stream=False, reasoning="medium",
            ):
                chunks.append(c)

        reasoning_chunks = [c for c in chunks if isinstance(c, ReasoningChunk)]
        self.assertEqual(len(reasoning_chunks), 1)
        self.assertEqual(reasoning_chunks[0].content, "pondering")
        text_chunks = [c for c in chunks if isinstance(c, TextChunk)]
        self.assertEqual("".join(t.content for t in text_chunks), "final answer")

    async def test_streaming_reasoning_field_yields_reasoning_chunk(self):
        from types import SimpleNamespace
        from core.types import ReasoningChunk

        handler = self._make_handler()

        delta1 = SimpleNamespace(content=None, reasoning="thinking...", tool_calls=None)
        choice1 = SimpleNamespace(delta=delta1, finish_reason=None)
        chunk1 = SimpleNamespace(choices=[choice1], usage=None)

        delta2 = SimpleNamespace(content="answer", reasoning=None, tool_calls=None)
        choice2 = SimpleNamespace(delta=delta2, finish_reason="stop")
        chunk2 = SimpleNamespace(choices=[choice2], usage=None)

        async def _fake_stream():
            for c in (chunk1, chunk2):
                yield c

        class _CtxStream:
            async def __aenter__(self):
                return _fake_stream()
            async def __aexit__(self, *a):
                return False

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_CtxStream())

        chunks = []
        with patch.object(handler, "_get_client", return_value=mock_client):
            async for c in handler.create_message(
                "", [{"role": "user", "content": "hi"}], stream=True, reasoning="medium",
            ):
                chunks.append(c)

        reasoning_chunks = [c for c in chunks if isinstance(c, ReasoningChunk)]
        self.assertEqual(len(reasoning_chunks), 1)
        self.assertEqual(reasoning_chunks[0].content, "thinking...")


class TestLiteLLMHandlerParams(unittest.IsolatedAsyncioTestCase):
    def _make_handler(self, model="cohere/command-r", **extra_config):
        from providers.handlers.litellm_handler import LiteLLMHandler
        return LiteLLMHandler({"api_key": "fake", "model": model, **extra_config})

    async def test_default_no_params_omits_both_but_still_sets_drop_params(self):
        handler = self._make_handler()
        with patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("stop"))) as mock_acompletion:
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_acompletion.call_args.kwargs
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("max_completion_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)
        self.assertTrue(kwargs.get("drop_params"), "drop_params safety net must always be set")

    async def test_params_forwarded_using_litellm_metadata(self):
        handler = self._make_handler(model="cohere/command-r", max_tokens=200, temperature=0.0)
        with patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("stop"))) as mock_acompletion:
            await _drain(handler.create_message("", [{"role": "user", "content": "hi"}], stream=False))
        kwargs = mock_acompletion.call_args.kwargs
        self.assertIn(kwargs.get(handler._token_limit_param), (200,))
        self.assertTrue(kwargs.get("drop_params"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
