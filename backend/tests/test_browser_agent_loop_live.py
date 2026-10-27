#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Browser-in-the-loop agent test (default Playwright MCP + background_process) --
standalone live-server test WITH a real LLM in the loop.

Where tests/test_playwright_mcp_live.py proves the plumbing (seed, enable,
sync, tool calls through MCPRuntime), this proves a real model can drive it.
Given one plain-language request, the agent must debug a broken page:

  1. start a static server for it with the background_process tool,
  2. open it in the browser (playwright__browser_*) and read the REAL runtime
     console error,
  3. fix the page in place, reload, confirm the console is clean,
  4. stop the server.

Design notes (each learned from a real run):
  - The broken page is PRE-SEEDED by this script, not written by the agent. When
    the agent wrote the buggy page itself as step 1, a plan/verify replan re-ran
    that step and silently put the bug back.
  - It is seeded inside the CONVERSATION's own directory. A page under the shared
    workspace/ folder counts as "outside the workspace" for EVA's forced approval
    gate (B2-1.1), which stalled the run.
  - To know that directory the conversation must exist first, so turn 1 is a
    trivial greeting (deliberately NOT a "reply with one word" instruction -- a model can carry that pattern into the real task and answer "READY"); turn 2 is the real task in the same
    conversation.
  - Every claim is checked against independent evidence (trace, file on disk,
    port), not the model's own summary. Non-deterministic by nature.

Usage:
    python tests/test_browser_agent_loop_live.py
    EVA_LIVE_MODEL=kimi-k2.7-code python tests/test_browser_agent_loop_live.py
    EVA_LIVE_PROVIDER=anthropic EVA_LIVE_MODEL=claude-haiku-4-5-20251001 python tests/test_browser_agent_loop_live.py

Requirements:
    - EVA server running at http://127.0.0.1:8000 with this code
    - Run from the backend directory (same eva.db / workspace as the server)
    - The chosen provider configured AND funded (a provider billing error shows up
      as an instant "completed" turn with no tool calls -- this script detects it)
    - Node.js (npx) + Edge/Chrome; port 8123 free
"""

import json
import os
import socket
import sys
import threading
import time
import uuid

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BASE = "http://127.0.0.1:8000"
TEST_PROVIDER = os.environ.get("EVA_LIVE_PROVIDER", "ollama_cloud")
TEST_MODEL = os.environ.get("EVA_LIVE_MODEL", "gpt-oss:120b")
TEST_USER = f"pwagent_{uuid.uuid4().hex[:8]}"
TEST_EMAIL = f"{TEST_USER}@eva-pwagent-live-test.com"
TEST_PASS = "PwAgentLiveTest#1!"
# Signup is rate-limited per address. To reuse an existing account instead (log in, and re-seed the
# default Playwright MCP config if a previous run's teardown removed it), set both of these.
REUSE_USER = os.environ.get("EVA_LIVE_USER")
REUSE_PASS = os.environ.get("EVA_LIVE_PASS")
PORT = int(os.environ.get("EVA_LIVE_PORT", "8123"))  # a different port per run lets two runs go in parallel
EXEC_TIMEOUT_SECONDS = int(os.environ.get("EVA_LIVE_TIMEOUT", "600"))
TERMINAL = {"completed", "failed", "cancelled", "aborted"}
RESULTS = []

BUGGY_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Todo probe</title>
  <link rel="icon" href="data:,">
</head>
<body>
  <h1>Todo probe</h1>
  <button id="add">Add</button>
  <ul id="list"></ul>
  <script>
    document.getElementById('add').addEventListener('click', function () { renderTodos(); });
    renderTodos();
  </script>
</body>
</html>
"""

TASK_PROMPT = f"""There is a small, broken web app in the folder todo_probe in your workspace (the page is todo_probe/index.html). Do this yourself using your tools:

1. Start a static web server for the todo_probe folder on 127.0.0.1 port {PORT} using the background_process tool (for example: python -m http.server {PORT} --bind 127.0.0.1, with todo_probe as its working directory).
2. Using the browser tools (the playwright ones), open http://127.0.0.1:{PORT}/ and read the browser console. Tell me the exact console error message the browser reported.
3. Fix todo_probe/index.html IN PLACE (edit that file, do not create a new copy elsewhere), reload the page in the browser, and confirm the console is now clean.
4. Stop the web server with the background_process tool.

Finish with a short summary of the error you saw and what you changed."""

# Skill DISCOVERY and skill CONTENT are different questions. By default the run only sees the skill in
# the skills menu (does the model open it on its own?). With EVA_LIVE_USE_SKILL=1 the user "asks for"
# it by name, which isolates whether the skill's content helps once it is read.
if os.environ.get("EVA_LIVE_USE_SKILL"):
    TASK_PROMPT = "Use the playwright-browser-testing skill for this task.\n\n" + TASK_PROMPT


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
        print(f"         {detail[:260]}")


def post(path, payload=None, token=None, timeout=30):
    r = requests.post(f"{BASE}{path}", json=payload, headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


def put(path, payload=None, token=None, timeout=30):
    r = requests.put(f"{BASE}{path}", json=payload, headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


def get(path, token=None, timeout=20):
    r = requests.get(f"{BASE}{path}", headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


def delete(path, token=None, timeout=20):
    r = requests.delete(f"{BASE}{path}", headers=_hdr(token), timeout=timeout)
    return r.status_code, r.json() if r.content else {}


def port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_turn(token, message, conversation_id=None, deadline=EXEC_TIMEOUT_SECONDS, label="turn"):
    """
    POST /chat and drive it to a terminal state (or the deadline). Answers the
    ReAct step-limit gate with "continue" the way the UI does: the token only
    travels on the SSE stream, so listen there -- reconnecting if the stream
    drops (a new connection replays the buffer; answered tokens are deduped).
    Returns (exec_id, conv_id, status, continues, elapsed_seconds, events) where `events` is
    every (event name, data) pair seen on the SSE stream -- the ONLY place tool calls and
    tool results are visible once a run has completed (GET /executions/{id}/trace only
    returns saved text messages for a finished run; tool activity is live-buffer only).
    """
    sc, chat = post("/chat", {"message": message, "conversation_id": conversation_id}, token=token)
    if sc not in (200, 202) or not chat.get("execution_id"):
        print(f"  FATAL: /chat failed ({sc}): {chat}")
        sys.exit(1)
    exec_id, conv_id = chat["execution_id"], chat["conversation_id"]
    print(f"  [{label}] execution_mode={chat.get('execution_mode')} execution_id={exec_id}")

    events, lock, stop = [], threading.Lock(), threading.Event()

    def _listen():
        while not stop.is_set():
            try:
                sc_t, tok = post("/auth/sse-token", token=token)
                if sc_t != 200:
                    time.sleep(2)
                    continue
                with requests.get(f"{BASE}/eva_response",
                                  params={"sse_token": tok["sse_token"], "conversation_id": conv_id},
                                  stream=True, timeout=deadline + 30) as resp:
                    current = None
                    for raw in resp.iter_lines(decode_unicode=True):
                        if stop.is_set():
                            return
                        if not raw:
                            continue
                        if raw.startswith("event:"):
                            current = raw[len("event:"):].strip()
                        elif raw.startswith("data:"):
                            try:
                                data = json.loads(raw[len("data:"):].strip())
                            except Exception:
                                data = {}
                            with lock:
                                events.append((current, data))
            except Exception:
                pass
            time.sleep(1)

    threading.Thread(target=_listen, daemon=True).start()

    answered, continues, status = set(), 0, "running"
    t0, last_print = time.time(), 0
    try:
        while time.time() - t0 < deadline:
            sc, st = get(f"/executions/{exec_id}/status", token=token)
            status = st.get("status", "?") if sc == 200 else f"http-{sc}"
            if time.time() - last_print > 30:
                print(f"  ... [{label}] {int(time.time() - t0)}s status={status} continues={continues}")
                last_print = time.time()
            if status in TERMINAL:
                break
            with lock:
                pending = [d for e, d in events
                           if e == "step_limit_reached" and d.get("step_extension_token") not in answered]
            for d in pending:
                tk = d["step_extension_token"]
                answered.add(tk)
                sc_x, _ = post(f"/executions/{exec_id}/extend",
                               {"decision": "continue", "step_extension_token": tk}, token=token)
                continues += 1
                print(f"  [{label}] step limit reached ({d.get('steps_used')}/{d.get('max_steps')}) -> continue: sc={sc_x}")
            if status.startswith("awaiting") and status != "awaiting_step_extension":
                print(f"  NOTE: [{label}] paused with status={status} (unexpected in auto mode)")
                break
            time.sleep(4)
    finally:
        # Give the listener a moment to drain the tail of the stream (the final tool results
        # and the "done" event) before it is told to stop.
        drain_until = time.time() + 8
        while time.time() < drain_until:
            with lock:
                if any(e == "done" for e, _ in events):
                    break
            time.sleep(0.5)
        stop.set()
    with lock:
        seen = list(events)
    return exec_id, conv_id, status, continues, int(time.time() - t0), seen


# ── preflight ────────────────────────────────────────────────────────────────
try:
    if requests.get(f"{BASE}/health", timeout=5).status_code != 200:
        print(f"FATAL: EVA server at {BASE} is not healthy.")
        sys.exit(1)
except Exception as exc:
    print(f"FATAL: could not reach EVA server at {BASE} ({exc}). Start it first.")
    sys.exit(1)

if port_open(PORT):
    print(f"FATAL: port {PORT} is already in use -- free it first.")
    sys.exit(1)

_sep(f"SETUP -- isolated user, model={TEST_PROVIDER}/{TEST_MODEL}, Playwright enabled (headless)")
if REUSE_USER and REUSE_PASS:
    _r = requests.post(f"{BASE}/auth/login", data={"username": REUSE_USER, "password": REUSE_PASS}, timeout=30)
    sc, body = _r.status_code, (_r.json() if _r.content else {})
    if sc != 200 or "access_token" not in body:
        print(f"  FATAL: login as {REUSE_USER} failed ({sc}): {body}")
        sys.exit(1)
else:
    sc, body = post("/auth/signup", {"username": TEST_USER, "email": TEST_EMAIL, "password": TEST_PASS})
    if sc not in (200, 201) or "access_token" not in body:
        print(f"  FATAL: signup failed ({sc}): {body}")
        sys.exit(1)
TOKEN = body["access_token"]
USER_ID = get("/auth/me", token=TOKEN)[1]["user_id"]

sc, configs = get("/mcp/", token=TOKEN)
pw = next((c for c in configs if c["name"] == "playwright"), None) if sc == 200 else None
if REUSE_USER and not pw:
    post("/mcp/seed-defaults", token=TOKEN)
    sc, configs = get("/mcp/", token=TOKEN)
    pw = next((c for c in configs if c["name"] == "playwright"), None) if sc == 200 else None
if not pw:
    print("  FATAL: signup did not seed the playwright MCP config.")
    sys.exit(1)
sc, upd = put(f"/mcp/{pw['id']}", {"args": pw["args"] + (["--headless"] if "--headless" not in pw["args"] else []), "enabled": True}, token=TOKEN, timeout=180)
if sc != 200 or upd.get("sync_error") or not upd.get("tool_count"):
    print(f"  FATAL: could not enable playwright: sc={sc} {upd}")
    sys.exit(1)
print(f"  Playwright enabled: {upd['tool_count']} tools")

sc, sess = post("/set-session", {
    "provider": TEST_PROVIDER, "model": TEST_MODEL, "tools": [], "agent_mode": "auto",
    "max_step_extensions": 6,
}, token=TOKEN)
if sc != 200:
    print(f"  FATAL: set-session failed ({sc}): {sess}")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# TURN 1 -- create the conversation cheaply, then seed the broken page into it
# ══════════════════════════════════════════════════════════════════════════════
_sep("TURN 1 -- create the conversation (trivial message)")
ex1, conv_id, st1, _, _, _ = run_turn(TOKEN, "Hi! In a moment I will ask you to debug a small web app. For now, just say hello.", label="setup", deadline=120)
sc, tr1 = get(f"/executions/{ex1}/trace", token=TOKEN, timeout=60)
turn1_text = " ".join(s.get("content", "") for s in (tr1.get("trace", []) if sc == 200 else []) if s.get("type") == "text")
if st1 != "completed" or "wasn't able to generate a response" in turn1_text.lower() or not turn1_text.strip():
    print(f"  FATAL: the model did not answer a trivial message (status={st1}, reply={turn1_text[:160]!r}).")
    print("         This is almost always the provider (out of credit, bad key, unavailable model) -- check")
    print("         logs/errors.log for the provider's own error, or pick another model with EVA_LIVE_MODEL.")
    delete(f"/mcp/{pw['id']}", token=TOKEN)
    sys.exit(2)

APP_DIR = os.path.join(os.path.expanduser("~"), "eva_workspace", USER_ID, "conversations", conv_id, "todo_probe")
os.makedirs(APP_DIR, exist_ok=True)
INDEX_PATH = os.path.join(APP_DIR, "index.html")
with open(INDEX_PATH, "w", encoding="utf-8") as _fh:
    _fh.write(BUGGY_HTML)
print(f"  seeded broken page: {INDEX_PATH}")

# ══════════════════════════════════════════════════════════════════════════════
# TURN 2 -- the real task
# ══════════════════════════════════════════════════════════════════════════════
_sep("TURN 2 -- debug the page (real model, one request)")
exec_id, _, status, continues, elapsed, sse_seen = run_turn(TOKEN, TASK_PROMPT, conversation_id=conv_id, label="debug")
print(f"  finished: status={status} after {elapsed}s, step-limit continues={continues}")

# Tool activity comes from the SSE stream (see run_turn); the saved-message trace supplies the text.
sc, tr = get(f"/executions/{exec_id}/trace", token=TOKEN, timeout=60)
trace = tr.get("trace", []) if sc == 200 else []
texts = [s for s in trace if s.get("type") == "text"]

_seen_calls, calls = set(), []
_seen_results, results = set(), []
for _name, _data in sse_seen:
    if not isinstance(_data, dict):
        continue
    if _name == "tool_call" and _data.get("call_id") not in _seen_calls:
        _seen_calls.add(_data.get("call_id"))
        calls.append({"tool_name": _data.get("tool_name"),
                      "content": _data.get("arguments", _data.get("args", {}))})
    elif _name == "tool_result" and _data.get("call_id") not in _seen_results:
        _seen_results.add(_data.get("call_id"))
        results.append({"tool_name": _data.get("tool_name"), "content": _data.get("result", _data)})
tool_names = [c.get("tool_name") for c in calls]

_sep("TRACE -- tool calls in order")
for i, c in enumerate(calls, 1):
    args = json.dumps(c.get("content"), ensure_ascii=False, default=str)
    print(f"  {i:>2}. {c.get('tool_name')}  {args[:140]}")
if not calls:
    print("  (no tool calls recorded)")


def _errored(step):
    c = step.get("content")
    if not isinstance(c, dict):
        return False
    inner = c.get("result")
    return c.get("ok") is False or bool(c.get("error")) or (isinstance(inner, dict) and "error" in inner)


n_err = sum(1 for r in results if _errored(r))
n_val = sum(1 for r in results if "validation error" in json.dumps(r.get("content"), default=str).lower())
n_describe = sum(1 for n in tool_names if n == "describe_tool")
print(f"\n  STATS: {len(calls)} tool calls | {n_err} errored results | {n_val} schema-validation errors | {n_describe} describe_tool")

# ══════════════════════════════════════════════════════════════════════════════
# CHECKS -- independent evidence, not the model's own summary
# ══════════════════════════════════════════════════════════════════════════════
_sep("CHECKS")


def blob(step):
    return json.dumps(step.get("content"), ensure_ascii=False, default=str)


record("C1 execution reached a terminal 'completed' state", status == "completed", f"status={status}")

record("C2 agent started the server via background_process (action=start)",
       any(c.get("tool_name") == "background_process" and '"start"' in blob(c) for c in calls),
       f"tools={sorted(set(n for n in tool_names if n))}")

def _loads_page(n, c):
    """A browser call that loads the page under test: navigate, or a NEW tab opened on the URL
    (playwright__browser_tabs {"action":"new","url":...} -- documented in the skill, and what
    gpt-oss:120b uses; C3/C5 originally counted only browser_navigate and failed a run that had
    read the real error through a tab)."""
    if str(PORT) not in blob(c):
        return False
    if n == "playwright__browser_navigate":
        return True
    return n == "playwright__browser_tabs" and '"new"' in blob(c) and "http" in blob(c)


record("C3 agent opened the page in the browser (browser_navigate, or a new tab on the URL)",
       any(_loads_page(n, c) for n, c in zip(tool_names, calls)))

console_like = [c for n, c in zip(tool_names, calls)
                if n in ("playwright__browser_console_messages", "playwright__browser_snapshot",
                         "playwright__browser_navigate", "playwright__browser_evaluate")]
saw_real_error = any("renderTodos" in blob(r) and "not defined" in blob(r).lower() for r in results)
record("C4 a browser tool result actually contained the REAL runtime error ('renderTodos is not defined')",
       saw_real_error and bool(console_like))

nav_count = sum(1 for n, c in zip(tool_names, calls) if _loads_page(n, c))
# The reload must come AFTER the last edit of the page (write_file / edit_file), not merely exist.
_edit_positions = [i for i, n in enumerate(tool_names) if n in ("edit_file", "write_file")]
_last_edit = max(_edit_positions) if _edit_positions else -1
_loads_after_edit = sum(1 for i, (n, c) in enumerate(zip(tool_names, calls)) if i > _last_edit and _loads_page(n, c))
reload_evidence = _last_edit >= 0 and (
    _loads_after_edit >= 1
    or any(i > _last_edit and n in ("playwright__browser_evaluate", "playwright__browser_click")
           for i, n in enumerate(tool_names)))
record("C5 agent re-loaded the page AFTER its last edit",
       reload_evidence, f"page_loads={nav_count} loads_after_last_edit={_loads_after_edit}")

html = open(INDEX_PATH, encoding="utf-8", errors="replace").read() if os.path.exists(INDEX_PATH) else ""
defined = ("function renderTodos" in html) or ("renderTodos =" in html) or ("renderTodos=" in html)
still_called = "renderTodos(" in html
record("C6 the seeded index.html was actually fixed in place (renderTodos defined, or the bad call removed)",
       bool(html) and (defined or not still_called), f"exists={bool(html)} defined={defined} still_called={still_called}")

sc, running = get("/executions/running", token=TOKEN)
leftover = [r for r in running if r.get("execution_mode") == "background_process"] if sc == 200 else []
record("C7 agent stopped the server (no running background process, port closed)",
       not leftover and not port_open(PORT),
       f"tracked_running={len(leftover)} port_open={port_open(PORT)}")

# Guard for the live finding of 2026-09-21: EVA forwarded every OMITTED optional argument to the MCP
# server as an explicit null, and Playwright rejects null ("expected boolean, received null"), so
# browser_console_messages could not be called without inventing values for `all` and `filename`.
null_rejects = [r for r in results if "received null" in json.dumps(r.get("content"), default=str)]
record("C8 no tool result was rejected for an argument arriving as null (omitted optionals must be omitted)",
       not null_rejects, f"null_rejections={len(null_rejects)}")

# ── cleanup (also exercises the Phase 3 stop endpoint if the agent left one) ──
_sep("TEARDOWN")
if status not in TERMINAL:
    sc_c, _ = post(f"/executions/{exec_id}/cancel", token=TOKEN)
    print(f"  execution still '{status}' at the deadline -> cancelled it: sc={sc_c}")
for r in leftover:
    sc, b = post(f"/processes/{r['execution_id']}/stop", token=TOKEN)
    print(f"  stopped leftover process {r['execution_id']}: sc={sc} {b}")
delete(f"/mcp/{pw['id']}", token=TOKEN)

_sep("AGENT'S FINAL MESSAGE")
# The trace read right after completion can hold only the tail of the streamed answer, so read the
# saved conversation instead and show the last non-empty assistant message.
sc_m, msgs = get(f"/conversations/{conv_id}/messages", token=TOKEN, timeout=60)
final_msg = ""
if sc_m == 200:
    for m in reversed(msgs if isinstance(msgs, list) else msgs.get("messages", [])):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            final_msg = m["content"]
            break
print("  " + (final_msg or "(none)").replace("\n", "\n  ")[:2500])

# Not a counted check. Known open gap A-14: in plan/execute mode each step is given only the original
# request plus its own step text, never the earlier steps' results, so the closing "summary" step can
# only guess. This line flags a final answer that cites a browser error other than the real one.
import re as _re
_cited = _re.findall(r"(?:Reference|Syntax|Type)Error:[^\n`*]*", final_msg or "")
_foreign = [c for c in _cited if "renderTodos" not in c]
if _foreign:
    print(f"\n  NOTE (known gap A-14, not counted): the final answer cites an error the browser never reported: {_foreign[:3]}")

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
print(f"\n  {'AGENT BROWSER LOOP PASSED' if failed == 0 else f'{failed} CHECK(S) FAILED'}\n")
sys.exit(1 if failed else 0)
