"""
EVA Logging Configuration

Sets up three log streams, all using HybridRotatingFileHandler — a subclass of
TimedRotatingFileHandler that also enforces a per-file size cap.  This gives:

  - Daily midnight rotation (files named by date suffix automatically)
  - Mid-day size overflow rotation if a single day's volume exceeds the cap
  - Sequenced suffix for same-day size rollovers (e.g. server.log.2026-05-29.2)

Three log streams:

  logs/server.log          root logger, INFO+   — app startup, DB, general events
  logs/errors.log          root logger, WARNING+ — errors/warnings only, long retention
  logs/observability.log   eva.observability logger only — OBS_EVENTs (execution,
                           tool calls, metrics).  Propagation to root is controlled
                           by LOG_OBS_PROPAGATE (default False = no duplication).

Option B — Rotate on restart:
  Each log file is rotated at startup if it already contains data.  This gives
  every server launch (including Electron relaunches) a clean, empty file.
  Archived files follow the standard date-suffix scheme.

Option C — Session ID in every log entry:
  A UUID4 is generated once at module import time and stored as SERVER_SESSION_ID
  (8-char short form) and SERVER_SESSION_ID_FULL (full UUID).  A _SessionFilter
  injects server_session_id into every LogRecord so that log analysis tools can
  filter by process launch — critical for multi-session analytics (B-085) and
  distinguishing output from concurrent worker processes.

All paths and thresholds are read from config.settings so they can be overridden
via env vars without touching code.  The Electron shell should set EVA_LOG_DIR to
the OS user-data path before spawning the backend.

Safe to call more than once (idempotent):
  - Never duplicates a handler for the same resolved file path
  - Re-applies formatter on existing handlers
  - The lifespan guard in main.py calls configure_logging() a second time after
    Alembic runs to restore any handlers Alembic's fileConfig() may have wiped
"""

import logging
import os
import time
import uuid
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


# ---------------------------------------------------------------------------
# Option C — Server session identity (generated once at import time)
# ---------------------------------------------------------------------------

_raw_session_id: str = str(uuid.uuid4())

SERVER_SESSION_ID: str = _raw_session_id[:8]
"""8-character short session ID — included in every log line."""

SERVER_SESSION_ID_FULL: str = _raw_session_id
"""Full UUID4 session ID — exported for health endpoints, Electron shell, analytics."""


_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(server_session_id)s | %(name)s | %(message)s"


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

class ExtraFormatter(logging.Formatter):
    """Appends any extra={} fields passed to logger calls to the log line."""

    _STANDARD_KEYS = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime",
        "taskName",
        "server_session_id",   # Option C — injected by _SessionFilter; not an "extra"
    }

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in self._STANDARD_KEYS
        }
        if extras:
            return f"{base} | extra={extras}"
        return base


# ---------------------------------------------------------------------------
# Option C — Session filter (injects server_session_id into every LogRecord)
# ---------------------------------------------------------------------------

class _SessionFilter(logging.Filter):
    """Stamps every LogRecord with the current server session ID."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.server_session_id = SERVER_SESSION_ID
        return True


# ---------------------------------------------------------------------------
# Hybrid rotating handler  (time + size)
# ---------------------------------------------------------------------------

class HybridRotatingFileHandler(TimedRotatingFileHandler):
    """
    TimedRotatingFileHandler extended with a per-file size cap.

    Rotation triggers when EITHER condition is met:
      1. The wall clock crosses midnight (when='midnight')
      2. The current log file exceeds max_bytes

    Filename scheme (suffix appended by base class at rollover):
      server.log.2026-05-29      — first rollover for that date (midnight or size)
      server.log.2026-05-29.2    — second rollover on the same date (size overflow)
      server.log.2026-05-29.3    — third, etc.

    backupCount is inherited from TimedRotatingFileHandler and counts rollover
    events (not calendar days) — the oldest file is deleted when the count is
    exceeded regardless of whether it was a date or size rollover.
    """

    def __init__(self, filename: str, max_bytes: int = 50 * 1024 * 1024,
                 backup_count: int = 30, encoding: str = "utf-8"):
        super().__init__(
            filename,
            when="midnight",
            backupCount=backup_count,
            encoding=encoding,
            utc=False,
        )
        self.max_bytes = max_bytes

    def shouldRollover(self, record: logging.LogRecord) -> int:  # type: ignore[override]
        """Return 1 if either the time condition or size condition is met."""
        # Time-based check from parent
        if super().shouldRollover(record):
            return 1
        # Size-based check
        if self.max_bytes > 0 and self.stream:
            self.stream.seek(0, 2)  # seek to end
            if self.stream.tell() >= self.max_bytes:
                return 1
        return 0

    def rotation_filename(self, default_name: str) -> str:
        """
        Append a sequence counter when multiple rollovers occur on the same date.

        The base class produces names like server.log.2026-05-29.  If that file
        already exists (same-day size overflow), we append .2, .3, etc.
        """
        candidate = default_name
        counter = 2
        while os.path.exists(candidate):
            candidate = f"{default_name}.{counter}"
            counter += 1
        return candidate


# ---------------------------------------------------------------------------
# WARNING-only filter (for errors.log)
# ---------------------------------------------------------------------------

class _WarningFilter(logging.Filter):
    """Pass only WARNING and above — keeps INFO noise out of errors.log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Shared filter instance — one instance is enough; all handlers share state-free filter.
_session_filter = _SessionFilter()


def configure_logging(
    log_dir: str | None = None,
    server_log_name: str = "server.log",
) -> None:
    """
    Set up (or restore) EVA's three-stream logging configuration.

    Parameters
    ----------
    log_dir:
        Absolute path to the directory where log files are written.
        Defaults to the value of LOG_DIR in config.settings
        (which itself defaults to <project_root>/logs/ and can be
        overridden via the EVA_LOG_DIR env var).
    server_log_name:
        Filename for the main log stream.  Use "worker.log" when
        calling from run_worker.py so the worker gets its own file
        while sharing the observability and errors streams.

    Streams created
    ---------------
    {log_dir}/{server_log_name}   — root logger, INFO+
    {log_dir}/errors.log          — root logger, WARNING+ only
    {log_dir}/observability.log   — eva.observability logger only

    Option B (rotate on restart):
        If a log file already exists and is non-empty when this function runs
        for the first time (i.e. a new handler is being created), it is rotated
        immediately so that the current server session starts with a clean file.

    Safe to call more than once (idempotent).
    """
    # ── Import settings lazily to avoid circular-import issues at startup ──
    from config.settings import (
        LOG_DIR,
        LOG_SERVER_MAX_BYTES, LOG_OBS_MAX_BYTES, LOG_ERROR_MAX_BYTES,
        LOG_SERVER_BACKUP_COUNT, LOG_OBS_BACKUP_COUNT, LOG_ERROR_BACKUP_COUNT,
        LOG_OBS_PROPAGATE,
    )

    resolved_dir = Path(log_dir if log_dir is not None else LOG_DIR).resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)

    server_path = resolved_dir / server_log_name
    obs_path    = resolved_dir / "observability.log"
    errors_path = resolved_dir / "errors.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # ── Console (StreamHandler) ───────────────────────────────────────────
    stream_handlers = [h for h in root.handlers if type(h) is logging.StreamHandler]
    if not stream_handlers:
        ch = logging.StreamHandler()
        ch.setFormatter(ExtraFormatter(_LOG_FORMAT))
        ch.addFilter(_session_filter)
        root.addHandler(ch)
    else:
        for h in stream_handlers:
            h.setFormatter(ExtraFormatter(_LOG_FORMAT))
            if _session_filter not in h.filters:
                h.addFilter(_session_filter)

    # ── Helper: find existing HybridRotatingFileHandler for a path ────────
    def _existing(logger_obj: logging.Logger, path: Path):
        return [
            h for h in logger_obj.handlers
            if isinstance(h, TimedRotatingFileHandler)
            and Path(h.baseFilename).resolve() == path.resolve()
        ]

    # ── Option B helper: rotate file on startup if non-empty ─────────────
    def _rotate_if_nonempty(handler: HybridRotatingFileHandler, path: Path) -> None:
        """
        If the log file already exists and has content, rotate it immediately.
        This ensures each server session starts with a fresh, empty log file.
        Errors during rotation are silently ignored — logging must never crash startup.
        """
        try:
            if path.exists() and path.stat().st_size > 0:
                handler.doRollover()
        except Exception:
            pass  # non-fatal — continue with the existing file

    # ── server.log — root logger, INFO+ ───────────────────────────────────
    existing_server = _existing(root, server_path)
    if not existing_server:
        fh = HybridRotatingFileHandler(
            str(server_path),
            max_bytes=LOG_SERVER_MAX_BYTES,
            backup_count=LOG_SERVER_BACKUP_COUNT,
        )
        fh.setFormatter(ExtraFormatter(_LOG_FORMAT))
        fh.addFilter(_session_filter)
        _rotate_if_nonempty(fh, server_path)   # Option B
        root.addHandler(fh)
    else:
        for h in existing_server:
            h.setFormatter(ExtraFormatter(_LOG_FORMAT))
            if _session_filter not in h.filters:
                h.addFilter(_session_filter)

    # ── errors.log — root logger, WARNING+ only ───────────────────────────
    existing_errors = _existing(root, errors_path)
    if not existing_errors:
        eh = HybridRotatingFileHandler(
            str(errors_path),
            max_bytes=LOG_ERROR_MAX_BYTES,
            backup_count=LOG_ERROR_BACKUP_COUNT,
        )
        eh.setFormatter(ExtraFormatter(_LOG_FORMAT))
        eh.addFilter(_WarningFilter())
        eh.addFilter(_session_filter)
        _rotate_if_nonempty(eh, errors_path)   # Option B
        root.addHandler(eh)
    else:
        for h in existing_errors:
            h.setFormatter(ExtraFormatter(_LOG_FORMAT))
            if _session_filter not in h.filters:
                h.addFilter(_session_filter)

    # ── observability.log — eva.observability logger only ─────────────────
    obs_logger = logging.getLogger("eva.observability")
    obs_logger.propagate = LOG_OBS_PROPAGATE  # False = no duplication in server.log

    existing_obs = _existing(obs_logger, obs_path)
    if not existing_obs:
        oh = HybridRotatingFileHandler(
            str(obs_path),
            max_bytes=LOG_OBS_MAX_BYTES,
            backup_count=LOG_OBS_BACKUP_COUNT,
        )
        oh.setFormatter(ExtraFormatter(_LOG_FORMAT))
        oh.addFilter(_session_filter)
        _rotate_if_nonempty(oh, obs_path)      # Option B
        obs_logger.addHandler(oh)
    else:
        for h in existing_obs:
            h.setFormatter(ExtraFormatter(_LOG_FORMAT))
            if _session_filter not in h.filters:
                h.addFilter(_session_filter)
