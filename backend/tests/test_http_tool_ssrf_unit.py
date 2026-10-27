# tests/test_http_tool_ssrf_unit.py
"""
Unit tests for B2-1.2 (2026-08-18): resolve-then-check SSRF hardening for
the http_get/http_post tools, plus per-redirect-hop re-validation.

This file verifies:
  - tools/default/http_tool.py::_resolve_is_blocked_ip() -- resolves the
    host via the OS resolver and classifies every returned address with
    Python's `ipaddress` module, instead of regex-matching the literal
    host string (which decimal/octal/hex-encoded IPs and IPv4-mapped IPv6
    all slip past).
  - ::_check_hop_not_blocked() -- an httpx `request` event hook re-running
    that same check on every redirect hop, not just the first request.

Two platform facts confirmed empirically before writing these tests
(2026-08-18, against this Windows dev environment) -- see the comments
inline for why some cases are exercised via mocked resolution rather than
real DNS:
  1. Real `getaddrinfo("2130706433", ...)` (decimal-encoded 127.0.0.1)
     FAILS outright on Windows (WSANO_DATA) -- httpx itself can't even
     connect to these encodings here, independent of this fix. The fix's
     fail-closed-on-gaierror behavior still correctly blocks them, but the
     *classification* logic (would it recognize 127.0.0.1 if resolution
     DID succeed, e.g. on glibc-based Linux) needs a mocked-resolution
     test to exercise directly.
  2. `ipaddress.ip_address("::ffff:127.0.0.1").is_loopback` is **False** --
     Python's ipaddress module does not auto-unwrap IPv4-mapped IPv6
     addresses. This was a real bug caught during implementation (the
     first version of _resolve_is_blocked_ip missed this case) and fixed
     via `ip.ipv4_mapped` before these tests were written -- the relevant
     test below is a regression guard for that exact bug, not a
     hypothetical.

Run with:
    cd <project_root>
    python tests/test_http_tool_ssrf_unit.py
    (or: pytest tests/test_http_tool_ssrf_unit.py -v)
"""

from __future__ import annotations

import os
import socket
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fake_resolution(ip_str: str, family=socket.AF_INET):
    """Build a getaddrinfo()-shaped return value resolving to a single IP."""
    return [(family, socket.SOCK_STREAM, 6, "", (ip_str, 0))]


# ---------------------------------------------------------------------------
# Layer 1 — _resolve_is_blocked_ip() in isolation
# ---------------------------------------------------------------------------

class TestResolveIsBlockedIp(unittest.IsolatedAsyncioTestCase):

    async def test_loopback_dotted_decimal_is_blocked(self):
        from tools.default.http_tool import _resolve_is_blocked_ip
        self.assertTrue(await _resolve_is_blocked_ip("127.0.0.1"))

    async def test_localhost_hostname_is_blocked(self):
        from tools.default.http_tool import _resolve_is_blocked_ip
        self.assertTrue(await _resolve_is_blocked_ip("localhost"))

    async def test_ipv4_mapped_ipv6_loopback_is_blocked(self):
        """Regression guard for the ipv4_mapped bug caught during
        implementation -- ip.is_loopback alone is False for this address."""
        from tools.default.http_tool import _resolve_is_blocked_ip
        self.assertTrue(await _resolve_is_blocked_ip("::ffff:127.0.0.1"))

    async def test_genuinely_unresolvable_host_fails_closed(self):
        from tools.default.http_tool import _resolve_is_blocked_ip
        self.assertTrue(
            await _resolve_is_blocked_ip("this-host-genuinely-does-not-exist-eva-test.invalid")
        )

    async def test_public_hostname_is_not_blocked(self):
        """Real DNS resolution -- example.com is a stable, public control."""
        from tools.default.http_tool import _resolve_is_blocked_ip
        self.assertFalse(await _resolve_is_blocked_ip("example.com"))

    async def test_decimal_encoded_loopback_blocked_via_mocked_resolution(self):
        """getaddrinfo("2130706433", ...) fails outright on this platform
        (confirmed empirically) -- mocked here to exercise the
        classification logic itself, portable to platforms where this
        legacy encoding does resolve (e.g. glibc)."""
        from tools.default.http_tool import _resolve_is_blocked_ip
        fake_loop = MagicMock()
        fake_loop.getaddrinfo = AsyncMock(return_value=_fake_resolution("127.0.0.1"))
        with patch("tools.default.http_tool.asyncio.get_event_loop", return_value=fake_loop):
            self.assertTrue(await _resolve_is_blocked_ip("2130706433"))

    async def test_hex_encoded_loopback_blocked_via_mocked_resolution(self):
        from tools.default.http_tool import _resolve_is_blocked_ip
        fake_loop = MagicMock()
        fake_loop.getaddrinfo = AsyncMock(return_value=_fake_resolution("127.0.0.1"))
        with patch("tools.default.http_tool.asyncio.get_event_loop", return_value=fake_loop):
            self.assertTrue(await _resolve_is_blocked_ip("0x7f000001"))

    async def test_link_local_and_private_ranges_blocked_via_mocked_resolution(self):
        from tools.default.http_tool import _resolve_is_blocked_ip
        for ip_str in ("169.254.1.1", "192.168.1.1", "10.0.0.1", "172.16.0.1"):
            fake_loop = MagicMock()
            fake_loop.getaddrinfo = AsyncMock(return_value=_fake_resolution(ip_str))
            with patch("tools.default.http_tool.asyncio.get_event_loop", return_value=fake_loop):
                self.assertTrue(
                    await _resolve_is_blocked_ip(f"attacker-controlled-hostname-{ip_str}.example"),
                    f"{ip_str} should be classified as blocked",
                )

    async def test_public_ip_via_mocked_resolution_not_blocked(self):
        from tools.default.http_tool import _resolve_is_blocked_ip
        fake_loop = MagicMock()
        fake_loop.getaddrinfo = AsyncMock(return_value=_fake_resolution("8.8.8.8"))
        with patch("tools.default.http_tool.asyncio.get_event_loop", return_value=fake_loop):
            self.assertFalse(await _resolve_is_blocked_ip("some-public-hostname.example"))


# ---------------------------------------------------------------------------
# Layer 2 — _check_hop_not_blocked() event hook + redirect re-validation,
# via httpx.MockTransport (deterministic, no real network for this part)
# ---------------------------------------------------------------------------

class TestRedirectRevalidation(unittest.IsolatedAsyncioTestCase):

    async def test_public_url_redirecting_to_internal_address_is_blocked(self):
        """The actual bug this fix closes: a URL that passes the initial
        check can redirect to an internal address, and the pre-fix code
        never re-checked redirect hops."""
        from tools.default.http_tool import _do_get

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"Location": "http://127.0.0.1:9999/admin"})
            return httpx.Response(200, text="LEAKED INTERNAL DATA")

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient  # capture before patching
        with patch("tools.default.http_tool.httpx.AsyncClient") as mock_client_cls:
            # Build a real AsyncClient using the mock transport so the real
            # event_hooks/redirect machinery runs, just against a fake
            # network -- only the transport is faked, not the client logic.
            mock_client_cls.side_effect = lambda **kwargs: real_async_client(
                transport=transport,
                timeout=kwargs.get("timeout"),
                follow_redirects=kwargs.get("follow_redirects", True),
                max_redirects=kwargs.get("max_redirects", 10),
                event_hooks=kwargs.get("event_hooks"),
            )
            result = await _do_get("http://example.com/start", headers=None, timeout=5)

        self.assertIn("error", result)
        self.assertIn("Blocked URL", result["error"])

    async def test_public_url_with_no_redirect_still_works(self):
        from tools.default.http_tool import _do_get

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="hello from a public site")

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient  # capture before patching
        with patch("tools.default.http_tool.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.side_effect = lambda **kwargs: real_async_client(
                transport=transport,
                timeout=kwargs.get("timeout"),
                follow_redirects=kwargs.get("follow_redirects", True),
                max_redirects=kwargs.get("max_redirects", 10),
                event_hooks=kwargs.get("event_hooks"),
            )
            result = await _do_get("http://example.com/page", headers=None, timeout=5)

        self.assertNotIn("error", result)
        self.assertEqual(result["status_code"], 200)
        self.assertIn("hello from a public site", result["body"])


# ---------------------------------------------------------------------------
# Layer 3 — real network sanity check (not mocked): the tool still works
# against the actual internet after the change.
# ---------------------------------------------------------------------------

class TestRealNetworkSanity(unittest.IsolatedAsyncioTestCase):

    async def test_real_public_url_still_succeeds(self):
        from tools.default.http_tool import _do_get
        result = await _do_get("http://example.com/", headers=None, timeout=10)
        self.assertNotIn("error", result, f"Unexpected error against a real public URL: {result}")
        self.assertEqual(result["status_code"], 200)

    async def test_real_localhost_url_is_blocked(self):
        from tools.default.http_tool import _do_get
        result = await _do_get("http://127.0.0.1:1/", headers=None, timeout=5)
        self.assertIn("error", result)
        self.assertIn("Blocked URL", result["error"])


if __name__ == "__main__":
    unittest.main()
