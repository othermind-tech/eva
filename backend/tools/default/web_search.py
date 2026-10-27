"""
Web search tool for EVA using DDGS (DuckDuckGo Search).

Consolidated (2026-07) from five separate tools (web_search_text,
web_search_images, web_search_videos, web_search_news, web_search_books)
into one tool with a `search_type` parameter. The five-tool split forced
the LLM to choose among six near-identical "retrieve information from the
internet" tools on every research task — a well-documented source of tool
selection errors, especially for smaller models. One tool with a type
parameter reduces this to a single unambiguous entry point.

Authority classification: read-only (all search_type values).

Design principles:
- Deterministic
- Explicit arguments
- Full DDGS surface exposed
- No hidden defaults
- Stateless

Async safety
------------
DDGS uses synchronous HTTP (httpx sync client internally).  Calling any
DDGS method directly in an ``async def`` blocks the event loop, which
prevents FastAPI from serving poll requests and the worker heartbeat from
firing — both of which cause spurious test timeouts.

The tool therefore runs DDGS inside ``asyncio.to_thread()`` so the
network call executes in a thread-pool worker while the event loop stays
responsive.  A 30-second ``asyncio.wait_for`` timeout is applied so a
hung or rate-limited DuckDuckGo request never stalls an execution
indefinitely.

Per-search_type argument sets
------------------------------
The five DDGS backend methods do NOT share one kwarg signature:
  - text / news   → query, region, safesearch, timelimit, max_results, page, backend
  - images        → text args + size, color, type_image, layout, license_image
  - videos        → text args + resolution, duration, license_videos
  - books         → query, max_results, page, backend (NO region/safesearch/timelimit)

The tool builds a per-search_type kwargs subset before calling DDGS —
forwarding the full merged args dict would raise a TypeError for `books`
and pass silently-ignored-or-erroring extras for `text`/`news`.

Schema shape — advanced_filters escape hatch
---------------------------------------------
Only query, search_type, max_results, and timelimit are top-level fields.
Every other DDGS parameter (region, safesearch, backend, page, and every
images/videos-specific filter) lives behind an optional `advanced_filters`
dict instead of being flattened onto the schema.

This is deliberate: a flat schema with all 14 DDGS parameters visible on
every call is its own source of tool-confusion for smaller models — they
tend to try to populate every visible field even when defaults are fine,
burning tokens and risking hallucinated filter values (e.g. a bogus
`license_image`). Keeping the default surface to 4 fields (matching the
"query + type + limit" shape a model has seen thousands of times in
training) while preserving the full DDGS filter surface behind the
optional dict gives the best of both: simple by default, fully capable
when needed.
"""

import asyncio

from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from core.tools.base import ToolBase, ToolResult
from core.tools.registry import default_tool
from ddgs import DDGS

_DDGS_TIMEOUT = 30.0   # seconds — caps any single DuckDuckGo network call

_VALID_SEARCH_TYPES = {"text", "news", "images", "videos", "books"}

_DEFAULT_REGION = "us-en"
_DEFAULT_SAFESEARCH = "moderate"
_DEFAULT_BACKEND = "auto"
_DEFAULT_PAGE = 1


class WebSearchArgs(BaseModel):
    query: str
    search_type: str = Field(
        default="text",
        description=(
            "Result type: 'text' (default, general web results), 'news' "
            "(articles from the last 7 days), 'images', 'videos', or 'books' "
            "(academic material)."
        ),
    )
    max_results: Optional[int] = 10
    timelimit: Optional[str] = Field(
        default=None,
        description="Time filter: 'd' (day), 'w' (week), 'm' (month), 'y' (year). Omit for no time filter.",
    )
    advanced_filters: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional advanced filters — omit for standard searches. "
            "For images: size (Small|Medium|Large|Wallpaper), color, "
            "type_image (photo|gif|clipart|transparent|line), layout (Square|Tall|Wide), "
            "license_image. "
            "For videos: resolution (high|standard), duration (short|medium|long), "
            "license_videos (creativeCommon|youtube). "
            "For any search_type: region (default 'us-en'), safesearch "
            "(on|moderate|off, default 'moderate'), backend (default 'auto'), "
            "page (default 1)."
        ),
    )


def _build_ddgs_kwargs(parsed: WebSearchArgs) -> dict:
    """Build the exact kwarg subset each DDGS backend method accepts."""
    adv = parsed.advanced_filters or {}

    region     = adv.get("region", _DEFAULT_REGION)
    safesearch = adv.get("safesearch", _DEFAULT_SAFESEARCH)
    backend    = adv.get("backend", _DEFAULT_BACKEND)
    page       = adv.get("page", _DEFAULT_PAGE)

    if parsed.search_type == "books":
        return {
            "query": parsed.query,
            "max_results": parsed.max_results,
            "page": page,
            "backend": backend,
        }

    base = {
        "query": parsed.query,
        "region": region,
        "safesearch": safesearch,
        "timelimit": parsed.timelimit,
        "max_results": parsed.max_results,
        "page": page,
        "backend": backend,
    }

    if parsed.search_type == "images":
        base.update({
            "size": adv.get("size"),
            "color": adv.get("color"),
            "type_image": adv.get("type_image"),
            "layout": adv.get("layout"),
            "license_image": adv.get("license_image"),
        })
    elif parsed.search_type == "videos":
        base.update({
            "resolution": adv.get("resolution"),
            "duration": adv.get("duration"),
            "license_videos": adv.get("license_videos"),
        })

    return base


@default_tool(
    name="web_search",
    description=(
        "Search the web using DuckDuckGo. Set search_type to 'text' (default), "
        "'news', 'images', 'videos', or 'books' to select the result type. "
        "Use max_results and timelimit to narrow results. For advanced filters "
        "(region, safesearch, backend, page, or images/videos-specific options "
        "like size, color, license, resolution, duration) pass an advanced_filters "
        "dict — omit it for standard searches. "
        "USE THIS for general research, factual lookups, current events, images, "
        "videos, or books — anything on the public internet. "
        "USE http_get INSTEAD when you already have a specific URL to fetch. "
        "USE rag_retrieve INSTEAD when searching the user's own uploaded documents "
        "— this tool only searches the public internet, never private uploads."
    ),
    args_schema=WebSearchArgs,
    category="search",
    summary="Search the web via DuckDuckGo (text, news, images, videos, or books).",
)
class WebSearchTool(ToolBase):

    async def run(self, args: dict) -> ToolResult:
        parsed = WebSearchArgs(**args)

        if parsed.search_type not in _VALID_SEARCH_TYPES:
            return ToolResult(result={
                "error": (
                    f"Invalid search_type '{parsed.search_type}'. "
                    f"Must be one of: {sorted(_VALID_SEARCH_TYPES)}."
                )
            })

        ddgs_kwargs = _build_ddgs_kwargs(parsed)
        method_name = parsed.search_type

        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(lambda: getattr(DDGS(), method_name)(**ddgs_kwargs)),
                timeout=_DDGS_TIMEOUT,
            )
            return ToolResult(result={"results": results})
        except asyncio.TimeoutError:
            return ToolResult(result={
                "error": f"Search timed out after {_DDGS_TIMEOUT:.0f} seconds."
            })
        except Exception as e:
            return ToolResult(result={"error": str(e)})
