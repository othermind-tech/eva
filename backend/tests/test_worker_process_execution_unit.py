# tests/test_worker_process_execution_unit.py
"""
Q-01 Phase 0/1/2: full-path test harness + characterization + adversarial
tests for core/execution/worker.py::ExecutionWorker._process_execution().

Why this file exists: despite ~30 existing
tests referencing core.execution.worker, _process_execution's actual
~800 lines of core logic (tool building, memory engine, prompt assembly,
the PEV/conversational fork, rollback, the four fire-and-forget
post-execution side effects, and _flush_metrics) had never been executed
by any test -- every existing reference either checks source-code
substrings (inspect.getsource()/Path.read_text()), reimplements
worker.py's logic inline against a mock instead of calling into it, or
short-circuits at the very first guard. This file is the missing safety
net, built before any decomposition of worker.py is attempted.

Harness discipline (mirrors tests/test_appclose_cancellation_unit.py's
TestDaemonCancellationReachesRealToken, the one existing precedent for
"drive a real end-to-end execution path, mock only the heavy edges"):
  REAL:   in-memory SQLite via AsyncSessionLocal monkeypatch, the real
          ExecutionRegistry singleton, the real LocalBroker, real
          UserToolRegistry/DynamicToolRegistry, real MemoryEngine.
  MOCKED: AgentEngine (a configurable fake -- see _FakeAgentEngine below),
          and the four fire-and-forget targets (memory_extractor,
          title_generator, project_artifact_updater, HookRunner) as
          AsyncMocks -- their own internals are covered by their own
          dedicated test suites; here we only assert whether and with
          what args worker.py calls them.

Critical import-binding detail (this is exactly the gap that made
TestWorkerProcessExecutionGuard in test_provider_config_guard_unit.py
silently hit the real dev eva.db): core/execution/worker.py does
`from core.db.session import AsyncSessionLocal` at its OWN module top
level (not a deferred/local import), so patching core.db.session's
attribute alone does nothing for worker.py's own DB calls -- every
`async with AsyncSessionLocal() as db:` inside _process_execution
resolves core.execution.worker's own bound name. Since Q-01 Phase 4 moved
_finalize_execution/_flush_metrics into their own module
(core/execution/worker_finalize.py), that module has its own independent
top-level `AsyncSessionLocal` import too -- a third bound name needing
the identical patch. The harness patches all three:
core.execution.worker.AsyncSessionLocal, core.execution.worker_finalize.
AsyncSessionLocal, and core.db.session.AsyncSessionLocal (a safety net
for anything reached indirectly, e.g. ProjectManager/MCPManager, that
might use a deferred import of its own).
"""
from __future__ import annotations

import pytest

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeAgentEngine:
    """Configurable stand-in for AgentEngine -- worker.py's real
    AgentEngine(...) constructor call is bypassed entirely (patched to
    return one of these directly), so no real provider handler is ever
    built. `.call()` yields a pre-configured chunk sequence and/or raises
    a pre-configured exception, so tests can drive every control-flow
    branch _process_execution has (plain completion, tool call, error
    chunk, mid-stream exception) without any real LLM traffic.
    """

    def __init__(self, chunks=None, raise_exc: Exception | None = None):
        self._chunks = chunks or []
        self._raise_exc = raise_exc
        self.call_args_list = []  # records each call(messages, stream=...) invocation

    async def call(self, messages, stream=True):
        self.call_args_list.append({"messages": messages, "stream": stream})
        for chunk in self._chunks:
            yield chunk
        if self._raise_exc is not None:
            raise self._raise_exc


class _CancelMidStreamFactory:
    """Callable stand-in for the AgentEngine CLASS itself (not an
    instance) -- worker.py's `AgentEngine(..., cancel_token=execution.cancel_token,
    on_step_complete=_save_tool_step, ...)` constructor call is patched to
    THIS, so it can capture the real ConversationExecution's cancel_token
    AND the real _save_tool_step closure (both constructed internally by
    _process_execution, never otherwise reachable from a test) --
    optionally invoking _save_tool_step first with a minimal but
    real-shaped tool-call step (so _first_tool_step_msg_id gets populated
    exactly the way a real ReAct tool step would), then flipping
    cancel_token.cancelled, driving the real runner.py cancellation branch
    for real rather than simulating a status.
    """

    def __init__(self, run_tool_step_first: bool = False):
        self._run_tool_step_first = run_tool_step_first
        self.captured_cancel_token = None
        self.captured_on_step_complete = None

    def __call__(self, *args, **kwargs):
        self.captured_cancel_token = kwargs.get("cancel_token")
        self.captured_on_step_complete = kwargs.get("on_step_complete")
        return self._build_fake()

    def _build_fake(self):
        factory = self

        class _Fake:
            def __init__(self):
                self.call_args_list = []

            async def call(self, messages, stream=True):
                from types import SimpleNamespace
                from core.types import TextChunk
                self.call_args_list.append({"messages": messages, "stream": stream})
                if factory._run_tool_step_first and factory.captured_on_step_complete:
                    tc = SimpleNamespace(call_id="call-1", tool_name="fake_tool", arguments={})
                    await factory.captured_on_step_complete([tc], [(tc, {"ok": True})])
                if factory.captured_cancel_token is not None:
                    factory.captured_cancel_token.cancelled = True
                # runner.py's cancellation check runs inside its stream
                # loop body -- but BufferedStream.stream_and_buffer() only
                # actually forwards a TextChunk to that loop when its
                # content is truthy (core/stream_buffer.py:38); an empty
                # TextChunk is buffered but never yielded onward, so the
                # loop body -- and therefore the cancellation check -- never
                # runs at all for it (found by this exact test failing:
                # execution.status ended up COMPLETED, not CANCELLED,
                # despite cancel_token.cancelled correctly reading True).
                # Non-empty content is what actually drives the real
                # CANCELLED transition; it also incidentally avoids
                # MessageLogger.save_final_message's separate "empty
                # response" placeholder-text substitution.
                yield TextChunk(content="(cancelled)")

        return _Fake()


class WorkerProcessExecutionHarness(unittest.IsolatedAsyncioTestCase):
    """Base test case. Subclass and call self._run(...) to drive a real
    ExecutionWorker()._process_execution() call end to end."""

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base, User, Conversation

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        # See module docstring: worker.py imports AsyncSessionLocal at its
        # own top level, so its own bound name must be patched directly --
        # patching core.db.session's attribute alone is not enough. Q-01
        # Phase 4 moved _finalize_execution/_flush_metrics to their own
        # module (worker_finalize.py), which does its own independent
        # `from core.db.session import AsyncSessionLocal` at ITS top level
        # -- a third, separately-bound name that needs the exact same
        # patch, or the rollback/metrics-flush DB sessions inside those
        # two functions silently fall through to the real dev DB instead
        # of this test's in-memory one (found live while verifying the
        # Phase 4 split -- this harness would have passed anyway, since
        # AsyncSessionLocal was only unpatched for those two call sites'
        # own DB writes, not for the rest of _process_execution, so the
        # symptom would have been silently-missing rollback/metrics rows
        # rather than a crash).
        import core.db.session as _dbs
        import core.execution.worker as worker_mod
        import core.execution.worker_finalize as worker_finalize_mod
        self._orig_dbs_session_local = _dbs.AsyncSessionLocal
        self._orig_worker_session_local = worker_mod.AsyncSessionLocal
        self._orig_worker_finalize_session_local = worker_finalize_mod.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session
        worker_mod.AsyncSessionLocal = self.Session
        worker_finalize_mod.AsyncSessionLocal = self.Session

        # Seed a real UserPreference row, not just an in-process session
        # cache entry: _process_execution's own "Hydrate User Preferences
        # from DB" phase unconditionally re-hydrates from
        # DBRepo.get_user_preferences() on every single call -- if that
        # returns None (no DB row), it re-hydrates with DEFAULT_SESSION
        # (empty provider/model) and silently overwrites anything set via
        # hydrate_user_session_from_db() beforehand. A real DB row is the
        # only thing that survives that phase.
        from core.db.models import UserPreference
        async with self.Session() as db:
            db.add(User(id="u-wpe", email="u-wpe@test.local", username="u-wpe", password_hash="x"))
            db.add(Conversation(id="c-wpe", user_id="u-wpe"))
            db.add(UserPreference(
                user_id="u-wpe", provider="anthropic", model="claude-haiku-4-5",
                tools=[], fallback_chain=[],
            ))
            await db.commit()

    async def asyncTearDown(self):
        import core.db.session as _dbs
        import core.execution.worker as worker_mod
        import core.execution.worker_finalize as worker_finalize_mod
        _dbs.AsyncSessionLocal = self._orig_dbs_session_local
        worker_mod.AsyncSessionLocal = self._orig_worker_session_local
        worker_finalize_mod.AsyncSessionLocal = self._orig_worker_finalize_session_local
        await self.engine.dispose()

    async def _seed_execution(self, *, execution_mode="conversational",
                               conversation_id="c-wpe", user_id="u-wpe"):
        """Seeds a real broker execution record the way api/chat.py /
        resume_from_wakeup() do -- so _process_execution's Phase 1
        `client.hgetall(key)` reads back exactly the shape it expects."""
        from core.execution import execution_registry
        execution = await execution_registry.create_execution_record(
            conversation_id=conversation_id,
            user_id=user_id,
            session_id="s-wpe",
            execution_mode=execution_mode,
        )
        return execution.execution_id

    def _patchers(self, *, agent_engine=None, agent_engine_factory=None,
                  run_execution_mock=None, run_pev_execution_mock=None):
        """Returns the list of patchers common to every full-path test.
        Caller starts them (self._start_all) and the harness stops them
        automatically via addCleanup. Pass either `agent_engine` (a plain
        instance, wrapped so every AgentEngine(...) call returns it) or
        `agent_engine_factory` (a callable invoked with the real
        constructor kwargs -- see _CancelMidStreamFactory).

        Q-01 Phase 4: memory_extractor/title_generator/project_artifact_
        updater/HookRunner are patched on core.execution.worker_finalize,
        not core.execution.worker -- _finalize_execution (which actually
        calls them) moved to that module and worker.py no longer imports
        them at all (dead-import cleanup during the move), so
        worker_mod.memory_extractor etc. would now be a plain
        AttributeError, not a silently-wrong patch target."""
        import core.execution.worker as worker_mod
        import core.execution.worker_finalize as worker_finalize_mod
        if agent_engine_factory is None:
            assert agent_engine is not None, "must pass agent_engine or agent_engine_factory"
            agent_engine_factory = MagicMock(return_value=agent_engine)
        patchers = [
            patch.object(worker_mod, "AgentEngine", agent_engine_factory),
            patch.object(worker_finalize_mod.memory_extractor, "extract_and_store",
                         new=AsyncMock()),
            patch.object(worker_finalize_mod.title_generator, "generate_and_save",
                         new=AsyncMock()),
            patch.object(worker_finalize_mod.project_artifact_updater, "update",
                         new=AsyncMock()),
            patch.object(worker_finalize_mod.HookRunner, "fire_event",
                         new=AsyncMock()),
        ]
        if run_execution_mock is not None:
            patchers.append(patch.object(worker_mod, "run_execution", run_execution_mock))
        if run_pev_execution_mock is not None:
            patchers.append(patch.object(worker_mod, "run_pev_execution", run_pev_execution_mock))
        return patchers

    def _start_all(self, patchers):
        mocks = []
        for p in patchers:
            m = p.start()
            self.addCleanup(p.stop)
            mocks.append(m)
        return mocks

    async def _run(self, execution_id: str):
        from core.execution.worker import ExecutionWorker
        worker = ExecutionWorker()
        await worker._process_execution(execution_id)
        return worker


@pytest.mark.usefixtures("stored_provider_keys")
class TestHappyPathConversational(WorkerProcessExecutionHarness):
    """Q-01 Phase 1: characterization test for the conversational happy
    path -- correct runner dispatched, AgentEngine actually driven,
    _flush_metrics reached, all conditional fire-and-forgets scheduled."""

    async def test_plain_completion_runs_to_completion(self):
        from core.types import TextChunk

        fake_agent = _FakeAgentEngine(chunks=[TextChunk(content="Hello there!")])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        execution_id = await self._seed_execution(execution_mode="conversational")
        worker = await self._run(execution_id)

        # AgentEngine.call() was actually invoked -- proves the real
        # run_execution() control flow reached the agent, not just that
        # AgentEngine() was constructed.
        self.assertEqual(len(fake_agent.call_args_list), 1)

        # Metrics row written.
        async with self.Session() as db:
            from core.db.repository import ExecutionMetricsRepo
            metrics = await ExecutionMetricsRepo.get(db, execution_id)
        self.assertIsNotNone(metrics, "execution_metrics row must exist after a normal run")
        self.assertEqual(metrics.status, "completed")

        # Title generation fired (first message on this conversation).
        import core.execution.worker_finalize as worker_finalize_mod
        worker_finalize_mod.title_generator.generate_and_save.assert_awaited_once()


@pytest.mark.usefixtures("stored_provider_keys")
class TestPEVDispatch(WorkerProcessExecutionHarness):
    """Q-01 Phase 1: execution_mode="plan_execute" must dispatch to
    run_pev_execution, not run_execution -- the PEV fork itself (not full
    PEV-loop correctness, which is pev_runner.py's own test suite's job).
    """

    async def test_plan_execute_mode_calls_run_pev_execution_not_run_execution(self):
        fake_agent = _FakeAgentEngine(chunks=[])
        run_exec_mock = AsyncMock()
        run_pev_mock = AsyncMock()
        patchers = self._patchers(
            agent_engine=fake_agent,
            run_execution_mock=run_exec_mock,
            run_pev_execution_mock=run_pev_mock,
        )
        self._start_all(patchers)

        execution_id = await self._seed_execution(execution_mode="plan_execute")
        await self._run(execution_id)

        run_pev_mock.assert_awaited_once()
        run_exec_mock.assert_not_awaited()

    async def test_conversational_mode_calls_run_execution_not_run_pev_execution(self):
        fake_agent = _FakeAgentEngine(chunks=[])
        run_exec_mock = AsyncMock()
        run_pev_mock = AsyncMock()
        patchers = self._patchers(
            agent_engine=fake_agent,
            run_execution_mock=run_exec_mock,
            run_pev_execution_mock=run_pev_mock,
        )
        self._start_all(patchers)

        execution_id = await self._seed_execution(execution_mode="conversational")
        await self._run(execution_id)

        run_exec_mock.assert_awaited_once()
        run_pev_mock.assert_not_awaited()


class TestEarlyReturnGuards(WorkerProcessExecutionHarness):
    """Q-01 Phase 1: all 3 explicit early returns in _process_execution --
    only the provider guard had any execution-level test before this
    file."""

    async def test_missing_broker_data_returns_without_processing(self):
        """No _seed_execution() call at all -- execution_id was never
        registered in the broker, so Phase 1's `client.hgetall(key)`
        returns an empty dict."""
        fake_agent = _FakeAgentEngine(chunks=[])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        await self._run("never-seeded-execution-id")

        self.assertEqual(len(fake_agent.call_args_list), 0,
                          "AgentEngine must never be reached when broker data is missing")

    async def test_unconfigured_provider_returns_without_building_agent(self):
        # Overwrite the seeded UserPreference row with an empty
        # provider/model -- exercises the guard through this harness's
        # own real-DB path, complementing (not replacing)
        # test_provider_config_guard_unit.py's version, which mocks
        # get_user_session directly instead.
        async with self.Session() as db:
            from core.db.models import UserPreference
            pref = await db.get(UserPreference, "u-wpe")
            pref.provider = ""
            pref.model = ""
            await db.commit()

        fake_agent = _FakeAgentEngine(chunks=[])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        execution_id = await self._seed_execution()
        await self._run(execution_id)

        self.assertEqual(len(fake_agent.call_args_list), 0,
                          "AgentEngine must never be constructed when provider/model is unconfigured")

    async def test_archived_project_returns_without_building_agent(self):
        from core.db.models import Project, Conversation

        async with self.Session() as db:
            db.add(Project(id="p-archived", user_id="u-wpe", name="Archived Proj",
                            status="archived"))
            db.add(Conversation(id="c-archived", user_id="u-wpe", project_id="p-archived"))
            await db.commit()

        fake_agent = _FakeAgentEngine(chunks=[])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        execution_id = await self._seed_execution(conversation_id="c-archived")
        await self._run(execution_id)

        self.assertEqual(len(fake_agent.call_args_list), 0,
                          "AgentEngine must never be constructed for an archived project's conversation")


@pytest.mark.usefixtures("stored_provider_keys")
class TestCancelRollbackAndGatingAsymmetry(WorkerProcessExecutionHarness):
    """Q-01 Phase 1: a cancelled execution rolls back its tool-step
    messages, and the three post-execution fire-and-forgets are NOT all
    gated the same way -- memory extraction and artifact update skip on
    cancel, title generation does not. This asymmetry is real, current
    behavior; a naive refactor is exactly the kind of thing that flips it
    by accident without a test catching it."""

    async def test_cancel_rolls_back_tool_step_and_skips_extraction_but_not_titling(self):
        import core.execution.worker_finalize as worker_finalize_mod

        factory = _CancelMidStreamFactory(run_tool_step_first=True)
        patchers = self._patchers(agent_engine_factory=factory)
        self._start_all(patchers)

        execution_id = await self._seed_execution()
        await self._run(execution_id)

        # The tool-step's own assistant/tool_call + tool/tool_result
        # messages must be rolled back -- this is what
        # _first_tool_step_msg_id is specifically scoped to catch.
        #
        # An earlier version of this fake's trailing chunk had EMPTY
        # content, which (see _CancelMidStreamFactory) meant
        # BufferedStream.stream_and_buffer() never forwarded it, so
        # run_execution's cancel-check never ran on that turn and
        # MessageLogger.save_final_message's "empty response" placeholder
        # got persisted as an extra, unrolled-back message. With a
        # non-empty trailing chunk (the actual fix) that placeholder path
        # never triggers, and rollback clears the conversation completely
        # -- confirmed via a standalone run showing 0 messages remain.
        async with self.Session() as db:
            from core.db.repository import DBRepo
            messages = await DBRepo.list_messages_for_conversation(db, "c-wpe")
        self.assertEqual(messages, [], "cancel-rollback must clear the conversation entirely")

        worker_finalize_mod.memory_extractor.extract_and_store.assert_not_awaited()
        worker_finalize_mod.project_artifact_updater.update.assert_not_awaited()
        # Asymmetry: title generation is NOT gated by _is_terminal_non_success.
        worker_finalize_mod.title_generator.generate_and_save.assert_awaited_once()


@pytest.mark.usefixtures("stored_provider_keys")
class TestProjectArtifactUpdateGating(WorkerProcessExecutionHarness):
    """Q-01 Phase 1: project_artifact_updater.update() fires only when the
    linked project's status is exactly "active" -- not merely "linked"."""

    async def _seed_project_conversation(self, status: str):
        from core.db.models import Project, Conversation
        async with self.Session() as db:
            db.add(Project(id=f"p-{status}", user_id="u-wpe", name=f"{status} proj", status=status))
            db.add(Conversation(id=f"c-{status}", user_id="u-wpe", project_id=f"p-{status}"))
            await db.commit()

    async def test_active_project_fires_artifact_update(self):
        import core.execution.worker_finalize as worker_finalize_mod
        from core.types import TextChunk

        await self._seed_project_conversation("active")
        fake_agent = _FakeAgentEngine(chunks=[TextChunk(content="done")])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        execution_id = await self._seed_execution(conversation_id="c-active")
        await self._run(execution_id)

        worker_finalize_mod.project_artifact_updater.update.assert_awaited_once()

    async def test_paused_project_does_not_fire_artifact_update(self):
        import core.execution.worker_finalize as worker_finalize_mod
        from core.types import TextChunk

        await self._seed_project_conversation("paused")
        fake_agent = _FakeAgentEngine(chunks=[TextChunk(content="done")])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        execution_id = await self._seed_execution(conversation_id="c-paused")
        await self._run(execution_id)

        worker_finalize_mod.project_artifact_updater.update.assert_not_awaited()


@pytest.mark.usefixtures("stored_provider_keys")
class TestFlushMetricsReachedOnFailedStatus(WorkerProcessExecutionHarness):
    """Q-01 Phase 1: _flush_metrics must be reached (and record status=
    "failed") when the agent stream ends via an ErrorChunk -- not just on
    a clean completion. This is the *normal-path* failure case, distinct
    from the exception-escapes-the-runner case covered below."""

    async def test_error_chunk_still_flushes_metrics_as_failed(self):
        from core.types import ErrorChunk

        fake_agent = _FakeAgentEngine(chunks=[ErrorChunk(message="synthetic provider error")])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        execution_id = await self._seed_execution()
        await self._run(execution_id)

        async with self.Session() as db:
            from core.db.repository import ExecutionMetricsRepo
            metrics = await ExecutionMetricsRepo.get(db, execution_id)
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics.status, "failed")


@pytest.mark.usefixtures("stored_provider_keys")
class TestExceptionEscapesRunnerSkipsMetricsFlush(WorkerProcessExecutionHarness):
    """Q-01 Phase 1 found, Phase 3 (2026-08-17) fixed: run_execution()
    itself already has a broad `except Exception` around its own stream
    loop that marks the execution FAILED and returns normally -- an
    exception raised from inside AgentEngine.call() does NOT skip
    _flush_metrics, since _process_execution falls through to
    _finalize_execution exactly as it would on any other completion. The
    real (now-fixed) gap was narrower and sat strictly BEFORE
    run_execution() is ever invoked -- anything raised while
    _process_execution builds the AgentEngine itself (or, by the same
    logic, during tool-building/prompt-assembly) had no local catch at
    all and propagated straight to _safe_process_execution's outer
    handler, which marked the broker failed and published SSE but never
    reached _flush_metrics.

    Fix: _process_execution now wraps the whole construction-through-run
    span in try/finally, with _finalize_execution (and therefore
    _flush_metrics) called unconditionally in the finally clause --
    seeded with empty captured_context/first_tool_step_msg_id defaults so
    it works even when construction never got far enough to populate
    them. The exception itself still propagates afterward, so
    _safe_process_execution's existing failure-marking/SSE-publish
    behavior is unchanged -- only the "no metrics row at all" gap is
    closed. Execution never reached RUNNING in this scenario, so the
    written row's status is "pending", which is itself the honest signal
    that construction crashed before the agent ever started."""

    async def test_exception_during_agent_construction_marks_broker_failed_and_still_writes_metrics_row(self):
        def _boom(*args, **kwargs):
            raise RuntimeError("boom while constructing AgentEngine")

        patchers = self._patchers(agent_engine_factory=_boom)
        self._start_all(patchers)

        execution_id = await self._seed_execution()

        # Full path via _safe_process_execution (not _run(), which calls
        # _process_execution directly) -- this is the real wrapper that
        # catches the exception in production.
        from core.execution.worker import ExecutionWorker
        worker = ExecutionWorker()
        with patch("core.execution.worker.publish_user_event", new=AsyncMock()) as mock_publish:
            await worker._safe_process_execution(execution_id)

        mock_publish.assert_awaited_once()
        self.assertEqual(mock_publish.await_args.kwargs["event"], "execution.failed")

        async with self.Session() as db:
            from core.db.repository import ExecutionMetricsRepo
            metrics = await ExecutionMetricsRepo.get(db, execution_id)
        self.assertIsNotNone(
            metrics,
            "FIXED: an execution_metrics row must now be written even when the runner "
            "raises past tool-building -- previously this was silently missing forever",
        )
        self.assertEqual(
            metrics.status, "pending",
            "execution never reached RUNNING before construction crashed -- 'pending' "
            "is the honest, distinguishing signal for this specific failure mode",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Q-01 Phase 2 -- adversarial / bug-hunt tests.
#
# These don't characterize a known behavior; they go looking for the kind
# of latent bug this session has repeatedly found live in exactly this
# file (ExecutionRegistry.attach(), the AWAITING_WAKEUP double-publish,
# the stuck-at-pending crash). Each class below documents what it found,
# even when the answer turned out to be "this is already safe."
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("stored_provider_keys")
class TestCancellationNotSwallowed(WorkerProcessExecutionHarness):
    """_process_execution wraps most of its prompt-assembly phases in
    `except Exception` blocks explicitly marked "non-fatal" in their log
    messages. Since asyncio.CancelledError inherits from BaseException
    (not Exception) as of Python 3.8+, `except Exception` structurally
    cannot catch it -- but that's a language guarantee, not a tested one,
    and a single careless `except Exception:` -> `except:` edit during a
    future refactor would silently start swallowing real cancellations.
    This locks the guarantee down at one representative site (the
    long-term-memory injection block, worker.py ~1119-1146) so that kind
    of regression fails loudly."""

    async def test_cancelled_error_from_memory_injection_propagates_not_swallowed(self):
        from core.types import TextChunk

        fake_agent = _FakeAgentEngine(chunks=[TextChunk(content="done")])
        patchers = self._patchers(agent_engine=fake_agent)
        patchers.append(
            patch(
                "core.memory.long_term.LongTermMemoryInjector.build_memory_block",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            )
        )
        self._start_all(patchers)

        execution_id = await self._seed_execution()

        with self.assertRaises(asyncio.CancelledError):
            await self._run(execution_id)

        # And it must not have been caught-and-continued either -- the
        # agent must never have been reached past the cancellation point.
        fake_agent_calls = fake_agent.call_args_list
        self.assertEqual(fake_agent_calls, [], "cancellation must abort before the agent is ever invoked")


class TestFireAndForgetSideEffectsSelfProtect(unittest.IsolatedAsyncioTestCase):
    """Q-01 Phase 2: of the four fire-and-forget post-execution side
    effects worker.py schedules via asyncio.create_task() (only one of
    which -- the cancel watcher -- is ever awaited), an unprotected one
    would fail silently: no caller ever retrieves its exception except
    Python's own "exception was never retrieved" GC warning. This
    confirms each of the three NOT already known to self-protect
    (title_generator was confirmed to have its own guard during earlier
    A-12 work) does in fact catch and swallow its own internal errors
    rather than relying on worker.py to protect it -- reading each
    implementation found `extract_and_store`/`update`/`fire_event` all
    wrap their real work in their own `except Exception`, so this is
    confirmation of existing safety, not a fix."""

    async def test_memory_extractor_swallows_internal_exception(self):
        from core.memory.extractor import memory_extractor

        with patch.object(memory_extractor, "_run", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await memory_extractor.extract_and_store(
                conversation_id="c-x", user_id="u-x", session={}, execution_id="e-x",
            )  # must not raise

    async def test_project_artifact_updater_swallows_internal_exception(self):
        from core.project.artifact_updater import project_artifact_updater

        with patch.object(project_artifact_updater, "_run", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await project_artifact_updater.update(
                project_id="p-x", conversation_id="c-x", user_id="u-x", session={},
            )  # must not raise

    async def test_hook_runner_fire_event_swallows_internal_exception(self):
        from core.hooks.runner import HookRunner

        with patch.object(HookRunner, "_dispatch", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await HookRunner.fire_event(
                event="stop",
                hook_definitions=[{"event": "stop", "async_": False}],
                user_id="u-x",
                session_id="s-x",
                workspace_root="/tmp",
            )  # must not raise


@pytest.mark.usefixtures("stored_provider_keys")
class TestReentrantExecutionConcurrency(WorkerProcessExecutionHarness):
    """Q-01 Phase 2: is _process_execution itself safe if the same
    execution_id is ever dispatched twice concurrently (e.g. a worker
    crash-restart re-dequeuing a job that a still-running old worker
    hasn't finished)? Production's actual protection against this is
    entirely upstream -- BLPOP's atomic single-consumer dequeue plus
    orphan-recovery's liveness-beacon ownership check -- _process_execution
    has no internal re-entrancy guard of its own.

    Forcing the bypass directly (this class) found TWO real, previously
    undocumented TOCTOU races, both only reachable if that upstream
    single-dispatch guarantee is ever violated. Both are now FIXED
    (2026-08-17) by serializing the racing read-then-write per key with an
    in-process asyncio.Lock (DBRepo._message_sequence_locks /
    ExecutionMetricsRepo._execution_metrics_locks, core/db/repository.py)
    -- correct and sufficient because EVA runs as a single process (see
    core/execution/registry.py's own in-memory _executions dict, which
    makes the same assumption); a multi-process deployment would need a
    DB-level constraint instead:

    1. DBRepo.add_message's own comment (core/db/repository.py) already
       flagged the risk in words -- MAX(sequence)+1 is "safer than COUNT
       if concurrent writers ever bypass the single-execution gate" -- but
       nothing had ever tested it. Two concurrent sessions could both read
       MAX=-1 before either committed, so both inserted sequence=0: a
       genuine duplicate, not just a gap.
    2. ExecutionMetricsRepo.upsert did a plain `get() -> None -> INSERT`,
       no SELECT...FOR UPDATE / ON CONFLICT. Two concurrent flushes for
       the same execution_id could both see no existing row, both INSERT,
       and the second would hit SQLite's UNIQUE constraint on
       execution_id -- caught by _flush_metrics's own `except Exception`
       (worker.py, "non-fatal") and logged away. Net effect: one of the
       two runs' cost/token/status metrics was silently dropped forever,
       with no error surfaced anywhere.

    Neither race ever crashed the worker (both exceptions were caught
    non-fatally exactly as designed) -- the bug was silent data
    corruption/loss, not a crash. The two fix-verification tests below
    were @unittest.expectedFailure before the fix landed; they now assert
    the fix directly."""

    async def test_same_execution_id_processed_concurrently_does_not_crash(self):
        from core.types import TextChunk

        fake_agent = _FakeAgentEngine(chunks=[TextChunk(content="done")])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        execution_id = await self._seed_execution()

        results = await asyncio.gather(
            self._run(execution_id), self._run(execution_id), return_exceptions=True,
        )
        crashes = [r for r in results if isinstance(r, BaseException)]
        self.assertEqual(
            crashes, [],
            "concurrent _process_execution calls for the same execution_id must not raise "
            f"(found: {crashes!r}) -- if this ever fails, it's a real crash bug, not a "
            "sequence-numbering cosmetic issue",
        )

        # Documents the actual (not crash-causing) consequence: the agent
        # really is invoked twice -- i.e. a duplicate LLM call/cost would
        # occur in production if the upstream single-dispatch guarantee
        # were ever violated. Not asserted as *desired* behavior, just
        # locked down as the honest, currently-shipped consequence so a
        # future fix (e.g. an explicit in-process lock keyed on
        # execution_id) has a test to flip green instead of a silent gap.
        self.assertEqual(
            len(fake_agent.call_args_list), 2,
            "current behavior: no in-process guard prevents a duplicate agent call when "
            "the same execution_id is dispatched twice -- protection is entirely upstream",
        )

    async def test_FIXED_concurrent_add_message_no_longer_collides_sequence_numbers(self):
        # Was @unittest.expectedFailure documenting a real bug (found
        # 2026-08-17): two concurrent add_message calls for the same
        # conversation_id both read the same MAX(sequence) before either
        # committed and both inserted the same sequence number. Fixed by
        # serializing add_message's read-then-insert per conversation_id
        # via DBRepo._message_sequence_locks (core/db/repository.py). This
        # now asserts the fix, not just the absence of a crash.
        from core.types import TextChunk

        fake_agent = _FakeAgentEngine(chunks=[TextChunk(content="done")])
        patchers = self._patchers(agent_engine=fake_agent)
        self._start_all(patchers)

        execution_id = await self._seed_execution()
        await asyncio.gather(self._run(execution_id), self._run(execution_id))

        async with self.Session() as db:
            from core.db.repository import DBRepo
            messages = await DBRepo.list_messages_for_conversation(db, "c-wpe")
        sequences = [m.sequence for m in messages]
        self.assertEqual(
            len(sequences), len(set(sequences)),
            f"sequence-number collision from concurrent add_message calls: {sequences!r}",
        )

    async def test_FIXED_concurrent_flush_metrics_no_longer_drops_a_row(self):
        # Was @unittest.expectedFailure documenting a real bug (found
        # 2026-08-17): ExecutionMetricsRepo.upsert's get-then-insert/update
        # was not atomic -- two concurrent upserts for the same
        # execution_id both saw no existing row and both attempted INSERT,
        # the second hitting a UNIQUE constraint violation (swallowed
        # non-fatally by worker.py's _flush_metrics, silently dropping one
        # run's metrics). Fixed by serializing upsert's get-then-write per
        # execution_id via ExecutionMetricsRepo._execution_metrics_locks
        # (core/db/repository.py). This now asserts the fix directly:
        # isolated, deterministic reproduction (not through the full worker
        # stack, whose timing is less predictable) of two concurrent
        # upserts for a brand-new execution_id.
        from core.db.repository import ExecutionMetricsRepo

        execution_id = "e-race-metrics"

        async def _upsert(status: str):
            async with self.Session() as db:
                await ExecutionMetricsRepo.upsert(db, {
                    "execution_id": execution_id, "conversation_id": "c-wpe",
                    "user_id": "u-wpe", "status": status,
                })

        # Both coroutines' first await (db.get) yields control back to the
        # loop before either commits -- asyncio.gather interleaves them
        # deterministically enough on a single-threaded event loop to hit
        # the race every time, no sleep/timing hack needed.
        results = await asyncio.gather(_upsert("completed"), _upsert("failed"), return_exceptions=True)
        integrity_errors = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(
            integrity_errors, [],
            f"ExecutionMetricsRepo.upsert raised for concurrent upserts of the same "
            f"execution_id: {integrity_errors!r}",
        )

        async with self.Session() as db:
            metrics = await ExecutionMetricsRepo.get(db, execution_id)
        self.assertIsNotNone(metrics, "the row must exist after both concurrent upserts")
        self.assertIn(
            metrics.status, ("completed", "failed"),
            "one of the two concurrent writers' status must have landed (last-writer-wins "
            "is fine now that both no longer race the INSERT itself)",
        )


class TestWatchForExternalCancellationImportContract(unittest.TestCase):
    """Cheap, permanent regression guard: core/daemons/runner.py:412 reuses
    _watch_for_external_cancellation directly from core.execution.worker
    (confirmed via an explicit comment there stating it's intentional
    reuse). Moving, renaming, or changing this function's importability
    during the eventual Phase 3/4 refactor would silently break that call
    site with no local error -- this makes the break loud and immediate."""

    def test_watch_for_external_cancellation_is_importable_from_worker_module(self):
        from core.execution.worker import _watch_for_external_cancellation
        import inspect

        self.assertTrue(inspect.iscoroutinefunction(_watch_for_external_cancellation))
