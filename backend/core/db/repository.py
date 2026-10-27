# core/db/repository.py
import asyncio
import os
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func, text, and_, or_
from .models import Conversation, Message, ToolCall, UserPreference, User, AuthSession, AuthAuditLog, ConversationSummary, ExecutionMetrics, ExecutionPlan, ScheduledWakeup, UserMemoryFact, AgentMemoryEntry, SubagentConfig, Project, Attachment, ConversationAttachment, UserHookScript, BackgroundProcess
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from core.db.models import MCPConfig, MCPToolMeta, MCPResourceMeta, MCPPromptMeta

# Per-key locks guarding critical sections that would otherwise race across
# concurrent callers writing through *different* AsyncSession objects for the
# *same* logical key -- add_message's MAX(sequence)+1 computation and
# ExecutionMetricsRepo.upsert's get-then-insert are both TOCTOU: two
# concurrent callers can both read the "before" state before either commits.
# Found and reproduced by tests/test_worker_process_execution_unit.py's
# TestReentrantExecutionConcurrency (2026-08-17), for the case of the same
# execution_id somehow reaching core/execution/worker.py::_process_execution
# twice concurrently. EVA runs as a single process today (no distributed
# workers -- core/execution/registry.py's own in-memory _executions dict
# already assumes this), so an in-process asyncio.Lock fully closes both
# races; a multi-process deployment would need a DB-level constraint instead.
_message_sequence_locks: Dict[str, asyncio.Lock] = {}
_execution_metrics_locks: Dict[str, asyncio.Lock] = {}


def _keyed_lock(registry: Dict[str, asyncio.Lock], key: str) -> asyncio.Lock:
    # dict.setdefault is a single synchronous step -- no await point inside
    # it -- so this can't itself race even under concurrent asyncio tasks.
    return registry.setdefault(key, asyncio.Lock())


class DBRepo:
    # DB-09 (2026-09-14): create_session/get_session/touch_session (the
    # SessionModel/"sessions" table CRUD) removed -- confirmed dead, never
    # called anywhere outside their own definitions. See core/db/models.py's
    # comment above Conversation for what actually populates session_id.

    @staticmethod
    async def create_conversation(
        db: AsyncSession,
        user_id: str,
        session_id: Optional[str] = None,
        title: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Conversation:
        convo = Conversation(
            user_id=user_id,
            session_id=session_id,
            title=title,
            project_id=project_id,
        )
        db.add(convo)
        await db.commit()
        await db.refresh(convo)
        return convo

    @staticmethod
    async def get_conversation(db: AsyncSession, conversation_id: str) -> Optional[Conversation]:
        return await db.get(Conversation, conversation_id)

    @staticmethod
    async def update_conversation_title(
        db: AsyncSession,
        conversation_id: str,
        title: str,
    ) -> Optional[Conversation]:
        """Update the title of a conversation. Returns None if not found."""
        convo = await db.get(Conversation, conversation_id)
        if not convo:
            return None
        convo.title = title
        await db.commit()
        await db.refresh(convo)
        return convo

    @staticmethod
    async def update_conversation(
        db: AsyncSession,
        conversation_id: str,
        updates,  # ConversationUpdate pydantic model
    ) -> Optional[Conversation]:
        """Update title and/or system_prompt on a conversation (B-054 / B-015)."""
        convo = await db.get(Conversation, conversation_id)
        if not convo:
            return None
        if updates.title is not None:
            convo.title = updates.title
        if updates.system_prompt is not None:
            convo.system_prompt = updates.system_prompt
        await db.commit()
        await db.refresh(convo)
        return convo

    @staticmethod
    async def set_conversation_project_id(
        db: AsyncSession,
        conversation_id: str,
        project_id: str,
    ) -> Optional[Conversation]:
        """
        Attach a conversation to a project by setting its project_id.

        Used by the promote endpoint (Scenario 1) to link an unattached
        conversation to a project workspace.  Call ``move_conversation_workspace_to_project``
        separately to also relocate the filesystem scratch directory.

        Returns the updated Conversation row, or None if not found.
        """
        convo = await db.get(Conversation, conversation_id)
        if not convo:
            return None
        convo.project_id = project_id
        await db.commit()
        await db.refresh(convo)
        return convo

    # -----------------------------
    # PAGINATED MESSAGES (B-013)
    # -----------------------------
    @staticmethod
    async def list_messages_paginated(
        db: AsyncSession,
        conversation_id: str,
        limit: int = 100,
        before_id: Optional[str] = None,
    ):
        """
        Return up to `limit` messages for a conversation, newest-last.

        Parameters
        ----------
        before_id : str, optional
            Cursor — return only messages BEFORE this message_id
            (i.e. older messages).

        Returns
        -------
        (messages, has_more, next_cursor)
        messages    : list of Message rows in ascending (chronological) order
        has_more    : True if older messages exist beyond this page
        next_cursor : id of the oldest message in this page; pass as ?before=
                      on the next request to load the page before it.
        """
        limit = min(limit, 200)

        base = select(Message).where(Message.conversation_id == conversation_id)

        if before_id:
            target = await db.get(Message, before_id)
            if target is not None:
                if target.sequence is not None:
                    base = base.where(Message.sequence < target.sequence)
                else:
                    base = base.where(Message.created_at < target.created_at)

        # Fetch limit+1 newest-first to detect has_more cheaply.
        # Order priority:
        #   1. Rows with sequence set come before NULL-sequence rows (non-NULL first).
        #   2. sequence DESC — newest sequence number (= most recent message) first.
        #   3. created_at DESC — timestamp tiebreaker for NULL-sequence rows.
        #   4. rowid DESC — SQLite insertion-order tiebreaker; fixes same-created_at
        #      ambiguity (e.g. batch-inserted messages, high-throughput tests).
        rows_desc = list((await db.execute(
            base.order_by(
                Message.sequence.is_(None),   # non-NULL rows first (ASC bool)
                Message.sequence.desc(),
                Message.created_at.desc(),
                text("messages.rowid DESC"),  # insertion-order tiebreaker
            ).limit(limit + 1)
        )).scalars().all())

        has_more = len(rows_desc) > limit
        if has_more:
            rows_desc = rows_desc[:limit]

        # Reverse into chronological (ascending) order for the client.
        rows_asc = list(reversed(rows_desc))
        next_cursor = rows_asc[0].id if (has_more and rows_asc) else None
        return rows_asc, has_more, next_cursor

    @staticmethod
    async def add_message(
        db: AsyncSession,
        conversation_id: str,
        role: str,
        content: Optional[str] = None,
        reasoning: Optional[str] = None,
        mtype: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Dict[str, Any]] = None,
        finish_reason: Optional[str] = None,
        chunked: bool = False,
    ) -> Message:
        # Compute the next sequence number using MAX rather than COUNT.
        # MAX is correct even when messages are deleted (no phantom gaps).
        # COALESCE(-1) + 1 = 0 for empty conversations.
        #
        # The read-then-insert below is serialized per conversation_id via
        # _message_sequence_locks: without it, two concurrent writers (same
        # conversation_id, different AsyncSession objects) can both read the
        # same MAX before either commits and both insert the same sequence
        # number -- a real, reproduced TOCTOU race, not a theoretical one.
        async with _keyed_lock(_message_sequence_locks, conversation_id):
            seq_result = await db.execute(
                select(func.coalesce(func.max(Message.sequence), -1))
                .where(Message.conversation_id == conversation_id)
            )
            next_sequence: int = (seq_result.scalar() or 0) + 1

            msg = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                reasoning=reasoning,
                type=mtype,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                finish_reason=finish_reason,
                chunked=chunked,
                sequence=next_sequence,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return msg

    @staticmethod
    async def add_tool_call(db: AsyncSession, message_id: str, name: str, args: Dict[str, Any]) -> ToolCall:
        tc = ToolCall(
            message_id=message_id,
            tool_name=name,
            arguments=args,
        )
        db.add(tc)
        await db.commit()
        await db.refresh(tc)
        return tc

    @staticmethod
    async def get_tool_calls_for_message(db: AsyncSession, message_id: str) -> list:
        result = await db.execute(
            select(ToolCall).where(ToolCall.message_id == message_id).order_by(ToolCall.created_at)
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def upsert_user_preferences(
        db: AsyncSession,
        user_id: str,
        prefs: Dict[str, Any],
    ):
        existing = await db.get(UserPreference, user_id)

        if existing:
            for k, v in prefs.items():
                setattr(existing, k, v)
        else:
            db.add(UserPreference(user_id=user_id, **prefs))

        await db.commit()

    @staticmethod
    async def get_user_preferences(
        db: AsyncSession,
        user_id: str,
    ) -> Optional[UserPreference]:
        return await db.get(UserPreference, user_id)
    # -----------------------------
    # LIST CONVERSATIONS FOR USER
    # -----------------------------
    @staticmethod
    async def list_conversations_for_user(db: AsyncSession, user_id: str):
        # Most-recently-updated first so the UI shows the active conversation at the top.
        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        )
        return result.scalars().all()

    # -----------------------------
    # LIST MESSAGES FOR CONVERSATION
    # -----------------------------
    @staticmethod
    async def list_messages_for_conversation(db: AsyncSession, conversation_id: str):
        # Order priority:
        #   1. Rows with sequence set first (sequence IS NULL sorts last).
        #   2. sequence ASC — chronological position within the conversation.
        #   3. created_at ASC — timestamp tiebreaker for NULL-sequence rows.
        #   4. rowid ASC — SQLite insertion-order tiebreaker; handles same-second
        #      timestamps and batch-inserted messages correctly.
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(
                Message.sequence.is_(None),   # NULLs last (False < True)
                Message.sequence,
                Message.created_at,
                text("messages.rowid"),        # insertion-order tiebreaker
            )
        )
        return result.scalars().all()

    # -----------------------------
    # GET A MESSAGE
    # -----------------------------
    @staticmethod
    async def get_message(db: AsyncSession, message_id: str):
        return await db.get(Message, message_id)

    # -----------------------------
    # DELETE MESSAGES FROM A POINT ONWARDS (edit / retry)
    # -----------------------------
    @staticmethod
    async def delete_messages_from(
        db: AsyncSession, conversation_id: str, from_message_id: str
    ) -> int:
        """
        Delete from_message_id and every message that follows it in
        conversation_id (same ordering as list_messages_for_conversation).
        Also removes child ToolCall rows (FK safety).
        Returns the number of messages deleted.
        """
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(
                Message.sequence.is_(None),
                Message.sequence,
                Message.created_at,
                text("messages.rowid"),
            )
        )
        msgs = result.scalars().all()

        ids_to_delete: list[str] = []
        found = False
        for msg in msgs:
            if msg.id == from_message_id:
                found = True
            if found:
                ids_to_delete.append(msg.id)

        if not ids_to_delete:
            return 0

        await db.execute(delete(ToolCall).where(ToolCall.message_id.in_(ids_to_delete)))
        del_result = await db.execute(
            delete(Message).where(Message.id.in_(ids_to_delete))
        )
        await db.commit()
        return del_result.rowcount


    # DB-05 (2026-09-14): considered auto-purging orphaned attachments here
    # (zero remaining ConversationAttachment links after a conversation
    # delete) but decided against it -- Attachment is a shared, user-level
    # file library (see its own docstring), and silently deleting a file the
    # user might still want, with no confirmation, was judged worse than the
    # disk-accumulation problem it would have solved. Deliberately left as a
    # manual action via the existing DELETE /attachments/{id} endpoint. The
    # decision came out of an option analysis (A/B/C/D).

    # -----------------------------
    # DELETE ALL CONVERSATIONS FOR USER + RELATED
    # -----------------------------
    @staticmethod
    async def delete_all_conversations_for_user(db: AsyncSession, user_id: str) -> int:
        """
        Deletes all conversations for a user including all child rows.
        Returns number of conversations deleted.

        Deletion order (child → parent) to satisfy FK constraints:
          ToolCall (child of Message)
          ConversationAttachment (child of Conversation)
          Message, ConversationSummary, ExecutionPlan (children of Conversation)
          Conversation
        ExecutionMetrics rows are intentionally retained — they are the
        source of truth for usage/cost reporting and must survive conversation
        deletion. ExecutionPlan (DB-02, closed 2026-08-11) is not: its
        goal/step content is as much conversation content as the messages
        themselves -- caught missing here during a Q-06 audit of the sibling
        single-conversation delete, which had already been fixed but this
        bulk variant had not.
        """
        result = await db.execute(select(Conversation.id).where(Conversation.user_id == user_id))
        convo_ids = [row[0] for row in result.all()]

        if not convo_ids:
            return 0

        msg_result = await db.execute(
            select(Message.id).where(Message.conversation_id.in_(convo_ids))
        )
        msg_ids = [row[0] for row in msg_result.all()]

        if msg_ids:
            await db.execute(delete(ToolCall).where(ToolCall.message_id.in_(msg_ids)))

        await db.execute(
            delete(ConversationAttachment).where(
                ConversationAttachment.conversation_id.in_(convo_ids)
            )
        )
        await db.execute(delete(Message).where(Message.conversation_id.in_(convo_ids)))
        await db.execute(
            delete(ConversationSummary).where(
                ConversationSummary.conversation_id.in_(convo_ids)
            )
        )
        await db.execute(
            delete(ExecutionPlan).where(ExecutionPlan.conversation_id.in_(convo_ids))
        )
        # P-19: same class of gap Q-06 caught for ExecutionPlan -- fixed here
        # from the start rather than found missing later in this sibling.
        await db.execute(
            delete(ScheduledWakeup).where(ScheduledWakeup.conversation_id.in_(convo_ids))
        )
        res = await db.execute(delete(Conversation).where(Conversation.user_id == user_id))
        await db.commit()
        return res.rowcount

    # -----------------------------
    # DELETE CONVERSATION + RELATED
    # -----------------------------
    @staticmethod
    async def delete_conversation_and_children(db: AsyncSession, conversation_id: str) -> None:
        """
        Deletes a single conversation and all child rows.

        Deletion order (child → parent):
          ToolCall → Message → ConversationAttachment, ConversationSummary,
          ExecutionPlan → Conversation
        ExecutionMetrics rows are intentionally retained for usage/cost history --
        aggregate numbers only (tokens, duration, status), no conversation content.
        ExecutionPlan (DB-02, closed 2026-08-11) is different: goal/step
        descriptions/result_summary/verification_notes are actual content, as
        much a part of "the conversation" as the messages themselves, so it's
        deleted here rather than following ExecutionMetrics' retain-for-audit
        precedent -- a user deleting a conversation expects that content gone.
        """
        msg_result = await db.execute(
            select(Message.id).where(Message.conversation_id == conversation_id)
        )
        msg_ids = [row[0] for row in msg_result.all()]

        if msg_ids:
            await db.execute(delete(ToolCall).where(ToolCall.message_id.in_(msg_ids)))

        await db.execute(
            delete(ConversationAttachment).where(
                ConversationAttachment.conversation_id == conversation_id
            )
        )
        await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await db.execute(
            delete(ConversationSummary).where(
                ConversationSummary.conversation_id == conversation_id
            )
        )
        await db.execute(
            delete(ExecutionPlan).where(ExecutionPlan.conversation_id == conversation_id)
        )
        # P-19: orphaned scheduled_wakeups would otherwise sit pending forever,
        # resolving later into resume_from_wakeup() trying to post a message
        # into a conversation that no longer exists. Same class of gap as
        # DB-02 (execution_plans) -- designed in here from the start instead
        # of discovered after the fact.
        await db.execute(
            delete(ScheduledWakeup).where(ScheduledWakeup.conversation_id == conversation_id)
        )
        await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await db.commit()

    # -------------------------------------------------------------------------
    # User Hook Scripts (B-064)
    # -------------------------------------------------------------------------

    @staticmethod
    async def create_hook_script(
        db: AsyncSession,
        user_id: str,
        name: str,
        filename: str,
        size_bytes: int,
        description: Optional[str] = None,
    ) -> UserHookScript:
        script = UserHookScript(
            user_id=user_id,
            name=name,
            filename=filename,
            size_bytes=size_bytes,
            description=description,
        )
        db.add(script)
        await db.commit()
        await db.refresh(script)
        return script

    @staticmethod
    async def get_hook_script(
        db: AsyncSession,
        user_id: str,
        name: str,
    ) -> Optional[UserHookScript]:
        result = await db.execute(
            select(UserHookScript).where(
                UserHookScript.user_id == user_id,
                UserHookScript.name == name,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_hook_scripts(
        db: AsyncSession,
        user_id: str,
    ) -> List[UserHookScript]:
        result = await db.execute(
            select(UserHookScript)
            .where(UserHookScript.user_id == user_id)
            .order_by(UserHookScript.name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_hook_script(
        db: AsyncSession,
        script: UserHookScript,
        size_bytes: int,
        description: Optional[str] = None,
    ) -> UserHookScript:
        script.size_bytes = size_bytes
        if description is not None:
            script.description = description
        script.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(script)
        return script

    @staticmethod
    async def delete_hook_script(
        db: AsyncSession,
        script: UserHookScript,
    ) -> None:
        await db.delete(script)
        await db.commit()


class AuthRepo:

    @staticmethod
    async def create_user(db: AsyncSession, **fields):
        user = User(**fields)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def create_session(db: AsyncSession, **fields):
        session = AuthSession(**fields)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    @staticmethod
    async def revoke_session(db: AsyncSession, session_id: str):
        session = await db.get(AuthSession, session_id)
        if session:
            session.revoked = True
            await db.commit()

    @staticmethod
    async def log_event(db: AsyncSession, **fields):
        log = AuthAuditLog(**fields)
        db.add(log)
        await db.commit()

    # ── DB-01: retention/cleanup (called from core/tasks/cleanup.py) ────────

    @staticmethod
    async def purge_expired_sessions(db: AsyncSession) -> int:
        """
        Delete AuthSession rows that can never authenticate again: revoked,
        or past their refresh_expires_at. Rows still in their access-token
        window but with no refresh token (or a not-yet-expired one) are left
        alone -- this only removes genuinely dead rows, not merely old ones.
        Returns the number of rows deleted.
        """
        now = datetime.now(timezone.utc)
        result = await db.execute(
            delete(AuthSession).where(
                (AuthSession.revoked == True)  # noqa: E712
                | (
                    (AuthSession.refresh_expires_at.is_not(None))
                    & (AuthSession.refresh_expires_at < now)
                )
            )
        )
        await db.commit()
        return result.rowcount

    @staticmethod
    async def purge_old_audit_logs(db: AsyncSession, retention_days: int) -> int:
        """
        Delete AuthAuditLog rows older than retention_days. Separate,
        longer-lived policy than purge_expired_sessions -- the audit log is
        the actual security record, kept independently of whether the
        session it references is still alive. Returns rows deleted.
        """
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await db.execute(
            delete(AuthAuditLog).where(AuthAuditLog.created_at < cutoff)
        )
        await db.commit()
        return result.rowcount

    @staticmethod
    async def get_user_by_username(
        db,
        username: str,
    ) -> Optional[User]:
        result = await db.execute(
            select(User).where(User.username == username)
        )
        return result.scalars().first()

    @staticmethod
    async def get_user_by_email(
        db,
        email: str,
    ) -> Optional[User]:
        result = await db.execute(
            select(User).where(User.email == email)
        )
        return result.scalars().first()

    @staticmethod
    async def get_user_by_id(
        db: AsyncSession,
        user_id: str,
    ) -> Optional[User]:
        """Fetch a User row by primary key."""
        return await db.get(User, user_id)

    @staticmethod
    async def update_user(
        db: AsyncSession,
        user_id: str,
        **fields,
    ) -> Optional[User]:
        """
        Partial update on the User row.  Only the keyword arguments supplied
        are written.  updated_at is always refreshed on every call.

        Returns the updated User, or None if user_id is not found.
        """
        user = await db.get(User, user_id)
        if not user:
            return None
        for key, value in fields.items():
            setattr(user, key, value)
        user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
        return user

    # ── Refresh Token methods (B-010) ────────────────────────────────────────

    @staticmethod
    async def set_refresh_token(
        db: AsyncSession,
        session_id: str,
        refresh_token: str,
        expires_at,
    ) -> None:
        """Store or rotate a refresh token on an auth session."""
        stmt = (
            update(AuthSession)
            .where(AuthSession.id == session_id)
            .values(refresh_token=refresh_token, refresh_expires_at=expires_at)
        )
        await db.execute(stmt)
        await db.commit()

    @staticmethod
    async def get_session_by_refresh_token(
        db: AsyncSession,
        refresh_token: str,
    ) -> Optional[AuthSession]:
        """
        Look up a non-revoked auth session by its refresh token.
        Returns None if not found, revoked, or token is blank.
        """
        if not refresh_token:
            return None
        result = await db.execute(
            select(AuthSession).where(
                AuthSession.refresh_token == refresh_token,
                AuthSession.revoked == False,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    # -----------------------------
    # LIST CONVERSATIONS FOR USER
    # -----------------------------
    @staticmethod
    async def list_conversations_for_user(db: AsyncSession, user_id: str):
        # Most-recently-updated first so the UI shows the active conversation at the top.
        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        )
        return result.scalars().all()

    # -----------------------------
    # LIST MESSAGES FOR CONVERSATION
    # -----------------------------
    @staticmethod
    async def list_messages_for_conversation(db: AsyncSession, conversation_id: str):
        # Order priority:
        #   1. Rows with sequence set first (sequence IS NULL sorts last).
        #   2. sequence ASC — chronological position within the conversation.
        #   3. created_at ASC — timestamp tiebreaker for NULL-sequence rows.
        #   4. rowid ASC — SQLite insertion-order tiebreaker; handles same-second
        #      timestamps and batch-inserted messages correctly.
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(
                Message.sequence.is_(None),   # NULLs last (False < True)
                Message.sequence,
                Message.created_at,
                text("messages.rowid"),        # insertion-order tiebreaker
            )
        )
        return result.scalars().all()

    # -----------------------------
    # GET A MESSAGE
    # -----------------------------
    @staticmethod
    async def get_message(db: AsyncSession, message_id: str):
        return await db.get(Message, message_id)


# ==============================
# MCP CONFIG & TOOL META REPOSITORY
# ==============================
class MCPRepo:

    # -------- MCP Configs --------

    @staticmethod
    async def list_configs(
        db: AsyncSession,
        user_id: str,
        *,
        project_id: Optional[str] = None,
    ):
        """
        List MCP configs for a user, ordered by name.

        Parameters
        ----------
        project_id : str, optional
            When supplied, return only configs whose project_id matches.
            When omitted (None), return ALL configs regardless of scope
            (used by the management UI which shows everything).
        """
        stmt = select(MCPConfig).where(MCPConfig.user_id == user_id)
        if project_id is not None:
            stmt = stmt.where(MCPConfig.project_id == project_id)
        stmt = stmt.order_by(MCPConfig.name)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def pause_mcp_for_project(
        db: AsyncSession,
        user_id: str,
        project_id: str,
    ) -> int:
        """
        Cascade-pause all currently-enabled MCPs that are scoped to project_id.

        Sets ``project_paused=True`` and ``enabled=False`` so the cascade can be
        reversed cleanly on project resume without re-enabling configs the user
        manually disabled.

        Returns the number of configs paused.
        """
        result = await db.execute(
            update(MCPConfig)
            .where(
                MCPConfig.user_id == user_id,
                MCPConfig.project_id == project_id,
                MCPConfig.enabled == True,   # noqa: E712
            )
            .values(project_paused=True, enabled=False)
        )
        await db.commit()
        return result.rowcount

    @staticmethod
    async def resume_mcp_for_project(
        db: AsyncSession,
        user_id: str,
        project_id: str,
    ) -> int:
        """
        Restore MCPs that were cascade-paused when project_id was paused/archived.

        Only re-enables configs that carry ``project_paused=True`` (i.e. paused by
        the cascade).  Configs the user disabled manually (project_paused=False,
        enabled=False) are intentionally untouched.

        Returns the number of configs restored.
        """
        result = await db.execute(
            update(MCPConfig)
            .where(
                MCPConfig.user_id == user_id,
                MCPConfig.project_id == project_id,
                MCPConfig.project_paused == True,   # noqa: E712
            )
            .values(project_paused=False, enabled=True)
        )
        await db.commit()
        return result.rowcount

    @staticmethod
    async def get_config(db: AsyncSession, user_id: str, config_id: str):
        """
        Get specific MCP config for this user.
        """
        result = await db.execute(
            select(MCPConfig).where(
                MCPConfig.user_id == user_id,
                MCPConfig.id == config_id
            )
        )
        return result.scalars().first()

    @staticmethod
    async def get_config_by_name(db: AsyncSession, user_id: str, name: str):
        """
        Get a user's MCP config by its friendly name (e.g. "playwright").

        Names are not unique per user at the DB level, so this returns the
        first match if more than one somehow exists -- callers (the CDP
        preview-pane resync in api/session.py today) only need "does this
        user have one enabled", not every duplicate.
        """
        result = await db.execute(
            select(MCPConfig).where(
                MCPConfig.user_id == user_id,
                MCPConfig.name == name,
            )
        )
        return result.scalars().first()

    @staticmethod
    async def create_config(db: AsyncSession, user_id: str, data: dict):
        """
        Create a new MCP config tied to a user.
        """
        cfg = MCPConfig(user_id=user_id, **data)
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
        return cfg

    @staticmethod
    async def update_config(db: AsyncSession, cfg: MCPConfig, data: dict):
        """
        Update an existing MCP config with provided fields.
        """
        for key, value in data.items():
            setattr(cfg, key, value)
        await db.commit()
        await db.refresh(cfg)
        return cfg

    @staticmethod
    async def delete_config(db: AsyncSession, cfg: MCPConfig):
        """
        Delete a MCP config instance.
        """
        await db.delete(cfg)
        await db.commit()

    @staticmethod
    async def delete_tools_for_config(db: AsyncSession, config_id: str):
        """
        Remove all cached tool metadata for a config.
        """
        await db.execute(delete(MCPToolMeta).where(MCPToolMeta.config_id == config_id))
        await db.commit()


    # -------- MCP Tool Meta --------

    @staticmethod
    async def get_tool_meta(db: AsyncSession, config_id: str, tool_name: str):
        """
        Fetch a single cached MCP tool by config + name.
        """
        result = await db.execute(
            select(MCPToolMeta).where(
                MCPToolMeta.config_id == config_id,
                MCPToolMeta.name == tool_name
            )
        )
        return result.scalars().first()

    @staticmethod
    async def list_tools_for_config(db: AsyncSession, config_id: str):
        """
        List all cached MCP tool metadata for a config, ordered by tool name.
        """
        result = await db.execute(
            select(MCPToolMeta)
            .where(MCPToolMeta.config_id == config_id)
            .order_by(MCPToolMeta.name)
        )
        return result.scalars().all()

    @staticmethod
    async def upsert_tool_meta(db: AsyncSession, config_id: str, tool_data: dict):
        """
        Create or update tool metadata for a config.
        If the tool with the same name exists, update fields.
        Otherwise, insert a new row.
        """
        existing = await MCPRepo.get_tool_meta(db, config_id, tool_data.get("name"))
        if existing:
            for key, value in tool_data.items():
                setattr(existing, key, value)
            await db.commit()
            await db.refresh(existing)
            return existing

        new_tool = MCPToolMeta(config_id=config_id, **tool_data)
        db.add(new_tool)
        await db.commit()
        await db.refresh(new_tool)
        return new_tool


    # -------- MCP Resource Meta --------

    @staticmethod
    async def list_resources_for_config(db: AsyncSession, config_id: str):
        """List all cached resource metadata for an MCP config, ordered by URI."""
        result = await db.execute(
            select(MCPResourceMeta)
            .where(MCPResourceMeta.config_id == config_id)
            .order_by(MCPResourceMeta.uri)
        )
        return result.scalars().all()

    @staticmethod
    async def get_resource_meta(db: AsyncSession, config_id: str, uri: str):
        """Fetch a single cached MCP resource by config + URI."""
        result = await db.execute(
            select(MCPResourceMeta).where(
                MCPResourceMeta.config_id == config_id,
                MCPResourceMeta.uri == uri,
            )
        )
        return result.scalars().first()

    @staticmethod
    async def upsert_resource_meta(db: AsyncSession, config_id: str, resource_data: dict):
        """Create or update resource metadata for a config."""
        existing = await MCPRepo.get_resource_meta(db, config_id, resource_data["uri"])
        if existing:
            for key, value in resource_data.items():
                setattr(existing, key, value)
            await db.commit()
            await db.refresh(existing)
            return existing

        new_res = MCPResourceMeta(config_id=config_id, **resource_data)
        db.add(new_res)
        await db.commit()
        await db.refresh(new_res)
        return new_res

    @staticmethod
    async def delete_resources_for_config(db: AsyncSession, config_id: str):
        """Remove all cached resource metadata for a config."""
        await db.execute(
            delete(MCPResourceMeta).where(MCPResourceMeta.config_id == config_id)
        )
        await db.commit()

    # -------- MCP Prompt Meta --------

    @staticmethod
    async def list_prompts_for_config(db: AsyncSession, config_id: str):
        """List all cached prompt template metadata for an MCP config, ordered by name."""
        result = await db.execute(
            select(MCPPromptMeta)
            .where(MCPPromptMeta.config_id == config_id)
            .order_by(MCPPromptMeta.name)
        )
        return result.scalars().all()

    @staticmethod
    async def get_prompt_meta(db: AsyncSession, config_id: str, name: str):
        """Fetch a single cached MCP prompt by config + name."""
        result = await db.execute(
            select(MCPPromptMeta).where(
                MCPPromptMeta.config_id == config_id,
                MCPPromptMeta.name == name,
            )
        )
        return result.scalars().first()

    @staticmethod
    async def upsert_prompt_meta(db: AsyncSession, config_id: str, prompt_data: dict):
        """Create or update prompt metadata for a config."""
        existing = await MCPRepo.get_prompt_meta(db, config_id, prompt_data["name"])
        if existing:
            for key, value in prompt_data.items():
                setattr(existing, key, value)
            await db.commit()
            await db.refresh(existing)
            return existing

        new_prompt = MCPPromptMeta(config_id=config_id, **prompt_data)
        db.add(new_prompt)
        await db.commit()
        await db.refresh(new_prompt)
        return new_prompt

    @staticmethod
    async def delete_prompts_for_config(db: AsyncSession, config_id: str):
        """Remove all cached prompt metadata for a config."""
        await db.execute(
            delete(MCPPromptMeta).where(MCPPromptMeta.config_id == config_id)
        )
        await db.commit()


# ==============================
# EXECUTION METRICS REPOSITORY
# ==============================

class ExecutionMetricsRepo:
    """
    Persistence layer for execution-level metrics.

    One row per execution_id.  Designed for upsert semantics: the worker
    may call upsert() at execution end (terminal state); subsequent calls
    with the same execution_id simply overwrite the row.
    """

    @staticmethod
    async def upsert(
        db: AsyncSession,
        data: dict,
    ) -> ExecutionMetrics:
        """
        Create or update the metrics row for data["execution_id"].

        Parameters
        ----------
        data : dict
            Must contain "execution_id".  All other fields are optional and
            will be written/overwritten if present.

        Returns
        -------
        ExecutionMetrics
            The persisted (and refreshed) ORM instance.
        """
        # get-then-insert/update below is serialized per execution_id via
        # _execution_metrics_locks: without it, two concurrent upserts for
        # the same execution_id can both see no existing row, both attempt
        # ExecutionMetrics(**data) INSERT, and the second hits a UNIQUE
        # constraint violation on execution_id -- a real, reproduced TOCTOU
        # race, not a theoretical one (see add_message's matching comment).
        async with _keyed_lock(_execution_metrics_locks, data["execution_id"]):
            existing = await db.get(ExecutionMetrics, data["execution_id"])

            if existing:
                for key, value in data.items():
                    setattr(existing, key, value)
                await db.commit()
                await db.refresh(existing)
                return existing

            record = ExecutionMetrics(**data)
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return record

    @staticmethod
    async def get(
        db: AsyncSession,
        execution_id: str,
    ) -> Optional[ExecutionMetrics]:
        """
        Retrieve the metrics row for execution_id.
        Returns None if not found.
        """
        return await db.get(ExecutionMetrics, execution_id)

    @staticmethod
    async def list_for_conversation(
        db: AsyncSession,
        conversation_id: str,
    ):
        """
        Return all metrics rows for a conversation, ordered by created_at ascending.
        Useful for displaying per-execution stats in a conversation view.
        """
        result = await db.execute(
            select(ExecutionMetrics)
            .where(ExecutionMetrics.conversation_id == conversation_id)
            .order_by(ExecutionMetrics.created_at)
        )
        return result.scalars().all()

    @staticmethod
    async def list_for_user(
        db: AsyncSession,
        user_id: str,
        *,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
        triggered_by: Optional[str] = None,
        daemon_name: Optional[str] = None,
        limit: int = 20,
    ):
        """
        Return recent executions for a user, newest first.
        Optionally filtered by conversation_id, status, triggered_by, daemon_name.
        """
        query = (
            select(ExecutionMetrics)
            .where(ExecutionMetrics.user_id == user_id)
            .order_by(
                ExecutionMetrics.created_at.desc(),
                text("execution_metrics.rowid DESC"),  # tiebreaker for same-timestamp entries
            )
            .limit(limit)
        )
        if conversation_id:
            query = query.where(ExecutionMetrics.conversation_id == conversation_id)
        if status:
            query = query.where(ExecutionMetrics.status == status)
        if triggered_by:
            query = query.where(ExecutionMetrics.triggered_by == triggered_by)
        if daemon_name:
            query = query.where(ExecutionMetrics.daemon_name == daemon_name)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def list_for_daemon(
        db: AsyncSession,
        user_id: str,
        daemon_name: str,
        limit: int = 20,
    ):
        """
        Return execution history for a specific daemon, newest first.
        Capped at limit (default 20, max 50 enforced by caller).
        """
        query = (
            select(ExecutionMetrics)
            .where(ExecutionMetrics.user_id == user_id)
            .where(ExecutionMetrics.daemon_name == daemon_name)
            .order_by(ExecutionMetrics.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def recover_orphaned(db: AsyncSession) -> list:
        """
        Find all execution_metrics rows stuck in a non-terminal state
        that are older than 5 minutes, mark them ABORTED, and return
        their execution_ids.

        Called by LocalBroker.recover_orphaned_executions() at startup.
        """
        from datetime import datetime, timezone as _tz, timedelta
        from sqlalchemy import update as _update

        cutoff = datetime.now(_tz.utc) - timedelta(minutes=5)
        non_terminal = ("pending", "running", "planning", "verifying",
                        "awaiting_approval", "awaiting_plan_approval")

        result = await db.execute(
            select(ExecutionMetrics.execution_id)
            .where(ExecutionMetrics.status.in_(non_terminal))
            .where(ExecutionMetrics.created_at < cutoff)
        )
        orphaned_ids = [row[0] for row in result.all()]

        if orphaned_ids:
            await db.execute(
                _update(ExecutionMetrics)
                .where(ExecutionMetrics.execution_id.in_(orphaned_ids))
                .values(
                    status="aborted",
                    termination_reason="process_restart",
                    completed_at=datetime.now(_tz.utc),
                )
            )
            await db.commit()

        return orphaned_ids

    @staticmethod
    async def mark_aborted(
        db: AsyncSession,
        execution_id: str,
        reason: Optional[str] = None,
    ) -> None:
        """
        Update a metrics row to status='aborted'.
        Called by the watchdog when it detects a stuck execution.
        No-op if the row doesn't exist (daemon run may not have written INSERT yet).
        """
        from datetime import datetime, timezone as _tz
        existing = await db.get(ExecutionMetrics, execution_id)
        if existing is None:
            return
        _prior_status = existing.status  # capture before overwrite
        existing.status = "aborted"
        existing.termination_reason = reason or "watchdog_timeout"
        existing.error_message = f"Execution aborted: exceeded timeout in {_prior_status!r} state"
        existing.completed_at = datetime.now(_tz.utc)
        await db.commit()

    @staticmethod
    async def purge_old_daemon_runs(
        db: AsyncSession,
        user_id: str,
        daemon_name: str,
        keep: int = 50,
    ) -> int:
        """
        Delete execution_metrics rows for daemon_name beyond the most recent `keep`.
        Called after each daemon run INSERT to enforce the retention policy.

        ⚠️ Retention policy: last 50 runs per daemon are kept. Older runs are
        permanently deleted. This is intentional — see B-058 in EVA_Backlog.md.

        Returns the number of rows deleted.
        """
        # Fetch all rows for this daemon ordered oldest-first
        result = await db.execute(
            select(ExecutionMetrics.execution_id)
            .where(ExecutionMetrics.user_id == user_id)
            .where(ExecutionMetrics.daemon_name == daemon_name)
            .order_by(ExecutionMetrics.created_at.asc())
        )
        all_ids = [row[0] for row in result.all()]

        to_delete = all_ids[:-keep] if len(all_ids) > keep else []
        if not to_delete:
            return 0

        await db.execute(
            ExecutionMetrics.__table__.delete().where(
                ExecutionMetrics.execution_id.in_(to_delete)
            )
        )
        await db.commit()
        return len(to_delete)

    @staticmethod
    async def aggregate_for_conversation(
        db: AsyncSession,
        conversation_id: str,
    ) -> dict:
        """
        Aggregate all execution_metrics rows for a single conversation.

        Returns a dict with totals and per-provider breakdown.
        Used by GET /metrics/session?conversation_id=<id>.
        """
        # Main aggregate row
        agg_result = await db.execute(
            select(
                func.count(ExecutionMetrics.execution_id).label("execution_count"),
                func.coalesce(func.sum(ExecutionMetrics.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.reasoning_tokens), 0).label("reasoning_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.cached_tokens), 0).label("cached_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.total_steps), 0).label("tool_calls_count"),
                func.sum(ExecutionMetrics.duration_ms).label("duration_total_ms"),
                func.sum(ExecutionMetrics.computed_cost_usd).label("total_cost_usd"),
                # COUNT(column) ignores NULLs — gives number of rows with cost data
                func.count(ExecutionMetrics.computed_cost_usd).label("executions_with_cost"),
            )
            .where(ExecutionMetrics.conversation_id == conversation_id)
        )
        row = agg_result.one()

        # Per-provider breakdown
        prov_result = await db.execute(
            select(
                ExecutionMetrics.provider_used,
                func.count(ExecutionMetrics.execution_id).label("execution_count"),
                func.coalesce(
                    func.sum(
                        ExecutionMetrics.prompt_tokens
                        + ExecutionMetrics.completion_tokens
                    ),
                    0,
                ).label("total_tokens"),
                func.sum(ExecutionMetrics.computed_cost_usd).label("total_cost_usd"),
            )
            .where(ExecutionMetrics.conversation_id == conversation_id)
            .group_by(ExecutionMetrics.provider_used)
            .order_by(func.count(ExecutionMetrics.execution_id).desc())
        )
        providers = [
            {
                "provider":        p.provider_used or "unknown",
                "execution_count": p.execution_count or 0,
                "total_tokens":    p.total_tokens or 0,
                "total_cost_usd":  p.total_cost_usd,
            }
            for p in prov_result.all()
        ]

        exec_count = row.execution_count or 0
        with_cost  = row.executions_with_cost or 0
        pt = row.prompt_tokens or 0
        ct = row.completion_tokens or 0
        rt = row.reasoning_tokens or 0
        return {
            "execution_count":   exec_count,
            "prompt_tokens":     pt,
            "completion_tokens": ct,
            "reasoning_tokens":  rt,
            "cached_tokens":  (row.cached_tokens or 0),
            "total_tokens":      pt + ct,   # reasoning is a SUBSET of completion (providers/usage.py)
            "total_cost_usd":    row.total_cost_usd,       # None when no data
            "cost_is_partial":   exec_count > 0 and with_cost < exec_count,
            "tool_calls_count":  row.tool_calls_count or 0,
            "duration_total_ms": row.duration_total_ms,    # None when all null
            "provider_breakdown": providers,
        }

    @staticmethod
    async def aggregate_for_user(
        db: AsyncSession,
        user_id: str,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> dict:
        """
        Aggregate all execution_metrics rows for a user, with optional time range.

        Returns the same dict shape as aggregate_for_conversation.
        Used by GET /metrics/usage and GET /metrics/usage/summary.
        """
        # Main aggregate row
        base_filter = ExecutionMetrics.user_id == user_id
        agg_q = (
            select(
                func.count(ExecutionMetrics.execution_id).label("execution_count"),
                func.coalesce(func.sum(ExecutionMetrics.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.reasoning_tokens), 0).label("reasoning_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.cached_tokens), 0).label("cached_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.total_steps), 0).label("tool_calls_count"),
                func.sum(ExecutionMetrics.duration_ms).label("duration_total_ms"),
                func.sum(ExecutionMetrics.computed_cost_usd).label("total_cost_usd"),
                func.count(ExecutionMetrics.computed_cost_usd).label("executions_with_cost"),
            )
            .where(base_filter)
        )
        if start:
            agg_q = agg_q.where(ExecutionMetrics.created_at >= start)
        if end:
            agg_q = agg_q.where(ExecutionMetrics.created_at <= end)

        agg_result = await db.execute(agg_q)
        row = agg_result.one()

        # Per-provider breakdown
        prov_q = (
            select(
                ExecutionMetrics.provider_used,
                func.count(ExecutionMetrics.execution_id).label("execution_count"),
                func.coalesce(
                    func.sum(
                        ExecutionMetrics.prompt_tokens
                        + ExecutionMetrics.completion_tokens
                    ),
                    0,
                ).label("total_tokens"),
                func.sum(ExecutionMetrics.computed_cost_usd).label("total_cost_usd"),
            )
            .where(base_filter)
            .group_by(ExecutionMetrics.provider_used)
            .order_by(func.count(ExecutionMetrics.execution_id).desc())
        )
        if start:
            prov_q = prov_q.where(ExecutionMetrics.created_at >= start)
        if end:
            prov_q = prov_q.where(ExecutionMetrics.created_at <= end)

        prov_result = await db.execute(prov_q)
        providers = [
            {
                "provider":        p.provider_used or "unknown",
                "execution_count": p.execution_count or 0,
                "total_tokens":    p.total_tokens or 0,
                "total_cost_usd":  p.total_cost_usd,
            }
            for p in prov_result.all()
        ]

        exec_count = row.execution_count or 0
        with_cost  = row.executions_with_cost or 0
        pt = row.prompt_tokens or 0
        ct = row.completion_tokens or 0
        rt = row.reasoning_tokens or 0
        return {
            "execution_count":   exec_count,
            "prompt_tokens":     pt,
            "completion_tokens": ct,
            "reasoning_tokens":  rt,
            "cached_tokens":  (row.cached_tokens or 0),
            "total_tokens":      pt + ct,   # reasoning is a SUBSET of completion (providers/usage.py)
            "total_cost_usd":    row.total_cost_usd,
            "cost_is_partial":   exec_count > 0 and with_cost < exec_count,
            "tool_calls_count":  row.tool_calls_count or 0,
            "duration_total_ms": row.duration_total_ms,
            "provider_breakdown": providers,
        }

    @staticmethod
    async def aggregate_for_project(
        db: AsyncSession,
        project_id: str,
    ) -> dict:
        """
        Aggregate all execution_metrics rows for a single project.

        Returns totals + per-(provider, model) breakdown + trigger split.
        Used by GET /projects/{id}/metrics.
        """
        base = ExecutionMetrics.project_id == project_id

        # ── Main aggregate ─────────────────────────────────────────────
        agg_result = await db.execute(
            select(
                func.count(ExecutionMetrics.execution_id).label("total"),
                func.coalesce(func.sum(ExecutionMetrics.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.reasoning_tokens), 0).label("reasoning_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.cached_tokens), 0).label("cached_tokens"),
                func.coalesce(func.sum(ExecutionMetrics.total_steps), 0).label("tool_calls_count"),
                func.sum(ExecutionMetrics.duration_ms).label("duration_total_ms"),
                func.sum(ExecutionMetrics.computed_cost_usd).label("total_cost_usd"),
                func.count(ExecutionMetrics.computed_cost_usd).label("executions_with_cost"),
                func.max(ExecutionMetrics.completed_at).label("last_active_at"),
            )
            .where(base)
        )
        row = agg_result.one()

        # ── Status breakdown ───────────────────────────────────────────
        status_result = await db.execute(
            select(
                ExecutionMetrics.status,
                func.count(ExecutionMetrics.execution_id).label("cnt"),
            )
            .where(base)
            .group_by(ExecutionMetrics.status)
        )
        status_counts: dict = {r.status: r.cnt for r in status_result.all()}

        # ── Trigger split ──────────────────────────────────────────────
        trigger_result = await db.execute(
            select(
                func.coalesce(ExecutionMetrics.triggered_by, "conversation").label("trigger"),
                func.count(ExecutionMetrics.execution_id).label("cnt"),
            )
            .where(base)
            .group_by(ExecutionMetrics.triggered_by)
        )
        trigger_counts: dict = {r.trigger: r.cnt for r in trigger_result.all()}

        # ── Per-(provider, model) breakdown ────────────────────────────
        # Groups by both columns — unique to this method; all other aggregates
        # group by provider alone.
        prov_result = await db.execute(
            select(
                ExecutionMetrics.provider_used,
                ExecutionMetrics.model_used,
                func.count(ExecutionMetrics.execution_id).label("execution_count"),
                func.coalesce(
                    func.sum(
                        ExecutionMetrics.prompt_tokens
                        + ExecutionMetrics.completion_tokens
                    ),
                    0,
                ).label("total_tokens"),
                func.sum(ExecutionMetrics.computed_cost_usd).label("total_cost_usd"),
            )
            .where(base)
            .group_by(ExecutionMetrics.provider_used, ExecutionMetrics.model_used)
            .order_by(func.count(ExecutionMetrics.execution_id).desc())
        )
        provider_breakdown = [
            {
                "provider":     p.provider_used or "unknown",
                "model":        p.model_used or "unknown",
                "executions":   p.execution_count or 0,
                "total_tokens": p.total_tokens or 0,
                "cost_usd":     p.total_cost_usd,
            }
            for p in prov_result.all()
        ]

        total     = row.total or 0
        with_cost = row.executions_with_cost or 0
        pt = row.prompt_tokens or 0
        ct = row.completion_tokens or 0
        rt = row.reasoning_tokens or 0
        successful = status_counts.get("completed", 0)

        return {
            "total_executions":   total,
            "status_counts":      status_counts,
            "successful":         successful,
            "success_rate":       round(successful / total, 3) if total > 0 else None,
            "trigger_counts":     trigger_counts,
            "prompt_tokens":      pt,
            "completion_tokens":  ct,
            "reasoning_tokens":   rt,
            "cached_tokens":   (row.cached_tokens or 0),
            "total_tokens":       pt + ct,   # reasoning is a SUBSET of completion (providers/usage.py)
            "total_cost_usd":     row.total_cost_usd,
            "cost_is_partial":    total > 0 and with_cost < total,
            "tool_calls_count":   row.tool_calls_count or 0,
            "duration_total_ms":  row.duration_total_ms,
            "last_active_at":     row.last_active_at,
            "provider_breakdown": provider_breakdown,
        }

    @staticmethod
    async def get_token_usage_summary(
        db: AsyncSession,
        user_id: str,
        *,
        provider: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> List[dict]:
        """
        Aggregate token usage and cost by (provider_used, model_used) for a user.

        Used by GET /providers/costs to power the cost intelligence dashboard.

        Returns a list of dicts, each containing:
            provider_used, model_used, total_executions,
            total_prompt_tokens, total_completion_tokens, total_tokens,
            total_cost_usd
        """
        query = (
            select(
                ExecutionMetrics.provider_used,
                ExecutionMetrics.model_used,
                func.count(ExecutionMetrics.execution_id).label("total_executions"),
                func.sum(ExecutionMetrics.prompt_tokens).label("total_prompt_tokens"),
                func.sum(ExecutionMetrics.completion_tokens).label("total_completion_tokens"),
                func.sum(
                    ExecutionMetrics.prompt_tokens + ExecutionMetrics.completion_tokens
                ).label("total_tokens"),
                func.sum(ExecutionMetrics.computed_cost_usd).label("total_cost_usd"),
            )
            .where(ExecutionMetrics.user_id == user_id)
            .group_by(ExecutionMetrics.provider_used, ExecutionMetrics.model_used)
            .order_by(ExecutionMetrics.provider_used, ExecutionMetrics.model_used)
        )
        if provider:
            query = query.where(ExecutionMetrics.provider_used == provider)
        if from_date:
            query = query.where(ExecutionMetrics.created_at >= from_date)
        if to_date:
            query = query.where(ExecutionMetrics.created_at <= to_date)

        result = await db.execute(query)
        rows = result.all()
        return [
            {
                "provider_used":          row.provider_used,
                "model_used":             row.model_used,
                "total_executions":       row.total_executions or 0,
                "total_prompt_tokens":    row.total_prompt_tokens or 0,
                "total_completion_tokens": row.total_completion_tokens or 0,
                "total_tokens":           row.total_tokens or 0,
                "total_cost_usd":         row.total_cost_usd,  # None if no data
            }
            for row in rows
        ]


# ===========================================================================
# ScheduledWakeup Repository (P-19 Phase 1)
# ===========================================================================

class WakeupRepo:
    """
    CRUD operations for ScheduledWakeup rows -- see core/db/models.py's
    ScheduledWakeup docstring for the full design. Resolution logic itself lives in
    core/execution/wakeup_scheduler.py, not here -- this repo is pure
    persistence.
    """

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        execution_id: str,
        conversation_id: str,
        user_id: str,
        wake_at: datetime,
        reason: str,
        watch_execution_id: Optional[str] = None,
    ) -> ScheduledWakeup:
        row = ScheduledWakeup(
            execution_id=execution_id,
            conversation_id=conversation_id,
            user_id=user_id,
            wake_at=wake_at,
            watch_execution_id=watch_execution_id,
            reason=reason,
            status="pending",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def get(db: AsyncSession, wakeup_id: str) -> Optional[ScheduledWakeup]:
        return await db.get(ScheduledWakeup, wakeup_id)

    @staticmethod
    async def list_pending_for_user(db: AsyncSession, user_id: str) -> List[ScheduledWakeup]:
        """User-scoped sibling of list_pending() -- for GET /wakeups (the
        manual pause/resume/cancel UI), not the scheduler's own poll loop,
        which needs every user's rows, not just one."""
        result = await db.execute(
            select(ScheduledWakeup).where(
                and_(
                    ScheduledWakeup.user_id == user_id,
                    ScheduledWakeup.status == "pending",
                )
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def count_pending_for_user(db: AsyncSession, user_id: str) -> int:
        result = await db.execute(
            select(func.count(ScheduledWakeup.id)).where(
                and_(
                    ScheduledWakeup.user_id == user_id,
                    ScheduledWakeup.status == "pending",
                )
            )
        )
        return result.scalar_one()

    @staticmethod
    async def list_pending(db: AsyncSession) -> List[ScheduledWakeup]:
        """
        All still-pending rows, across every user -- the WakeupScheduler's
        poll loop decides per-row whether wake_at has elapsed or
        watch_execution_id has reached a terminal state. Deliberately not
        filtered to "due" in SQL: watch_execution_id's terminal check needs
        a lookup against ExecutionMetrics the scheduler does itself, and the
        pending-row volume is bounded by WAKEUP_MAX_PENDING_PER_USER per
        user on a single-tenant deployment, so there's no need for a more
        selective query.
        """
        result = await db.execute(
            select(ScheduledWakeup).where(ScheduledWakeup.status == "pending")
        )
        return list(result.scalars().all())

    @staticmethod
    async def mark_resolved(
        db: AsyncSession, wakeup_id: str, resumed_execution_id: str
    ) -> None:
        await db.execute(
            update(ScheduledWakeup)
            .where(ScheduledWakeup.id == wakeup_id)
            .values(
                status="resolved",
                resolved_at=datetime.now(timezone.utc),
                resumed_execution_id=resumed_execution_id,
            )
        )
        await db.commit()

    @staticmethod
    async def mark_cancelled(db: AsyncSession, wakeup_id: str) -> None:
        await db.execute(
            update(ScheduledWakeup)
            .where(ScheduledWakeup.id == wakeup_id)
            .values(status="cancelled", resolved_at=datetime.now(timezone.utc))
        )
        await db.commit()

    @staticmethod
    async def delete_for_conversation(db: AsyncSession, conversation_id: str) -> None:
        """Cascade helper -- called from delete_conversation_and_children()."""
        await db.execute(
            delete(ScheduledWakeup).where(
                ScheduledWakeup.conversation_id == conversation_id
            )
        )

    @staticmethod
    async def purge_old_resolved(db: AsyncSession, retention_days: int) -> int:
        """
        Delete resolved/cancelled rows older than retention_days (by
        resolved_at). Never touches a still-pending row regardless of age --
        pending rows are live state, not history, and are bounded anyway by
        WAKEUP_MAX_PENDING_PER_USER.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await db.execute(
            delete(ScheduledWakeup).where(
                and_(
                    ScheduledWakeup.status.in_(("resolved", "cancelled")),
                    ScheduledWakeup.resolved_at.is_not(None),
                    ScheduledWakeup.resolved_at < cutoff,
                )
            )
        )
        await db.commit()
        return result.rowcount


# ===========================================================================
# BackgroundProcess Repository
# ===========================================================================

class ProcessRepo:
    """
    CRUD for BackgroundProcess rows -- see core/db/models.py's
    BackgroundProcess docstring. Pure persistence; the actual spawn/kill/tail
    mechanics live in core/shell/executor.py (ShellExecutor.start_background /
    stop_background / tail_background), which calls into this repo.
    """

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        user_id: str,
        command: List[str],
        cwd: str,
        log_path: str,
        pid: int,
        conversation_id: Optional[str] = None,
        project_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        pid_create_time: Optional[float] = None,
        keep_alive_on_app_shutdown: bool = False,
    ) -> BackgroundProcess:
        row = BackgroundProcess(
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=project_id,
            execution_id=execution_id,
            command=command,
            cwd=cwd,
            log_path=log_path,
            pid=pid,
            pid_create_time=pid_create_time,
            keep_alive_on_app_shutdown=keep_alive_on_app_shutdown,
            status="running",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def get(db: AsyncSession, process_id: str) -> Optional[BackgroundProcess]:
        return await db.get(BackgroundProcess, process_id)

    @staticmethod
    async def list_running_for_user(db: AsyncSession, user_id: str) -> List[BackgroundProcess]:
        result = await db.execute(
            select(BackgroundProcess).where(
                and_(
                    BackgroundProcess.user_id == user_id,
                    BackgroundProcess.status == "running",
                )
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_all_running(db: AsyncSession) -> List[BackgroundProcess]:
        """Every still-'running' row across every user -- for the boot-time
        reconciliation sweep, which is not scoped to one user."""
        result = await db.execute(
            select(BackgroundProcess).where(BackgroundProcess.status == "running")
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_stale_running(
        db: AsyncSession, max_lifetime_seconds: int
    ) -> List[BackgroundProcess]:
        """
        Still-'running' rows whose started_at is older than
        max_lifetime_seconds -- for the cleanup sweep's force-stop pass.

        Filtered in SQL, not Python: SQLite's DateTime(timezone=True)
        columns don't actually round-trip tzinfo on read-back (rows come
        back offset-naive even though they were written with
        datetime.now(timezone.utc)) -- subtracting an aware `now` from a
        naive `row.started_at` in Python raises TypeError. Comparing in the
        WHERE clause against an aware `cutoff` sidesteps this entirely and
        matches purge_old_terminal()'s (and WakeupRepo.purge_old_resolved()'s)
        already-working pattern. Found live by
        test_force_stops_process_past_max_lifetime (2026-08-18).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_lifetime_seconds)
        result = await db.execute(
            select(BackgroundProcess).where(
                and_(
                    BackgroundProcess.status == "running",
                    BackgroundProcess.started_at < cutoff,
                )
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_offset(db: AsyncSession, process_id: str, offset: int) -> None:
        await db.execute(
            update(BackgroundProcess)
            .where(BackgroundProcess.id == process_id)
            .values(last_output_offset=offset)
        )
        await db.commit()

    @staticmethod
    async def mark_status(
        db: AsyncSession,
        process_id: str,
        status: str,
        *,
        exit_code: Optional[int] = None,
    ) -> None:
        """status: 'stopped' | 'crashed' | 'exited'. Always sets ended_at."""
        await db.execute(
            update(BackgroundProcess)
            .where(BackgroundProcess.id == process_id)
            .values(
                status=status,
                exit_code=exit_code,
                ended_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    @staticmethod
    async def set_keep_alive_on_app_shutdown(
        db: AsyncSession, process_id: str, value: bool
    ) -> None:
        """
        Toggle the app-quit persistence opt-in for one row -- the Activity
        panel's per-process "keep running after EVA closes" pin. Ownership
        is the caller's responsibility (matches mark_status/update_offset
        above; api/processes.py's _get_owned_process() does this check
        before calling in, same as every other write in this feature).
        """
        await db.execute(
            update(BackgroundProcess)
            .where(BackgroundProcess.id == process_id)
            .values(keep_alive_on_app_shutdown=value)
        )
        await db.commit()

    @staticmethod
    async def purge_old_terminal(db: AsyncSession, retention_hours: int) -> int:
        """
        Delete TERMINAL-status rows (stopped/crashed/exited) whose ended_at is
        older than retention_hours -- and their log files on disk. Never
        touches a "running" row regardless of age (see BackgroundProcess's
        docstring / the plan's retention note: a background_processes row is
        operational state, not history, so unlike WakeupRepo.purge_old_resolved()
        this also has to reclaim the associated log file, not just the DB
        row, or a purged row would leave an orphaned file with nothing left
        to identify or clean it up).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        result = await db.execute(
            select(BackgroundProcess).where(
                and_(
                    BackgroundProcess.status.in_(("stopped", "crashed", "exited")),
                    BackgroundProcess.ended_at.is_not(None),
                    BackgroundProcess.ended_at < cutoff,
                )
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            return 0

        for row in rows:
            try:
                os.remove(row.log_path)
            except OSError:
                pass  # already missing/deleted -- not an error

        ids = [row.id for row in rows]
        await db.execute(delete(BackgroundProcess).where(BackgroundProcess.id.in_(ids)))
        await db.commit()
        return len(ids)


# ===========================================================================
# ExecutionPlan Repository
# ===========================================================================

class PlanRepo:
    """
    CRUD operations for ExecutionPlan rows.

    One plan is created per plan_execute execution, written at the start of
    the PEV loop by PlannerEngine and updated step-by-step by VerifierEngine.
    """

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        execution_id: str,
        conversation_id: str,
        user_id: str,
        goal: str,
        steps: List[Dict[str, Any]],
        success_criteria: Optional[List[str]] = None,
    ) -> ExecutionPlan:
        plan = ExecutionPlan(
            execution_id=execution_id,
            conversation_id=conversation_id,
            user_id=user_id,
            goal=goal,
            success_criteria=success_criteria,
            steps=steps,
            status="pending",
            current_step_index=0,
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def get_by_execution(
        db: AsyncSession,
        execution_id: str,
    ) -> Optional[ExecutionPlan]:
        result = await db.execute(
            select(ExecutionPlan).where(ExecutionPlan.execution_id == execution_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_status(
        db: AsyncSession,
        execution_id: str,
        status: str,
        current_step_index: Optional[int] = None,
    ) -> None:
        values: Dict[str, Any] = {"status": status}
        if current_step_index is not None:
            values["current_step_index"] = current_step_index
        await db.execute(
            update(ExecutionPlan)
            .where(ExecutionPlan.execution_id == execution_id)
            .values(**values)
        )
        await db.commit()

    @staticmethod
    async def update_steps(
        db: AsyncSession,
        execution_id: str,
        steps: List[Dict[str, Any]],
        current_step_index: Optional[int] = None,
        status: Optional[str] = None,
        goal: Optional[str] = None,
        success_criteria: Optional[List[str]] = None,
    ) -> None:
        """
        Replace the entire steps JSON blob (simplest update strategy).

        goal/success_criteria (A-20): optional -- only the plan-edit call
        site (core/execution/pev_runner.py, decision="edit") passes these,
        since editing is the one path where the in-memory plan can now hold
        a different goal/success_criteria than what PlanRepo.create()
        originally persisted. Every other caller (adaptive re-planning,
        normal step progress) only ever changes `steps`; goal/success_criteria
        don't change mid-run outside an edit, so they're correctly left
        alone (None here means "don't touch this column").
        """
        values: Dict[str, Any] = {"steps": steps}
        if current_step_index is not None:
            values["current_step_index"] = current_step_index
        if status is not None:
            values["status"] = status
        if goal is not None:
            values["goal"] = goal
        if success_criteria is not None:
            values["success_criteria"] = success_criteria
        await db.execute(
            update(ExecutionPlan)
            .where(ExecutionPlan.execution_id == execution_id)
            .values(**values)
        )
        await db.commit()

    @staticmethod
    async def update_goal_verification(
        db: AsyncSession,
        execution_id: str,
        *,
        goal_verdict: str,
        goal_verification_message: Optional[str] = None,
        goal_criteria_results: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """
        A-20: persists PlanVerifierEngine's holistic verdict, written once,
        right after that check runs (core/execution/pev_runner.py) -- a
        separate method from update_steps() since this is a distinct
        concern written at a distinct point in the run (after the loop
        ends), not part of the per-step steps/status bookkeeping.

        goal_verdict is required (not Optional) -- unlike update_steps()'s
        optional fields, this method is only ever called once the check has
        actually produced a real verdict; there's no "don't touch this
        column" case for it the way there is for an edit-only field.
        """
        await db.execute(
            update(ExecutionPlan)
            .where(ExecutionPlan.execution_id == execution_id)
            .values(
                goal_verdict=goal_verdict,
                goal_verification_message=goal_verification_message,
                goal_criteria_results=goal_criteria_results,
            )
        )
        await db.commit()


# ===========================================================================
# Long-Term Memory Repository (V3.0)
# ===========================================================================

class MemoryRepo:
    """
    CRUD operations for the three long-term memory scopes:
      Scope 1 — Chat  : read-only (ConversationSummary already written by summarizer)
      Scope 2 — User  : UserMemoryFact — facts about the person
      Scope 3 — Agent : AgentMemoryEntry — shared scope across conversations
    """

    # ── Scope 1: Chat ────────────────────────────────────────────────────────

    @staticmethod
    async def get_recent_chat_summaries(
        db: AsyncSession,
        user_id: str,
        exclude_conversation_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[ConversationSummary]:
        """
        Return the most recent ConversationSummary rows for a user,
        newest first, optionally excluding the active conversation.
        """
        stmt = (
            select(ConversationSummary)
            .where(ConversationSummary.user_id == user_id)
        )
        if exclude_conversation_id:
            stmt = stmt.where(
                ConversationSummary.conversation_id != exclude_conversation_id
            )
        stmt = stmt.order_by(ConversationSummary.updated_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    # ── Scope 2: User facts ──────────────────────────────────────────────────

    @staticmethod
    async def search_user_facts(
        db: AsyncSession,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> List[UserMemoryFact]:
        """
        Case-insensitive substring search over user memory facts (B-001).

        If ``query`` is empty or blank, returns the most recent ``limit`` facts.
        """
        stmt = select(UserMemoryFact).where(UserMemoryFact.user_id == user_id)
        if query and query.strip():
            stmt = stmt.where(UserMemoryFact.fact.ilike(f"%{query.strip()}%"))
        stmt = stmt.order_by(UserMemoryFact.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def search_agent_entries(
        db: AsyncSession,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> List[AgentMemoryEntry]:
        """
        Case-insensitive substring search across all agent memory entries for a
        user (across all scope_key values) (B-001).

        If ``query`` is empty or blank, returns the most recent ``limit`` entries.
        """
        stmt = select(AgentMemoryEntry).where(AgentMemoryEntry.user_id == user_id)
        if query and query.strip():
            stmt = stmt.where(AgentMemoryEntry.content.ilike(f"%{query.strip()}%"))
        stmt = stmt.order_by(AgentMemoryEntry.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_user_facts(
        db: AsyncSession,
        user_id: str,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> List[UserMemoryFact]:
        """List user-level memory facts, newest first."""
        stmt = select(UserMemoryFact).where(UserMemoryFact.user_id == user_id)
        if category:
            stmt = stmt.where(UserMemoryFact.category == category)
        stmt = stmt.order_by(UserMemoryFact.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_user_fact(
        db: AsyncSession,
        fact_id: str,
        user_id: str,
    ) -> Optional[UserMemoryFact]:
        """Fetch a single fact with ownership check."""
        result = await db.execute(
            select(UserMemoryFact).where(
                UserMemoryFact.id == fact_id,
                UserMemoryFact.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_fact_by_slot(
        db: AsyncSession,
        user_id: str,
        slot_key: str,
    ) -> Optional[UserMemoryFact]:
        """
        Return the single fact that occupies a given slot_key for this user.
        Used to enforce one-fact-per-slot on singular slots (B-041).
        Returns None if no fact with that slot_key exists.
        """
        result = await db.execute(
            select(UserMemoryFact).where(
                UserMemoryFact.user_id == user_id,
                UserMemoryFact.slot_key == slot_key,
            ).limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert_profile_fact(
        db: AsyncSession,
        user_id: str,
        slot_key: str,
        fact: str,
    ) -> UserMemoryFact:
        """
        Insert or overwrite a profile-sourced fact for a given slot.
        Always sets source="profile" and confidence=1.0.
        Used only by PATCH /auth/profile — bypasses the 'allowed' field
        restriction in update_user_fact() so profile fields can be
        written unconditionally.
        """
        existing = await MemoryRepo.get_user_fact_by_slot(db, user_id, slot_key)
        if existing:
            existing.fact       = fact
            existing.source     = "profile"
            existing.confidence = 1.0
            existing.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(existing)
            return existing
        row = UserMemoryFact(
            user_id=user_id,
            fact=fact,
            slot_key=slot_key,
            source="profile",
            confidence=1.0,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def add_user_fact(
        db: AsyncSession,
        user_id: str,
        fact: str,
        category: Optional[str] = None,
        slot_key: Optional[str] = None,
        source: str = "manual",
        confidence: float = 1.0,
    ) -> UserMemoryFact:
        """Insert a new user-level fact."""
        row = UserMemoryFact(
            user_id=user_id,
            fact=fact,
            category=category,
            slot_key=slot_key,
            source=source,
            confidence=confidence,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def update_user_fact(
        db: AsyncSession,
        fact_id: str,
        user_id: str,
        updates: Dict[str, Any],
    ) -> Optional[UserMemoryFact]:
        """Update allowed fields on a fact.  Returns None if not found."""
        row = await MemoryRepo.get_user_fact(db, fact_id, user_id)
        if not row:
            return None
        allowed = {"fact", "category", "slot_key", "confidence", "expires_at", "source"}
        for k, v in updates.items():
            if k in allowed:
                setattr(row, k, v)
        row.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def delete_user_fact(
        db: AsyncSession,
        fact_id: str,
        user_id: str,
    ) -> bool:
        """Delete a fact.  Returns True if deleted, False if not found."""
        row = await MemoryRepo.get_user_fact(db, fact_id, user_id)
        if not row:
            return False
        await db.delete(row)
        await db.commit()
        return True

    @staticmethod
    async def clear_user_facts(db: AsyncSession, user_id: str) -> int:
        """Delete ALL facts for a user.  Returns count of deleted rows."""
        result = await db.execute(
            delete(UserMemoryFact).where(UserMemoryFact.user_id == user_id)
        )
        await db.commit()
        return result.rowcount

    # ── Scope 3: Agent entries ───────────────────────────────────────────────

    @staticmethod
    async def list_agent_scopes(
        db: AsyncSession,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        """
        List all distinct agent_scope_key values for a user with entry counts. (B-012)

        Returns: [{"scope_key": str, "entry_count": int}, ...] sorted by scope_key.
        """
        stmt = (
            select(
                AgentMemoryEntry.agent_scope_key,
                func.count().label("entry_count"),
            )
            .where(AgentMemoryEntry.user_id == user_id)
            .group_by(AgentMemoryEntry.agent_scope_key)
            .order_by(AgentMemoryEntry.agent_scope_key)
        )
        result = await db.execute(stmt)
        rows = result.all()
        return [{"scope_key": r.agent_scope_key, "entry_count": r.entry_count} for r in rows]

    @staticmethod
    async def get_agent_entries(
        db: AsyncSession,
        user_id: str,
        scope_key: str,
        entry_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[AgentMemoryEntry]:
        """List agent-scope entries for a given scope_key, newest first."""
        stmt = select(AgentMemoryEntry).where(
            AgentMemoryEntry.user_id == user_id,
            AgentMemoryEntry.agent_scope_key == scope_key,
        )
        if entry_type:
            stmt = stmt.where(AgentMemoryEntry.entry_type == entry_type)
        stmt = stmt.order_by(AgentMemoryEntry.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_agent_entry(
        db: AsyncSession,
        entry_id: str,
        user_id: str,
    ) -> Optional[AgentMemoryEntry]:
        """Fetch a single agent entry with ownership check."""
        result = await db.execute(
            select(AgentMemoryEntry).where(
                AgentMemoryEntry.id == entry_id,
                AgentMemoryEntry.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def add_agent_entry(
        db: AsyncSession,
        user_id: str,
        scope_key: str,
        entry_type: str,
        content: str,
        tags: Optional[List[str]] = None,
        source_conversation_id: Optional[str] = None,
        source_execution_id: Optional[str] = None,
        sensitive: bool = False,
        expires_at: Optional[datetime] = None,
    ) -> AgentMemoryEntry:
        """Insert a new agent-scope memory entry. expires_at=None (default)
        means permanent -- see A-04 Phase B / AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS."""
        row = AgentMemoryEntry(
            user_id=user_id,
            agent_scope_key=scope_key,
            entry_type=entry_type,
            content=content,
            tags=tags,
            source_conversation_id=source_conversation_id,
            source_execution_id=source_execution_id,
            sensitive=sensitive,
            expires_at=expires_at,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def update_agent_entry(
        db: AsyncSession,
        entry_id: str,
        user_id: str,
        updates: Dict[str, Any],
    ) -> Optional[AgentMemoryEntry]:
        """Update allowed fields on an agent entry."""
        row = await MemoryRepo.get_agent_entry(db, entry_id, user_id)
        if not row:
            return None
        # source_conversation_id/source_execution_id included (A-04 fix) so a
        # "supersedes" update can refresh provenance to the confirming
        # conversation/execution instead of leaving it pointed at whichever
        # session first created the entry.
        allowed = {
            "entry_type", "content", "tags", "expires_at",
            "source_conversation_id", "source_execution_id",
        }
        for k, v in updates.items():
            if k in allowed:
                setattr(row, k, v)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def delete_agent_entry(
        db: AsyncSession,
        entry_id: str,
        user_id: str,
    ) -> bool:
        """Delete an agent entry.  Returns True if deleted."""
        row = await MemoryRepo.get_agent_entry(db, entry_id, user_id)
        if not row:
            return False
        await db.delete(row)
        await db.commit()
        return True

    @staticmethod
    async def clear_agent_scope(
        db: AsyncSession,
        user_id: str,
        scope_key: str,
    ) -> int:
        """Delete ALL entries for a given agent scope.  Returns count."""
        result = await db.execute(
            delete(AgentMemoryEntry).where(
                AgentMemoryEntry.user_id == user_id,
                AgentMemoryEntry.agent_scope_key == scope_key,
            )
        )
        await db.commit()
        return result.rowcount

    @staticmethod
    async def purge_expired(db: AsyncSession) -> Dict[str, int]:
        """
        A-04 Phase B: delete rows past their own expires_at, across both
        memory scopes. Unlike WakeupRepo.purge_old_resolved(), there's no
        retention_days parameter here -- the TTL is decided once, at write
        time (see AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS), not at purge time.
        A row with expires_at=null (the default -- "permanent") is never
        touched regardless of age.
        """
        now = datetime.now(timezone.utc)
        facts_result = await db.execute(
            delete(UserMemoryFact).where(
                UserMemoryFact.expires_at.is_not(None),
                UserMemoryFact.expires_at < now,
            )
        )
        entries_result = await db.execute(
            delete(AgentMemoryEntry).where(
                AgentMemoryEntry.expires_at.is_not(None),
                AgentMemoryEntry.expires_at < now,
            )
        )
        await db.commit()
        return {
            "user_facts_purged":    facts_result.rowcount,
            "agent_entries_purged": entries_result.rowcount,
        }

    # ── Summary ──────────────────────────────────────────────────────────────

    @staticmethod
    async def get_memory_summary(
        db: AsyncSession,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Aggregate counts across all memory scopes for a user.
        Used by GET /memory/summary.
        """
        from sqlalchemy import distinct as sa_distinct

        chat_count_result = await db.execute(
            select(func.count()).where(ConversationSummary.user_id == user_id)
        )
        chat_count = chat_count_result.scalar() or 0

        user_count_result = await db.execute(
            select(func.count()).where(UserMemoryFact.user_id == user_id)
        )
        user_fact_count = user_count_result.scalar() or 0

        agent_count_result = await db.execute(
            select(func.count()).where(AgentMemoryEntry.user_id == user_id)
        )
        agent_entry_count = agent_count_result.scalar() or 0

        scope_result = await db.execute(
            select(sa_distinct(AgentMemoryEntry.agent_scope_key))
            .where(AgentMemoryEntry.user_id == user_id)
        )
        scope_keys = [r[0] for r in scope_result.all()]

        return {
            "chat_summaries_count":  chat_count,
            "user_facts_count":      user_fact_count,
            "agent_entries_count":   agent_entry_count,
            "agent_active_scopes":   scope_keys,
        }


# ── B-045: Project Registry ───────────────────────────────────────────────────

class ProjectRepo:
    """
    CRUD for the projects table (B-045).
    The DB is the project registry only — identity, workspace path, and status.
    All rich artifacts live in .eva files under workspace_path.
    """

    @staticmethod
    async def create(
        db: AsyncSession,
        user_id: str,
        name: str,
        description: Optional[str] = None,
        synopsis: Optional[str] = None,
        workspace_path: Optional[str] = None,
        allowed_mcp_servers: Optional[List] = None,  # B-053
    ) -> Project:
        project = Project(
            user_id=user_id,
            name=name,
            description=description,
            synopsis=synopsis,
            workspace_path=workspace_path,
            allowed_mcp_servers=allowed_mcp_servers,
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        return project

    @staticmethod
    async def get(db: AsyncSession, project_id: str, user_id: str) -> Optional[Project]:
        result = await db.execute(
            select(Project).where(Project.id == project_id, Project.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession,
        user_id: str,
        name: str,
        exclude_id: Optional[str] = None,
    ) -> Optional[Project]:
        """
        Return the project with the given name for this user, or None.
        Pass exclude_id to ignore a specific project row (used during rename
        to avoid the project conflicting with itself).
        """
        stmt = select(Project).where(
            Project.user_id == user_id,
            Project.name == name,
        )
        if exclude_id:
            stmt = stmt.where(Project.id != exclude_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_active_for_user(db: AsyncSession, user_id: str) -> List[Project]:
        """Return all active projects for a user (B-082: multiple active projects allowed)."""
        result = await db.execute(
            select(Project).where(
                Project.user_id == user_id,
                Project.status == "active",
            ).order_by(Project.updated_at.desc().nullslast())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_for_user(
        db: AsyncSession,
        user_id: str,
        status: Optional[str] = None,
    ) -> List[Project]:
        stmt = select(Project).where(Project.user_id == user_id)
        if status:
            stmt = stmt.where(Project.status == status)
        stmt = stmt.order_by(Project.updated_at.desc().nullslast(), Project.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession,
        project_id: str,
        user_id: str,
        updates: Dict[str, Any],
    ) -> Optional[Project]:
        # Filter to allowed mutable fields only
        allowed = {"name", "description", "synopsis", "status", "workspace_path", "allowed_mcp_servers"}
        clean = {k: v for k, v in updates.items() if k in allowed}
        if not clean:
            return await ProjectRepo.get(db, project_id, user_id)

        clean["updated_at"] = datetime.now(timezone.utc)
        await db.execute(
            update(Project)
            .where(Project.id == project_id, Project.user_id == user_id)
            .values(**clean)
        )
        await db.commit()
        return await ProjectRepo.get(db, project_id, user_id)

    @staticmethod
    async def delete(db: AsyncSession, project_id: str, user_id: str) -> bool:
        result = await db.execute(
            delete(Project).where(Project.id == project_id, Project.user_id == user_id)
        )
        await db.commit()
        return result.rowcount > 0

    @staticmethod
    async def list_conversations(
        db: AsyncSession,
        project_id: str,
        user_id: str,
    ) -> List[Conversation]:
        """Return all conversations linked to this project."""
        result = await db.execute(
            select(Conversation)
            .where(Conversation.project_id == project_id, Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())


# ===============================
# Attachment Repository (B-066)
# ===============================

class AttachmentRepo:
    """CRUD for the attachments table — user file/image library."""

    @staticmethod
    async def create(
        db: AsyncSession,
        user_id: str,
        attachment_type: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        blob_path: Optional[str] = None,
        token_estimate: Optional[int] = None,
        size_bytes: Optional[int] = None,
        project_id: Optional[str] = None,
    ) -> Attachment:
        att = Attachment(
            user_id=user_id,
            attachment_type=attachment_type,
            filename=filename,
            mime_type=mime_type,
            blob_path=blob_path,
            token_estimate=token_estimate,
            size_bytes=size_bytes,
            project_id=project_id,
        )
        db.add(att)
        await db.commit()
        await db.refresh(att)
        return att

    @staticmethod
    async def get(db: AsyncSession, attachment_id: str) -> Optional[Attachment]:
        return await db.get(Attachment, attachment_id)

    @staticmethod
    async def list_for_user(
        db: AsyncSession,
        user_id: str,
        project_id: Optional[str] = None,
    ) -> List[Attachment]:
        stmt = select(Attachment).where(Attachment.user_id == user_id)
        if project_id is not None:
            stmt = stmt.where(Attachment.project_id == project_id)
        stmt = stmt.order_by(Attachment.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def delete(db: AsyncSession, attachment_id: str) -> bool:
        att = await db.get(Attachment, attachment_id)
        if not att:
            return False
        await db.delete(att)
        await db.commit()
        return True


# ===============================
# ConversationAttachment Repository (B-066)
# ===============================

class ConversationAttachmentRepo:
    """CRUD for the conversation_attachments table."""

    @staticmethod
    async def create(
        db: AsyncSession,
        conversation_id: str,
        user_id: str,
        attachment_type: str,
        attachment_id: Optional[str] = None,
        ref_conversation_id: Optional[str] = None,
        ref_execution_id: Optional[str] = None,
        message_id: Optional[str] = None,
        rag_indexed: bool = False,
        rag_job_id: Optional[str] = None,
    ) -> ConversationAttachment:
        ca = ConversationAttachment(
            conversation_id=conversation_id,
            user_id=user_id,
            attachment_type=attachment_type,
            attachment_id=attachment_id,
            ref_conversation_id=ref_conversation_id,
            ref_execution_id=ref_execution_id,
            message_id=message_id,
            rag_indexed=rag_indexed,
            rag_job_id=rag_job_id,
        )
        db.add(ca)
        await db.commit()
        await db.refresh(ca)
        return ca

    @staticmethod
    async def get(db: AsyncSession, ca_id: str) -> Optional[ConversationAttachment]:
        return await db.get(ConversationAttachment, ca_id)

    @staticmethod
    async def list_for_conversation(
        db: AsyncSession,
        conversation_id: str,
    ) -> List[ConversationAttachment]:
        result = await db.execute(
            select(ConversationAttachment)
            .where(ConversationAttachment.conversation_id == conversation_id)
            .order_by(ConversationAttachment.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete(db: AsyncSession, ca_id: str) -> bool:
        ca = await db.get(ConversationAttachment, ca_id)
        if not ca:
            return False
        await db.delete(ca)
        await db.commit()
        return True

    @staticmethod
    async def get_for_conversation_and_attachment(
        db: AsyncSession,
        conversation_id: str,
        attachment_id: str,
    ) -> Optional[ConversationAttachment]:
        """Return an existing link if this attachment is already linked to the conversation."""
        result = await db.execute(
            select(ConversationAttachment).where(
                ConversationAttachment.conversation_id == conversation_id,
                ConversationAttachment.attachment_id == attachment_id,
            )
        )
        return result.scalar_one_or_none()
