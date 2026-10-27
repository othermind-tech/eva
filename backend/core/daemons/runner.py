# core/daemons/runner.py
#
# G-008: Daemon Agent runner
#
# Executes a single daemon run: builds an AgentEngine from the DaemonConfig,
# runs it with the configured trigger_message, and returns a result summary.
#
# Design notes:
#   - Daemon runs use a minimal ConversationExecution (no SSE buffer, no shadow)
#     since daemons run in the background without a live SSE consumer.
#   - The daemon's blackboard is created fresh per run (per execution_id).
#   - caller_agent_name is set to the daemon's name so inter-agent messaging
#     and blackboard ownership work correctly (G-004/G-007).
#   - Output is captured in memory; the daemon can also write to its own
#     long-term memory via memory_store (if the tool is in its whitelist).
#   - Errors are logged and returned as {success: False, error: "..."}.
#     The caller (scheduler or trigger endpoint) decides what to do.

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from datetime import datetime, timezone

from core.db.models import DaemonConfig
from core.execution.models import ConversationExecution, CancellationToken, ExecutionStatus
from core.memory.conversation_journal import ConversationJournal

logger = logging.getLogger(__name__)


def _classify_error(exc: Exception) -> str:
    """
    Classify an exception as 'transient' or 'permanent'.
    Transient errors (API rate limits, network timeouts) are worth retrying.
    Permanent errors (bad config, missing tool, auth failure) are not.
    Returns the failure class string stored on the daemon record.
    """
    msg = str(exc).lower()
    transient_markers = (
        "rate limit", "timeout", "connection", "network", "503", "502",
        "429", "server error", "temporarily unavailable",
    )
    if any(m in msg for m in transient_markers):
        return "transient"
    return "permanent"

_DEFAULT_TRIGGER = (
    "You have been activated. "
    "Check your inbox for messages from other agents (use read_agent_messages), "
    "then proceed with your configured tasks."
)


async def run_daemon(
    daemon: DaemonConfig,
    execution_id: Optional[str] = None,
    override_trigger: Optional[str] = None,
) -> dict:
    """
    Execute one run of the given daemon agent.

    Parameters
    ----------
    daemon       : DaemonConfig DB row.
    execution_id : Pre-generated execution ID from the trigger endpoint so that
                   the caller can register it in the response before the run
                   starts.  A new UUID is generated if not supplied.

    Returns
    -------
    {
        "success":      bool,
        "output":       str,   # final text output from the agent
        "error":        str,   # set if success is False
        "execution_id": str,
        "failure_class": str,  # "transient" | "permanent" | ""
    }
    """
    if execution_id is None:
        execution_id = str(uuid.uuid4())
    user_id = daemon.user_id

    logger.info(
        "Daemon run starting",
        extra={"daemon": daemon.name, "execution_id": execution_id, "user_id": user_id},
    )

    # ------------------------------------------------------------------
    # Register this execution in the broker execution registry so that
    # GET /executions/{id}/status works for daemon runs exactly as it
    # does for normal chat executions.
    # ------------------------------------------------------------------
    # daemon.id is the stable UUID folder for this daemon's journal.
    # It never changes even if the daemon is renamed, and has no colon
    # (which is illegal in Windows paths).
    daemon_conv_id = daemon.id

    # ── Journal: write daemon meta (idempotent — same content each run) ───────
    ConversationJournal.write_daemon_meta(user_id, daemon_conv_id, {
        "daemon_id":    daemon.id,
        "daemon_name":  daemon.name,
        "description":  daemon.description,
        "model":        daemon.model,
        "provider":     daemon.provider,
    })
    # ── Journal: execution start ──────────────────────────────────────────────
    ConversationJournal.log_daemon_exec(user_id, daemon_conv_id, execution_id, {
        "type":         "execution_start",
        "execution_id": execution_id,
        "daemon_name":  daemon.name,
        "trigger":      override_trigger or daemon.trigger_message or _DEFAULT_TRIGGER,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    })

    # Registered through ExecutionRegistry (not the broker directly) so this
    # execution's ConversationExecution -- carrying the real cancel_token --
    # is reachable by GET /executions/running and POST /cancel, exactly like
    # a conversational execution. Previously daemons were only written to the
    # broker's status hash, which nothing ever read cancellation back out of
    # (POST /daemons/{name}/cancel flipped that hash's status field but had
    # no way to reach the token this run's AgentEngine actually checks).
    # `execution` stays None on failure so the fallback branch below still
    # runs the daemon standalone -- broker/registry issues are non-fatal to
    # daemon execution, same as the pre-existing behaviour.
    execution: Optional[ConversationExecution] = None
    try:
        from core.execution import execution_registry
        execution = await execution_registry.create_execution_record(
            conversation_id=daemon_conv_id,
            user_id=user_id,
            session_id="",
            execution_mode="daemon",
            execution_id=execution_id,
            initial_message=override_trigger or daemon.trigger_message or _DEFAULT_TRIGGER,
        )
        execution.status = ExecutionStatus.RUNNING  # daemons skip the PENDING/queued phase
        from core.execution.redis_registry import redis_execution_registry
        await redis_execution_registry.mark_execution_running(execution_id)
    except Exception as exc:
        logger.warning(
            "Daemon: could not register execution in ExecutionRegistry "
            "(non-fatal — daemon will still run, but won't be listable/"
            "cancellable via /executions/running): %s", exc,
        )

    # ------------------------------------------------------------------
    # B-058: Phase 1 — INSERT execution_metrics row at run start.
    # Captures the run even if the server crashes mid-execution.
    # The watchdog will mark any stuck "running" rows as "aborted" (B-059).
    # ------------------------------------------------------------------
    project_id = getattr(daemon, "project_id", None)
    try:
        from core.db.session import AsyncSessionLocal as _ASL
        from core.db.repository import ExecutionMetricsRepo as _EMRepo
        async with _ASL() as _start_db:
            await _EMRepo.upsert(_start_db, {
                "execution_id":   execution_id,
                "conversation_id": daemon_conv_id,
                "user_id":        user_id,
                "status":         "running",
                "triggered_by":   "daemon",
                "daemon_name":    daemon.name,
                "project_id":     project_id,
                "started_at":     datetime.now(timezone.utc),   # Gap 11
            })
            # Enforce last-50 retention policy per daemon.
            # ⚠️ Intentional: runs beyond the 50 most recent are purged.
            # See B-058 in EVA_Backlog.md for rationale.
            await _EMRepo.purge_old_daemon_runs(
                _start_db, user_id, daemon.name, keep=50
            )
    except Exception as _exc:
        logger.warning("Daemon: could not write phase-1 metrics: %s", _exc)

    # ------------------------------------------------------------------
    # Build blackboard using LocalBroker adapter
    # ------------------------------------------------------------------
    blackboard = None
    try:
        from core.blackboard import ExecutionBlackboard
        from core.broker import local_broker as _broker
        blackboard = ExecutionBlackboard(
            redis=await _broker.connect(),   # LocalRedisAdapter (duck-typed broker client)
            execution_id=execution_id,
        )
    except Exception as exc:
        logger.warning("Daemon: could not create blackboard: %s", exc)

    # ------------------------------------------------------------------
    # Minimal execution context — no SSE streaming needed for daemons.
    # If registration above succeeded, `execution` is already the live,
    # registered object (with a reachable cancel_token) -- just attach the
    # fields the registry doesn't know how to build (blackboard, caller
    # name). Only build a fresh standalone object as a fallback when
    # registration itself failed.
    # ------------------------------------------------------------------
    if execution is None:
        execution = ConversationExecution(
            execution_id=execution_id,
            conversation_id=daemon_conv_id,
            user_id=user_id,
            session_id="",
            status=ExecutionStatus.RUNNING,
            buffer=None,          # type: ignore[arg-type]  — no SSE buffer for daemon runs
            cancel_token=CancellationToken(),
            execution_mode="daemon",
        )
    execution.blackboard = blackboard
    execution.caller_agent_name = daemon.name

    # ------------------------------------------------------------------
    # Resolve provider / model / api_key from user session
    #
    # No hardcoded provider/model fallback (V6.4): EVA has no default model
    # of its own. If neither the daemon config nor the user session has a
    # provider/model set, that is a real configuration failure -- routed
    # through the same finalisation path as any other pre-flight error
    # below (agent_configured guard) rather than silently guessing one.
    # ------------------------------------------------------------------
    from config.settings import get_user_session
    session  = get_user_session(user_id)
    if daemon.provider:
        # A provider override is a provider+model PAIR: never combine it with
        # the session's model, which belongs to a different provider.
        provider = daemon.provider
        model    = daemon.model
    else:
        provider = session.get("provider")
        model    = daemon.model or session.get("model")
    api_key  = session.get("api_key", "")
    agent_configured = bool(provider and model)

    # V6.59 Phase 5: run-time usability gate (core/providers/usability.py).
    # The saved choice may have gone stale -- key removed, model unusable. Use
    # the first usable candidate (this daemon's target, then the session's
    # fallback chain); if none is usable the run is recorded as a permanent
    # ConfigurationError with the reason, instead of failing opaquely mid-run.
    _unusable_msg = ""
    _fallbacks    = session.get("fallback_chain") or []
    if agent_configured:
        from core.providers.usability import plan_run
        _plan = plan_run(provider, model, _fallbacks)
        if _plan.ok:
            provider, model = _plan.primary["provider"], _plan.primary["model"]
            _fallbacks      = _plan.fallbacks
            if _plan.promoted:
                logger.warning(
                    "Daemon %s: configured provider unusable (%s) -- running on fallback %s/%s",
                    daemon.name, _plan.primary_problem.reason, provider, model,
                )
        else:
            agent_configured = False
            _unusable_msg    = f"Daemon '{daemon.name}': {_plan.failure_message()}"

    # ------------------------------------------------------------------
    # B-082: Resolve project context for project-linked daemons.
    #
    # Three things are wired up when daemon.project_id is set:
    #   1. execution.project_workspace  — file tools resolve relative paths
    #      against the project's codebase root, not the daemon scratch dir
    #   2. execution.parent_project_id  — project-scoped MCPs become available
    #      (MCPRuntime uses this to include project-specific MCP configs)
    #   3. System prompt augmented with project context block (goals, policies,
    #      tasks) so the daemon is self-orienting — it knows what the project is
    #      about and what the rules are without requiring the user to hard-code
    #      that information in the daemon config
    #
    # The daemon journal logs are deliberately NOT written to the project
    # workspace — they are large JSON files that belong in the daemon's own
    # scratch folder (daemon.id UUID dir).  Only the daemon's output feeds
    # back into the project via the artifact updater (already implemented).
    #
    # Non-fatal: if the project fetch fails the daemon runs without context.
    # ------------------------------------------------------------------
    daemon_system_prompt = daemon.system_prompt or ""
    allowed_mcp_servers = None
    if daemon.project_id:
        try:
            from core.db.session import AsyncSessionLocal as _DASL
            from core.db.repository import ProjectRepo as _DProjRepo
            async with _DASL() as _proj_db:
                _dproj = await _DProjRepo.get(
                    _proj_db, project_id=daemon.project_id, user_id=user_id
                )
            if _dproj:
                # 1. Workspace — file tools use the project codebase root
                if _dproj.workspace_path:
                    execution.project_workspace = _dproj.workspace_path
                    logger.debug(
                        "Daemon: project workspace attached",
                        extra={
                            "daemon": daemon.name,
                            "project_id": daemon.project_id,
                            "workspace_path": _dproj.workspace_path,
                        },
                    )

                # 2. MCP scope — project-scoped MCPs available to this daemon
                execution.parent_project_id = daemon.project_id
                if getattr(_dproj, "allowed_mcp_servers", None):
                    allowed_mcp_servers = _dproj.allowed_mcp_servers

                # 3. Project context — inject goals, policies, tasks
                from core.project.manager import ProjectManager as _DPM
                _ctx = _DPM.load_context(
                    user_id=user_id,
                    project_id=daemon.project_id,
                    tier="focused",  # project.eva + policies.eva + tasks.eva (~600 tokens)
                    workspace_path=execution.project_workspace,
                )
                if _ctx:
                    daemon_system_prompt = daemon_system_prompt + "\n\n" + _ctx
                    logger.debug(
                        "Daemon: project context injected into system prompt",
                        extra={
                            "daemon": daemon.name,
                            "project_id": daemon.project_id,
                            "context_chars": len(_ctx),
                        },
                    )
        except Exception as _dpe:
            logger.warning(
                "Daemon: could not resolve project context — running without it: %s",
                _dpe,
                extra={"daemon": daemon.name, "project_id": daemon.project_id},
            )

    # ------------------------------------------------------------------
    # Sync MCP tools for this daemon's user. Daemons run standalone (no
    # parent execution to inherit a dynamic_registry from -- see worker.py
    # for the conversational/PEV equivalent), so this mirrors that same
    # sync call independently. Non-fatal: a sync failure just means the
    # daemon runs without MCP tools this run, same as worker.py's handling.
    # ------------------------------------------------------------------
    from core.tools.dynamic_registry import DynamicToolRegistry
    from core.mcp.manager import MCPManager
    dynamic_registry = DynamicToolRegistry()
    try:
        from core.db.session import AsyncSessionLocal as _MASL
        async with _MASL() as _mcp_db:
            await MCPManager(registry=dynamic_registry).register_tools_for_user(
                _mcp_db, user_id, allowed_mcp_servers=allowed_mcp_servers
            )
    except Exception as _mcp_exc:
        logger.warning(
            "Daemon: MCP tool registration failed (non-fatal): %s",
            _mcp_exc,
            extra={"daemon": daemon.name, "execution_id": execution_id},
        )

    # ------------------------------------------------------------------
    # Build restricted tool registry
    # ------------------------------------------------------------------
    from core.tools.user_registry import UserToolRegistry
    allowed_tools = daemon.tools or []
    child_tools   = UserToolRegistry(
        default_tools=True,
        enabled_tools=allowed_tools,
        dynamic_registry=dynamic_registry,
        # allowed_tools=[] means "all user tools" (existing convention);
        # otherwise actually restrict the default toolset too -- previously
        # daemon.tools only gated custom/MCP tools, not defaults, despite
        # its own docstring calling it an "allowed tool names" list.
        allowed_default_tools=set(allowed_tools) if allowed_tools else None,
    )

    # ------------------------------------------------------------------
    # Run the daemon (skipped entirely if provider/model are unconfigured)
    # ------------------------------------------------------------------
    trigger = override_trigger or daemon.trigger_message or _DEFAULT_TRIGGER
    messages = [{"role": "user", "content": trigger}]

    output_parts:     list[str] = []
    success           = True
    error_msg         = ""
    failure_class     = ""
    error_type        = ""   # Gap 4: exception class name, mirrors worker.py path
    prompt_tokens     = 0
    completion_tokens = 0
    reasoning_tokens  = 0
    cached_tokens     = 0

    _run_start = time.monotonic()  # Gap 5: capture start time for duration_ms

    if not agent_configured:
        success       = False
        error_msg     = _unusable_msg or (
            f"Daemon '{daemon.name}' has no provider/model configured "
            "(neither the daemon config nor the user session has one set)."
        )
        failure_class = "permanent"
        error_type    = "ConfigurationError"
        logger.warning(
            "Daemon run skipped — no provider/model configured",
            extra={"daemon": daemon.name, "execution_id": execution_id},
        )
        ConversationJournal.log_daemon_exec(user_id, daemon_conv_id, execution_id, {
            "type":      "error",
            "message":   error_msg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    else:
        # --------------------------------------------------------------
        # Build AgentEngine
        # --------------------------------------------------------------
        from core.tools.dispatcher import ToolDispatcher
        from core.agent_engine import AgentEngine

        dispatcher = ToolDispatcher()
        agent = AgentEngine(
            provider=provider,
            api_key=api_key,
            model=model,
            system_prompt=daemon_system_prompt,
            reasoning=None,
            tools=child_tools,
            dispatcher=dispatcher,
            session_id=None,
            memory_provider=None,
            execution=execution,
            fallback_chain=_fallbacks,  # V6.5 Phase 3 (usable entries only -- V6.59 Phase 5)
            max_steps=max(1, daemon.max_steps or 5),
            dynamic_registry=dynamic_registry,
        )

        # Background broker-poll watcher -- same mechanism worker.py uses for
        # conversational executions (see _watch_for_external_cancellation()'s
        # docstring there for the full "why"). Required because a daemon's
        # own chunk-consumption loop below can't observe a cancel signal
        # while it's sitting inside a single in-flight tool/LLM call; this
        # task polls the broker independently and flips this run's actual
        # cancel_token the moment POST /cancel marks it cancelled in the
        # broker. Reused, not reimplemented, to avoid two divergent copies
        # of the same cancellation bridge.
        from core.execution.worker import _watch_for_external_cancellation
        _cancel_watcher = asyncio.create_task(
            _watch_for_external_cancellation(execution),
            name=f"daemon-cancel-watcher-{execution_id}",
        )
        try:
            async for chunk in agent.call(messages, stream=True):
                chunk_type = getattr(chunk, "type", "")
                if chunk_type == "text":
                    content = getattr(chunk, "content", "")
                    output_parts.append(content)
                    ConversationJournal.log_daemon_exec(user_id, daemon_conv_id, execution_id, {
                        "type":      "assistant_text",
                        "content":   content,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                elif chunk_type == "tool_call":
                    ConversationJournal.log_daemon_exec(user_id, daemon_conv_id, execution_id, {
                        "type":      "tool_call",
                        "tool_name": getattr(chunk, "tool_name", ""),
                        "call_id":   getattr(chunk, "call_id", ""),
                        "arguments": getattr(chunk, "arguments", {}),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                elif chunk_type == "usage":
                    # Gap 7: accumulate token counts from UsageChunk
                    prompt_tokens     += getattr(chunk, "prompt_tokens",     0) or 0
                    completion_tokens += getattr(chunk, "completion_tokens", 0) or 0
                    reasoning_tokens  += getattr(chunk, "reasoning_tokens",  0) or 0
                    cached_tokens     += getattr(chunk, "cached_tokens",     0) or 0
                elif chunk_type == "error":
                    error_msg     = getattr(chunk, "message", "Unknown error")
                    success       = False
                    failure_class = "permanent"   # agent-level errors are not retried
                    error_type    = "AgentError"
                    ConversationJournal.log_daemon_exec(user_id, daemon_conv_id, execution_id, {
                        "type":      "error",
                        "message":   error_msg,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    break
        except asyncio.CancelledError:
            success       = False
            error_msg     = "Daemon run was cancelled."
            failure_class = "transient"
            error_type    = "CancelledError"
            logger.warning("Daemon run cancelled: %s", daemon.name)
        except Exception as exc:
            success       = False
            error_msg     = str(exc)
            failure_class = _classify_error(exc)
            error_type    = type(exc).__name__
            logger.exception(
                "Daemon run raised an exception",
                extra={"daemon": daemon.name, "execution_id": execution_id},
            )
        finally:
            _cancel_watcher.cancel()
            try:
                await _cancel_watcher
            except asyncio.CancelledError:
                pass

    _duration_ms = int((time.monotonic() - _run_start) * 1000)  # Gap 5

    output = "".join(output_parts)

    # ------------------------------------------------------------------
    # Update broker execution status to match the outcome
    # ------------------------------------------------------------------
    try:
        from core.execution.redis_registry import redis_execution_registry
        if success:
            await redis_execution_registry.mark_execution_finished(execution_id)
        else:
            await redis_execution_registry.mark_execution_failed(execution_id, error_msg)
    except Exception as exc:
        logger.warning("Daemon: could not update broker execution status: %s", exc)

    logger.info(
        "Daemon run finished",
        extra={
            "daemon":        daemon.name,
            "execution_id":  execution_id,
            "success":       success,
            "output_chars":  len(output),
        },
    )

    # B-051: publish daemon event for the user-facing SSE feed.
    try:
        from core.events_publisher import publish_user_event
        await publish_user_event(
            user_id=user_id,
            event="daemon.completed" if success else "daemon.failed",
            execution_id=execution_id,
            daemon_name=daemon.name,
            summary=(
                f"Daemon '{daemon.name}' completed successfully."
                if success
                else f"Daemon '{daemon.name}' failed: {error_msg[:120]}"
            ),
        )
    except Exception:
        pass

    # B-094 (was dead as B-056): fire project artifact updater if this daemon
    # is linked to a project. Uses daemon.project_id directly — no DB fetch
    # needed, it's on the model. daemon_conv_id (= daemon.id) is the stable
    # journal folder UUID -- kept here only for the updater's own logging,
    # not for a DB lookup (see conversation_text below).
    #
    # Excluded: "system"-kind daemons (currently just the project's own
    # auto-created sync daemon) already write to this project's .eva files
    # directly via skill-runner's project_artifact_write calls during the
    # run itself. Running this hook for one too would be a second,
    # redundant LLM extraction pass over that daemon's own thin top-level
    # summary ("delegated to skill-runner, completed") -- real risk of a
    # weaker model producing duplicate/low-value content from a
    # content-free summary, not just wasted tokens.
    #
    # 2026-08-18: was a name-string comparison against sync_daemon_name(),
    # a fragile check coupled to a computed slug rather than a real type.
    # daemon.kind == "system" is the same behavior (only the auto-created
    # sync daemon is ever given that kind, via api/projects.py's
    # _ensure_sync_daemon()), just no longer name-coupled.
    if (
        success
        and getattr(daemon, "project_id", None)
        and getattr(daemon, "kind", "standard") != "system"
    ):
        try:
            from core.project.artifact_updater import project_artifact_updater
            from config.settings import get_user_session
            # B-094: the DB has no Message rows for a daemon run (daemons
            # write .eva files directly, by design, to avoid message-table
            # growth for repetitive automated runs) -- so pass this run's
            # own in-memory trigger + output directly instead of a
            # conversation_id the updater could never find anything for.
            asyncio.create_task(
                project_artifact_updater.update(
                    project_id=daemon.project_id,
                    conversation_id=daemon_conv_id,
                    conversation_text=f"USER: {trigger}\nASSISTANT: {output}",
                    user_id=user_id,
                    session=get_user_session(user_id),
                    execution_id=execution_id,
                    workspace_path=getattr(execution, "project_workspace", None),
                )
            )
        except Exception as _exc:
            logger.debug("B-094: could not fire project artifact updater for daemon: %s", _exc)

    # ------------------------------------------------------------------
    # B-058: Phase 2 — UPDATE execution_metrics row at run end.
    # Records final status, duration, model/provider, token counts, and error.
    # ------------------------------------------------------------------
    try:
        from core.db.session import AsyncSessionLocal as _ASL2
        from core.db.repository import ExecutionMetricsRepo as _EMRepo2
        final_status = "completed" if success else "failed"
        _now = datetime.now(timezone.utc)
        async with _ASL2() as _end_db:
            await _EMRepo2.upsert(_end_db, {
                "execution_id":       execution_id,
                "conversation_id":    daemon_conv_id,
                "user_id":            user_id,
                "status":             final_status,
                "triggered_by":       "daemon",
                "daemon_name":        daemon.name,
                "project_id":         project_id,
                "termination_reason": error_type if not success else "success",  # Gap 4: exception class name, consistent with worker path
                "error_message":      error_msg if not success else None,
                "duration_ms":        _duration_ms,                          # Gap 5
                "model_used":         model,                                  # Gap 6
                "provider_used":      provider,                               # Gap 6
                "prompt_tokens":      prompt_tokens,                          # Gap 7
                "completion_tokens":  completion_tokens,                      # Gap 7
                "reasoning_tokens":   reasoning_tokens,                       # Gap 7
                "cached_tokens":      cached_tokens,
                "completed_at":       _now,
            })
    except Exception as _exc:
        logger.warning("Daemon: could not write phase-2 metrics: %s", _exc)

    # ── Journal: execution end ────────────────────────────────────────────────
    ConversationJournal.log_daemon_exec(user_id, daemon_conv_id, execution_id, {
        "type":          "execution_end",
        "execution_id":  execution_id,
        "daemon_name":   daemon.name,
        "success":       success,
        "output":        output,
        "output_chars":  len(output),
        "error":         error_msg,
        "failure_class": failure_class,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    })

    return {
        "success":       success,
        "output":        output,
        "error":         error_msg,
        "execution_id":  execution_id,
        "failure_class": failure_class,
    }
