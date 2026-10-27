# Load startup config FIRST — creates the token-signing key on first run and runs the
# eva.env → credential-store migration of provider keys.  No secret is put in os.environ:
# provider keys are resolved on demand from the OS credential store (providers/credentials.py)
# and the signing key via core/auth/signing_key.py.  See config/env_loader.py.
from config.env_loader import load_eva_env
load_eva_env()

from config.logging import configure_logging
from config.settings import LOG_DIR
configure_logging(log_dir=LOG_DIR)
# Register the structured-logging subscriber with the event bus.
# Must come AFTER configure_logging() (so logging handlers are ready) and
# BEFORE any business-logic imports that might publish events at import time.
import observability.logger  # noqa: F401  (side-effect: registers bus subscribers)
# Register the metrics collector with the event bus.
# Must come immediately after observability.logger so that both
# subscribers are in place before any business-logic modules load.
import core.metrics.collector  # noqa: F401  (side-effect: registers metrics subscriber)
import asyncio
from typing import Optional
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from core.auth.dependencies import get_current_auth
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from starlette.exceptions import HTTPException as StarletteHTTPException
from api.chat import router as chat_router
from api.session import router as session_router
from api.eva_response import router as eva_response_router  
from api.cancel import router as cancel_router
from api.auth import router as auth_router
from api.rag import router as rag_router
from api.conversation_management import router as conversation_management_router
from api.mcp import router as mcp_router
from api.tools import router as tools_router
from api.executions import router as executions_router
from api.wakeups import router as wakeups_router
from api.processes import router as processes_router
from api.internal import router as internal_router
from api.providers import router as providers_router
from api.approval import router as approval_router
from api.memory import router as memory_router
from api.skills import router as skills_router
from api.secrets import router as secrets_router
from api.agents import router as agents_router
from api.daemons import router as daemons_router
from api.webhooks import router as webhooks_router
from api.projects import router as projects_router
from api.events import router as events_router
from api.prompts import router as prompts_router
from api.attachments import router as attachments_router
from api.hooks import router as hooks_router
from api.metrics import router as metrics_router
from core.providers import capability_registry
from core.tools.loader import load_all_tools
from core.db.session import engine
from sqlalchemy import text
from contextlib import asynccontextmanager
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command
from core.rag.init import ensure_rag_collection
from logging import getLogger

logger = getLogger(__name__)

def _run_alembic_upgrade() -> None:
    """
    Run 'alembic upgrade head' synchronously.

    Alembic's command interface is synchronous even for async migrations
    (the async engine is invoked internally via asyncio.run() inside env.py).
    Called via asyncio.to_thread() from the async lifespan to avoid blocking
    the event loop during startup.
    """
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_command.upgrade(alembic_cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # run at startup — apply all pending Alembic migrations (idempotent)
    logger.info("[DB] Running Alembic migrations...")
    await asyncio.to_thread(_run_alembic_upgrade)
    # Re-apply EVA's logging config after alembic's fileConfig() call.
    # alembic/env.py uses disable_existing_loggers=False, but we call
    # configure_logging() again here as a belt-and-suspenders guard.
    # The idempotency check in configure_logging() prevents handler duplication
    # on the second call — all three streams (server, errors, observability) are
    # restored if alembic stripped any of them.
    configure_logging(log_dir=LOG_DIR)
    # Suppress LiteLLM's verbose logger output (it also uses print() which
    # can't be silenced via logging, but this keeps the log level consistent).
    import logging as _logging
    _logging.getLogger("LiteLLM").setLevel(_logging.WARNING)
    _logging.getLogger("litellm").setLevel(_logging.WARNING)
    logger.info("[DB] Alembic migrations complete.")

    # ── LocalBroker startup ───────────────────────────────────────────────────
    from core.broker import local_broker as _local_broker
    await _local_broker.start()
    logger.info("[BROKER] LocalBroker started (Redis-free mode).")

    # ── MCP session pool startup (P-06b) ─────────────────────────────────────
    from core.mcp.session_pool import mcp_session_pool as _mcp_pool
    await _mcp_pool.start()
    logger.info("[MCP] Session pool started.")

    # ── MCP credential migration (S-11) ──────────────────────────────────────
    # One-time idempotent pass, safe on every boot -- mirrors
    # config/env_loader.py::load_eva_env()'s existing provider-key migration
    # precedent. Non-fatal: a failure here leaves those rows exactly as they
    # were (still plaintext, still functional) rather than blocking startup.
    try:
        from core.db.session import AsyncSessionLocal as _McpASL
        from core.mcp.credentials import migrate_existing_plaintext_credentials
        async with _McpASL() as _mcp_db:
            _mcp_migrated = await migrate_existing_plaintext_credentials(_mcp_db)
        if _mcp_migrated:
            logger.info(
                "[MCP] Credential migration: %d config(s) had plaintext "
                "secrets moved to the OS keyring.",
                _mcp_migrated,
            )
    except Exception as _mcp_migrate_exc:
        logger.warning(
            "[MCP] Credential migration failed (non-fatal) -- existing "
            "configs left untouched: %s",
            _mcp_migrate_exc,
        )

    # Ensure RAG collection exists
    ensure_rag_collection()

    # Recover orphaned executions (queries SQLite via broker)
    recovered = await _local_broker.recover_orphaned_executions()
    if recovered:
        logger.warning(
            "[RECOVERY] orphaned executions marked aborted",
            extra={"count": len(recovered)},
        )

    # Reconcile background_processes rows still marked "running" from a
    # prior process lifetime (2026-08-18 background-process-supervision
    # feature). A background process is spawned detached (its own process
    # group via start_new_session/CREATE_NEW_PROCESS_GROUP -- see
    # core/shell/executor.py's start_background()) specifically so it can
    # survive a backend restart; this sweep is what keeps the DB row in
    # sync with that reality instead of losing track of it. Non-fatal: a
    # failure here just means those rows keep whatever status they already
    # have until the next restart or the daily cleanup sweep's max-lifetime
    # reap catches them.
    try:
        from core.db.session import AsyncSessionLocal as _BgASL
        from core.db.repository import ProcessRepo as _BgProcRepo
        from core.shell.executor import _pid_matches_process as _bg_pid_matches
        async with _BgASL() as _bg_db:
            _running_rows = await _BgProcRepo.list_all_running(_bg_db)
            _reconciled_dead = 0
            for _row in _running_rows:
                # Identity check, not just liveness (2026-09-29): a bare PID
                # check here would risk re-adopting an unrelated process the
                # OS reused this PID for after a reboot/long uptime, instead
                # of correctly recognizing our own process is actually gone.
                if not _bg_pid_matches(_row.pid, _row.pid_create_time):
                    await _BgProcRepo.mark_status(_bg_db, _row.id, "crashed")
                    _reconciled_dead += 1
                    # Phase 3: publish process.crashed for consistency with
                    # stop_background()'s process.stopped event. Honest
                    # caveat: this sweep runs at boot, before any client has
                    # an SSE connection open, so this particular publish is
                    # realistically inert for live delivery -- the frontend
                    # will actually learn about the crash from its next
                    # GET /executions/running poll/fetch, not a push. Kept
                    # here anyway so the event fires wherever the status
                    # transition happens (same discipline as every other
                    # publish_user_event() call site), and so it *does* fire
                    # live for any future caller of this same reconciliation
                    # logic that isn't boot-time-only.
                    try:
                        from core.events_publisher import publish_user_event
                        await publish_user_event(
                            user_id=_row.user_id,
                            event="process.crashed",
                            execution_id=_row.id,
                            summary=f"Background process crashed: {' '.join(_row.command)}",
                        )
                    except Exception:
                        pass
                # else: still alive -- the row already says "running", which
                # is still accurate, so there's nothing to change. This is
                # the "re-adopt" case: no action needed because the DB
                # never claimed anything false in the first place.
        if _running_rows:
            logger.info(
                "[BACKGROUND_PROCESS] Boot reconciliation: %d row(s) checked, "
                "%d marked crashed (process no longer alive), %d re-adopted as still running.",
                len(_running_rows), _reconciled_dead, len(_running_rows) - _reconciled_dead,
            )
    except Exception as _bg_reconcile_exc:
        logger.warning(
            "[BACKGROUND_PROCESS] Boot reconciliation failed (non-fatal): %s",
            _bg_reconcile_exc,
        )

    # Seed provider capability registry from LiteLLM (non-fatal if network unavailable)
    await capability_registry.refresh_from_litellm(force=True)
    # NOTE (V4.3): Ollama models are no longer registered in the static capability
    # registry.  They are listed on demand via resolver.list_models("ollama") with
    # a TTL cache.  The refresh_from_ollama() call has been removed.
    # Skills registries are now per-user and loaded lazily on first access
    # (~/eva_workspace/{user_id}/skills/).  No startup pre-load is needed or
    # possible since there is no single global skills directory.

    # Backfill any skills shipped since a user's last seed (e.g. new
    # skills/ folders added to the repo after that user already signed up)
    # into every existing user's workspace. New users already get every
    # current skill automatically at signup (core/defaults/seeder.py's
    # seed_user_defaults(), called from api/auth.py) -- this covers
    # everyone who signed up before this backfill existed. Idempotent,
    # non-fatal, safe on every boot -- see the function's own docstring.
    try:
        from core.defaults.seeder import backfill_new_skills_for_all_users
        await backfill_new_skills_for_all_users()
    except Exception as _skill_backfill_exc:
        logger.warning(
            "[DEFAULTS] Skill backfill failed (non-fatal): %s", _skill_backfill_exc
        )

    # ── Start ExecutionWorker as an in-process asyncio task ──────────────────
    # No longer requires a separate `python run_worker.py` process.
    _worker_task = None
    try:
        from core.execution.worker import ExecutionWorker
        _worker = ExecutionWorker()
        _worker_task = asyncio.create_task(_worker.start(), name="execution-worker")
        logger.info("[WORKER] ExecutionWorker started in-process.")
    except Exception as _worker_exc:
        logger.warning("[WORKER] ExecutionWorker failed to start (non-fatal): %s", _worker_exc)

    # Start daemon scheduler (G-008). Non-fatal: if it fails to start,
    # daemons can still be triggered manually via POST /daemons/{name}/trigger.
    _scheduler_task = None
    try:
        from core.daemons.scheduler import DaemonScheduler
        # DaemonScheduler uses LocalBroker via run_daemon() internally
        _scheduler = DaemonScheduler()
        _scheduler_task = asyncio.create_task(_scheduler.run(), name="daemon-scheduler")
        logger.info("[DAEMONS] Scheduler started.")
    except Exception as sched_exc:
        logger.warning("[DAEMONS] Scheduler failed to start (non-fatal): %s", sched_exc)

    # Start wakeup scheduler (P-19 Phase 1). Non-fatal: if it fails to
    # start, schedule_wakeup calls still persist a row, it just won't be
    # resolved until the server is restarted with this working.
    _wakeup_scheduler_task = None
    try:
        from core.execution.wakeup_scheduler import WakeupScheduler
        _wakeup_scheduler = WakeupScheduler()
        _wakeup_scheduler_task = asyncio.create_task(
            _wakeup_scheduler.run(), name="wakeup-scheduler"
        )
        logger.info("[WAKEUP] Scheduler started.")
    except Exception as wakeup_sched_exc:
        logger.warning(
            "[WAKEUP] Scheduler failed to start (non-fatal): %s", wakeup_sched_exc
        )

    # Start workspace cleanup task (B-075). Runs every 24 hours. Non-fatal.
    _cleanup_task = None
    try:
        from core.tasks.cleanup import workspace_cleanup_loop
        _cleanup_task = asyncio.create_task(
            workspace_cleanup_loop(), name="workspace-cleanup"
        )
        logger.info("[CLEANUP] Workspace cleanup task started (runs every 24h).")
    except Exception as _cleanup_exc:
        logger.warning(
            "[CLEANUP] Workspace cleanup task failed to start (non-fatal): %s", _cleanup_exc
        )
    # Register event-triggered daemon subscriptions (G-009).
    # Sync handlers (event_bus is synchronous) schedule async tasks via create_task.
    try:
        from core.event_bus import event_bus
        from core.events import ExecutionCompleted, ExecutionFailed
        import json as _json

        async def _trigger_subscribed_daemons(
            event_type: str, event_data: dict, project_id: Optional[str] = None
        ) -> None:
            from core.daemons.registry import daemon_registry as _dr
            from core.daemons.runner import run_daemon as _run
            import uuid as _uuid
            # P-05b fix (2026-07-27): project_id scopes which daemons are
            # even eligible to react -- see list_subscribed()'s docstring.
            daemons = await _dr.list_subscribed(event_type, project_id=project_id)
            for _daemon in daemons:
                _eid = str(_uuid.uuid4())
                _trigger = (
                    f"You have been triggered by an internal EVA event.\n"
                    f"Event type: {event_type}\n"
                    f"Event data: {_json.dumps(event_data)}"
                )
                try:
                    await _dr.set_lifecycle_status(_daemon.user_id, _daemon.name, "running", execution_id=_eid)
                except Exception as _ls_exc:
                    logger.warning("[DAEMONS] set_lifecycle_status failed for %s: %s", _daemon.name, _ls_exc)

                async def _run_ev(_d=_daemon, _e=_eid, _t=_trigger) -> None:
                    # run_daemon uses LocalBroker internally
                    _res = await _run(daemon=_d, execution_id=_e, override_trigger=_t)
                    try:
                        if _res.get("success"):
                            await _dr.record_success(_d.user_id, _d.name)
                        else:
                            await _dr.record_failure(_d.user_id, _d.name, _res.get("error", ""))
                    except Exception as _rec_exc:
                        logger.warning("[DAEMONS] record_success/failure failed for %s: %s", _d.name, _rec_exc)

                asyncio.create_task(_run_ev(), name=f"event-daemon-{_daemon.name}")

        def _make_event_handler(evt_type: str):
            def _handler(event) -> None:
                _data = {
                    "execution_id": getattr(event, "execution_id", None),
                    "duration_ms":  getattr(event, "duration_ms", None),
                }
                if evt_type == "execution.failed":
                    _data["error_type"] = getattr(event, "error_type", None)
                _project_id = getattr(event, "project_id", None)
                try:
                    asyncio.get_running_loop().create_task(
                        _trigger_subscribed_daemons(evt_type, _data, _project_id),
                        name=f"daemon-event-trigger-{evt_type}",
                    )
                except Exception as _ct_exc:
                    logger.warning("[DAEMONS] Failed to schedule event-triggered daemon for %s: %s", evt_type, _ct_exc)
            return _handler

        event_bus.subscribe(ExecutionCompleted, _make_event_handler("execution.completed"))
        event_bus.subscribe(ExecutionFailed,    _make_event_handler("execution.failed"))
        logger.info("[DAEMONS] Event subscriptions registered.")
    except Exception as _ev_exc:
        logger.warning("[DAEMONS] Event subscription setup failed (non-fatal): %s", _ev_exc)
    yield

    # ------------------------------------------------------------------
    # SHUTDOWN
    # ------------------------------------------------------------------
    if _worker_task is not None:
        _worker_task.cancel()
        logger.info("[WORKER] ExecutionWorker stopped.")
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        logger.info("[DAEMONS] Scheduler stopped.")
    if _wakeup_scheduler_task is not None:
        _wakeup_scheduler_task.cancel()
        logger.info("[WAKEUP] Scheduler stopped.")
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        logger.info("[CLEANUP] Workspace cleanup task stopped.")
    await _mcp_pool.stop()
    logger.info("[MCP] Session pool stopped.")
    await _local_broker.stop()
    logger.info("[BROKER] LocalBroker stopped.")

app = FastAPI(lifespan=lifespan)

# ---------------------------------------------------------------------------
# C-3 — CORS middleware
# Allow browser clients on the Vite dev server (and any configured origins)
# to call the API.  Without this, preflight OPTIONS requests are rejected and
# the frontend cannot communicate with the backend when they run on different
# ports/domains.  In production extend CORS_ORIGINS via environment variable.
# ---------------------------------------------------------------------------
import os as _os

_cors_origins_env = _os.environ.get("CORS_ORIGINS", "")
_cors_origins: list[str] = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env
    else ["http://localhost:5173", "http://127.0.0.1:5173"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID", "Cache-Control"],
)

# ---------------------------------------------------------------------------
# B-014 — Consistent error response envelope
# ---------------------------------------------------------------------------
_HTTP_CODE_MAP = {
    400: "EVA_BAD_REQUEST",
    401: "EVA_UNAUTHORIZED",
    403: "EVA_FORBIDDEN",
    404: "EVA_NOT_FOUND",
    409: "EVA_CONFLICT",
    422: "EVA_VALIDATION_ERROR",
    500: "EVA_INTERNAL_ERROR",
}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Pydantic V2 exc.errors() may include non-JSON-serializable values in the
    # 'ctx' dict (e.g. ctx={'error': ValueError(...)}).  Stringify them so
    # JSONResponse never raises TypeError and returns 500 instead of 422.
    def _safe_errors(errors):
        safe = []
        for err in errors:
            e = dict(err)
            if "ctx" in e and isinstance(e["ctx"], dict):
                e["ctx"] = {k: str(v) for k, v in e["ctx"].items()}
            if "url" in e:
                e["url"] = str(e["url"])
            safe.append(e)
        return safe

    return JSONResponse(
        status_code=422,
        content={
            "code": "EVA_VALIDATION_ERROR",
            "detail": "Request validation failed",
            "fields": _safe_errors(exc.errors()),
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    code = _HTTP_CODE_MAP.get(exc.status_code, f"EVA_{exc.status_code}")
    # Preserve structured detail (dict/list) so clients can parse typed error payloads.
    # Only stringify when the detail is a plain scalar (str, int, etc.).
    detail = exc.detail if isinstance(exc.detail, (dict, list)) else str(exc.detail)
    # Live-found (S-02 verification): exc.headers was never passed through,
    # so any HTTPException raised with custom headers anywhere in the app --
    # e.g. Retry-After on the webhook (S-3) and auth (S-02) rate limiters --
    # silently lost them, since this is the only StarletteHTTPException
    # handler registered and it intercepts every HTTPException raised.
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": code, "detail": detail},
        headers=getattr(exc, "headers", None),
    )


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# B-017 — Detailed health/readiness endpoint
# Auth-gated: exposes internal infrastructure topology (broker, vector store, worker
# liveness) — should only be visible to authenticated operators/admins.
# The basic /health endpoint remains public for load-balancer liveness probes.
# ---------------------------------------------------------------------------
@app.get("/health/ready")
async def health_ready(auth=Depends(get_current_auth)):
    from core.rag.client import get_chroma_client  # noqa: PLC0415

    checks: dict = {}

    # DB
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        logger.warning("Health check: DB unavailable", exc_info=exc)
        checks["db"] = f"error: {exc}"

    # Broker (LocalBroker — Redis-free single-process mode)
    try:
        from core.broker import local_broker as _lb
        adapter = _lb.client
        await adapter.ping()
        checks["broker"] = "ok"
    except Exception as exc:
        logger.warning("Health check: LocalBroker unavailable", exc_info=exc)
        checks["broker"] = f"error: {exc}"

    # ChromaDB (synchronous client — run in thread to avoid blocking event loop)
    try:
        chroma_client = get_chroma_client()
        await asyncio.to_thread(chroma_client.list_collections)
        checks["vector_store"] = "ok"
    except Exception as exc:
        logger.warning("Health check: ChromaDB unavailable", exc_info=exc)
        checks["vector_store"] = f"error: {exc}"

    # Worker liveness (reads heartbeat key set by ExecutionWorker every ~15 s)
    try:
        from core.broker import local_broker as _lb
        heartbeat = await _lb.client.get("worker:heartbeat")
        checks["worker"] = "ok" if heartbeat else "stale"
    except Exception as exc:
        logger.warning("Health check: Worker heartbeat check failed", exc_info=exc)
        checks["worker"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )

# Load all tools at startup (also called inside lifespan before worker start)
load_all_tools()
# Add routers
app.include_router(chat_router)
app.include_router(session_router)
app.include_router(eva_response_router)
app.include_router(cancel_router)
app.include_router(auth_router)
app.include_router(rag_router)
app.include_router(conversation_management_router)
app.include_router(mcp_router)
app.include_router(tools_router)
app.include_router(executions_router)
app.include_router(wakeups_router)  # Manual pause/resume/cancel for scheduled_wakeups (P-19 Tier A)
app.include_router(providers_router)
app.include_router(approval_router)  # Human-in-the-Loop approve/reject
app.include_router(memory_router)    # Long-Term Memory CRUD (V3.0)
app.include_router(skills_router)    # Skills management (V3.1)
app.include_router(secrets_router)   # Secrets / API key management (V4.4)
app.include_router(agents_router)    # Subagent configuration CRUD (G-003 / V4.8)
app.include_router(daemons_router)   # Daemon agent CRUD + trigger (G-008 / V4.12)
app.include_router(webhooks_router)       # Daemon webhooks public endpoint (G-009)
app.include_router(projects_router)      # Project entity CRUD + .eva context (B-045)
app.include_router(events_router)        # User-facing Event SSE feed (B-051)
app.include_router(prompts_router)       # Prompt Enhancer (B-055)
app.include_router(attachments_router)   # Attachment library + conversation attachments (B-066)
app.include_router(hooks_router)         # Hook script CRUD (B-064)
app.include_router(metrics_router)      # Usage Metrics aggregation (B-073)
app.include_router(internal_router)     # Localhost-only graceful-shutdown endpoint (V6.58 Phase 3)
app.include_router(processes_router)    # Background process stop endpoint (Phase 3, background-process-supervision plan)
