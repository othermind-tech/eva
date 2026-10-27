from pydantic import BaseModel, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime

# ── B-005: Entity ID naming convention ───────────────────────────────────────
#
# All response schemas in this file MUST follow this convention:
#
#   Primary-key fields   → always named  `id`
#   Foreign-key fields   → named  `{entity}_id`  (e.g. conversation_id, user_id)
#
# This makes typed client generation (openapi-typescript, Pydantic) predictable:
# any client that accesses `resource.id` will work across all EVA entities.
#
# Examples:
#   ConversationResponse.id             ← primary key
#   ConversationResponse.user_id        ← FK to users
#   MessageResponse.id                  ← primary key
#   MessageResponse.conversation_id     ← FK to conversations
#
# When adding new response schemas, verify that the primary key is named `id`
# before committing.  FK references should use the snake_case entity name + _id.
# ─────────────────────────────────────────────────────────────────────────────


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    session_id: Optional[str]
    title: Optional[str]
    # B-045: project this conversation belongs to (None = no project)
    project_id: Optional[str] = None
    # B-054: per-conversation system prompt
    system_prompt: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: Optional[str]
    type: Optional[str]
    tool_name: Optional[str]
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[Dict[str, Any]] = None
    is_error: Optional[bool] = None
    created_at: datetime

    class Config:
        from_attributes = True

    @model_validator(mode="after")
    def _derive_is_error(self) -> "MessageResponse":
        if self.role == "tool" and isinstance(self.tool_result, dict):
            self.is_error = self.tool_result.get("ok") is False
        return self


class DeleteConversationResponse(BaseModel):
    deleted_conversation_id: str


class DeleteAllConversationsResponse(BaseModel):
    deleted_conversations: int


# ── Q-03: generic shared response shapes ─────────────────────────────────────
#
# Many simple action/delete endpoints across api/*.py return one of these
# exact shapes. Reuse them instead of a bespoke one-off model per endpoint
# wherever the real return value genuinely matches — do not force-fit an
# endpoint whose response has extra/different fields into one of these.

class StatusResponse(BaseModel):
    """Bare {"status": "..."} confirmation, no other fields."""
    status: str


class DeleteByNameResponse(BaseModel):
    """Delete confirmation keyed by name, e.g. {"status": "deleted", "name": "foo"}."""
    status: str
    name: str


class DeleteAttachmentResponse(BaseModel):
    status: str
    deleted_attachment_id: str


class PaginatedMessagesResponse(BaseModel):
    messages: List[MessageResponse]
    has_more: bool
    next_cursor: Optional[str] = None  # pass as ?before= on the next request


class ChatAcceptedResponse(BaseModel):
    conversation_id: str
    execution_id: str
    status: str
    # Execution mode determined by IntentRouter for this message.
    execution_mode: str = "conversational"


class CancelExecutionResponse(BaseModel):
    conversation_id: str
    status: str


class SessionUpdateResponse(BaseModel):
    status: str
    # None = genuinely unconfigured (2026-07-27 cleanup) -- EVA has no
    # default provider/model of its own. Frontend should treat this as a
    # real, expected state, not a loading glitch.
    provider: Optional[str] = None
    model: Optional[str] = None
    # V6.7: level string ("minimal"/"low"/"medium"/"high"/"xhigh"), or
    # None when off. Was bool.
    reasoning: Optional[str] = None
    tools_enabled: list[str]
    system_prompt: str
    fallback_chain: List[dict] = []
    # Human-in-the-Loop tool approval setting: "auto" | "always_ask" | "per_conversation"
    tool_approval_mode: str = "auto"
    # Permitted tool authority levels (comma-separated).
    allowed_authorities: str = "read_only,mutating,destructive"
    # Intent routing mode: "auto" | "chat" | "plan_execute"
    agent_mode: str = "auto"
    # Whether the user must explicitly approve the generated plan before execution begins.
    require_plan_approval: bool = False
    # Maximum number of extra step extensions the agent may request during
    # execution before being forced to conclude.
    max_step_extensions: int = 2
    # ── Long-Term Memory settings (V3.0) ──────────────────────────────────
    user_memory_enabled:  bool            = True
    agent_scope_key:      Optional[str]   = None
    agent_memory_enabled: bool            = True
    # ── Skills mode (V3.1) ──────────────────────────────────────────────
    skills_mode: str = "auto"
    # ── PreToolUse / PostToolUse Hooks (B-053) ──────────────────────────
    # Current hook definitions stored in this user's session.
    # Each entry: {tool, event, handler, config?}
    tool_hooks: List[dict] = []
    # ── Shell safety (B-052) ────────────────────────────────────────────
    # When True, shell tool blocks all non-read-only commands.
    shell_readonly: bool = False
    # ── Environment context (V5.5) ──────────────────────────────────────
    user_timezone: Optional[str] = None
    user_location: Optional[str] = None
    user_language: Optional[str] = None
    # ── PII Redaction (S-03) ─────────────────────────────────────────────
    pii_redaction_enabled: bool = False
    # ── Adaptable PEV runner (A-21) ──────────────────────────────────────
    pev_lookahead_skip_enabled: bool = True
    # Capability warnings: populated when a requested parameter was auto-corrected
    # because the selected model does not support it.
    warnings: List[str] = []


class PreviewEndpointItem(BaseModel):
    """One CDP preview-pane registration -- see core/mcp/preview_registry.py."""
    name: str
    cdp_endpoint: str
    registered_at: datetime


class PreviewEndpointsResponse(BaseModel):
    """GET /session/preview-endpoints -- current registrations, keyed by name."""
    endpoints: Dict[str, PreviewEndpointItem]
