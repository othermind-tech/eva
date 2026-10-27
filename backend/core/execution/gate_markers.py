# core/execution/gate_markers.py
"""
File-based liveness markers for EVA's blocking pause gates (B2-1.7/4.3).

Background
----------
Three execution-pause mechanisms share an identical shape: generate a
token, register broker keys, transition the execution's status, then
block on `client.blpop(...)` inside the SAME in-flight asyncio task until
a decision arrives or a timeout fires.

  - core/tools/approval.py::gate_approval()               -> AWAITING_APPROVAL
  - core/execution/pev_runner.py::_gate_plan_approval()    -> AWAITING_PLAN_APPROVAL
  - core/execution/step_extension.py::gate_step_extension() -> AWAITING_STEP_EXTENSION

All of that state -- the broker keys AND the suspended coroutine itself --
lives only in-process (LocalRedisAdapter is in-memory only; a suspended
Python `await` cannot be serialized across a process boundary). A real
process restart doesn't just lose the data, it destroys the waiting
coroutine outright, so there is no way to truly resume it -- only to
detect, after the fact, that it died mid-wait and fail it cleanly.

Design
----------------------------------------------------------------------
Deliberately NOT a DB table. core/db/models.py::ExecutionMetrics is
written exactly once, already in a terminal state, at execution finalize
-- SQLite in this codebase only ever records COMPLETED executions, never
in-progress granularity. Adding an early/in-progress row here would blur
that boundary for the sake of a narrow crash-detection need. A small file
per currently-paused execution satisfies the actual requirement (detect
"was this execution paused when the process died", not "resume it") at a
fraction of the weight, and never becomes durable history: it's deleted
the instant the gate resolves, or by the boot-time scan that discovers it
was orphaned.

Location: config.settings.EXECUTION_RUNTIME_DIR -- derived from
EVA_CONFIG_DIR (Electron's app.getPath('userData'), already passed to the
packaged backend for EVA_LOG_DIR/DATABASE_URL) in production, a
project-relative .runtime/executions/ dev fallback otherwise. Survives
reinstalls, per-user-writable, no admin rights needed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import EXECUTION_RUNTIME_DIR

logger = logging.getLogger(__name__)

GATE_KIND_APPROVAL = "approval"
GATE_KIND_PLAN_APPROVAL = "plan_approval"
GATE_KIND_STEP_EXTENSION = "step_extension"

_VALID_GATE_KINDS = {GATE_KIND_APPROVAL, GATE_KIND_PLAN_APPROVAL, GATE_KIND_STEP_EXTENSION}


def _runtime_dir() -> Path:
    d = Path(EXECUTION_RUNTIME_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _marker_path(execution_id: str) -> Path:
    return _runtime_dir() / f"{execution_id}.json"


def write_gate_marker(
    *,
    execution_id: str,
    conversation_id: str,
    user_id: str,
    gate_kind: str,
    token: str,
    tool_name: Optional[str] = None,
    authority: Optional[str] = None,
) -> None:
    """
    Write (or overwrite) the liveness marker for a paused execution.

    Best-effort: a write failure must never block the gate itself, matching
    the existing broker pending-key registration's own best-effort error
    handling in these same gate functions -- a missing marker just means
    this particular pause won't be detected as orphaned on a future crash,
    not that the gate can't proceed.

    Atomic: writes to a sibling .tmp file then os.replace()s it into place,
    so a crash mid-write can never leave a half-written/corrupt marker for
    the boot scan to choke on (os.replace is atomic on the same volume on
    both Windows and POSIX).
    """
    if gate_kind not in _VALID_GATE_KINDS:
        raise ValueError(f"Unknown gate_kind '{gate_kind}'")

    data = {
        "execution_id": execution_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "gate_kind": gate_kind,
        "token": token,
        "tool_name": tool_name,
        "authority": authority,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _marker_path(execution_id)
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.warning(
            "[GateMarker] failed to write marker (non-fatal)",
            extra={"execution_id": execution_id, "gate_kind": gate_kind, "error": str(exc)},
        )


def delete_gate_marker(execution_id: str) -> None:
    """
    Best-effort delete -- called when a gate resolves (approved, rejected,
    edited, continued, or timed out) and by boot-time reconciliation once
    an orphaned marker has been processed.
    """
    try:
        _marker_path(execution_id).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning(
            "[GateMarker] failed to delete marker (non-fatal)",
            extra={"execution_id": execution_id, "error": str(exc)},
        )


def scan_orphaned_markers() -> List[Dict[str, Any]]:
    """
    Return every marker currently on disk.

    Intended to be called exactly once, early at boot, before any new gate
    in this process could possibly have written one -- so everything found
    here is provably left over from a previous process that died before
    resolving (or explicitly cleaning up) it.

    A marker that fails to parse (rare -- would require a crash in the
    exact instant between the temp-file write and the atomic rename, which
    the write path is specifically designed to avoid) is logged and
    removed rather than left to jam future scans.
    """
    results: List[Dict[str, Any]] = []
    d = _runtime_dir()
    for path in d.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(data)
        except Exception as exc:
            logger.warning(
                "[GateMarker] failed to parse marker during boot scan -- removing",
                extra={"path": str(path), "error": str(exc)},
            )
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
    return results
