# tests/test_duplicate_tool_call_unit.py
#
# Unit tests for a bug found while investigating a live test-suite failure
# (test_apis.py section 35b, 2026-07-21): EVA's duplicate-tool-call ReAct
# loop guard (core/agent_engine.py) and progressive-disclosure auto-promotion
# (core/tools/dispatcher.py) were in direct conflict.
#
# Background: when a model calls a tool before promoting it via
# describe_tool(), dispatcher.py auto-promotes the tool and returns a stub
# result explicitly instructing the model to "call it directly on your next
# turn" -- same tool, same arguments, by design. But agent_engine.py's
# duplicate guard fingerprints every requested tool call purely as
# (tool_name, arguments), with no awareness that the first "call" was never
# actually executed (just a gating stub). The model's mandatory,
# EVA-instructed retry always collided with that fingerprint and killed the
# whole ReAct loop with a FAILED execution -- deterministically, for any
# tool discovered without describe_tool() first, not just memory_recall
# (which is where this was first observed).
#
# Fix (two files):
#   - core/tools/executor.py: execute_tool_safely() now propagates
#     ToolExecutionResult.metadata through its return dict (previously
#     dropped entirely) so the auto_promoted signal survives up to
#     agent_engine.py.
#   - core/agent_engine.py: the fingerprint is still recorded immediately
#     when a tool call is requested (preserving same-batch duplicate
#     detection), but is now explicitly undone after dispatch if the result
#     turns out to have been an auto-promotion stub -- so the model's
#     mandatory retry is not blocked, while a genuine repeat of a REAL call
#     is still caught (regression-tested below).
#
# V6.26 addendum: the guard was redesigned from cumulative ("seen N times
# anywhere in this step") to consecutive-streak ("seen N times IN A ROW,
# reset by any different call") counting, with the trigger threshold tiered
# by the tool's ToolAuthority (core/tools/base.py) instead of one global
# number. TestConsecutiveStreakAndTieredThresholds below covers the new
# behavior; the three tests above are unchanged and still pass, since none
# of their scenarios involve an intervening different call or a non-default
# authority tier.
#
# V6.50 addendum: the progressive-disclosure stub no longer exists. The dispatcher
# now promotes an undescribed tool AND executes the call (metadata.auto_activated),
# so there is no "not a real execution" call left to exempt from the guard and the
# fingerprint un-reservation in agent_engine.py was removed. Every dispatched call
# is real and counts. TestFirstCallThatActivatesAToolIsARealCall below replaces the
# old stub-exemption tests; everything else in this file is unchanged.
#
# Run with:
#     cd <project_root>
#     pytest tests/test_duplicate_tool_call_unit.py -v

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


def _sequence_stream(*factories):
    """Each *call* to the returned callable advances to the next factory
    (clamped to the last one once exhausted) -- lets a single fake handler
    simulate multiple distinct ReAct turns."""
    state = {"n": 0}

    def create_message(*args, **kwargs):
        idx = min(state["n"], len(factories) - 1)
        state["n"] += 1
        return factories[idx](*args, **kwargs)

    return create_message


def _toolcall_stream(tool_name, arguments, call_id):
    from core.types import ToolCallChunk

    async def gen(*_a, **_kw):
        yield ToolCallChunk(tool_name=tool_name, arguments=arguments, call_id=call_id)
    return gen


def _text_stream(*texts):
    from core.types import TextChunk

    async def gen(*_a, **_kw):
        for t in texts:
            yield TextChunk(content=t)
    return gen


async def _collect(agen):
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


def _build_agent():
    """Minimal AgentEngine wired to a fake provider handler + fake
    dispatcher -- no real provider client, no real tool registry."""
    from core.agent_engine import AgentEngine

    fake_handler = MagicMock()
    fake_handler.tool_classes = {}
    fake_dispatcher = MagicMock()
    fake_dispatcher.execute_tool = AsyncMock()

    with patch("core.agent_engine.create_provider_handler", return_value=fake_handler):
        agent = AgentEngine(
            provider="p", api_key="k", model="m",
            reasoning=None, dispatcher=fake_dispatcher,
        )
    return agent, fake_handler, fake_dispatcher


class TestFirstCallThatActivatesAToolIsARealCall(unittest.IsolatedAsyncioTestCase):
    """
    The first call to an undescribed tool used to return a stub the model had to
    repeat, which the guard had to exempt. It now really executes (flagged with
    metadata.auto_activated), so it is an ordinary call for duplicate counting.
    """

    async def test_one_repeat_of_an_activating_call_is_not_yet_a_duplicate(self):
        from core.types import ToolExecutionResult, ToolExecutionStatus, ErrorChunk, TextChunk

        agent, handler, dispatcher = _build_agent()

        handler.create_message = _sequence_stream(
            _toolcall_stream("memory_recall", {"query": "x", "scope": "user"}, "call_1"),
            _toolcall_stream("memory_recall", {"query": "x", "scope": "user"}, "call_2"),
            _text_stream("done"),
        )

        dispatcher.execute_tool.side_effect = [
            ToolExecutionResult(  # call_1 -- activated the tool AND ran for real
                status=ToolExecutionStatus.SUCCESS,
                output={"total_found": 0, "message": "No memories found matching 'x'"},
                metadata={"tool": "memory_recall", "call_id": "call_1", "auto_activated": True},
            ),
            ToolExecutionResult(
                status=ToolExecutionStatus.SUCCESS,
                output={"total_found": 0, "message": "No memories found matching 'x'"},
                metadata=None,
            ),
        ]

        chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        error_chunks = [c for c in chunks if isinstance(c, ErrorChunk)]
        self.assertFalse(any(c.error_type == "duplicate_tool_call" for c in error_chunks), error_chunks)
        self.assertEqual(dispatcher.execute_tool.call_count, 2)
        self.assertTrue(any(isinstance(c, TextChunk) and c.content == "done" for c in chunks))

    async def test_a_third_identical_call_is_a_duplicate_and_the_activating_call_counts(self):
        """
        The guard is not weakened: calls that genuinely executed -- including the
        first, activating one -- all count toward the consecutive-streak threshold.
        """
        from core.types import ToolExecutionResult, ToolExecutionStatus, ErrorChunk

        agent, handler, dispatcher = _build_agent()

        handler.create_message = _sequence_stream(
            _toolcall_stream("memory_recall", {"query": "x", "scope": "user"}, "call_1"),
            _toolcall_stream("memory_recall", {"query": "x", "scope": "user"}, "call_2"),
            _toolcall_stream("memory_recall", {"query": "x", "scope": "user"}, "call_3"),
            _toolcall_stream("memory_recall", {"query": "x", "scope": "user"}, "call_4"),
        )

        dispatcher.execute_tool.side_effect = [
            ToolExecutionResult(  # call_1 -- activating call, executed for real
                status=ToolExecutionStatus.SUCCESS,
                output={"total_found": 0},
                metadata={"auto_activated": True},
            ),
            ToolExecutionResult(  # call_2 -- 2nd real occurrence
                status=ToolExecutionStatus.SUCCESS,
                output={"total_found": 0},
                metadata=None,
            ),
            # call_3 is the 3rd identical call in a row: caught before dispatch.
        ]

        chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        error_chunks = [c for c in chunks if isinstance(c, ErrorChunk)]
        self.assertTrue(
            any(c.error_type == "duplicate_tool_call" for c in error_chunks),
            "a real repeat of an already-executed call must still break the loop",
        )
        self.assertEqual(
            dispatcher.execute_tool.call_count, 2,
            "the third identical call must never reach the dispatcher",
        )

    async def test_two_different_tools_are_never_confused_as_duplicates(self):
        """Sanity guard: different tool names must never collide in the
        fingerprint set regardless of auto-promotion bookkeeping."""
        from core.types import ToolExecutionResult, ToolExecutionStatus, ErrorChunk, TextChunk

        agent, handler, dispatcher = _build_agent()

        handler.create_message = _sequence_stream(
            _toolcall_stream("memory_recall", {"query": "x"}, "call_1"),
            _toolcall_stream("web_search", {"query": "x"}, "call_2"),
            _text_stream("done"),
        )

        dispatcher.execute_tool.side_effect = [
            ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, output={"a": 1}, metadata=None),
            ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, output={"b": 2}, metadata=None),
        ]

        chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        error_chunks = [c for c in chunks if isinstance(c, ErrorChunk)]
        self.assertFalse(any(c.error_type == "duplicate_tool_call" for c in error_chunks))
        self.assertEqual(dispatcher.execute_tool.call_count, 2)


class TestConsecutiveStreakAndTieredThresholds(unittest.IsolatedAsyncioTestCase):
    """V6.26 redesign: consecutive-streak counting (reset by any different
    call) and per-tool-authority threshold tiers, replacing the flat
    cumulative counter."""

    async def test_interleaved_repeats_never_trip_the_guard(self):
        """The motivating scenario: the same call repeated several times
        total, but never twice IN A ROW because a different call always
        comes in between (e.g. re-running a check after each fix). Under
        the old cumulative counter this tripped once the SAME call had
        occurred 3 times anywhere in the step; under consecutive-streak
        counting it must never trip, since no run of identical calls ever
        exceeds length 1."""
        from core.types import ToolExecutionResult, ToolExecutionStatus, ErrorChunk, TextChunk

        agent, handler, dispatcher = _build_agent()

        handler.create_message = _sequence_stream(
            _toolcall_stream("memory_recall", {"query": "x"}, "call_1"),
            _toolcall_stream("memory_recall", {"query": "y"}, "call_2"),
            _toolcall_stream("memory_recall", {"query": "x"}, "call_3"),
            _toolcall_stream("memory_recall", {"query": "y"}, "call_4"),
            _toolcall_stream("memory_recall", {"query": "x"}, "call_5"),
            _text_stream("done"),
        )
        dispatcher.execute_tool.side_effect = [
            ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, output={}, metadata=None)
            for _ in range(5)
        ]

        chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        error_chunks = [c for c in chunks if isinstance(c, ErrorChunk)]
        self.assertFalse(
            any(c.error_type == "duplicate_tool_call" for c in error_chunks),
            f"an interleaved repeat pattern must never trip the guard; got: {error_chunks}",
        )
        self.assertEqual(dispatcher.execute_tool.call_count, 5)
        self.assertTrue(any(isinstance(c, TextChunk) and c.content == "done" for c in chunks))

    async def test_destructive_tool_trips_at_the_lowest_threshold(self):
        """A DESTRUCTIVE tool gets zero tolerance -- a back-to-back repeat
        is blocked before the second call ever reaches the dispatcher."""
        from core.types import ToolExecutionResult, ToolExecutionStatus, ErrorChunk
        from core.tools.base import ToolBase, ToolAuthority

        class _DestructiveTool(ToolBase):
            name = "wipe_data"
            authority = ToolAuthority.DESTRUCTIVE

        agent, handler, dispatcher = _build_agent()
        handler.tool_classes["wipe_data"] = _DestructiveTool

        handler.create_message = _sequence_stream(
            _toolcall_stream("wipe_data", {"target": "cache"}, "call_1"),
            _toolcall_stream("wipe_data", {"target": "cache"}, "call_2"),
        )
        dispatcher.execute_tool.side_effect = [
            ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, output={}, metadata=None),
        ]

        chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        error_chunks = [c for c in chunks if isinstance(c, ErrorChunk)]
        self.assertTrue(any(c.error_type == "duplicate_tool_call" for c in error_chunks))
        self.assertEqual(
            dispatcher.execute_tool.call_count, 1,
            "the second identical DESTRUCTIVE call must be blocked before dispatch",
        )

    async def test_read_only_tool_tolerates_more_back_to_back_repeats(self):
        """A READ_ONLY tool (e.g. polling a status check) is given real
        tolerance -- four identical calls in a row must not trip the guard,
        unlike a MUTATING or DESTRUCTIVE tool at the same repeat count."""
        from core.types import ToolExecutionResult, ToolExecutionStatus, ErrorChunk, TextChunk
        from core.tools.base import ToolBase, ToolAuthority

        class _ReadOnlyTool(ToolBase):
            name = "check_status"
            authority = ToolAuthority.READ_ONLY

        agent, handler, dispatcher = _build_agent()
        handler.tool_classes["check_status"] = _ReadOnlyTool

        handler.create_message = _sequence_stream(
            _toolcall_stream("check_status", {"job": "build-1"}, "call_1"),
            _toolcall_stream("check_status", {"job": "build-1"}, "call_2"),
            _toolcall_stream("check_status", {"job": "build-1"}, "call_3"),
            _toolcall_stream("check_status", {"job": "build-1"}, "call_4"),
            _text_stream("done"),
        )
        dispatcher.execute_tool.side_effect = [
            ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, output={}, metadata=None)
            for _ in range(4)
        ]

        chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        error_chunks = [c for c in chunks if isinstance(c, ErrorChunk)]
        self.assertFalse(any(c.error_type == "duplicate_tool_call" for c in error_chunks))
        self.assertEqual(dispatcher.execute_tool.call_count, 4)

    async def test_different_argument_types_are_not_conflated(self):
        """int 1 and str "1" stringify identically but must not fingerprint
        as the same call."""
        from core.types import ToolExecutionResult, ToolExecutionStatus, ErrorChunk

        agent, handler, dispatcher = _build_agent()

        handler.create_message = _sequence_stream(
            _toolcall_stream("memory_recall", {"limit": 1}, "call_1"),
            _toolcall_stream("memory_recall", {"limit": "1"}, "call_2"),
            _text_stream("done"),
        )
        dispatcher.execute_tool.side_effect = [
            ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, output={}, metadata=None),
            ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, output={}, metadata=None),
        ]

        chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        error_chunks = [c for c in chunks if isinstance(c, ErrorChunk)]
        self.assertFalse(
            any(c.error_type == "duplicate_tool_call" for c in error_chunks),
            "an int and a string argument that stringify the same must not collide",
        )
        self.assertEqual(dispatcher.execute_tool.call_count, 2)

    async def test_fingerprint_error_fallback_does_not_merge_distinct_calls(self):
        """If fingerprinting throws (malformed arguments), the fallback
        must not merge unrelated calls into one shared bucket -- that would
        falsely block calls that were never actually duplicates of each
        other. Each failed-to-fingerprint call must be treated as unique.

        A value whose __str__ raises breaks fingerprinting, but the same
        object also can't survive core/agent_engine.py's later
        json.dumps(tc.arguments) step that builds the assistant message for
        the next turn (core/agent_engine.py ~1443) -- an unrelated,
        pre-existing requirement that tool arguments be JSON-serializable,
        nothing to do with the duplicate guard. json.dumps is patched here
        to tolerate that specific value so the test can isolate the one
        thing actually under test: the fingerprint fallback's behavior.
        """
        import json as _json_module
        from core.types import ToolExecutionResult, ToolExecutionStatus, ErrorChunk, ToolCallChunk

        class _Poison:
            def __str__(self):
                raise ValueError("cannot stringify")

        _real_dumps = _json_module.dumps

        def _tolerant_dumps(obj, *a, **kw):
            try:
                return _real_dumps(obj, *a, **kw)
            except TypeError:
                return "{}"

        agent, handler, dispatcher = _build_agent()

        def _make_bad_call(call_id):
            async def gen(*_a, **_kw):
                yield ToolCallChunk(tool_name="memory_recall", arguments={"bad": _Poison()}, call_id=call_id)
            return gen

        handler.create_message = _sequence_stream(
            _make_bad_call("call_1"),
            _make_bad_call("call_2"),
            _make_bad_call("call_3"),
            _text_stream("done"),
        )
        dispatcher.execute_tool.side_effect = [
            ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, output={}, metadata=None)
            for _ in range(3)
        ]

        with patch("core.agent_engine.json.dumps", side_effect=_tolerant_dumps):
            chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        error_chunks = [c for c in chunks if isinstance(c, ErrorChunk)]
        self.assertFalse(
            any(c.error_type == "duplicate_tool_call" for c in error_chunks),
            f"three independently-unfingerprintable calls must not be merged into one bucket; got: {error_chunks}",
        )
        self.assertEqual(dispatcher.execute_tool.call_count, 3)


class TestExecuteToolSafelyPropagatesMetadata(unittest.IsolatedAsyncioTestCase):
    """execute_tool_safely() previously dropped ToolExecutionResult.metadata
    entirely. It is carried through so flags such as auto_activated reach the
    trace and the UI."""

    async def test_metadata_present_in_success_payload(self):
        from core.tools.executor import execute_tool_safely
        from core.types import ToolExecutionResult, ToolExecutionStatus

        mock_dispatcher = MagicMock()
        mock_dispatcher.execute_tool = AsyncMock(return_value=ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            output={"total_found": 0},
            metadata={"auto_activated": True, "tool": "memory_recall"},
        ))

        payload = await execute_tool_safely(
            dispatcher=mock_dispatcher, call_id="c1", tool_name="memory_recall",
            arguments={}, tool_classes={}, execution=None,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["metadata"], {"auto_activated": True, "tool": "memory_recall"})

    async def test_metadata_absent_defaults_to_none(self):
        """Regular tool executions never set metadata -- must not KeyError
        or default to something truthy that looks like auto_activated."""
        from core.tools.executor import execute_tool_safely
        from core.types import ToolExecutionResult, ToolExecutionStatus

        mock_dispatcher = MagicMock()
        mock_dispatcher.execute_tool = AsyncMock(return_value=ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            output={"real": "result"},
        ))

        payload = await execute_tool_safely(
            dispatcher=mock_dispatcher, call_id="c1", tool_name="web_search",
            arguments={}, tool_classes={}, execution=None,
        )

        self.assertTrue(payload["ok"])
        self.assertIsNone(payload.get("metadata"))


if __name__ == "__main__":
    unittest.main()
