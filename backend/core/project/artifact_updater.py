# core/project/artifact_updater.py
"""
ProjectArtifactUpdater (B-045).

Runs asynchronously after each execution that has a project_id attached.
Mirrors the MemoryExtractor pattern — called via asyncio.create_task(), never
blocks the response, all exceptions are caught and logged as non-fatal.

Responsibilities:
  1. Ask the LLM what changed this turn (diff-style prompt).
  2. Apply updates to tasks.eva, knowledge.eva, errors.eva.
  3. Append a summary entry to journal.eva (always — no LLM needed).
  4. Touch the `updated:` timestamp in project.eva.

The LLM output is a structured JSON list of update instructions, each with:
  {
    "file": "tasks" | "knowledge" | "errors",
    "action": "append" | "replace_section",
    "section": "TASKS" | "KNOWLEDGE" | "ERRORS",   (for replace_section)
    "content": "...new content..."
  }
"""

from __future__ import annotations

import json
import re
from logging import getLogger
from typing import Any, Dict, List, Optional

from core.db.session import AsyncSessionLocal
from core.db.repository import DBRepo
from core.tools.user_registry import UserToolRegistry
from core.project.manager import ProjectManager
from core.project.eva_format import append_entry, set_section, read_file as read_eva_file, write_file as write_eva_file, _now_iso
from core.providers.fallback_call import call_with_full_fallback
from prompts.project import PROJECT_ARTIFACT_UPDATE_PROMPT
from tools.default.file_tools import replace_first_match

logger = getLogger(__name__)

_FILE_MAP = {
    "tasks":     "tasks.eva",
    "knowledge": "knowledge.eva",
    "errors":    "errors.eva",
    "policies":  "policies.eva",
}
_VALID_ACTIONS = {"append", "replace_section", "edit"}


class ProjectArtifactUpdater:
    """
    Post-execution project artifact updater.

    Call update() as an asyncio background task after each execution
    where session['project_id'] is set.
    """

    async def update(
        self,
        project_id: str,
        conversation_id: str,
        user_id: str,
        session: Dict[str, Any],
        execution_id: Optional[str] = None,
        turn: Optional[int] = None,
        workspace_path: Optional[str] = None,
        conversation_text: Optional[str] = None,
    ) -> None:
        """
        Main entry point.  Safe to call from asyncio.create_task().
        All exceptions are caught — this method must never raise.

        workspace_path: the project's resolved workspace root (external or
        managed). Must be passed through for external-workspace projects
        (B-051) -- without it every ProjectManager call below falls back to
        the managed-location guess, which is wrong for external projects
        and causes this whole update to silently no-op (see workspace_exists
        check in _run()).

        conversation_text (B-094): when supplied, used directly as the text
        to analyze instead of reading Message rows for conversation_id from
        the DB. Default None preserves the original behaviour exactly (DB
        read) for every existing caller -- organic chat conversations really
        do have persisted Message rows to read. Daemon runs don't (by
        design, to avoid message-table growth for repetitive automated
        runs), so core/daemons/runner.py passes its own in-memory run output
        directly here instead of relying on a DB read that could never find
        anything for a daemon-shaped conversation_id.
        """
        try:
            await self._run(
                project_id=project_id,
                conversation_id=conversation_id,
                user_id=user_id,
                session=session,
                execution_id=execution_id,
                turn=turn,
                workspace_path=workspace_path,
                conversation_text=conversation_text,
            )
        except Exception as exc:
            logger.warning(
                "ProjectArtifactUpdater: update failed (non-fatal)",
                exc_info=exc,
                extra={
                    "project_id": project_id,
                    "conversation_id": conversation_id,
                    "execution_id": execution_id,
                },
            )

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _run(
        self,
        project_id: str,
        conversation_id: str,
        user_id: str,
        session: Dict[str, Any],
        execution_id: Optional[str],
        turn: Optional[int],
        workspace_path: Optional[str] = None,
        conversation_text: Optional[str] = None,
    ) -> None:
        # Verify workspace exists
        if not ProjectManager.workspace_exists(user_id, project_id, workspace_path):
            logger.debug(
                "ProjectArtifactUpdater: no workspace for project — skipping",
                extra={"project_id": project_id},
            )
            return

        provider = session.get("provider", "")
        model    = session.get("model", "")
        api_key  = session.get("api_key")

        if not provider or not model:
            return

        # B-094: conversation_text supplied directly (e.g. a daemon run's own
        # in-memory output) skips the DB read entirely -- there's no Message
        # row to find for a daemon-shaped conversation_id, by design.
        if conversation_text is None:
            # Load last N messages for context
            async with AsyncSessionLocal() as db:
                messages = await DBRepo.list_messages_for_conversation(db, conversation_id)

            if not messages:
                return

            # Build conversation text (last 20 messages to keep context tight)
            recent = messages[-20:]
            conversation_text = "\n".join(
                f"{m.role.upper()}: {(m.content or '').strip()}"
                for m in recent
                if m.content
            )
            if not conversation_text:
                return

        # Load existing artifact summaries for context
        tasks_text     = ProjectManager.read_artifact(user_id, project_id, "tasks.eva", workspace_path)
        knowledge_text = ProjectManager.read_artifact(user_id, project_id, "knowledge.eva", workspace_path)
        errors_text    = ProjectManager.read_artifact(user_id, project_id, "errors.eva", workspace_path)
        policies_text  = ProjectManager.read_artifact(user_id, project_id, "policies.eva", workspace_path)

        system_prompt = PROJECT_ARTIFACT_UPDATE_PROMPT.format(
            tasks=tasks_text or "(empty)",
            knowledge=knowledge_text or "(empty)",
            errors=errors_text or "(empty)",
            policies=policies_text or "(empty)",
        )

        raw = await self._call_llm(
            system_prompt=system_prompt,
            conversation_text=conversation_text,
            session=session,
            user_id=user_id,
        )

        instructions = self._parse_instructions(raw)
        applied = 0

        for inst in instructions:
            file_key = (inst.get("file") or "").strip().lower()
            action   = (inst.get("action") or "").strip().lower()
            content  = (inst.get("content") or "").strip()
            old_string = inst.get("old_string")
            new_string = inst.get("new_string")

            if file_key not in _FILE_MAP or action not in _VALID_ACTIONS:
                continue
            if action in ("append", "replace_section") and not content:
                continue
            if action == "edit" and (old_string is None or new_string is None):
                continue

            filename = _FILE_MAP[file_key]
            path = ProjectManager.get_artifact_path(user_id, project_id, filename, workspace_path)

            if action == "append":
                append_entry(path, content)
            elif action == "replace_section":
                section = (inst.get("section") or file_key.upper()).strip().upper()
                set_section(path, section, content)
            elif action == "edit":
                current = read_eva_file(path)
                updated = replace_first_match(current, old_string, new_string)
                if updated is None:
                    logger.debug(
                        "ProjectArtifactUpdater: edit skipped, old_string not found",
                        extra={"project_id": project_id, "file": filename},
                    )
                    continue
                write_eva_file(path, updated)

            applied += 1

        # Always append a journal entry (no LLM needed for this)
        ts = _now_iso()
        turn_part = f" turn={turn}" if turn is not None else ""
        journal_summary = (
            f"[{ts} agent=eva{turn_part}] "
            f"Execution {execution_id or 'unknown'} completed. "
            f"Artifact updates applied: {applied}."
        )
        ProjectManager.append_journal(
            user_id=user_id,
            project_id=project_id,
            content=f"Execution {execution_id or 'unknown'} completed. Artifact updates applied: {applied}.",
            turn=turn,
            workspace_path=workspace_path,
        )

        # Touch the updated timestamp in project.eva
        ProjectManager.touch_updated(user_id, project_id, workspace_path)

        logger.info(
            "ProjectArtifactUpdater: artifacts updated",
            extra={
                "project_id": project_id,
                "conversation_id": conversation_id,
                "instructions_received": len(instructions),
                "applied": applied,
            },
        )

    # ── LLM helpers ──────────────────────────────────────────────────────────

    async def _call_llm(
        self,
        system_prompt: str,
        conversation_text: str,
        session: Dict[str, Any],
        user_id: str,
    ) -> str:
        # Reactive Loop tier (V6.8): fire-and-forget background task (never
        # awaited by the response path), so the extra retry/fallback
        # latency costs nothing -- and the failure mode this is protecting
        # against is real: this reads only the last 20 raw messages each
        # run, so a sustained outage across several turns lets the
        # earliest of those turns scroll out of the window and never make
        # it into the artifact at all (see Notes discussion).
        return await call_with_full_fallback(
            session=session,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": conversation_text}],
            user_id=user_id,
            tools=UserToolRegistry(default_tools=False, enabled_tools=[]),
            caller_tag="ProjectArtifactUpdater",
        )

    def _parse_instructions(self, raw: str) -> List[Dict[str, Any]]:
        """Parse JSON array of update instructions from LLM output."""
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        # Try direct parse first — handles clean JSON arrays correctly,
        # including those with bracket-like tokens inside string values
        # (e.g. "[DONE]", "[PENDING]") that trip up a non-greedy regex.
        try:
            result = json.loads(raw)
            if isinstance(result, list):
                return result[:10]
        except json.JSONDecodeError:
            pass
        # Fallback: extract the outermost [...] block from mixed LLM output
        # (e.g. "Here are the updates: [...]"). Use greedy match so inner
        # brackets inside string values are included in the captured span.
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result[:10]  # cap at 10 instructions per turn
        except json.JSONDecodeError:
            pass
        return []


# Singleton
project_artifact_updater = ProjectArtifactUpdater()
