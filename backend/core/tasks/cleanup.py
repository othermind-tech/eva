"""
Workspace Lifecycle Cleanup Task (B-075) + Auth Retention (DB-01) +
Scheduled Wakeup Retention (P-19 Phase 1) + Background Process Retention
(2026-08-18) + Rate Limiter Key Retention (B2-1.4, 2026-08-18)

Runs as an asyncio background task every 24 hours from server startup.
Six independent passes on each cycle, each retried with exponential
backoff (B2-4.1, 2026-08-18) on failure -- see _run_pass_with_retry below
-- before giving up on that pass for this cycle:
  1. Deletes conversation scratch directories that have not been accessed
     in WORKSPACE_TTL_DAYS days (default 90).
  2. DB-01: purges dead auth_sessions rows and old auth_audit_logs rows
     (see _purge_auth_data below).
  3. P-19: purges resolved/cancelled scheduled_wakeups rows older than
     SCHEDULED_WAKEUP_RETENTION_DAYS (see _purge_wakeup_data below).
  4. Background processes: force-stops anything still "running" past
     BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS, then purges TERMINAL-status
     rows (and their log files) older than
     BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS (see
     _purge_background_process_data below).
  5. Rate limiter keys: evicts stale keys (no calls within the current
     window) from every registered SlidingWindowRateLimiter, closing the
     unbounded-key-space growth the class itself documents (see
     _purge_rate_limiter_keys below).
  6. A-04 Phase B: purges user_memory_facts / agent_memory_entries rows
     past their own per-row expires_at (see _purge_expired_memory below).

Safety guarantees
-----------------
- Workspace pass only touches ~/eva_workspace/{user_id}/conversations/{conv_id}/
  subdirs. NEVER deletes workspace/, projects/, rag/, memory/, skills/, or
  hooks/. Uses ignore_errors=True during rmtree — a partial delete is
  acceptable; the next daily pass will clean up whatever remains.
- Auth pass only deletes AuthSession rows that are already dead (revoked or
  past refresh_expires_at) and AuthAuditLog rows older than
  AUTH_AUDIT_LOG_RETENTION_DAYS — never a live session.
- Wakeup pass only deletes rows already resolved or cancelled, never a
  still-pending one — a pending row is live state the scheduler still
  needs, not history.
- Background process pass only force-stops a "running" row once it exceeds
  the max-lifetime ceiling (a hard safety net, not routine behavior), and
  only purges rows already in a terminal status — never a still-running one,
  regardless of age.
- Rate limiter pass only evicts a key once its deque is empty (no calls in
  the current window) — never touches a key that's actually rate-limited
  or has recent activity.
- Memory expiry pass only deletes a row that has an explicit, already-past
  expires_at — a row with expires_at=null (the default for every user fact
  and every non-"error" agent entry) is never touched, regardless of age.
- A failing pass is retried up to CLEANUP_RETRY_MAX_ATTEMPTS times (with
  exponential backoff) within the same cycle before being logged as
  ERROR and deferred to the next scheduled cycle; other passes are
  unaffected either way -- server continues normally regardless.
"""

import asyncio
import logging
import pathlib

from config.settings import (
    WORKSPACE_CLEANUP_INTERVAL_SECONDS,
    AUTH_AUDIT_LOG_RETENTION_DAYS,
    SCHEDULED_WAKEUP_RETENTION_DAYS,
    BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS,
    BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS,
    CLEANUP_RETRY_MAX_ATTEMPTS,
    CLEANUP_RETRY_BASE_DELAY_SECONDS,
    CLEANUP_RETRY_MAX_DELAY_SECONDS,
)

logger = logging.getLogger(__name__)


def _cleanup_pass() -> dict:
    """
    Synchronous cleanup pass — runs in a thread pool via asyncio.to_thread().

    Walks all user directories under WORKSPACE_ROOT, finds stale conversation
    scratch dirs for each user, and deletes them.

    Returns a summary dict: {users_scanned, dirs_deleted, bytes_freed}.
    """
    from config.settings import WORKSPACE_ROOT, WORKSPACE_TTL_DAYS
    from utils.workspace import find_stale_conversation_dirs, delete_conversation_workspace

    workspace_root = pathlib.Path(WORKSPACE_ROOT)
    if not workspace_root.exists():
        return {"users_scanned": 0, "dirs_deleted": 0, "bytes_freed": 0}

    users_scanned = 0
    dirs_deleted  = 0
    bytes_freed   = 0

    for user_dir in workspace_root.iterdir():
        if not user_dir.is_dir():
            continue
        user_id = user_dir.name
        users_scanned += 1
        try:
            stale = find_stale_conversation_dirs(user_id, WORKSPACE_TTL_DAYS)
            for conv_id in stale:
                freed = delete_conversation_workspace(user_id, conv_id)
                bytes_freed += freed
                dirs_deleted += 1
                logger.info(
                    "[CLEANUP] Deleted stale conversation workspace: "
                    "user=%s conv=%s freed_bytes=%d",
                    user_id, conv_id, freed,
                )
        except Exception as exc:
            logger.warning(
                "[CLEANUP] Error scanning user=%s (skipping): %s", user_id, exc
            )

    summary = {
        "users_scanned": users_scanned,
        "dirs_deleted":  dirs_deleted,
        "bytes_freed":   bytes_freed,
    }
    logger.info("[CLEANUP] Workspace cleanup pass complete: %s", summary)
    return summary


async def _purge_auth_data() -> dict:
    """
    DB-01: delete dead AuthSession rows and old AuthAuditLog rows.

    Returns a summary dict: {sessions_purged, audit_logs_purged}.
    """
    from core.db.session import AsyncSessionLocal
    from core.db.repository import AuthRepo

    async with AsyncSessionLocal() as db:
        sessions_purged = await AuthRepo.purge_expired_sessions(db)
    async with AsyncSessionLocal() as db:
        audit_logs_purged = await AuthRepo.purge_old_audit_logs(
            db, AUTH_AUDIT_LOG_RETENTION_DAYS
        )

    summary = {
        "sessions_purged":   sessions_purged,
        "audit_logs_purged": audit_logs_purged,
    }
    logger.info("[CLEANUP] Auth retention pass complete: %s", summary)
    return summary


async def _purge_rate_limiter_keys() -> dict:
    """
    B2-1.4 (2026-08-18): sweep every registered SlidingWindowRateLimiter
    (login/signup/refresh, webhooks) for keys that will never recur --
    e.g. a failed login attempt from a username/IP never seen again --
    which would otherwise occupy an in-process dict entry for the life of
    the server. See core/rate_limit.py's own class docstring for why this
    is needed (the key space is unbounded by design).

    Returns a summary dict: {limiters_swept, keys_purged}.
    """
    from core.rate_limit import purge_all_stale_rate_limit_keys

    summary = await purge_all_stale_rate_limit_keys()
    logger.info("[CLEANUP] Rate limiter key purge complete: %s", summary)
    return summary


async def _purge_wakeup_data() -> dict:
    """
    P-19 Phase 1: delete resolved/cancelled scheduled_wakeups rows older
    than SCHEDULED_WAKEUP_RETENTION_DAYS.

    Returns a summary dict: {wakeups_purged}.
    """
    from core.db.session import AsyncSessionLocal
    from core.db.repository import WakeupRepo

    async with AsyncSessionLocal() as db:
        wakeups_purged = await WakeupRepo.purge_old_resolved(
            db, SCHEDULED_WAKEUP_RETENTION_DAYS
        )

    summary = {"wakeups_purged": wakeups_purged}
    logger.info("[CLEANUP] Wakeup retention pass complete: %s", summary)
    return summary


async def _purge_expired_memory() -> dict:
    """
    A-04 Phase B: delete user_memory_facts / agent_memory_entries rows past
    their own expires_at. Unlike the other retention passes, there's no
    single interval config here -- the TTL is per-row, decided once at
    write time (see AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS; only "error"-type
    agent entries get a non-null expires_at today). A row with
    expires_at=null (permanent) is never touched, regardless of age --
    that's the default for every user fact and every non-"error" agent
    entry, by design.

    Returns a summary dict: {user_facts_purged, agent_entries_purged}.
    """
    from core.db.session import AsyncSessionLocal
    from core.db.repository import MemoryRepo

    async with AsyncSessionLocal() as db:
        summary = await MemoryRepo.purge_expired(db)

    logger.info("[CLEANUP] Memory expiry pass complete: %s", summary)
    return summary


async def _purge_background_process_data() -> dict:
    """
    Two responsibilities for the background_processes table (2026-08-18):

    1. Force-stop anything still "running" past
       BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS, even if nobody ever called
       action="stop" on it. This is the auto-reap safety net (mirrors the
       auto-terminate behaviour comparable background-process tools use) --
       an untracked-owner-action process left running indefinitely is
       exactly the "invisible, unkillable orphan" risk flagged in the
       2026-08 backend security audit.
    2. Purge TERMINAL-status rows (and their log files on disk) older than
       BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS. Deliberately a short
       window (hours, not the days/months used for auth/wakeup retention) --
       see BackgroundProcess's docstring: a finished background process's
       row is operational state, not history, so it goes stale almost
       immediately once terminal.

    A row force-stopped in step 1 becomes eligible for step 2's purge only
    on a later cycle (its ended_at is "now" at the moment it's stopped, so
    it won't yet be older than the retention window) -- this is intentional,
    not a bug: it guarantees at least one full retention window during
    which a just-stopped row's final output/exit_code is still inspectable.

    Returns a summary dict: {force_stopped, rows_purged}.
    """
    from core.db.session import AsyncSessionLocal
    from core.db.repository import ProcessRepo
    from core.shell.executor import ShellExecutor

    force_stopped = 0
    async with AsyncSessionLocal() as db:
        stale_rows = await ProcessRepo.list_stale_running(
            db, BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS
        )
        stale = [(row.id, row.user_id) for row in stale_rows]

    for process_id, user_id in stale:
        try:
            await ShellExecutor.stop_background(process_id, user_id)
            force_stopped += 1
        except Exception as exc:
            logger.warning(
                "[CLEANUP] Failed to force-stop stale background process %s: %s",
                process_id, exc,
            )

    async with AsyncSessionLocal() as db:
        rows_purged = await ProcessRepo.purge_old_terminal(
            db, BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS
        )

    summary = {"force_stopped": force_stopped, "rows_purged": rows_purged}
    logger.info("[CLEANUP] Background process pass complete: %s", summary)
    return summary


async def _run_pass_with_retry(pass_name: str, pass_fn) -> None:
    """
    B2-4.1 (2026-08-18): runs one cleanup pass with retry-with-exponential-
    backoff on failure, instead of the previous single-attempt-then-wait-
    for-next-cycle behaviour. Reuses the same backoff shape
    core/daemons/registry.py's daemon retry policy already established
    (delay * 2^(attempt-1), capped) rather than a second retry idiom.

    Gives up to CLEANUP_RETRY_MAX_ATTEMPTS chances within THIS cycle before
    logging an ERROR (not just WARNING -- exhausting retries is a more
    serious signal than one failed attempt) and moving on; the next
    scheduled cycle (WORKSPACE_CLEANUP_INTERVAL_SECONDS later) starts
    fresh regardless.

    Cancellation (server shutdown) always propagates immediately, both
    from the pass itself and from a retry-backoff sleep -- never retried.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            await pass_fn()
            return
        except asyncio.CancelledError:
            logger.info("[CLEANUP] Workspace cleanup task cancelled.")
            raise
        except Exception as exc:
            if attempt >= CLEANUP_RETRY_MAX_ATTEMPTS:
                logger.error(
                    "[CLEANUP] %s pass failed after %d attempt(s) this cycle -- "
                    "giving up until the next scheduled run: %s",
                    pass_name, attempt, exc,
                )
                return
            backoff = min(
                CLEANUP_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                CLEANUP_RETRY_MAX_DELAY_SECONDS,
            )
            logger.warning(
                "[CLEANUP] %s pass failed (attempt %d/%d), retrying in %ds: %s",
                pass_name, attempt, CLEANUP_RETRY_MAX_ATTEMPTS, backoff, exc,
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                logger.info("[CLEANUP] Workspace cleanup task cancelled during retry backoff.")
                raise


async def workspace_cleanup_loop() -> None:
    """
    Infinite loop: run all five cleanup passes (each retried on failure
    via _run_pass_with_retry), then sleep 24 hours, repeat.

    Designed to be launched as ``asyncio.create_task(workspace_cleanup_loop())``.
    Task cancellation (on server shutdown) exits the loop cleanly. The five
    passes are independent — exhausting retries on one does not prevent
    the others from running this cycle.
    """
    from config.settings import WORKSPACE_TTL_DAYS
    logger.info(
        "[CLEANUP] Workspace cleanup task started (TTL=%d days, auth audit "
        "retention=%d days, wakeup retention=%d days, background process "
        "max lifetime=%ds / terminal retention=%dh, retry=%d attempts "
        "(base %ds, cap %ds), interval=%ds).",
        WORKSPACE_TTL_DAYS,
        AUTH_AUDIT_LOG_RETENTION_DAYS,
        SCHEDULED_WAKEUP_RETENTION_DAYS,
        BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS,
        BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS,
        CLEANUP_RETRY_MAX_ATTEMPTS,
        CLEANUP_RETRY_BASE_DELAY_SECONDS,
        CLEANUP_RETRY_MAX_DELAY_SECONDS,
        WORKSPACE_CLEANUP_INTERVAL_SECONDS,
    )
    while True:
        await _run_pass_with_retry(
            "Workspace cleanup", lambda: asyncio.to_thread(_cleanup_pass)
        )
        await _run_pass_with_retry("Auth retention", _purge_auth_data)
        await _run_pass_with_retry("Wakeup retention", _purge_wakeup_data)
        await _run_pass_with_retry("Background process", _purge_background_process_data)
        await _run_pass_with_retry("Rate limiter key purge", _purge_rate_limiter_keys)
        await _run_pass_with_retry("Memory expiry", _purge_expired_memory)

        try:
            await asyncio.sleep(WORKSPACE_CLEANUP_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("[CLEANUP] Workspace cleanup task cancelled during sleep.")
            raise
