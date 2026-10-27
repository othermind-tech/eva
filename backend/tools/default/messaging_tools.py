# tools/default/messaging_tools.py
#
# G-007: Inter-Agent Messaging tools
#
# Enables agents running in the same execution (e.g. under parallel_delegate
# or coordinate) to pass structured messages to each other via the blackboard's
# inbox namespace.
#
# Design notes:
#   - Messages are stored in the blackboard under slot "inbox:{to_agent}" using
#     append mode.  This means each write adds a new message to the list; prior
#     messages are preserved until the inbox is cleared.
#   - The inbox: prefix is universally writable (any agent may write to any
#     other agent's inbox).  This was added to core/blackboard.py in G-007.
#   - The sender identity is taken from execution.caller_agent_name — the LLM
#     never controls the "from" field, preventing spoofing.
#   - read_agent_messages reads only the calling agent's own inbox.  Agents
#     cannot read each other's inboxes (no cross-agent snooping).
#   - mark_read=True (default) clears the inbox after reading so messages are
#     not re-processed on the next call.
#
# Authority:
#   send_agent_message  — MUTATING  (writes shared state)
#   read_agent_messages — READ_ONLY (reads own inbox, optionally clears it)

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from core.tools.base import ToolBase, ToolAuthority, ToolResult
from core.tools.registry import default_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# send_agent_message
# ---------------------------------------------------------------------------

class SendMessageArgs(BaseModel):
    to_agent: str = Field(
        description=(
            "Name of the agent to send this message to. "
            "Must match the exact name of another agent working in this same "
            "execution (case-sensitive)."
        )
    )
    subject: str = Field(
        description="Short summary of the message (1 sentence)."
    )
    body: str = Field(
        description="Full message body. Be specific — the receiving agent has no context."
    )


@default_tool(
    name="send_agent_message",
    description=(
        "Send a direct message to another agent running in the same execution. "
        "The message is placed in the recipient's inbox on the shared blackboard. "
        "Use this when you need another agent to act on information you have produced, "
        "or to coordinate work mid-execution. "
        "The recipient reads their inbox via read_agent_messages."
    ),
    args_schema=SendMessageArgs,
    authority=ToolAuthority.MUTATING,
    category="communication",
    chat_excluded=True,
    summary="Send a direct message to another agent's inbox in the same execution.",
)
class SendAgentMessageTool(ToolBase):
    """
    Writes a structured message to another agent's inbox on the execution blackboard.

    The inbox slot is "inbox:{to_agent}" — universally writable by any agent (G-007).
    The sender is taken from execution.caller_agent_name (LLM cannot forge identity).
    """

    async def run(self, args: Dict[str, Any]) -> ToolResult:
        parsed = SendMessageArgs(**args)
        to_agent = parsed.to_agent.strip()
        subject  = parsed.subject.strip()
        body     = parsed.body.strip()

        if not to_agent:
            return ToolResult(result={"error": "to_agent must not be empty."})

        # ------------------------------------------------------------------
        # Resolve execution context
        # ------------------------------------------------------------------
        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={"error": "send_agent_message: no execution context."})

        blackboard = getattr(execution, "blackboard", None)
        if blackboard is None:
            return ToolResult(result={
                "error": "send_agent_message: blackboard is unavailable for this execution."
            })

        sender = getattr(execution, "caller_agent_name", "orchestrator")

        # ------------------------------------------------------------------
        # Write to inbox:{to_agent} in append mode
        # ------------------------------------------------------------------
        content = json.dumps({
            "subject":  subject,
            "body":     body,
            "sent_at":  time.time(),
        })

        try:
            # Use the dedicated send_message() method — NOT the general write().
            # This keeps inbox: slots off-limits to blackboard_write tool calls,
            # preventing any agent from spoofing or flooding inboxes directly.
            await blackboard.send_message(
                from_agent=sender,
                to_agent=to_agent,
                content=content,
            )
        except Exception as exc:
            logger.error(
                "send_agent_message: failed to=%s from=%s: %s",
                to_agent, sender, exc,
            )
            return ToolResult(result={
                "error": f"Failed to deliver message to '{to_agent}': {exc}"
            })

        logger.info(
            "send_agent_message: sent",
            extra={"from": sender, "to": to_agent, "subject": subject},
        )

        return ToolResult(result={
            "sent":    True,
            "from":    sender,
            "to":      to_agent,
            "subject": subject,
        })


# ---------------------------------------------------------------------------
# read_agent_messages
# ---------------------------------------------------------------------------

class ReadMessagesArgs(BaseModel):
    mark_read: bool = Field(
        default=True,
        description=(
            "If True (default), clear the inbox after reading so messages are "
            "not seen again on the next call. "
            "Set to False to peek without consuming."
        ),
    )


@default_tool(
    name="read_agent_messages",
    description=(
        "Read messages from this agent's inbox. "
        "Other agents can send messages via send_agent_message. "
        "By default, the inbox is cleared after reading (mark_read=True). "
        "Returns a list of messages with sender, subject, body, and timestamp."
    ),
    args_schema=ReadMessagesArgs,
    authority=ToolAuthority.READ_ONLY,
    category="communication",
    chat_excluded=True,
    summary="Read messages from this agent's inbox (optionally clearing after read).",
)
class ReadAgentMessagesTool(ToolBase):
    """
    Reads the calling agent's inbox from the execution blackboard.

    Inbox slot: "inbox:{caller_agent_name}"
    If mark_read=True, the inbox list is deleted after reading (consume semantics).
    """

    async def run(self, args: Dict[str, Any]) -> ToolResult:
        parsed    = ReadMessagesArgs(**args)
        mark_read = parsed.mark_read

        # ------------------------------------------------------------------
        # Resolve execution context
        # ------------------------------------------------------------------
        execution = getattr(self, "execution", None)
        if execution is None:
            return ToolResult(result={"error": "read_agent_messages: no execution context."})

        blackboard = getattr(execution, "blackboard", None)
        if blackboard is None:
            return ToolResult(result={
                "messages":      [],
                "message_count": 0,
                "note":          "Blackboard unavailable — no messages.",
            })

        agent_name = getattr(execution, "caller_agent_name", "orchestrator")

        # ------------------------------------------------------------------
        # Read inbox via dedicated method (never exposes general read path)
        # ------------------------------------------------------------------
        envelopes = await blackboard.read_messages(agent_name)

        messages: List[Dict[str, Any]] = []
        for env in envelopes:
            try:
                payload = json.loads(env.get("content", "{}"))
            except (json.JSONDecodeError, TypeError):
                payload = {"body": env.get("content", "")}

            messages.append({
                "from":    env.get("written_by", "unknown"),
                "subject": payload.get("subject", "(no subject)"),
                "body":    payload.get("body", ""),
                "sent_at": payload.get("sent_at", env.get("written_at")),
            })

        # ------------------------------------------------------------------
        # Optionally consume (clear) the inbox
        # ------------------------------------------------------------------
        if mark_read and messages:
            await blackboard.clear_inbox(agent_name)

        logger.info(
            "read_agent_messages: read",
            extra={
                "agent": agent_name,
                "count": len(messages),
                "mark_read": mark_read,
            },
        )

        return ToolResult(result={
            "message_count": len(messages),
            "messages":      messages,
            "cleared":       mark_read and bool(messages),
        })
