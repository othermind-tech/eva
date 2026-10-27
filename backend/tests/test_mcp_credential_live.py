#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S-11 MCP Credential Security — standalone live-server test.

Focused, fast alternative to running the full tests/test_behaviour.py suite
when the only thing that changed is MCP credential handling. No LLM/chat
calls at all -- unlike test_behaviour.py's B18 (which drives a real chat
turn to prove a synced MCP tool reaches the model), this only needs a real
MCP stdio subprocess and EVA's own config/sync/health endpoints, so it runs
in seconds, not minutes.

Uses tests/fixtures/mock_mcp_credential_server.py -- a genuine MCP protocol
server (built on the official `mcp` SDK) whose one tool's *description*
embeds the value of an environment variable it received at launch. That
makes a single POST /mcp/{id}/sync decisive, fast proof that
core/mcp/credentials.py::resolve_secrets() actually delivers the real
secret value to the connected subprocess -- not just that the DB/API layer
redacts it correctly, which the mocked-keyring unit tests already cover.

Usage:
    python tests/test_mcp_credential_live.py

Requirements:
    - EVA server running at http://127.0.0.1:8000
"""

import requests
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BASE       = "http://127.0.0.1:8000"
TEST_USER  = f"mcpsectest_{uuid.uuid4().hex[:8]}"
TEST_EMAIL = f"{TEST_USER}@eva-mcp-sec-test.com"
TEST_PASS  = "McpSecTest#1!"
RESULTS    = []

_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "mock_mcp_credential_server.py"
)
_REAL_SECRET = f"live-secret-{uuid.uuid4().hex[:12]}"

# ── helpers (same shapes as test_behaviour.py / test_pii_redaction_live.py) ──

def _hdr(token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def _sep(title):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")

def record(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    RESULTS.append((name, mark, detail))
    icon = "✓" if ok else "✗"
    print(f"  [{mark}] {icon} {name}")
    if detail:
        print(f"         {detail[:220]}")

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
    _h = requests.get(f"{BASE}/health", timeout=5)
    if _h.status_code != 200:
        print(f"FATAL: EVA server at {BASE} responded {_h.status_code} to /health. Is it up?")
        sys.exit(1)
except Exception as exc:
    print(f"FATAL: could not reach EVA server at {BASE} ({exc}). Start it first.")
    sys.exit(1)

# ── setup: isolated test user ────────────────────────────────────────────────

_sep("SETUP — Create isolated test user")
sc, body = post("/auth/signup", {
    "username": TEST_USER,
    "email":    TEST_EMAIL,
    "password": TEST_PASS,
})
if sc not in (200, 201) or "access_token" not in body:
    print(f"  FATAL: Could not create test user ({sc}): {body}")
    sys.exit(1)
TOKEN = body["access_token"]
print(f"  Test user: {TEST_USER}  (token acquired)")

# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — direct core/mcp/credentials.py sanity check (no server round-trip)
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 1 — direct credentials module sanity check")
try:
    from core.mcp.credentials import (
        is_secret_key, extract_and_store_secrets_for_field, REDACTED_PLACEHOLDER,
    )
    ok_p1a = is_secret_key("API_KEY") and is_secret_key("Authorization") and not is_secret_key("DEBUG")
    record("P1a is_secret_key() heuristic", ok_p1a)
except Exception as exc:
    record("P1a is_secret_key() heuristic", False, f"EXCEPTION: {exc}")

# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — create a real MCP config with a secret-shaped env key
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 2 — create MCP config, verify redaction in the response")

sc_create, body_create = post("/mcp/", {
    "name": "mcp_credential_live_test",
    "transport": "stdio",
    "command": sys.executable,
    "args": [_FIXTURE],
    "enabled": True,
    "env": {"TEST_SECRET_TOKEN": _REAL_SECRET, "DEBUG": "false"},
}, token=TOKEN)
config_id = body_create.get("id") if sc_create == 201 else None

ok_2a = sc_create == 201 and config_id is not None
record("P2a POST /mcp/ creates config", ok_2a, f"sc={sc_create} id={config_id!r}")

ok_2b = (
    ok_2a
    and body_create.get("env", {}).get("TEST_SECRET_TOKEN") == REDACTED_PLACEHOLDER
    and body_create.get("env", {}).get("DEBUG") == "false"
    and _REAL_SECRET not in str(body_create)
)
record("P2b create response redacts the secret, leaves DEBUG alone", ok_2b,
       f"env={body_create.get('env')}")

# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — GET the config back, confirm it's still redacted (not just on create)
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 3 — GET config, confirm redaction persists")

if config_id:
    sc_get, body_get = get(f"/mcp/{config_id}", token=TOKEN)
    ok_3a = (
        sc_get == 200
        and body_get.get("env", {}).get("TEST_SECRET_TOKEN") == REDACTED_PLACEHOLDER
        and _REAL_SECRET not in str(body_get)
    )
    record("P3a GET /mcp/{id} still returns the redacted placeholder", ok_3a,
           f"env={body_get.get('env')}")
else:
    record("P3a GET /mcp/{id} still returns the redacted placeholder", False, "SKIP — no config_id")

# ══════════════════════════════════════════════════════════════════════════════
# PART 4 — sync tools: decisive proof the REAL secret reaches the subprocess
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 4 — POST /mcp/{id}/sync: real secret must reach the live subprocess")

if config_id:
    sc_sync, body_sync = post(f"/mcp/{config_id}/sync", token=TOKEN, timeout=30)
    tools = body_sync.get("tools", []) if sc_sync == 200 else []
    descriptions = " ".join(t.get("description", "") or "" for t in tools)
    ok_4a = sc_sync == 200 and f"received_secret={_REAL_SECRET}" in descriptions
    record("P4a synced tool description proves resolve_secrets() delivered the real value", ok_4a,
           f"sc={sc_sync} descriptions={descriptions!r}")
else:
    record("P4a synced tool description proves resolve_secrets() delivered the real value", False,
           "SKIP — no config_id")

# ══════════════════════════════════════════════════════════════════════════════
# PART 5 — update an unrelated field, confirm the secret still resolves after
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 5 — update unrelated field, re-sync, secret must still resolve")

if config_id:
    sc_upd, body_upd = put(f"/mcp/{config_id}", {"enabled": True}, token=TOKEN)
    ok_5a = sc_upd == 200 and body_upd.get("env", {}).get("TEST_SECRET_TOKEN") == REDACTED_PLACEHOLDER
    record("P5a update to an unrelated field leaves env's redaction/refs intact", ok_5a,
           f"sc={sc_upd} env={body_upd.get('env')}")

    sc_sync2, body_sync2 = post(f"/mcp/{config_id}/sync", token=TOKEN, timeout=30)
    tools2 = body_sync2.get("tools", []) if sc_sync2 == 200 else []
    descriptions2 = " ".join(t.get("description", "") or "" for t in tools2)
    ok_5b = sc_sync2 == 200 and f"received_secret={_REAL_SECRET}" in descriptions2
    record("P5b secret still resolves correctly after an unrelated update", ok_5b,
           f"sc={sc_sync2} descriptions={descriptions2!r}")
else:
    record("P5a update to an unrelated field leaves env's redaction/refs intact", False, "SKIP")
    record("P5b secret still resolves correctly after an unrelated update", False, "SKIP")

# ── teardown ─────────────────────────────────────────────────────────────────
_sep("TEARDOWN")
if config_id:
    delete(f"/mcp/{config_id}", token=TOKEN)
print("  Test data cleaned up.")

# ── summary ──────────────────────────────────────────────────────────────────
_sep("SUMMARY")
passed = sum(1 for _, m, _ in RESULTS if m == "PASS")
failed = sum(1 for _, m, _ in RESULTS if m == "FAIL")
print(f"\n  Total : {passed + failed}")
print(f"  PASS  : {passed}")
print(f"  FAIL  : {failed}")
if failed:
    print("\n  --- Failed checks ---")
    for name, mark, detail in RESULTS:
        if mark == "FAIL":
            print(f"    ✗ {name}")
            if detail:
                print(f"      {detail}")
print(f"\n  {'ALL MCP CREDENTIAL TESTS PASSED' if failed == 0 else f'{failed} TEST(S) FAILED'}\n")
sys.exit(1 if failed else 0)
