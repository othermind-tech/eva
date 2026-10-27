"""
V6.59 Phase 6 -- token usage and cost accounting.

Before this phase the numbers EVA stored were wrong in several independent ways;
each is pinned here:
  * streamed OpenAI / LiteLLM calls reported no usage (the usage-only final chunk
    has no `choices` and was skipped; OpenAI also needs stream_options);
  * reasoning tokens were read from the wrong attribute -> always None;
  * cached prompt tokens were never read, so cache savings were invisible;
  * Anthropic's input count excludes cache reads/writes, undercounting a warm
    cache to a sliver of the real input;
  * a multi-step run stored only the LAST model call's usage;
  * cost was priced on the session's primary instead of the model that served;
  * total_tokens added reasoning on top of completion (it is a subset of it).
Shapes below match what the real APIs returned in live probes (Groq, Ollama Cloud).
"""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.types import TextChunk, UsageChunk
from providers import credentials
from providers.usage import make_anthropic_usage_chunk, make_usage_chunk, usage_details


def _usage(prompt=73, completion=25, reasoning=None, cached=None, **extra):
    """OpenAI-shaped usage object (nested *_details, as returned by the real APIs)."""
    return NS(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion,
        completion_tokens_details=NS(reasoning_tokens=reasoning) if reasoning is not None else None,
        prompt_tokens_details=NS(cached_tokens=cached) if cached is not None else None,
        **extra,
    )


async def _collect(agen):
    return [c async for c in agen]


class ExtractionTests(unittest.TestCase):
    def test_reasoning_and_cached_from_nested_details(self):
        self.assertEqual(usage_details(_usage(reasoning=21, cached=68)), (21, 68))

    def test_groq_shape_reasoning_is_a_subset_of_completion(self):
        # live Groq response: completion 25, reasoning 15
        c = make_usage_chunk(_usage(prompt=73, completion=25, reasoning=15), provider="groq", model="m")
        self.assertEqual((c.prompt_tokens, c.completion_tokens, c.reasoning_tokens, c.total_tokens), (73, 25, 15, 98))

    def test_ollama_cloud_shape_cached(self):
        # live Ollama Cloud response: 68 of 69 prompt tokens cached
        c = make_usage_chunk(_usage(prompt=69, completion=43, cached=68), provider="ollama_cloud", model="m")
        self.assertEqual((c.prompt_tokens, c.cached_tokens), (69, 68))
        self.assertIsNone(c.reasoning_tokens)

    def test_missing_details_are_none_not_zero(self):
        self.assertEqual(usage_details(_usage()), (None, None))
        self.assertEqual(usage_details(NS(prompt_tokens=1, completion_tokens=1)), (None, None))

    def test_dict_shaped_usage(self):
        u = {"prompt_tokens": 10, "completion_tokens": 5,
             "completion_tokens_details": {"reasoning_tokens": 3}, "prompt_tokens_details": {"cached_tokens": 8}}
        self.assertEqual(usage_details(u), (3, 8))
        self.assertEqual(make_usage_chunk(u, provider="p", model="m").total_tokens, 15)

    def test_deepseek_native_cache_hit_field(self):
        self.assertEqual(usage_details(NS(prompt_cache_hit_tokens=40))[1], 40)

    def test_old_top_level_reasoning_field_still_read(self):
        self.assertEqual(usage_details(NS(reasoning_tokens=7))[0], 7)

    def test_booleans_and_junk_ignored(self):
        self.assertEqual(usage_details(NS(prompt_tokens_details=NS(cached_tokens=True))), (None, None))
        self.assertEqual(usage_details(None), (None, None))

    def test_anthropic_input_includes_cache_reads_and_writes(self):
        # warm cache: only 12 uncached input tokens, 4000 read from cache, 300 written
        c = make_anthropic_usage_chunk(input_tokens=12, output_tokens=50, cache_read_tokens=4000,
                                       cache_creation_tokens=300, provider="anthropic", model="m")
        self.assertEqual(c.prompt_tokens, 4312)
        self.assertEqual(c.cached_tokens, 4000)         # writes are not a discount
        self.assertEqual(c.total_tokens, 4362)

    def test_anthropic_without_caching(self):
        c = make_anthropic_usage_chunk(input_tokens=100, output_tokens=10, provider="anthropic", model="m")
        self.assertEqual((c.prompt_tokens, c.cached_tokens, c.total_tokens), (100, 0, 110))


class AggregationTests(unittest.IsolatedAsyncioTestCase):
    async def _agg(self, chunks):
        from core.stream_buffer import BufferedStream

        async def gen():
            for c in chunks:
                yield c
        b = BufferedStream(gen())
        async for _ in b.stream_and_buffer():
            pass
        return await b.aggregated()

    async def test_multi_step_usage_is_summed_last_call_kept_separately(self):
        chunks = [
            UsageChunk(prompt_tokens=100, completion_tokens=10, reasoning_tokens=4, cached_tokens=0, total_tokens=110, provider="groq", model="a"),
            TextChunk(content="x"),
            UsageChunk(prompt_tokens=150, completion_tokens=20, reasoning_tokens=6, cached_tokens=100, total_tokens=170, provider="groq", model="a"),
            UsageChunk(prompt_tokens=200, completion_tokens=30, reasoning_tokens=None, cached_tokens=160, total_tokens=230, provider="ollama_cloud", model="b"),
        ]
        final = await self._agg(chunks)
        t = final["usage_total"]
        self.assertEqual((t["prompt_tokens"], t["completion_tokens"], t["total_tokens"]), (450, 60, 510))
        self.assertEqual((t["reasoning_tokens"], t["cached_tokens"]), (10, 260))
        self.assertEqual((t["provider"], t["model"], t["calls"]), ("ollama_cloud", "b", 3))
        # the per-message figure is unchanged: the last call only
        self.assertEqual((final["usage"]["prompt_tokens"], final["usage"]["model"]), (200, "b"))

    async def test_no_usage_means_none(self):
        final = await self._agg([TextChunk(content="hi")])
        self.assertIsNone(final["usage_total"])
        self.assertIsNone(final["usage"])

    async def test_single_call_total_equals_the_call(self):
        final = await self._agg([UsageChunk(prompt_tokens=7, completion_tokens=3, total_tokens=10, provider="p", model="m")])
        self.assertEqual(final["usage_total"]["prompt_tokens"], 7)
        self.assertEqual(final["usage_total"]["calls"], 1)


# ---------------------------------------------------------------------------
# Handlers, driven through fake provider clients
# ---------------------------------------------------------------------------

def _chunk(content=None, finish=None, usage=None, with_choice=True):
    choices = []
    if with_choice:
        choices = [NS(delta=NS(content=content, tool_calls=None, reasoning_content=None, reasoning=None, role=None),
                      finish_reason=finish)]
    return NS(choices=choices, usage=usage)


class _AsyncStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _tools():
    from core.tools.user_registry import UserToolRegistry
    return UserToolRegistry(default_tools=False, enabled_tools=[])


class OpenAIHandlerUsageTests(unittest.IsolatedAsyncioTestCase):
    def _handler(self, chunks):
        credentials.set_api_key("openai", "sk-test")
        from core.factory import HandlerFactory
        h = HandlerFactory.create_handler("openai", {"api_key": None, "model": "gpt-4o", "tools": _tools()})
        create = AsyncMock(return_value=_AsyncStream(chunks))
        h.client = MagicMock()
        h.client.chat.completions.create = create
        return h, create

    async def test_streamed_usage_only_final_chunk_is_reported_and_requested(self):
        chunks = [_chunk("Hel"), _chunk("lo", finish="stop"),
                  _chunk(usage=_usage(prompt=69, completion=43, reasoning=30, cached=68), with_choice=False)]
        h, create = self._handler(chunks)
        out = await _collect(h.create_message("sys", [{"role": "user", "content": "hi"}], stream=True))
        usage = [c for c in out if isinstance(c, UsageChunk)]
        self.assertEqual(len(usage), 1)
        self.assertEqual((usage[0].prompt_tokens, usage[0].reasoning_tokens, usage[0].cached_tokens), (69, 30, 68))
        self.assertEqual(create.await_args.kwargs.get("stream_options"), {"include_usage": True})

    async def test_usage_emitted_once_even_if_repeated(self):
        u = _usage(prompt=10, completion=5)
        chunks = [_chunk("a", finish="stop", usage=u), _chunk(usage=u, with_choice=False)]
        h, _ = self._handler(chunks)
        out = await _collect(h.create_message("", [{"role": "user", "content": "x"}], stream=True))
        self.assertEqual(len([c for c in out if isinstance(c, UsageChunk)]), 1)

    async def test_non_streaming_reports_reasoning_and_cached(self):
        credentials.set_api_key("openai", "sk-test")
        from core.factory import HandlerFactory
        h = HandlerFactory.create_handler("openai", {"api_key": None, "model": "gpt-4o", "tools": _tools()})
        resp = NS(choices=[NS(message=NS(content="ok", tool_calls=None, reasoning=None), finish_reason="stop")],
                  usage=_usage(prompt=20, completion=8, reasoning=5, cached=16))
        h.client = MagicMock()
        h.client.chat.completions.create = AsyncMock(return_value=resp)
        out = await _collect(h.create_message("", [{"role": "user", "content": "x"}], stream=False))
        u = [c for c in out if isinstance(c, UsageChunk)][0]
        self.assertEqual((u.reasoning_tokens, u.cached_tokens), (5, 16))
        self.assertNotIn("stream_options", h.client.chat.completions.create.await_args.kwargs)


class GroqHandlerUsageTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, chunks):
        credentials.set_api_key("groq", "gsk-test")
        from core.factory import HandlerFactory
        h = HandlerFactory.create_handler("groq", {"api_key": None, "model": "openai/gpt-oss-20b", "tools": _tools()})
        h.client = MagicMock()
        h.client.chat.completions.create = AsyncMock(return_value=_AsyncStream(chunks))
        out = await _collect(h.create_message("", [{"role": "user", "content": "x"}], stream=True))
        return out, h.client.chat.completions.create.await_args.kwargs

    async def test_final_chunk_usage_reports_reasoning_and_is_not_duplicated(self):
        # live Groq: usage rides the final chunk WITH choices
        chunks = [_chunk("OK"), _chunk(finish="stop", usage=_usage(prompt=73, completion=31, reasoning=21))]
        out, kwargs = await self._run(chunks)
        usage = [c for c in out if isinstance(c, UsageChunk)]
        self.assertEqual(len(usage), 1)
        self.assertEqual((usage[0].completion_tokens, usage[0].reasoning_tokens), (31, 21))
        # asking Groq for include_usage as well would add a duplicate chunk
        self.assertNotIn("stream_options", str(kwargs))      # not top-level, not via extra_body either

    async def test_duplicate_usage_chunk_does_not_double_count(self):
        u = _usage(prompt=73, completion=25, reasoning=15)
        chunks = [_chunk("OK"), _chunk(finish="stop", usage=u), _chunk(usage=u, with_choice=False)]
        out, _ = await self._run(chunks)
        self.assertEqual(len([c for c in out if isinstance(c, UsageChunk)]), 1)


class LiteLLMHandlerUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_usage_only_chunk_reported_and_requested(self):
        credentials.set_api_key("deepseek", "ds-test")
        from core.factory import HandlerFactory
        h = HandlerFactory.create_handler("deepseek", {"api_key": None, "model": "deepseek-chat", "tools": _tools()})
        chunks = [_chunk("Hi"), _chunk(finish="stop"),
                  _chunk(usage=_usage(prompt=50, completion=12, reasoning=0, cached=40), with_choice=False)]
        acompletion = AsyncMock(return_value=_AsyncStream(chunks))
        with patch("providers.handlers.litellm_handler.litellm.acompletion", acompletion):
            out = await _collect(h.create_message("", [{"role": "user", "content": "x"}], stream=True))
        usage = [c for c in out if isinstance(c, UsageChunk)]
        self.assertEqual(len(usage), 1)
        self.assertEqual((usage[0].prompt_tokens, usage[0].cached_tokens, usage[0].provider), (50, 40, "litellm"))
        self.assertEqual(acompletion.await_args.kwargs.get("stream_options"), {"include_usage": True})
        self.assertTrue(acompletion.await_args.kwargs.get("drop_params"))     # unsupported providers drop it, no error

    async def test_non_streaming_has_no_stream_options(self):
        credentials.set_api_key("deepseek", "ds-test")
        from core.factory import HandlerFactory
        h = HandlerFactory.create_handler("deepseek", {"api_key": None, "model": "deepseek-chat", "tools": _tools()})
        resp = NS(choices=[NS(message=NS(content="ok", tool_calls=None, reasoning_content=None), finish_reason="stop")],
                  usage=_usage(prompt=5, completion=2, cached=4))
        acompletion = AsyncMock(return_value=resp)
        with patch("providers.handlers.litellm_handler.litellm.acompletion", acompletion):
            out = await _collect(h.create_message("", [{"role": "user", "content": "x"}], stream=False))
        self.assertEqual([c for c in out if isinstance(c, UsageChunk)][0].cached_tokens, 4)
        self.assertNotIn("stream_options", acompletion.await_args.kwargs)


class OllamaCloudHandlerUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_only_chunk_carries_cached_tokens(self):
        credentials.set_api_key("ollama_cloud", "oc-test")
        from core.factory import HandlerFactory
        h = HandlerFactory.create_handler("ollama_cloud", {"api_key": None, "model": "gpt-oss:20b", "tools": _tools()})
        chunks = [_chunk("OK", finish="stop"), _chunk(usage=_usage(prompt=69, completion=43, cached=68), with_choice=False)]
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=_AsyncStream(chunks))
        with patch.object(h, "_get_client", return_value=client):
            out = await _collect(h.create_message("", [{"role": "user", "content": "x"}], stream=True))
        usage = [c for c in out if isinstance(c, UsageChunk)]
        self.assertEqual(len(usage), 1)
        self.assertEqual((usage[0].prompt_tokens, usage[0].cached_tokens), (69, 68))


class AnthropicHandlerUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_usage_includes_cache_reads_and_writes(self):
        credentials.set_api_key("anthropic", "ant-test")
        from core.factory import HandlerFactory
        h = HandlerFactory.create_handler("anthropic", {"api_key": None, "model": "claude-haiku-4-5", "tools": _tools()})
        events = [
            NS(type="message_start", message=NS(usage=NS(input_tokens=12, cache_read_input_tokens=4000,
                                                          cache_creation_input_tokens=300))),
            NS(type="message_delta", delta=NS(stop_reason="end_turn"), usage=NS(output_tokens=50)),
            NS(type="message_stop"),
        ]
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=_AsyncStream(events))
        with patch.object(h, "_get_client", return_value=client):
            out = await _collect(h.create_message("", [{"role": "user", "content": "x"}], stream=True))
        u = [c for c in out if isinstance(c, UsageChunk)][0]
        self.assertEqual((u.prompt_tokens, u.cached_tokens, u.completion_tokens, u.total_tokens), (4312, 4000, 50, 4362))

    async def test_non_streaming_usage_includes_cache(self):
        credentials.set_api_key("anthropic", "ant-test")
        from core.factory import HandlerFactory
        h = HandlerFactory.create_handler("anthropic", {"api_key": None, "model": "claude-haiku-4-5", "tools": _tools()})
        resp = NS(usage=NS(input_tokens=5, output_tokens=9, cache_read_input_tokens=100, cache_creation_input_tokens=0),
                  stop_reason="end_turn", content=[NS(type="text", text="ok")])
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=resp)
        with patch.object(h, "_get_client", return_value=client):
            out = await _collect(h.create_message("", [{"role": "user", "content": "x"}], stream=False))
        u = [c for c in out if isinstance(c, UsageChunk)][0]
        self.assertEqual((u.prompt_tokens, u.cached_tokens), (105, 100))


# ---------------------------------------------------------------------------
# Cost and totals
# ---------------------------------------------------------------------------

class CostTests(unittest.TestCase):
    def setUp(self):
        from core.providers.resolver import resolver
        self.cost = resolver.compute_call_cost

    def test_cached_tokens_lower_the_cost(self):
        for provider, model in (("openai", "gpt-4o"), ("anthropic", "claude-haiku-4-5")):
            plain = self.cost(provider, model, 10000, 100)
            cached = self.cost(provider, model, 10000, 100, cached_tokens=8000)
            self.assertIsNotNone(plain, provider)
            self.assertLess(cached, plain, provider)

    def test_no_cached_tokens_is_unchanged(self):
        self.assertEqual(self.cost("openai", "gpt-4o", 1000, 50), self.cost("openai", "gpt-4o", 1000, 50, cached_tokens=0))

    def test_cached_cannot_exceed_prompt(self):
        self.assertEqual(self.cost("openai", "gpt-4o", 100, 10, cached_tokens=100),
                         self.cost("openai", "gpt-4o", 100, 10, cached_tokens=5000))


class CostLookupTests(unittest.TestCase):
    """Groq executions never had a cost: LiteLLM keys Groq models as 'groq/<model>'
    but the lookup used only the bare name (55 stored Groq runs, 0 priced)."""

    def setUp(self):
        from core.providers.resolver import resolver
        self.r = resolver

    def test_groq_model_is_priced(self):
        cost = self.r.compute_call_cost("groq", "openai/gpt-oss-20b", 3420, 24)
        self.assertIsNotNone(cost)
        self.assertGreater(cost, 0)

    def test_groq_cache_discount_applies(self):
        full = self.r.compute_call_cost("groq", "openai/gpt-oss-20b", 3420, 24)
        cached = self.r.compute_call_cost("groq", "openai/gpt-oss-20b", 3420, 24, cached_tokens=3000)
        self.assertLess(cached, full)

    def test_bare_keyed_provider_still_priced(self):
        self.assertIsNotNone(self.r.compute_call_cost("anthropic", "claude-haiku-4-5", 1000, 100))

    def test_unpriced_models_are_none_never_fabricated(self):
        self.assertIsNone(self.r.compute_call_cost("groq", "not-a-real-model", 1000, 100))
        self.assertIsNone(self.r.compute_call_cost("ollama_cloud", "gpt-oss:20b", 1000, 100))

    def test_local_is_free(self):
        self.assertEqual(self.r.compute_call_cost("ollama", "llama3", 1000, 100), 0.0)

    def test_model_listing_price_uses_the_same_lookup(self):
        info = self.r.get_model_cost("groq", "openai/gpt-oss-20b")
        self.assertIsNotNone(info["input_cost_per_token"])
        self.assertIsNotNone(info["output_cost_per_token"])

    def test_litellm_key_order(self):
        self.assertEqual(self.r.litellm_key("groq", "openai/gpt-oss-20b"), "groq/openai/gpt-oss-20b")
        self.assertEqual(self.r.litellm_key("anthropic", "claude-haiku-4-5"), "claude-haiku-4-5")
        self.assertIsNone(self.r.litellm_key("groq", "nope"))


class TotalsConventionTests(unittest.TestCase):
    """reasoning_tokens is a SUBSET of completion_tokens; total = prompt + completion."""

    def test_no_total_adds_reasoning_on_top(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        repo = (root / "core/db/repository.py").read_text(encoding="utf-8")
        self.assertNotIn("pt + ct + rt", repo)
        self.assertNotIn("+ ExecutionMetrics.reasoning_tokens\n                    ),", repo)
        self.assertNotIn("pt + ct + rt", (root / "api/executions.py").read_text(encoding="utf-8"))

    def test_metrics_row_has_cached_tokens(self):
        from core.db.models import ExecutionMetrics
        self.assertIn("cached_tokens", ExecutionMetrics.__table__.columns)

    def test_finalize_prices_the_serving_model_with_cache_discount(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core/execution/worker_finalize.py").read_text(encoding="utf-8")
        self.assertIn('_provider = usage.get("provider") or session.get("provider")', src)
        self.assertIn('_model    = usage.get("model")    or session.get("model")', src)
        self.assertIn("cached_tokens=cached_tokens", src)
        self.assertIn('"cached_tokens":      cached_tokens', src)

    def test_worker_records_summed_usage(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core/execution/worker.py").read_text(encoding="utf-8")
        self.assertIn('final.get("usage_total") or final.get("usage")', src)


class MigrationTests(unittest.TestCase):
    def test_0019_follows_0018_and_is_reversible(self):
        import importlib.util
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "alembic/versions/0019_add_execution_metrics_cached_tokens.py"
        spec = importlib.util.spec_from_file_location("mig0019", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertEqual((m.revision, m.down_revision), ("0019", "0018"))
        self.assertTrue(callable(m.upgrade) and callable(m.downgrade))


if __name__ == "__main__":
    unittest.main()
