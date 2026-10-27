"""Post-build smoke test of the PACKAGED eva-backend.exe (run by build-backend.ps1, or by hand).

Usage: python smoke_test_packaged_backend.py <path to eva-backend.exe> [backend source folder]

What the packaged backend must contain is worked out from the source tree each run (tools by running the
real tool loader, default agents/skills/MCPs by counting the shipped files, the migration head from Alembic),
so adding a tool, agent or migration never needs a change here. The source folder defaults to the backend
folder this script lives in.

This is a test tool, not part of the app: it lives in backend/scripts and is never packaged. It runs the exe
against a throw-away database, vector store and workspace in a temp folder and never touches real user data.

Starts the exe against a throw-away database and log folder (port 8000, like Electron does), then checks:
health, migrations, signup/login, tools, default agents, skills, default MCPs, daemons, the provider
capability data, a real PDF through ingest + search (pypdf + the bundled ONNX model + ChromaDB), a
password-protected PDF (must fail with a clear error), and that the logs contain no missing-module errors.
Exit code 0 only when every check passes. Needs `requests` and `pypdf` (both in the build environment).
"""
import io
import json
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import os
from pathlib import Path

import requests

if len(sys.argv) < 2:
    sys.exit(__doc__)
EXE = Path(sys.argv[1])
SRC = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="eva-smoke-"))
BASE = "http://127.0.0.1:8000"

_PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from core.tools.loader import load_all_tools
from core.tools.registry import DefaultToolRegistry, CustomToolRegistry
load_all_tools()
src = Path(sys.argv[1])
cfg = Config(str(src / "alembic.ini"))
cfg.set_main_option("script_location", str(src / "alembic"))
print("PROBE" + json.dumps({
    "tools": len(DefaultToolRegistry.all()) + len(CustomToolRegistry.all()),
    "agents": len(list((src / "default_agents").glob("*.yaml"))),
    "skills": len([d for d in (src / "skills").iterdir() if (d / "SKILL.md").is_file()]),
    "mcps": len(list((src / "default_mcps").glob("*.yaml"))),
    "head": ScriptDirectory.from_config(cfg).get_current_head(),
}))
"""


def expected_from_source():
    """What the packaged backend should contain, taken from the source tree (isolated config/log dirs)."""
    probe_env = dict(os.environ, EVA_CONFIG_DIR=str(T / "probe"), EVA_LOG_DIR=str(T / "probe"),
                     DATABASE_URL="sqlite+aiosqlite:///" + str(T / "probe.db").replace("\\", "/"),
                     CHROMA_PATH=str(T / "probe_chroma"), EVA_WORKSPACE_ROOT=str(T / "probe_workspace"))
    out = subprocess.run([sys.executable, "-c", _PROBE, str(SRC)], cwd=SRC, env=probe_env,
                         capture_output=True, text=True, timeout=300)
    for line in out.stdout.splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[5:])
    sys.exit("Could not work out the expected contents from the source tree:\n" + out.stderr[-1500:])


with socket.socket() as _s:
    if _s.connect_ex(("127.0.0.1", 8000)) == 0:
        sys.exit("Port 8000 is already in use - stop the running EVA backend and try again.")

env = dict(
    os.environ,
    EVA_CONFIG_DIR=str(T),
    EVA_LOG_DIR=str(T),
    DATABASE_URL="sqlite+aiosqlite:///" + str(T / "eva.db").replace("\\", "/"),
    CORS_ORIGINS="http://127.0.0.1:3000",
    # The vector store and per-user workspace default to ~/eva_workspace; keep them in the temp folder too.
    CHROMA_PATH=str(T / "chroma_db"),
    EVA_WORKSPACE_ROOT=str(T / "workspace"),
)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def make_pdf(text):
    stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def locked_pdf():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(make_pdf("secret text"))).pages[0])
    writer.encrypt(user_password="hunter2", algorithm="AES-256")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def wait_for_job(headers, job_id, tries):
    status = {}
    for _ in range(tries):
        status = requests.get(f"{BASE}/rag/ingest/{job_id}/status", headers=headers, timeout=30).json()
        if status.get("status") not in ("processing", "queued", "pending"):
            break
        time.sleep(2)
    return status


EXPECTED = expected_from_source()
print("expected from source:", EXPECTED, flush=True)

proc = subprocess.Popen(
    [str(EXE)], cwd=EXE.parent, env=env,
    stdout=open(T / "out.txt", "wb"), stderr=open(T / "err.txt", "wb"),
)
try:
    started = time.time()
    up = False
    while time.time() - started < 180 and proc.poll() is None:
        try:
            if requests.get(BASE + "/health", timeout=3).status_code == 200:
                up = True
                break
        except requests.RequestException:
            time.sleep(2)
    check("health 200", up, f"{time.time() - started:.1f}s")
    if not up:
        raise SystemExit(1)

    cred = dict(username="smoke_user", email="smoke@example.com", password="Smoke-Test-Pass-123!")
    check("signup", requests.post(BASE + "/auth/signup", json=cred, timeout=60).status_code == 200)
    login = requests.post(BASE + "/auth/login", data=dict(username=cred["username"], password=cred["password"]), timeout=60)
    check("login", login.status_code == 200)
    H = {"Authorization": "Bearer " + login.json()["access_token"]}

    def get(path):
        return requests.get(BASE + path, headers=H, timeout=60)

    tools = get("/tools").json()
    check(f"tools == {EXPECTED['tools']} (as loaded from source)", len(tools) == EXPECTED["tools"], str(len(tools)))

    agents = get("/agents")
    count = len(agents.json()) if agents.status_code == 200 else 0
    check(f"default agents == {EXPECTED['agents']}", count == EXPECTED["agents"], f"{count} (HTTP {agents.status_code})")

    skills = get("/skills")
    names = [s.get("name") for s in skills.json()] if skills.status_code == 200 else []
    check(f"skills == {EXPECTED['skills']}", len(names) == EXPECTED["skills"], f"{len(names)} (HTTP {skills.status_code})")
    if names:
        files = get(f"/skills/{names[0]}/files")
        check("skill files readable", files.status_code == 200 and len(str(files.json())) > 50, f"{names[0]}: {files.status_code}")

    mcp = get("/mcp/")
    check(f"default MCP configs == {EXPECTED['mcps']}", mcp.status_code == 200 and len(mcp.json()) == EXPECTED["mcps"],
          str([m.get("name") for m in mcp.json()]) if mcp.status_code == 200 else str(mcp.status_code))

    daemons = get("/daemons")
    check("daemons endpoint works", daemons.status_code == 200, str(daemons.status_code))

    caps = get("/providers/capabilities")
    check("provider capability data", caps.status_code == 200 and len(caps.json()) > 0,
          str(len(caps.json())) if caps.status_code == 200 else str(caps.status_code))

    # A real PDF: pypdf text extraction, then the bundled ONNX model + ChromaDB (this is what crashed when the
    # bundle shipped an old Visual C++ runtime).
    marker = "zebracornflower" + uuid.uuid4().hex[:6]
    resp = requests.post(BASE + "/rag/ingest", headers=H, timeout=60, files={
        "file": ("smoke.pdf", make_pdf(f"EVA packaged smoke test {marker} apples and oranges"), "application/pdf")})
    check("pdf ingest accepted", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        status = wait_for_job(H, resp.json()["job_id"], 60)
        check("pdf ingest completed with chunks",
              status.get("status") in ("completed", "done", "complete") and status.get("total_chunks", 0) >= 1, str(status))
        found = requests.post(BASE + "/rag/search", headers=H, timeout=60,
                              data={"query": f"apples oranges {marker}", "top_k": 3}).json()
        hit = any(marker in (item.get("text") or "") for item in found.get("results", []))
        check("search finds the PDF text", hit, str(found)[:160])

    resp = requests.post(BASE + "/rag/ingest", headers=H, timeout=60,
                         files={"file": ("locked.pdf", locked_pdf(), "application/pdf")})
    if resp.status_code == 200:
        status = wait_for_job(H, resp.json()["job_id"], 30)
        check("locked PDF fails with a clear error",
              status.get("status") in ("failed", "error") and bool(status.get("error")), str(status)[:200])
    else:
        check("locked PDF rejected up front", resp.status_code in (400, 415, 422), f"{resp.status_code} {resp.text[:120]}")

    versions = sqlite3.connect(T / "eva.db").execute("select version_num from alembic_version").fetchall()
    check(f"migrations at head ({EXPECTED['head']})", versions == [(EXPECTED["head"],)], str(versions))
finally:
    exited = proc.poll()
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    time.sleep(2)

if exited is not None:
    check("backend stayed running", False, f"exit code {exited}")

bad = []
for log in T.rglob("*"):
    if log.is_file() and log.suffix in (".txt", ".log"):
        for line in log.read_text(errors="ignore").splitlines():
            if re.search(r"ModuleNotFoundError|ImportError|No module named", line):
                bad.append(f"{log.name}: {line.strip()[:160]}")
check("no missing-module errors in logs", not bad, "; ".join(sorted(set(bad))[:6]))
print(f"\n{sum(results)}/{len(results)} checks passed.  logs: {T}")
sys.exit(0 if all(results) else 1)
