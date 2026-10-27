# core/tools/message_builder.py

import json
from typing import Any, Dict, List

from core.mcp.runtime import IMAGE_RESULT_KEY
from core.providers.multimodal import build_multimodal_content


def build_tool_message(
    *,
    tool_call_id: str,
    tool_name: str,
    result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Build the provider-safe role=tool message(s) for one tool result.

    Returns a LIST, always -- one element in the ordinary case (unchanged from before M-07:
    everything the caller already did with `.append(build_tool_message(...))` still works once
    it becomes `.extend(...)`), two when the result carries real images (M-07).

    IMPORTANT:
    - Uses `name`, NOT `tool_name`
    - Compatible with OpenAI, Groq, LiteLLM
    - The tool-role message's own `content` is ALWAYS a plain string -- OpenAI's API rejects
      multimodal content parts on a message with role="tool" (only "user" may carry them; this
      is a protocol constraint, not an EVA choice). An image therefore travels in a SEPARATE,
      immediately-following role="user" message instead of inside the tool message itself.
      core.providers.multimodal.build_multimodal_content() gives both message shapes the same
      canonical (OpenAI-style) form; only AnthropicHandler._convert_messages needs to know how
      to render it (including folding this follow-up user turn back into the preceding
      tool-result turn, since Anthropic requires strict user/assistant alternation).
    """
    # Never mutate `result` -- this is the SAME dict object the caller (core/agent_engine.py)
    # already yielded via ToolResultChunk and is about to persist to the DB via
    # on_step_complete(); popping the image key out of it in place would silently strip the
    # image from both, purely as a side effect of building this message.
    images = result.get(IMAGE_RESULT_KEY) if isinstance(result, dict) else None
    content_for_tool_message = (
        {k: v for k, v in result.items() if k != IMAGE_RESULT_KEY} if images else result
    )

    tool_message = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": json.dumps(content_for_tool_message, ensure_ascii=False),
    }

    if not images:
        return [tool_message]

    image_content = build_multimodal_content(
        text=None,
        images=[(img.get("mime_type", ""), img.get("data_b64", "")) for img in images],
    )
    if not image_content:
        return [tool_message]

    return [
        tool_message,
        {"role": "user", "content": image_content},
    ]
