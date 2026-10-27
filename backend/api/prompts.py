# api/prompts.py
# B-055: System Prompt Enhancer
#
# Stateless endpoint that takes a raw user-written system prompt and returns
# an improved version using the user's currently configured provider and model.
# Single non-streaming LLM call — no tools, no memory, no conversation history.

from logging import getLogger
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from core.auth.dependencies import get_current_auth
from config.settings import get_user_session
from core.providers.fallback_call import resolve_and_call_once
from core.providers.fallback_state import resolve_api_key

router = APIRouter()
logger = getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PromptEnhanceRequest(BaseModel):
    raw_prompt: str
    # Optional context hint — shapes the meta-prompt framing.
    # Values: "conversation" | "agent" | "project"
    context: Optional[str] = "conversation"

    @field_validator("raw_prompt")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("raw_prompt must not be empty")
        return v.strip()


class PromptEnhanceResponse(BaseModel):
    enhanced_prompt: str
    original_prompt: str


# ---------------------------------------------------------------------------
# Meta-prompt (instructs the LLM to rewrite system prompts)
# ---------------------------------------------------------------------------

_CONTEXT_LABELS = {
    "conversation": "a specific conversation",
    "agent":        "a reusable AI agent persona",
    "project":      "a project workspace context",
}

_META_SYSTEM = """\
You are an expert prompt engineer specialising in crafting clear, effective \
system prompts for AI assistants.

When given a raw, user-written system prompt, rewrite it into a polished \
version that:
- States the assistant's role and responsibilities clearly at the top
- Uses concise, imperative language (avoid filler like "You are a helpful...")
- Organises distinct concerns into labelled sections (e.g. ## Role, ## Constraints, ## Style)
- Preserves all specific instructions and domain knowledge from the original
- Adds sensible defaults the user likely intended but did not spell out
- Removes redundancy, vague phrasing, and unnecessary verbosity

Return ONLY the enhanced system prompt text — no preamble, no explanation, \
no markdown code fences.  The output will be inserted directly into a system \
prompt field.\
"""


def _build_meta_user_message(raw_prompt: str, context: str) -> str:
    label = _CONTEXT_LABELS.get(context, "a specific conversation")
    return (
        f"Enhance the following system prompt for {label}.\n\n"
        f"RAW PROMPT:\n{raw_prompt}"
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/prompts/enhance", response_model=PromptEnhanceResponse)
async def enhance_prompt(
    body: PromptEnhanceRequest,
    auth=Depends(get_current_auth),
):
    """
    Take a raw user-written system prompt and return an LLM-polished version.

    Uses the user's currently configured provider and model (from their session),
    so the enhancer automatically benefits from whatever model they have set.
    """
    user_id = auth["user_id"]
    session = get_user_session(user_id)

    # No hardcoded provider/model fallback (V6.4): EVA has no default model
    # of its own -- an unconfigured session is a real 424, not a guess.
    provider = session.get("provider")
    model    = session.get("model")
    if not provider or not model:
        raise HTTPException(
            status_code=424,
            detail={
                "error_code": "provider_not_configured",
                "message": (
                    "No provider/model configured for this session. "
                    "Configure one via POST /set-session before using the prompt enhancer."
                ),
            },
        )
    api_key  = resolve_api_key(session.get("api_key"), provider)

    if not api_key and provider.lower() != "ollama":
        raise HTTPException(
            status_code=424,
            detail={
                "error_code": "api_key_not_configured",
                "message": (
                    f"No API key configured for provider '{provider}'. "
                    f"Add one under Profile → API Keys (POST /secrets/{provider})."
                ),
            },
        )

    try:
        messages = [
            {
                "role": "user",
                "content": _build_meta_user_message(
                    body.raw_prompt, body.context or "conversation"
                ),
            }
        ]

        # Resolve-Attempt-Record tier (V6.8): tries the first candidate not
        # currently cooling down from a recent failure (session's
        # provider/model, or the first fallback_chain entry). On failure,
        # records into the shared cross-execution cooldown store so the
        # user's *next* click of "Enhance" -- or any other call site
        # sharing this user's fallback_chain -- automatically resolves to
        # a healthier candidate instead of retrying the same dead one.
        enhanced = (await resolve_and_call_once(
            session=session,
            system_prompt=_META_SYSTEM,
            messages=messages,
            user_id=user_id,
            caller_tag="prompts.enhance",
        )).strip()

        if not enhanced:
            raise HTTPException(
                status_code=502,
                detail="LLM returned an empty response. Try again.",
            )

        logger.info(
            "Prompt enhanced",
            extra={
                "user_id": user_id,
                "provider": provider,
                "model": model,
                "context": body.context,
                "input_chars": len(body.raw_prompt),
                "output_chars": len(enhanced),
            },
        )

        return PromptEnhanceResponse(
            enhanced_prompt=enhanced,
            original_prompt=body.raw_prompt,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Prompt enhancement failed",
            exc_info=exc,
            extra={"user_id": user_id, "provider": provider, "model": model},
        )
        raise HTTPException(status_code=500, detail=f"Enhancement failed: {exc}") from exc
