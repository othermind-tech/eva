# core/providers/multimodal.py
"""
M-07: shared, provider-agnostic helpers for sending a real image to a model instead of a
text placeholder.

Background
----------
Two places in EVA used to strip an image to text unconditionally:
  1. core/mcp/runtime.py -- an MCP tool result image (e.g. a Playwright screenshot) became
     "[image omitted: <type>, ~N KB]".
  2. A chat-attached image was injected as a "[IMAGE ATTACHMENT] ... refer to it" text note
     -- despite api/conversation_management.py already gating the attach itself on
     supports_vision and its own comment claiming "actual base64 is forwarded at inference
     time" (it never was).
Neither is wrong to keep doing for a model that cannot see images; the gap was having no path
at all for a model that CAN.

Design
------
* The canonical EVA-internal shape for a message that carries an image is OpenAI's own content-
  part list -- {"role": ..., "content": [{"type": "text", ...}, {"type": "image_url", ...}]} --
  chosen because OpenAIHandler and OllamaCloudHandler already pass `messages` straight through
  to their (OpenAI-compatible) SDK unchanged; this shape needs zero translation for either.
  Only AnthropicHandler._convert_messages needs new logic, to turn an image_url part into
  Anthropic's own image block.
* Nothing here touches the database. Message.content stays a plain-text column; the real image
  bytes live in the existing Attachment.blob_path / MCP tool result, and are assembled into this
  shape only at the moment a request is built for the provider, then discarded.
* Vision capability is read from core.providers.capability_registry, the same source
  api/conversation_management.py's attach-time gate already uses. A model with no cached
  capability entry, or one whose supports_vision is not exactly True, is treated as NOT
  vision-capable -- never send image content on a guess. A missing/erroring registry lookup
  degrades to "not vision-capable", the same safe direction, never the reverse.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from config.settings import EVA_VISION_ENABLED, IMAGE_INLINE_MAX_BYTES

logger = logging.getLogger(__name__)


def is_vision_capable(provider: Optional[str], model: Optional[str]) -> bool:
    """
    True only when EVA_VISION_ENABLED is on AND the capability registry has a cached record
    for this exact (provider, model) pair with supports_vision is True.

    False for: the global kill-switch off, no provider/model given, no cached record at all
    (a model EVA has never queried capabilities for, or one behind a provider with no
    capability data), supports_vision explicitly False, or a lookup that raises. Every one of
    these is "we don't know this model can take an image" -- treated the same, deliberately:
    the cost of sending image content to a model that silently ignores or errors on it is worse
    than the cost of one more model going through the existing text-only path a little longer.
    """
    if not EVA_VISION_ENABLED or not provider or not model:
        return False
    try:
        from core.providers import capability_registry  # noqa: PLC0415  (avoid import cycles)
        info = capability_registry.get_model(provider, model)
        return bool(info and info.supports_vision)
    except Exception:
        logger.debug("is_vision_capable: capability lookup failed — treating as not vision-capable",
                    exc_info=True, extra={"provider": provider, "model": model})
        return False


def within_inline_limit(byte_size: int) -> bool:
    """True if an image this large may be inlined as real content (see IMAGE_INLINE_MAX_BYTES)."""
    return 0 <= byte_size <= IMAGE_INLINE_MAX_BYTES


def image_url_part(mime_type: str, data_b64: str) -> Dict[str, Any]:
    """One OpenAI-style content part carrying an inline base64 image."""
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type or 'application/octet-stream'};base64,{data_b64}"},
    }


def text_part(text: str) -> Dict[str, Any]:
    """One OpenAI-style content part carrying plain text."""
    return {"type": "text", "text": text}


def build_multimodal_content(
    text: Optional[str],
    images: List[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """
    The OpenAI-style content-part list for one message: optional text, then each (mime_type,
    data_b64) pair in `images` as its own image_url part. `images` empty and `text` falsy
    returns [] -- callers should treat that as "nothing to send" rather than an empty message.
    """
    parts: List[Dict[str, Any]] = []
    if text:
        parts.append(text_part(text))
    for mime_type, data_b64 in images:
        parts.append(image_url_part(mime_type, data_b64))
    return parts


def is_multimodal_content(content: Any) -> bool:
    """True if `content` is a non-empty OpenAI-style content-part list, not a plain string."""
    return (
        isinstance(content, list) and bool(content)
        and all(isinstance(p, dict) and "type" in p for p in content)
    )


def data_uri_to_anthropic_source(url: str) -> Optional[Dict[str, str]]:
    """
    Parse an OpenAI-style image_url data URI ("data:image/png;base64,....") into Anthropic's
    own image source shape ({"type": "base64", "media_type": ..., "data": ...}). Returns None
    for anything else (e.g. a real http(s) URL, which EVA never generates here but a foreign
    caller could) rather than guessing.
    """
    if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url:
        return None
    header, data_b64 = url.split(";base64,", 1)
    media_type = header[len("data:"):] or "application/octet-stream"
    return {"type": "base64", "media_type": media_type, "data": data_b64}
