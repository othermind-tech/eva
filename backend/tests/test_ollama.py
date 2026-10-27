#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EVA Ollama Provider — Standalone Test Runner (Section 24)
=========================================================
Runs ONLY the Ollama provider checks without executing the full 175-check suite.

Usage:
    python test_ollama.py

Prerequisites:
    - EVA server running on http://127.0.0.1:8000
    - Ollama running on http://localhost:11500  (or set OLLAMA_HOST env var)
    - qwen3:8b model installed:  ollama pull qwen3:8b

Checks:
    24a  GET /providers/list includes "ollama" as a local, chat-ready provider
    24b  GET /providers/ollama/models → valid shape  (404 is acceptable when Ollama is down)
    24c  POST /set-session accepts provider=ollama + model=qwen3:8b
    24d  GET /session reflects provider=ollama after set-session
    24e  Unauthenticated /providers/ollama/models → 401
"""

import json
import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Configuration ──────────────────────────────────────────────────────────────

BASE = "http://127.0.0.1:8000"

# Ollama host used in the set-session api_key field (repurposed as host URL).
# Change this if your Ollama instance is on a different port.
OLLAMA_HOST = "http://localhost:11500"

# Model to use for session tests — must be installed on your Ollama instance.
OLLAMA_TEST_MODEL = "qwen3:8b"

# Temporary test user credentials (created fresh each run)
TS = str(int(time.time()))
TEST_USER  = f"eva_ollama_test_{TS}"
TEST_EMAIL = f"{TEST_USER}@example.com"
TEST_PASS  = "TestPass123!"

# ── Helpers ────────────────────────────────────────────────────────────────────

RESULTS = []


def hdr(token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def record(name, ok, code, body):
    mark = "PASS" if ok else "FAIL"
    RESULTS.append((name, mark, code, body))
    short = json.dumps(body)[:250] if isinstance(body, (dict, list)) else str(body)[:250]
    print(f"  [{mark}] [{code}] {name}")
    print(f"         {short}")


def do_get(path, token=None, expect=200):
    try:
        r = requests.get(f"{BASE}{path}", headers=hdr(token), timeout=30)
        body = r.json() if r.content else {}
        if expect is not None:
            record(f"GET {path}", r.status_code == expect, r.status_code, body)
        return r.status_code, body
    except Exception as e:
        record(f"GET {path}", False, 0, str(e))
        return 0, {}


def do_post(path, payload=None, token=None, expect=200):
    try:
        r = requests.post(f"{BASE}{path}", json=payload, headers=hdr(token), timeout=15)
        body = r.json() if r.content else {}
        if expect is not None:
            record(f"POST {path}", r.status_code == expect, r.status_code, body)
        return r.status_code, body
    except Exception as e:
        record(f"POST {path}", False, 0, str(e))
        return 0, {}


def do_post_form(path, data, token=None, expect=200):
    try:
        h = {}
        if token:
            h["Authorization"] = f"Bearer {token}"
        r = requests.post(f"{BASE}{path}", data=data, headers=h, timeout=15)
        body = r.json() if r.content else {}
        record(f"POST {path}", r.status_code == expect, r.status_code, body)
        return r.status_code, body
    except Exception as e:
        record(f"POST {path}", False, 0, str(e))
        return 0, {}


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── Startup check ──────────────────────────────────────────────────────────────

section("HEALTH CHECK")
code, body = do_get("/health")
if code != 200:
    print("\nFATAL: EVA server not reachable at http://127.0.0.1:8000 — aborting.")
    sys.exit(1)
print("  Server is up.")

# ── Auth (create a temporary test user) ───────────────────────────────────────

section("AUTH — Create test user")

code, body = do_post("/auth/signup", {
    "username": TEST_USER,
    "email": TEST_EMAIL,
    "password": TEST_PASS,
})
if code not in (200, 201):
    print(f"\nFATAL: Signup failed ({code}) — aborting.")
    sys.exit(1)

code, body = do_post_form("/auth/login", {
    "username": TEST_USER,
    "password": TEST_PASS,
})
TOKEN = body.get("access_token")
if not TOKEN:
    print("\nFATAL: Login failed — cannot proceed.")
    sys.exit(1)
print(f"  Logged in as {TEST_USER}  token={TOKEN[:40]}...")

# ── Section 24 — Ollama Provider ───────────────────────────────────────────────

section("24. OLLAMA PROVIDER INTEGRATION")
print(f"  Ollama host  : {OLLAMA_HOST}")
print(f"  Test model   : {OLLAMA_TEST_MODEL}")

# 24a — GET /providers/list includes "ollama" as a local, chat-ready provider.
#        (The old GET /providers route no longer exists; the curated list is
#        /providers/list. Local Ollama is always listed and always chat-ready --
#        it needs a host URL, not a key -- so this does NOT depend on Ollama
#        actually running, and is part of the all_24_ok gate.)
code_24a, body_24a = do_get("/providers/list", token=TOKEN, expect=None)
providers_list_24a = body_24a if isinstance(body_24a, list) else []
ollama_entry_24a = next((p for p in providers_list_24a if isinstance(p, dict) and p.get("provider") == "ollama"), None)
ok_24a = (
    code_24a == 200
    and ollama_entry_24a is not None
    and ollama_entry_24a.get("is_local") is True
    and ollama_entry_24a.get("chat_ready") is True
)
if ok_24a:
    print("  24a  GET /providers/list → ollama present, local, chat-ready: OK")
else:
    print(f"  24a  GET /providers/list → FAIL [{code_24a}] ollama entry: {ollama_entry_24a}")
record("24a. Ollama appears in GET /providers/list (local, chat-ready)", ok_24a, code_24a, body_24a)

# 24b — GET /providers/ollama/models → valid shape
#        200 + correct shape = OK.  404 = acceptable (Ollama was down at startup).
code_24b, body_24b = do_get("/providers/ollama/models", token=TOKEN, expect=None)
if code_24b == 200:
    ok_24b = (
        isinstance(body_24b, dict)
        and body_24b.get("provider") == "ollama"
        and isinstance(body_24b.get("models"), list)
    )
    if ok_24b:
        found = [m.get("model", "") for m in body_24b.get("models", []) if isinstance(m, dict)]
        print(f"  24b  GET /providers/ollama/models → OK [{len(found)} model(s): {found[:5]}]")
    else:
        print(f"  24b  GET /providers/ollama/models → FAIL (bad shape) [{code_24b}]")
elif code_24b == 404:
    ok_24b = True
    print("  24b  GET /providers/ollama/models → 404 (Ollama not running at startup) — acceptable")
else:
    ok_24b = False
    print(f"  24b  GET /providers/ollama/models → FAIL [{code_24b}] {str(body_24b)[:120]}")
record("24b. GET /providers/ollama/models shape", ok_24b, code_24b, body_24b)

# 24c — POST /set-session accepts provider=ollama
code_24c, body_24c = do_post(
    "/set-session",
    token=TOKEN,
    payload={
        "provider": "ollama",
        "model": OLLAMA_TEST_MODEL,
        "api_key": OLLAMA_HOST,   # repurposed as Ollama host URL
        "stream": False,
        "reasoning": False,
    },
)
ok_24c = code_24c == 200 and isinstance(body_24c, dict) and body_24c.get("provider") == "ollama"
print(f"  24c  POST /set-session provider=ollama model={OLLAMA_TEST_MODEL}: {'OK' if ok_24c else 'FAIL'} [{code_24c}]")
record("24c. /set-session accepts provider=ollama", ok_24c, code_24c, body_24c)

# 24d — GET /session reflects ollama
code_24d, body_24d = do_get("/session", token=TOKEN, expect=None)
ok_24d = (
    code_24d == 200
    and isinstance(body_24d, dict)
    and body_24d.get("provider") == "ollama"
    and body_24d.get("model") == OLLAMA_TEST_MODEL
)
print(f"  24d  GET /session reflects provider=ollama model={OLLAMA_TEST_MODEL}: {'OK' if ok_24d else 'FAIL'} [{code_24d}]")
record("24d. GET /session reflects ollama provider", ok_24d, code_24d, body_24d)

# 24e — Unauthenticated /providers/ollama/models → 401
code_24e, body_24e = do_get("/providers/ollama/models", token="bad-token", expect=None)
ok_24e = code_24e == 401
print(f"  24e  /providers/ollama/models unauth → 401: {'OK' if ok_24e else 'FAIL'} [{code_24e}]")
record("24e. /providers/ollama/models rejects unauth", ok_24e, code_24e, body_24e)

all_24_ok = ok_24a and ok_24b and ok_24c and ok_24d and ok_24e

# ── Summary ────────────────────────────────────────────────────────────────────

section("RESULTS SUMMARY")

passes  = sum(1 for _, m, _, _ in RESULTS if m == "PASS")
fails   = sum(1 for _, m, _, _ in RESULTS if m == "FAIL")
total   = len(RESULTS)

print(f"\n  Ollama checks : {'ALL PASS' if all_24_ok else 'SOME FAILED'}")
print(f"  24a (provider list): {'OK' if ok_24a else 'FAIL'}")
print(f"  24b (shape)        : {'OK' if ok_24b else 'FAIL'}")
print(f"  24c (set-session)  : {'OK' if ok_24c else 'FAIL'}")
print(f"  24d (session get)  : {'OK' if ok_24d else 'FAIL'}")
print(f"  24e (unauth 401)   : {'OK' if ok_24e else 'FAIL'}")
print(f"\n  Total checks  : {total}   PASS={passes}  FAIL={fails}")

if fails:
    print("\n  FAILED checks:")
    for name, mark, code, body in RESULTS:
        if mark == "FAIL":
            print(f"    • {name}  [{code}]")

print()
sys.exit(0 if all_24_ok else 1)
