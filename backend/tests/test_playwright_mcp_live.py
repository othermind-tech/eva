#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Default Playwright MCP (M-01 seeder + M-02 browser automation) -- standalone
live-server test. No LLM calls.

Two halves, both against real things (real server, real npx, real Edge/Chrome):

  HTTP half (the running EVA server): a fresh signup is seeded with the
  playwright config (disabled); POST /mcp/seed-defaults is a no-op re-seed;
  enabling it (PUT /mcp/{id}) launches the real Playwright MCP server and
  syncs its tools; GET /tools/mcp lists them.

  Runtime half (this process, same eva.db): drives real browser tool calls
  through EVA's own MCPRuntime + session pool against a page served locally
  -- the exact path an agent's tool call takes -- and checks the parts the
  app-building loop depends on: page snapshot, console errors, failed network
  requests, and that a screenshot lands as a FILE in the user's workspace
  instead of a base64 blob in the model's context.

The test forces --headless on its own copy of the seeded config (also
exercising the user-edits-args path) so no browser window appears.

Usage:
    python tests/test_playwright_mcp_live.py

Requirements:
    - EVA server running at http://127.0.0.1:8000 with this code
    - Run from the backend directory (same eva.db as the server)
    - Node.js (npx) and Microsoft Edge (Windows) / Google Chrome; network on
      the first run so npx can fetch the pinned @playwright/mcp package
"""

import asyncio
import functools
import http.server
import os
import sys
import tempfile
import threading
import uuid

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures.playwright_mcp_tools import PINNED_VERSION, TOOL_NAMES  # noqa: E402
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BASE = "http://127.0.0.1:8000"
TEST_USER = f"pwmcplive_{uuid.uuid4().hex[:8]}"
TEST_EMAIL = f"{TEST_USER}@eva-pwmcp-live-test.com"
TEST_PASS = "PwMcpLiveTest#1!"
RESULTS = []

PAGE_HTML = """<!doctype html><html><head><title>Live Probe App</title></head><body>
<h1>Todo probe</h1><button onclick="console.error('deliberate click error')">Break it</button>
<script>console.error('deliberate console error on load'); fetch('/api/missing').catch(()=>{});</script>
</body></html>"""


def _hdr(token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _sep(title):
    print(f"\n{'='*72}\n  {title}\n{'='*72}")


def record(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    RESULTS.append((name, mark, detail))
    print(f"  [{mark}] {'✓' if ok else '✗'} {name}")
    if detail:
        print(f"         {detail[:240]}")


def post(path, payload=None, token=None, timeout=20):
    r = requests.post(f"{BASE}{path}", json=payload, headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


def put(path, payload=None, token=None, timeout=20):
    r = requests.put(f"{BASE}{path}", json=payload, headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


def get(path, token=None, timeout=15):
    r = requests.get(f"{BASE}{path}", headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


def delete(path, token=None, timeout=15):
    r = requests.delete(f"{BASE}{path}", headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


# ── preflight ────────────────────────────────────────────────────────────────
try:
    if requests.get(f"{BASE}/health", timeout=5).status_code != 200:
        print(f"FATAL: EVA server at {BASE} is not healthy.")
        sys.exit(1)
except Exception as exc:
    print(f"FATAL: could not reach EVA server at {BASE} ({exc}). Start it first.")
    sys.exit(1)

_sep("SETUP -- isolated test user (signup seeds the default MCPs)")
sc, body = post("/auth/signup", {"username": TEST_USER, "email": TEST_EMAIL, "password": TEST_PASS})
if sc not in (200, 201) or "access_token" not in body:
    print(f"  FATAL: signup failed ({sc}): {body}")
    sys.exit(1)
TOKEN = body["access_token"]
USER_ID = get("/auth/me", token=TOKEN)[1]["user_id"]
print(f"  Test user: {TEST_USER}  internal id: {USER_ID}")

# ══════════════════════════════════════════════════════════════════════════════
# PART 1 -- seeding over HTTP
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 1 -- default config is seeded, disabled, placeholders resolved")

sc, configs = get("/mcp/", token=TOKEN)
pw = next((c for c in configs if c["name"] == "playwright"), None) if sc == 200 else None
record("P1a signup seeded a 'playwright' MCP config", pw is not None, f"sc={sc} names={[c['name'] for c in configs] if sc == 200 else configs}")
config_id = pw["id"] if pw else None

record("P1b seeded disabled (opt-in: no tool bloat, no surprise browser window)",
       pw is not None and pw["enabled"] is False)

args = (pw or {}).get("args") or []
record("P1c placeholders resolved in args/cwd (no '{' left)",
       pw is not None and "{" not in " ".join(args) and "{" not in ((pw or {}).get("cwd") or ""),
       f"cwd={(pw or {}).get('cwd')!r} args={args}")

sc, seed2 = post("/mcp/seed-defaults", token=TOKEN)
record("P1d POST /mcp/seed-defaults is an idempotent no-op for an already-seeded user",
       sc == 200 and seed2.get("mcps_seeded") == 0 and seed2.get("mcps_skipped") == 1, f"sc={sc} body={seed2}")

# ══════════════════════════════════════════════════════════════════════════════
# PART 2 -- enable => real Playwright MCP server launches + tools sync
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 2 -- enable (headless for this test) => real server launch + tool sync")

tool_names = []
if config_id:
    sc, upd = put(f"/mcp/{config_id}", {"args": args + ["--headless"], "enabled": True}, token=TOKEN, timeout=180)
    record("P2a PUT enabled=true launches the real server and syncs tools (no sync_error)",
           sc == 200 and upd.get("sync_error") is None and (upd.get("tool_count") or 0) >= 20,
           f"sc={sc} tool_count={upd.get('tool_count')} sync_error={upd.get('sync_error')}")

    sc, mcp_tools = get("/tools/mcp", token=TOKEN)
    tool_names = [t["name"] for t in mcp_tools] if sc == 200 else []
    needed = {"playwright__browser_navigate", "playwright__browser_snapshot",
              "playwright__browser_console_messages", "playwright__browser_network_requests",
              "playwright__browser_click", "playwright__browser_take_screenshot"}
    record("P2b GET /tools/mcp lists the browser tools the build-test-fix loop needs",
           needed.issubset(tool_names), f"missing={sorted(needed - set(tool_names))}")

    served = {n[len("playwright__"):] for n in tool_names if n.startswith("playwright__")}
    record(f"P2c the server's real tool list equals tests/fixtures/playwright_mcp_tools.py (pinned {PINNED_VERSION}) "
           "-- the drift guard behind skills/playwright-browser-testing",
           served == set(TOOL_NAMES),
           f"only on server={sorted(served - set(TOOL_NAMES))} only in fixture={sorted(set(TOOL_NAMES) - served)}")
else:
    record("P2a PUT enabled=true launches the real server and syncs tools", False, "SKIP -- no config")
    record("P2b GET /tools/mcp lists the browser tools", False, "SKIP -- no config")

# ══════════════════════════════════════════════════════════════════════════════
# PART 3 -- real browser tool calls through EVA's own runtime
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 3 -- real tool calls (MCPRuntime + session pool) against a local page")


async def runtime_half():
    from core.db.session import AsyncSessionLocal
    from core.mcp.runtime import MCPRuntime
    from core.mcp.session_pool import mcp_session_pool
    from utils.workspace import get_user_workspace

    site_dir = tempfile.mkdtemp(prefix="pwmcp_live_site_")
    with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(PAGE_HTML)

    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=site_dir))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"

    async def call(tool, arguments=None):
        async with AsyncSessionLocal() as db:
            res = await MCPRuntime.execute(
                db=db, user_id=USER_ID, tool_name=f"playwright__{tool}", arguments=arguments or {})
        return res.result

    def text_of(result):
        return "\n".join(result) if isinstance(result, list) else str(result)

    try:
        nav = await call("browser_navigate", {"url": url})
        nav_text = text_of(nav)
        record("P3a browser_navigate loads the local page", "Live Probe App" in nav_text,
               nav_text[:200] if "Live Probe App" not in nav_text else "")

        snap_text = text_of(await call("browser_snapshot"))
        record("P3b browser_snapshot returns the accessibility tree inline (the model can read and target elements)",
               'heading "Todo probe"' in snap_text and 'button "Break it"' in snap_text, snap_text[:240])

        console_text = text_of(await call("browser_console_messages", {"level": "error"}))
        record("P3c browser_console_messages surfaces the page's runtime console error",
               "deliberate console error on load" in console_text, console_text[:240])

        net_text = text_of(await call("browser_network_requests"))
        record("P3d browser_network_requests surfaces the failed request (404)",
               "/api/missing" in net_text and "404" in net_text, net_text[:240])

        shot = await call("browser_take_screenshot")
        shot_text = text_of(shot)
        record("P3e screenshot result is small text, never a base64 payload in the model's context",
               isinstance(shot, list) and len(shot_text) < 2000 and "iVBOR" not in shot_text,
               f"len={len(shot_text)} :: {shot_text[:160]}")

        out_dir = os.path.join(get_user_workspace(USER_ID), "_browser_output")
        pngs = [f for f in os.listdir(out_dir) if f.endswith(".png")] if os.path.isdir(out_dir) else []
        record("P3f the screenshot was written as a file inside the user's workspace (_browser_output)",
               len(pngs) >= 1 and os.path.getsize(os.path.join(out_dir, pngs[0])) > 1000,
               f"dir={out_dir} files={pngs}")

        stray = os.path.join(os.getcwd(), ".playwright-mcp")
        record("P3g no stray .playwright-mcp directory was created in the backend directory",
               not os.path.exists(stray), stray)
    finally:
        httpd.shutdown()
        await mcp_session_pool.stop()


asyncio.run(runtime_half())

# ── teardown ─────────────────────────────────────────────────────────────────
_sep("TEARDOWN")
if config_id:
    delete(f"/mcp/{config_id}", token=TOKEN)
print("  Test config removed (test user left in the DB, same convention as other live tests).")

# ── summary ──────────────────────────────────────────────────────────────────
_sep("SUMMARY")
passed = sum(1 for _, m, _ in RESULTS if m == "PASS")
failed = sum(1 for _, m, _ in RESULTS if m == "FAIL")
print(f"\n  Total : {passed + failed}\n  PASS  : {passed}\n  FAIL  : {failed}")
if failed:
    print("\n  --- Failed checks ---")
    for name, mark, detail in RESULTS:
        if mark == "FAIL":
            print(f"    ✗ {name}")
            if detail:
                print(f"      {detail}")
print(f"\n  {'ALL PLAYWRIGHT MCP TESTS PASSED' if failed == 0 else f'{failed} TEST(S) FAILED'}\n")
sys.exit(1 if failed else 0)
