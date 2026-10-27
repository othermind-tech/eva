# core/memory/conversation_journal.py
#
# Real-time append-only JSONL journal for conversations, executions,
# subagents, and daemons.
#
# Design decisions:
#   - Uses Python's logging.FileHandler so writes are:
#       * Thread-safe (internal RLock — no asyncio lock needed)
#       * Decoupled from the event loop (never blocks coroutines)
#       * Failure-silent (logging framework absorbs all errors)
#       * Non-propagating (propagate=False — never bleeds into app logs)
#   - Registry pattern: one Logger+FileHandler per conversation for the
#     process lifetime — no repeated file open/close on every event.
#   - All public methods are synchronous and never raise. Call them
#     inline after DB writes without await or try/except.
#
# Workspace layout produced:
#   conversations/{conv_id}/
#     conversation.jsonl         ← regular conversation (all executions)
#     summary.md                 ← generated on first reference, mtime-cached
#     subagents/
#       {agent_id}.jsonl         ← one file per subagent (Option B isolation)
#     exec_{execution_id}.jsonl  ← daemon per-execution journals
#     meta.json                  ← daemon identity (written once)

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any

from utils.workspace import get_conversation_dir, get_subagent_journal_dir


class _JsonlFormatter(logging.Formatter):
    """Formats each log record as a single compact JSON line."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        return json.dumps(record.msg, ensure_ascii=False, default=str)


class ConversationJournal:
    """
    Append-only JSONL journal for EVA conversations.

    Usage:
        ConversationJournal.log(user_id, conv_id, {"type": "user_message", ...})
        ConversationJournal.log_subagent(user_id, conv_id, agent_id, {...})
        ConversationJournal.log_daemon_exec(user_id, conv_id, exec_id, {...})
        ConversationJournal.write_daemon_meta(user_id, conv_id, {...})
    """

    _registry: dict[str, logging.Logger] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _get_logger(cls, log_path: pathlib.Path, logger_name: str) -> logging.Logger:
        if logger_name not in cls._registry:
            lgr = logging.getLogger(logger_name)
            lgr.setLevel(logging.DEBUG)
            lgr.propagate = False
            lgr.handlers.clear()
            handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
            handler.setFormatter(_JsonlFormatter())
            lgr.addHandler(handler)
            cls._registry[logger_name] = lgr
        return cls._registry[logger_name]

    # ------------------------------------------------------------------
    # Public API — all synchronous, all failure-silent
    # ------------------------------------------------------------------

    @classmethod
    def log(cls, user_id: str, conversation_id: str, event: dict[str, Any]) -> None:
        """Append one event to the conversation journal. Never raises."""
        try:
            log_path = get_conversation_dir(user_id, conversation_id) / "conversation.jsonl"
            cls._get_logger(log_path, f"journal.conv.{conversation_id}").info(event)
        except Exception:
            pass

    @classmethod
    def log_subagent(
        cls,
        user_id: str,
        conversation_id: str,
        agent_id: str,
        event: dict[str, Any],
    ) -> None:
        """Append one event to a subagent's isolated journal. Never raises."""
        try:
            log_path = get_subagent_journal_dir(user_id, conversation_id) / f"{agent_id}.jsonl"
            # B-095: must include conversation_id -- a shared, reusable subagent
            # (e.g. skill-runner, invoked from many different conversations)
            # otherwise has every call after the first silently reuse the first
            # conversation's cached Logger/FileHandler, misattributing its
            # journal entries into the wrong conversation's directory. The
            # log_path above was already conversation-scoped; only the cache
            # key wasn't. Matches log_daemon_exec's existing
            # {conversation_id}.{specific_id} shape.
            cls._get_logger(log_path, f"journal.subagent.{conversation_id}.{agent_id}").info(event)
        except Exception:
            pass

    @classmethod
    def log_daemon_exec(
        cls,
        user_id: str,
        conversation_id: str,
        execution_id: str,
        event: dict[str, Any],
    ) -> None:
        """Append one event to a daemon's per-execution journal. Never raises."""
        try:
            log_path = get_conversation_dir(user_id, conversation_id) / f"exec_{execution_id}.jsonl"
            cls._get_logger(log_path, f"journal.daemon.{conversation_id}.{execution_id}").info(event)
        except Exception:
            pass

    @classmethod
    def write_daemon_meta(
        cls,
        user_id: str,
        conversation_id: str,
        meta: dict[str, Any],
    ) -> None:
        """Write/overwrite meta.json for a daemon conversation. Never raises."""
        try:
            meta_path = get_conversation_dir(user_id, conversation_id) / "meta.json"
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass

    @classmethod
    def generate_daemon_summary(
        cls,
        user_id: str,
        daemon_conv_id: str,
    ) -> str | None:
        """
        Build (or return cached) summary.md for a daemon folder.

        Layout read:
          conversations/{daemon_conv_id}/meta.json          ← daemon identity
          conversations/{daemon_conv_id}/exec_*.jsonl       ← one per run

        Caching: summary.md is reused if its mtime >= newest exec_*.jsonl mtime.
        Returns the markdown string, or None if the folder doesn't exist.
        Never raises.
        """
        try:
            daemon_dir = get_conversation_dir(user_id, daemon_conv_id)
            if not daemon_dir.exists():
                return None

            summary_path = daemon_dir / "summary.md"

            # Collect all exec_*.jsonl files sorted by mtime (oldest first)
            exec_files = sorted(
                daemon_dir.glob("exec_*.jsonl"),
                key=lambda p: p.stat().st_mtime,
            )

            # Cache check: if summary.md is newer than all exec files, reuse it
            if summary_path.exists() and exec_files:
                newest_exec_mtime = max(p.stat().st_mtime for p in exec_files)
                if summary_path.stat().st_mtime >= newest_exec_mtime:
                    return summary_path.read_text(encoding="utf-8")

            # Read daemon identity from meta.json
            meta: dict = {}
            meta_path = daemon_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            daemon_name = meta.get("daemon_name", daemon_conv_id)
            description = meta.get("description") or "No description."
            model       = meta.get("model") or "inherited"
            provider    = meta.get("provider") or "inherited"

            # Build execution table from exec_*.jsonl files
            rows: list[dict] = []
            for exec_path in exec_files:
                start_event: dict = {}
                end_event:   dict = {}
                tool_names:  list[str] = []
                try:
                    for line in exec_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        ev_type = ev.get("type", "")
                        if ev_type == "execution_start":
                            start_event = ev
                        elif ev_type == "execution_end":
                            end_event = ev
                        elif ev_type == "tool_call":
                            tn = ev.get("tool_name", "")
                            if tn and tn not in tool_names:
                                tool_names.append(tn)
                except Exception:
                    continue

                exec_id   = exec_path.stem.replace("exec_", "")
                timestamp = start_event.get("timestamp", "")[:19].replace("T", " ")
                success   = end_event.get("success", None)
                status    = ("completed" if success else "failed") if success is not None else "unknown"
                output    = end_event.get("output", "")
                # Key finding: first 200 chars of output (enough for a table cell)
                key_finding = (output[:200] + "…") if len(output) > 200 else output
                key_finding = key_finding.replace("\n", " ").replace("|", "\\|") or end_event.get("error", "—")

                rows.append({
                    "exec_id":     exec_id,
                    "timestamp":   timestamp,
                    "status":      status,
                    "tools":       ", ".join(tool_names) or "—",
                    "key_finding": key_finding,
                })

            # Build markdown
            lines = [
                f"# Daemon: {daemon_name}",
                f"**ID:** `{daemon_conv_id}`  "
                f"**Model:** {model}  **Provider:** {provider}",
                "",
                "## What This Daemon Does",
                description,
                "",
            ]

            if rows:
                lines += [
                    "## Execution Log",
                    "",
                    "| Date (UTC) | Execution ID | Status | Tools Used | Key Finding |",
                    "|---|---|---|---|---|",
                ]
                for r in reversed(rows):  # newest first
                    lines.append(
                        f"| {r['timestamp']} "
                        f"| `{r['exec_id'][:8]}…` "
                        f"| {r['status']} "
                        f"| {r['tools']} "
                        f"| {r['key_finding']} |"
                    )
                lines.append("")
            else:
                lines += ["## Execution Log", "", "_No executions recorded yet._", ""]

            lines += [
                "## Full Execution Logs",
                f"conversations/{daemon_conv_id}/exec_{{exec_id}}.jsonl",
            ]

            md = "\n".join(lines)
            try:
                summary_path.write_text(md, encoding="utf-8")
            except Exception:
                pass
            return md

        except Exception:
            return None
