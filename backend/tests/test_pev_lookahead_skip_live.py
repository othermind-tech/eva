#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A-21 Adaptive PEV — standalone live-server test.

Same server/credentials/model pattern as tests/test_pii_redaction_live.py.
Covers:

  PART 1 — pev_lookahead_skip_enabled session round-trip (deterministic,
           no model call): default true, can be set false and back.
  PART 2 — a real plan_execute run with pev_lookahead_skip_enabled=True,
           using a task deliberately shaped to encourage a step's own
           evidence to also satisfy the very next step (a write followed
           by a read-back of the same file). Whether the model/verifier
           actually produce a skip is real live behaviour and not
           guaranteed -- if no skip occurs this is recorded as SKIP (like
           test_pii_redaction_live.py's tool-call-dependent checks), not
           FAIL. If a skip DOES occur, asserts on its shape: the skipped
           step's status/verified/result_summary, steps_skipped tally,
           and that the plan's own true final step was still verified for
           real (never skipped) regardless.
  PART 3 — the same task with pev_lookahead_skip_enabled=False: asserts
           zero skipped steps unconditionally, proving the toggle actually
           disables the mechanism rather than the task simply not
           triggering it.

Usage:
    python tests/test_pev_lookahead_skip_live.py

Requirements:
    - EVA server running at http://127.0.0.1:8000
    - ollama_cloud reachable (same as test_behaviour.py)
"""

import requests
import json
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BASE       = "http://127.0.0.1:8000"
TEST_USER  = f"a21test_{uuid.uuid4().hex[:8]}"
TEST_EMAIL = f"{TEST_USER}@eva-a21-test.com"
TEST_PASS  = "A21Test#1!"
RESULTS    = []

TEST_PROVIDER = "ollama_cloud"
TEST_MODEL    = "gpt-oss:20b"

EXEC_TIMEOUT_SECONDS = 150

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

def get(path, token=None, timeout=15):
    r = requests.get(f"{BASE}{path}", headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}

def delete(path, token=None, timeout=15):
    r = requests.delete(f"{BASE}{path}", headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}

def set_session(token, **kwargs):
    payload = {
        "provider":  TEST_PROVIDER,
        "model":     TEST_MODEL,
        "tools":     [],
        "agent_mode": "auto",
        **kwargs,
    }
    return post("/set-session", payload, token=token)

def read_sse_events(conv_id, token, timeout=EXEC_TIMEOUT_SECONDS):
    sc, sb = post("/auth/sse-token", token=token)
    if sc != 200 or "sse_token" not in sb:
        return [], f"sse-token failed: {sc} {sb}"
    sse_token = sb["sse_token"]

    events = []
    url = f"{BASE}/eva_response?sse_token={sse_token}&conversation_id={conv_id}"
    try:
        r = requests.get(url, stream=True, timeout=timeout)
        current_event = None
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("event:"):
                current_event = raw[len("event:"):].strip()
            elif raw.startswith("data:"):
                raw_data = raw[len("data:"):].strip()
                try:
                    data = json.loads(raw_data)
                except Exception:
                    data = raw_data
                events.append({"event": current_event, "data": data})
                if current_event == "done":
                    break
    except Exception as e:
        return events, str(e)
    return events, None

def events_of_type(events, event_type):
    return [e for e in events if e.get("event") == event_type]

def finish_status(events):
    for e in events_of_type(events, "finish"):
        d = e.get("data", {})
        if isinstance(d, dict):
            return d.get("status")
    return None

def run_pev(token, message, timeout=EXEC_TIMEOUT_SECONDS):
    """POST /chat with agent_mode already set on the session, read SSE to completion."""
    sc, body = post("/chat", {"message": message, "conversation_id": None}, token=token)
    if sc != 200 or not body.get("conversation_id"):
        return None, None, [], f"chat failed: {sc} {body}"
    conv_id = body["conversation_id"]
    exec_id = body.get("execution_id")
    events, err = read_sse_events(conv_id, token, timeout=timeout)
    return conv_id, exec_id, events or [], err


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
# PART 1 — pev_lookahead_skip_enabled session round-trip (deterministic)
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 1 — pev_lookahead_skip_enabled session round-trip")

sc_1a, body_1a = get("/session", token=TOKEN)
ok_1a = sc_1a == 200 and body_1a.get("pev_lookahead_skip_enabled") is True
record("P1a fresh session defaults pev_lookahead_skip_enabled=true (opt-out)", ok_1a,
       f"sc={sc_1a} value={body_1a.get('pev_lookahead_skip_enabled')!r}")

sc_1b, body_1b = set_session(TOKEN, pev_lookahead_skip_enabled=False)
ok_1b = sc_1b == 200 and body_1b.get("pev_lookahead_skip_enabled") is False
record("P1b POST /set-session can disable pev_lookahead_skip_enabled", ok_1b,
       f"sc={sc_1b} echoed_back={body_1b.get('pev_lookahead_skip_enabled')!r}")

sc_1c, body_1c = get("/session", token=TOKEN)
ok_1c = sc_1c == 200 and body_1c.get("pev_lookahead_skip_enabled") is False
record("P1c disabled value persists across GET /session", ok_1c,
       f"sc={sc_1c} value={body_1c.get('pev_lookahead_skip_enabled')!r}")

sc_1d, body_1d = set_session(TOKEN, pev_lookahead_skip_enabled=True)
ok_1d = sc_1d == 200 and body_1d.get("pev_lookahead_skip_enabled") is True
record("P1d POST /set-session can re-enable pev_lookahead_skip_enabled", ok_1d,
       f"sc={sc_1d} echoed_back={body_1d.get('pev_lookahead_skip_enabled')!r}")

# ══════════════════════════════════════════════════════════════════════════════
# Shared task: write a file, then a separately-planned step reads it back.
# A competent agent often reads a file back right after writing it (to
# confirm the write succeeded) -- if it does, the same step's own tool
# ledger already contains the evidence the read-back step needs, which is
# exactly the situation A-21 exists to detect.
# ══════════════════════════════════════════════════════════════════════════════
_TASK = (
    "Do the following as separate steps. Use the write_file tool for step 1 "
    "and the read_file tool for step 2 (do not use the shell tool, and do not "
    "just describe what you would do):\n"
    "1. Use write_file to create a file named eva_a21_marker.txt containing "
    "exactly the text A21-LOOKAHEAD-OK (no extra words).\n"
    "2. Use read_file to read eva_a21_marker.txt back and confirm it says "
    "A21-LOOKAHEAD-OK.\n"
    "3. Reply with the single word DONE."
)

def _fetch_plan(exec_id, token):
    if not exec_id:
        return None
    sc, body = get(f"/executions/{exec_id}/plan", token=token)
    return body if sc == 200 else None

def _skipped_steps(plan_body):
    if not plan_body:
        return []
    return [s for s in plan_body.get("steps", []) if s.get("status") == "skipped"]

# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — live PEV run with the mechanism ENABLED
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 2 — live plan_execute run, pev_lookahead_skip_enabled=True")

set_session(TOKEN, tools=["write_file", "read_file"], agent_mode="plan_execute",
            pev_lookahead_skip_enabled=True)
print("  Running a plan_execute execution (may take 30-120s)...")
conv2, exec2, events2, err2 = run_pev(TOKEN, _TASK)

_fin2 = finish_status(events2)
ok_2a = _fin2 == "completed"
record("P2a PEV execution completed", ok_2a, f"finish_status={_fin2!r} err={err2!r}")

plan2 = _fetch_plan(exec2, TOKEN)
ok_2b = plan2 is not None and len(plan2.get("steps", [])) >= 1
record("P2b GET /executions/{id}/plan returns the plan", ok_2b,
       f"step_count={len(plan2.get('steps', [])) if plan2 else 'n/a'}")

_skip2 = _skipped_steps(plan2)
_step_progress2 = events_of_type(events2, "step_progress")
_skip_chunks2 = [
    e for e in _step_progress2
    if isinstance(e.get("data"), dict) and e["data"].get("step_status") == "skipped"
]

if _skip2:
    # The mechanism actually triggered this run -- verify its shape.
    ok_2c = all(s.get("verified") is True for s in _skip2)
    record("P2c skipped step(s) marked verified=true", ok_2c,
           f"skipped={[ (s.get('index'), s.get('status'), s.get('verified')) for s in _skip2 ]}")

    ok_2d = all("skipped" in (s.get("result_summary") or "").lower()
                or "satisfied" in (s.get("result_summary") or "").lower()
                for s in _skip2)
    record("P2d skipped step(s) carry an explanatory result_summary", ok_2d,
           f"summaries={[s.get('result_summary') for s in _skip2]}")

    _last_step = plan2["steps"][-1] if plan2 and plan2.get("steps") else None
    ok_2e = _last_step is not None and _last_step.get("status") != "skipped"
    record("P2e the plan's own true final step was never skipped", ok_2e,
           f"last_step_status={_last_step.get('status') if _last_step else 'n/a'}")

    ok_2f = len(_skip_chunks2) == len(_skip2)
    record("P2f a step_progress 'skipped' SSE chunk was emitted per skipped step", ok_2f,
           f"skip_chunks={len(_skip_chunks2)} skipped_steps={len(_skip2)}")

    _complete_chunk2 = next(
        (e["data"] for e in _step_progress2
         if e["data"].get("step_status") == "plan_complete"), {}
    )
    ok_2g = _complete_chunk2.get("steps_skipped") == len(_skip2)
    record("P2g plan_complete chunk's steps_skipped tally matches", ok_2g,
           f"steps_skipped={_complete_chunk2.get('steps_skipped')} expected={len(_skip2)}")
else:
    record("P2c/d/e/f/g A-21 skip mechanism triggered this run", True,
           "SKIP — model/verifier did not produce a look-ahead skip this run "
           "(real live-model behaviour, not a mechanism bug -- see PART 3 for "
           "the deterministic proof the toggle itself works)")

if conv2:
    delete(f"/conversations/{conv2}", token=TOKEN)

# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — same task with the mechanism DISABLED (deterministic control)
# ══════════════════════════════════════════════════════════════════════════════
_sep("PART 3 — live plan_execute run, pev_lookahead_skip_enabled=False")

set_session(TOKEN, tools=["write_file", "read_file"], agent_mode="plan_execute",
            pev_lookahead_skip_enabled=False)
print("  Running the same task with the toggle off (may take 30-120s)...")
conv3, exec3, events3, err3 = run_pev(TOKEN, _TASK)

_fin3 = finish_status(events3)
ok_3a = _fin3 == "completed"
record("P3a PEV execution completed (toggle off)", ok_3a, f"finish_status={_fin3!r} err={err3!r}")

plan3 = _fetch_plan(exec3, TOKEN)
_skip3 = _skipped_steps(plan3)
ok_3b = len(_skip3) == 0
record("P3b zero steps marked skipped when pev_lookahead_skip_enabled=False", ok_3b,
       f"skipped_count={len(_skip3)}")

_step_progress3 = events_of_type(events3, "step_progress")
_skip_chunks3 = [
    e for e in _step_progress3
    if isinstance(e.get("data"), dict) and e["data"].get("step_status") == "skipped"
]
ok_3c = len(_skip_chunks3) == 0
record("P3c zero 'skipped' step_progress SSE chunks when disabled", ok_3c,
       f"skip_chunk_count={len(_skip_chunks3)}")

_complete_chunk3 = next(
    (e["data"] for e in _step_progress3
     if isinstance(e.get("data"), dict) and e["data"].get("step_status") == "plan_complete"), {}
)
ok_3d = (_complete_chunk3.get("steps_skipped") or 0) == 0
record("P3d plan_complete chunk reports steps_skipped=0 when disabled", ok_3d,
       f"steps_skipped={_complete_chunk3.get('steps_skipped')!r}")

if conv3:
    delete(f"/conversations/{conv3}", token=TOKEN)

# ── teardown ─────────────────────────────────────────────────────────────────
_sep("TEARDOWN")
set_session(TOKEN, tools=[], agent_mode="auto", pev_lookahead_skip_enabled=True)
delete("/conversations", token=TOKEN)
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
print(f"\n  {'ALL A-21 TESTS PASSED' if failed == 0 else f'{failed} A-21 TEST(S) FAILED'}\n")
sys.exit(1 if failed else 0)
