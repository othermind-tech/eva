#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Background-process-supervision Phase 3 -- standalone live-server test.

Focused, fast alternative to the full tests/test_behaviour.py suite when the
only thing that changed is the human-facing wiring on top of the already
live-verified Phase 2 primitives (ShellExecutor.start_background/
stop_background/tail_background, the background_process LLM tool). This
script does NOT drive a chat turn to start the process (Phase 2's own
V6.37 live verification already proved the LLM tool path end to end) --
instead it calls ShellExecutor.start_background() directly, in-process,
against the SAME eva.db file the live server reads from, then verifies
everything Phase 3 actually added over plain HTTP against the real running
server: GET /executions/running surfacing the row, POST /processes/{id}/stop
actually killing the real OS process, ownership enforcement, 404/409
handling, and the process.stopped SSE event firing live.

Usage:
    python tests/test_background_process_live.py

Requirements:
    - EVA server running at http://127.0.0.1:8000 (already restarted with
      the Phase 3 code -- api/processes.py, the updated GET /executions/running)
    - Run from the same directory the server itself runs from, so this
      script's own AsyncSessionLocal resolves to the identical eva.db file
      (DATABASE_URL defaults to sqlite+aiosqlite:///./eva.db, relative to cwd).
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BASE = "http://127.0.0.1:8000"
TEST_USER = f"bgproclive_{uuid.uuid4().hex[:8]}"
TEST_EMAIL = f"{TEST_USER}@eva-bgproc-live-test.com"
TEST_PASS = "BgProcLiveTest#1!"
OTHER_USER = f"bgproclive_other_{uuid.uuid4().hex[:8]}"
OTHER_EMAIL = f"{OTHER_USER}@eva-bgproc-live-test.com"
OTHER_PASS = "BgProcLiveTest#2!"
RESULTS = []

# ── helpers (same shapes as test_mcp_credential_live.py / test_pii_redaction_live.py) ──


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


def get(path, token=None, timeout=15):
    r = requests.get(f"{BASE}{path}", headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


def pid_alive_via_tasklist(pid: int) -> bool:
    """Independent, out-of-process liveness check -- deliberately NOT calling
    core.shell.executor._pid_alive() here, so this doesn't just re-check the
    same code path stop_background() itself uses."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
        )
        return str(pid) in out.stdout
    except Exception:
        return False


def signup(username, email, password):
    sc, body = post("/auth/signup", {"username": username, "email": email, "password": password})
    if sc not in (200, 201) or "access_token" not in body:
        print(f"  FATAL: Could not create test user {username!r} ({sc}): {body}")
        sys.exit(1)
    return body["access_token"]


# ── preflight ────────────────────────────────────────────────────────────────

try:
    _h = requests.get(f"{BASE}/health", timeout=5)
    if _h.status_code != 200:
        print(f"FATAL: EVA server at {BASE} responded {_h.status_code} to /health. Is it up?")
        sys.exit(1)
except Exception as exc:
    print(f"FATAL: could not reach EVA server at {BASE} ({exc}). Start it first.")
    sys.exit(1)

_sep("SETUP -- create isolated test users")
TOKEN = signup(TEST_USER, TEST_EMAIL, TEST_PASS)
OTHER_TOKEN = signup(OTHER_USER, OTHER_EMAIL, OTHER_PASS)
print(f"  Test user : {TEST_USER}  (token acquired)")
print(f"  Other user: {OTHER_USER}  (token acquired)")

# The JWT's "sub" (and therefore every endpoint's auth["user_id"]) is the
# internal DB user id (core/auth/security.py::create_access_token(user_id=user.id, ...)),
# NOT the username string -- GET /auth/me is the one place that maps a
# token back to that internal id, needed here because this script calls
# ShellExecutor.start_background() directly (bypassing the HTTP auth layer)
# and must pass the SAME user_id value the live server's endpoints will
# compare ownership against.
sc_me, body_me = get("/auth/me", token=TOKEN)
sc_me_other, body_me_other = get("/auth/me", token=OTHER_TOKEN)
if sc_me != 200 or sc_me_other != 200:
    print(f"  FATAL: GET /auth/me failed (sc={sc_me}/{sc_me_other})")
    sys.exit(1)
TEST_USER_ID = body_me["user_id"]
OTHER_USER_ID = body_me_other["user_id"]
print(f"  Test user internal id : {TEST_USER_ID}")
print(f"  Other user internal id: {OTHER_USER_ID}")

# ══════════════════════════════════════════════════════════════════════════════
# PART 1 -- start a real background process directly via ShellExecutor,
# exactly the primitive the background_process LLM tool itself calls,
# against the SAME eva.db the live server reads from.
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 1 -- start a real background process (ShellExecutor.start_background)")

from core.shell.executor import ShellExecutor  # noqa: E402

_cmd = [sys.executable, "-c", "import time; [print(f'tick {i}', flush=True) or time.sleep(1) for i in range(120)]"]


async def _start():
    return await ShellExecutor.start_background(cmd=_cmd, user_id=TEST_USER_ID)


started = asyncio.run(_start())
ok_1a = "error" not in started and "process_id" in started and "pid" in started
record("P1a start_background() returns a real process_id/pid", ok_1a, f"{started}")
process_id = started.get("process_id")
pid = started.get("pid")

ok_1b = pid is not None and pid_alive_via_tasklist(pid)
record("P1b independent tasklist check confirms the OS process is alive", ok_1b, f"pid={pid}")

# ══════════════════════════════════════════════════════════════════════════════
# PART 2 -- GET /executions/running (real HTTP, real running server) must
# surface this row with the correct Phase 3 fields.
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 2 -- GET /executions/running surfaces the background process")

sc_run, body_run = get("/executions/running", token=TOKEN)
match = next((r for r in body_run if r.get("execution_id") == process_id), None) if sc_run == 200 else None

ok_2a = sc_run == 200 and match is not None
record("P2a row for this process_id appears in GET /executions/running", ok_2a,
       f"sc={sc_run} count={len(body_run) if sc_run == 200 else 'n/a'}")

ok_2b = (
    match is not None
    and match.get("execution_mode") == "background_process"
    and match.get("status") == "running"
    and match.get("pid") == pid
    and match.get("log_path") == started.get("log_path")
    and match.get("conversation_id") == ""
)
record("P2b row has correct execution_mode/status/pid/log_path/conversation_id fallback", ok_2b,
       f"match={match}")

ok_2d = match is not None and match.get("command") == " ".join(_cmd)
record("P2d row's command field is the joined argv string (frontend-requested field)", ok_2d,
       f"command={match.get('command') if match else None!r} expected={' '.join(_cmd)!r}")

sc_run_other, body_run_other = get("/executions/running", token=OTHER_TOKEN)
ok_2c = sc_run_other == 200 and all(r.get("execution_id") != process_id for r in body_run_other)
record("P2c a different user's GET /executions/running does NOT show this row", ok_2c,
       f"sc={sc_run_other}")

# ══════════════════════════════════════════════════════════════════════════════
# PART 3 -- SSE: open a real stream, then trigger the stop over HTTP, and
# confirm process.stopped actually arrives live (not just a poll-catchable
# DB change).
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 3 -- live SSE process.stopped event")

sc_sse_tok, body_sse_tok = post("/auth/sse-token", token=TOKEN)
sse_events = []
sse_error = []


def _listen_sse(sse_token, stop_flag):
    try:
        with requests.get(
            f"{BASE}/events/stream", params={"sse_token": sse_token}, stream=True, timeout=15
        ) as resp:
            current_event = None
            for raw_line in resp.iter_lines(decode_unicode=True):
                if stop_flag["done"]:
                    break
                if raw_line is None:
                    continue
                line = raw_line.strip()
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                elif line.startswith("data:") and current_event:
                    try:
                        payload = json.loads(line[len("data:"):].strip())
                    except Exception:
                        payload = {}
                    sse_events.append((current_event, payload))
                    current_event = None
    except Exception as exc:
        sse_error.append(str(exc))


ok_3setup = sc_sse_tok == 200 and "sse_token" in body_sse_tok
record("P3setup POST /auth/sse-token issues a token", ok_3setup, f"sc={sc_sse_tok}")

_stop_flag = {"done": False}
if ok_3setup:
    _listener = threading.Thread(
        target=_listen_sse, args=(body_sse_tok["sse_token"], _stop_flag), daemon=True
    )
    _listener.start()
    time.sleep(1.0)  # let the SSE connection actually establish before we trigger the stop

# ══════════════════════════════════════════════════════════════════════════════
# PART 4 -- POST /processes/{id}/stop over real HTTP against the real server;
# must actually kill the OS process (checked independently, not via the API
# response), and the DB row must move to terminal.
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 4 -- POST /processes/{id}/stop kills the real OS process")

sc_stop, body_stop = post(f"/processes/{process_id}/stop", token=TOKEN)
ok_4a = sc_stop == 200 and body_stop.get("status") == "stopped"
record("P4a POST /processes/{id}/stop returns 200 {status: stopped}", ok_4a, f"sc={sc_stop} body={body_stop}")

time.sleep(1.0)  # give the kill (terminate-then-grace-then-kill) a moment to land
ok_4b = not pid_alive_via_tasklist(pid)
record("P4b independent tasklist check confirms the OS process is actually gone", ok_4b, f"pid={pid}")

sc_run2, body_run2 = get("/executions/running", token=TOKEN)
ok_4c = sc_run2 == 200 and all(r.get("execution_id") != process_id for r in body_run2)
record("P4c GET /executions/running no longer lists the stopped process", ok_4c, f"sc={sc_run2}")

# give the SSE listener a moment to receive the event, then stop it
time.sleep(1.5)
_stop_flag["done"] = True

ok_3event = any(
    ev == "process.stopped" and payload.get("execution_id") == process_id
    for ev, payload in sse_events
)
record("P3event process.stopped arrived live over the open SSE connection", ok_3event,
       f"events_seen={sse_events!r}" + (f" sse_error={sse_error[0]}" if sse_error else ""))

# ══════════════════════════════════════════════════════════════════════════════
# PART 5 -- error handling: 404 for unknown/unowned, 409 for already-terminal.
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 5 -- 404 (unknown/unowned) and 409 (already terminal) handling")

sc_404a, body_404a = post("/processes/not-a-real-process-id-at-all/stop", token=TOKEN)
record("P5a stopping a nonexistent process_id returns 404", sc_404a == 404, f"sc={sc_404a} body={body_404a}")

sc_409, body_409 = post(f"/processes/{process_id}/stop", token=TOKEN)
record("P5b stopping the already-stopped process again returns 409", sc_409 == 409, f"sc={sc_409} body={body_409}")

# A second process, owned by TEST_USER, to prove OTHER_USER gets 404 (not
# 409/403) trying to stop it -- must look identical to "doesn't exist".


async def _start2():
    return await ShellExecutor.start_background(cmd=_cmd, user_id=TEST_USER_ID)


started2 = asyncio.run(_start2())
process_id2 = started2.get("process_id")
pid2 = started2.get("pid")

sc_404b, body_404b = post(f"/processes/{process_id2}/stop", token=OTHER_TOKEN)
record("P5c a different user stopping this process returns 404 (not 409/403 -- no existence leak)",
       sc_404b == 404, f"sc={sc_404b} body={body_404b}")

# confirm it's genuinely untouched by the rejected attempt from OTHER_USER
ok_5d = pid2 is not None and pid_alive_via_tasklist(pid2)
record("P5d the rejected cross-user stop attempt left the process running", ok_5d, f"pid={pid2}")

# ── teardown ─────────────────────────────────────────────────────────────────
_sep("TEARDOWN")
sc_cleanup, body_cleanup = post(f"/processes/{process_id2}/stop", token=TOKEN)
print(f"  Cleanup stop of second process: sc={sc_cleanup} body={body_cleanup}")
print("  Test data cleaned up (test users are left in the DB, same convention as other live tests).")

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
print(f"\n  {'ALL BACKGROUND PROCESS PHASE 3 LIVE TESTS PASSED' if failed == 0 else f'{failed} TEST(S) FAILED'}\n")
sys.exit(1 if failed else 0)
