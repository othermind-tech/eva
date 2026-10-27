"""
HTTP request tools for EVA.

Provides two tools for making outbound HTTP requests:

    http_get  (READ_ONLY)  — HTTP GET request
    http_post (MUTATING)   — HTTP POST request with JSON body

Security measures
-----------------
- Localhost and private IP ranges are blocked to prevent SSRF attacks.
- Response bodies are capped at 10 MB to prevent memory exhaustion.
- Request JSON payloads are capped at 1 MB.
- Default timeout is 30 seconds; caller may lower it, not raise above 120s.
- Redirects are followed automatically (max 10 hops).

Errors are returned as structured dicts inside ToolResult — tools never raise
unhandled exceptions.
"""

import asyncio
import ipaddress
import json
import logging
import re
import socket
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel, Field

from config.settings import (
    HTTP_TOOL_MAX_RESPONSE_BYTES,
    HTTP_TOOL_MAX_PAYLOAD_BYTES,
    HTTP_TOOL_MAX_TIMEOUT_SECONDS,
)
from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.registry import default_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSRF protection — blocked URL patterns
# ---------------------------------------------------------------------------

_MAX_RESPONSE_BYTES = HTTP_TOOL_MAX_RESPONSE_BYTES
_MAX_PAYLOAD_BYTES  = HTTP_TOOL_MAX_PAYLOAD_BYTES
_MAX_TIMEOUT        = HTTP_TOOL_MAX_TIMEOUT_SECONDS

# Regex patterns matched against the URL's netloc (host[:port]).
_BLOCKED_PATTERNS = re.compile(
    r"^("
    r"localhost"
    r"|127\.\d+\.\d+\.\d+"
    r"|0\.0\.0\.0"
    r"|::1"
    r"|169\.254\.\d+\.\d+"          # link-local
    r"|192\.168\.\d+\.\d+"          # RFC-1918
    r"|10\.\d+\.\d+\.\d+"           # RFC-1918
    r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"  # RFC-1918
    r"|fd[0-9a-f]{2}:"              # ULA IPv6
    r")",
    re.IGNORECASE,
)


def _is_blocked(url: str) -> bool:
    """
    Fast literal pre-check only (regex against the URL's host string, zero
    DNS round-trip) -- lets the caller skip constructing an AsyncClient at
    all for the obvious cases. NOT the authoritative check on its own: a
    literal string match can't catch decimal/octal/hex-encoded IPs or
    IPv4-mapped IPv6 (e.g. "http://2130706433/" resolves to 127.0.0.1 but
    matches no pattern here). See _check_hop_not_blocked() below for the
    resolve-based check that's actually authoritative, applied via an
    httpx request event hook to both this initial request and every
    redirect hop.
    """
    try:
        parsed = httpx.URL(url)
        host = parsed.host
        return bool(_BLOCKED_PATTERNS.match(host))
    except Exception:
        # Malformed URL — block it.
        return True


async def _resolve_is_blocked_ip(host: str) -> bool:
    """
    Resolve `host` via the OS resolver and check every returned address
    against loopback/link-local/private/reserved/unspecified ranges using
    Python's `ipaddress` module.

    This is what actually closes the encoding bypasses a literal regex
    can't catch (B2-1.2, 2026-08-18): whatever encoding the caller used --
    decimal ("2130706433"), octal ("017700000001"), hex ("0x7f.0x0.0x0.0x1"),
    or IPv4-mapped IPv6 ("::ffff:127.0.0.1") -- getaddrinfo() normalizes it
    to the same real socket address before ipaddress.ip_address() ever sees
    it, so there's no encoding left to bypass.

    Fails closed: an unresolvable host or a malformed resolved address is
    treated as blocked, matching _is_blocked()'s existing
    "malformed → block" convention.
    """
    loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror:
        return True

    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        # IPv4-mapped IPv6 (e.g. "::ffff:127.0.0.1") does NOT set
        # is_loopback/is_private on the IPv6 address itself -- verified
        # empirically 2026-08-18: ipaddress.ip_address("::ffff:127.0.0.1")
        # .is_loopback is False even though it plainly targets loopback.
        # Unwrap to the mapped IPv4 address (ip.ipv4_mapped) and check
        # that instead when present.
        check_ip = ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip
        if (
            check_ip.is_loopback
            or check_ip.is_link_local
            or check_ip.is_private
            or check_ip.is_reserved
            or check_ip.is_unspecified
        ):
            return True
    return False


class _SSRFBlocked(Exception):
    """Raised from _check_hop_not_blocked() to abort a request/redirect hop
    before it's sent -- caught in _do_get()/_do_post() and turned into the
    same structured error dict shape _is_blocked()'s early-return produces."""
    def __init__(self, url: str):
        super().__init__(f"Blocked URL: {url}")
        self.url = url


async def _check_hop_not_blocked(request: "httpx.Request") -> None:
    """
    httpx `request` event hook -- fires once per hop, including every
    redirect-followed request, not just the initial one. Verified
    2026-08-18 against the installed httpx==0.28.1: _send_handling_redirects()
    runs the full request-hook list inside its own redirect while-loop,
    before each _send_single_request() call -- confirmed both by reading
    that source directly and by a live MockTransport test showing a hook
    that raises on the second hop prevents that hop's request from ever
    reaching the transport at all (not just "detected after the send").

    Raising here is therefore sufficient on its own to stop a redirect to
    an internal/private address from ever being followed, with no separate
    manual redirect loop needed.
    """
    host = request.url.host
    if _BLOCKED_PATTERNS.match(host) or await _resolve_is_blocked_ip(host):
        raise _SSRFBlocked(str(request.url))


def _clamp_timeout(value: int) -> int:
    """Clamp the caller-supplied timeout to [1, _MAX_TIMEOUT]."""
    return max(1, min(value, _MAX_TIMEOUT))


# ---------------------------------------------------------------------------
# Args schemas
# ---------------------------------------------------------------------------

class HttpGetArgs(BaseModel):
    url: str = Field(..., description="URL to fetch (must be a public HTTP/HTTPS URL)")
    headers: Optional[Dict[str, str]] = Field(
        None,
        description="Optional HTTP request headers (dict of string key-value pairs)",
    )
    timeout: int = Field(
        30,
        description=f"Request timeout in seconds (1–{_MAX_TIMEOUT})",
        ge=1,
        le=_MAX_TIMEOUT,
    )


class HttpPostArgs(BaseModel):
    url: str = Field(..., description="URL to POST to (must be a public HTTP/HTTPS URL)")
    data: Optional[Dict[str, Any]] = Field(
        None,
        description="JSON request body (dict). Serialised as application/json.",
    )
    headers: Optional[Dict[str, str]] = Field(
        None,
        description="Optional HTTP request headers (dict of string key-value pairs)",
    )
    timeout: int = Field(
        30,
        description=f"Request timeout in seconds (1–{_MAX_TIMEOUT})",
        ge=1,
        le=_MAX_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Shared request helpers
# ---------------------------------------------------------------------------

async def _do_get(url: str, headers: Optional[Dict], timeout: int) -> dict:
    """Execute an HTTP GET request and return a structured result dict."""
    if _is_blocked(url):
        return {
            "error": "Blocked URL: localhost and private IP ranges are not permitted.",
            "url": url,
        }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=10,
            event_hooks={"request": [_check_hop_not_blocked]},
        ) as client:
            response = await client.get(url, headers=headers or {})

            body_bytes = response.content
            truncated = False
            if len(body_bytes) > _MAX_RESPONSE_BYTES:
                body_bytes = body_bytes[:_MAX_RESPONSE_BYTES]
                truncated = True

            body_text = body_bytes.decode("utf-8", errors="replace")

            return {
                "url": str(response.url),
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body_text,
                "truncated": truncated,
            }

    except _SSRFBlocked as exc:
        return {
            "error": "Blocked URL: localhost and private IP ranges are not permitted.",
            "url": exc.url,
        }
    except httpx.TimeoutException:
        return {"error": "Request timed out.", "url": url, "timeout_seconds": timeout}
    except httpx.TooManyRedirects:
        return {"error": "Too many redirects.", "url": url}
    except Exception as exc:
        logger.error("http_get failed", extra={"url": url}, exc_info=True)
        return {"error": str(exc), "url": url}


async def _do_post(
    url: str,
    data: Optional[Dict],
    headers: Optional[Dict],
    timeout: int,
) -> dict:
    """Execute an HTTP POST request and return a structured result dict."""
    if _is_blocked(url):
        return {
            "error": "Blocked URL: localhost and private IP ranges are not permitted.",
            "url": url,
        }

    # Guard payload size before sending.
    payload_bytes = json.dumps(data or {}).encode()
    if len(payload_bytes) > _MAX_PAYLOAD_BYTES:
        return {
            "error": (
                f"Request payload too large ({len(payload_bytes)} bytes). "
                f"Maximum allowed: {_MAX_PAYLOAD_BYTES} bytes."
            ),
            "url": url,
        }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=10,
            event_hooks={"request": [_check_hop_not_blocked]},
        ) as client:
            response = await client.post(url, json=data or {}, headers=headers or {})

            body_bytes = response.content
            truncated = False
            if len(body_bytes) > _MAX_RESPONSE_BYTES:
                body_bytes = body_bytes[:_MAX_RESPONSE_BYTES]
                truncated = True

            body_text = body_bytes.decode("utf-8", errors="replace")

            return {
                "url": str(response.url),
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body_text,
                "truncated": truncated,
            }

    except _SSRFBlocked as exc:
        return {
            "error": "Blocked URL: localhost and private IP ranges are not permitted.",
            "url": exc.url,
        }
    except httpx.TimeoutException:
        return {"error": "Request timed out.", "url": url, "timeout_seconds": timeout}
    except httpx.TooManyRedirects:
        return {"error": "Too many redirects.", "url": url}
    except Exception as exc:
        logger.error("http_post failed", extra={"url": url}, exc_info=True)
        return {"error": str(exc), "url": url}


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------

@default_tool(
    name="http_get",
    description=(
        "Fetch the contents of any public URL via HTTP GET — web pages, REST API "
        "endpoints, data feeds, documentation pages, or webhook responses. Supports "
        "custom headers (e.g. Authorization, Content-Type) for authenticated API "
        "calls. Localhost and private IP ranges are blocked; response body capped "
        "at 10 MB. "
        "USE THIS WHEN you already have a specific URL or API endpoint to call. "
        "USE web_search INSTEAD WHEN you need to discover URLs or information you "
        "don't already have a link for."
    ),
    args_schema=HttpGetArgs,
    authority=ToolAuthority.READ_ONLY,
    category="search",
    summary="Make an HTTP GET request to a public URL and return the response body.",
)
class HttpGetTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        result = await _do_get(
            url=args["url"],
            headers=args.get("headers"),
            timeout=_clamp_timeout(args.get("timeout", 30)),
        )
        return ToolResult(result=result)


@default_tool(
    name="http_post",
    description=(
        "Make an HTTP POST request with a JSON body to a public URL. "
        "Localhost and private IP ranges are blocked. "
        "Request payload capped at 1 MB; response body capped at 10 MB."
    ),
    args_schema=HttpPostArgs,
    authority=ToolAuthority.MUTATING,
    category="general",
    summary="Make an HTTP POST request with a JSON body to a public URL.",
)
class HttpPostTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        result = await _do_post(
            url=args["url"],
            data=args.get("data"),
            headers=args.get("headers"),
            timeout=_clamp_timeout(args.get("timeout", 30)),
        )
        return ToolResult(result=result)
