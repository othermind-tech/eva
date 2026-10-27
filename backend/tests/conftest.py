"""
tests/conftest.py — shared pytest fixtures.

Autouse fixtures here apply to every test in the suite without needing
to be imported or declared in individual test files.
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Test database ────────────────────────────────────────────────────────────
# Must run BEFORE anything imports config.settings (which reads DATABASE_URL once,
# at import time; the default is ./eva.db, the developer's real database).
#
# Several unit tests do a real round trip through AsyncSessionLocal. Before this,
# they read and wrote the developer's own eva.db (and failed with "no such table"
# on a clean checkout, where no eva.db exists). Every pytest run now gets a private
# SQLite file that is built from the migrations (see _migrated_test_database) and
# deleted at exit. To run against another database on purpose (e.g. Postgres), set
# EVA_TEST_DATABASE_URL.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if os.environ.get("EVA_TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["EVA_TEST_DATABASE_URL"]
else:
    _TEST_DB_DIR = tempfile.mkdtemp(prefix="eva_test_db_")
    atexit.register(shutil.rmtree, _TEST_DB_DIR, ignore_errors=True)
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (Path(_TEST_DB_DIR) / "eva_test.db").as_posix()

import pytest
import utils.workspace as ws
import core.execution.gate_markers as gate_markers

# These are standalone manual scripts meant to be run directly against a
# live EVA server (`python tests/test_apis.py`, etc.), not pytest suites --
# they have no real test_* functions for pytest to collect, and each does a
# module-level live-server preflight check that calls sys.exit(1) when no
# server is reachable. Under pytest's assertion-rewrite import hook, that
# module-level SystemExit surfaces as an INTERNALERROR instead of a clean
# collection failure, which can abort an entire `pytest tests/` run. Exclude
# them from automatic collection; direct invocation is unaffected.
collect_ignore = ["test_apis.py", "test_behaviour.py", "test_ollama.py", "test_pii_redaction_live.py", "test_mcp_credential_live.py", "test_background_process_live.py", "test_playwright_mcp_live.py", "test_browser_agent_loop_live.py", "test_pev_lookahead_skip_live.py"]


@pytest.fixture(scope="session", autouse=True)
def _migrated_test_database():
    """
    Build the test database schema once per run with `alembic upgrade head`
    (about 5 s). Using the real migrations, not Base.metadata.create_all(),
    means every run also proves the migrations work from an empty database.
    Run in a subprocess so alembic's logging setup and its own event loop
    cannot interfere with the tests.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_BACKEND_ROOT), env=os.environ.copy(),
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        pytest.exit(
            "Could not build the test database with `alembic upgrade head`:\n"
            + (result.stderr or result.stdout)[-2000:],
            returncode=3,
        )


@pytest.fixture(autouse=True)
def redirect_workspace_root(tmp_path, monkeypatch):
    """
    Redirect WORKSPACE_ROOT to a throwaway temp directory for every test.

    Without this, workspace helpers (get_user_root, get_conversation_dir, etc.)
    write real directories into ~/eva_workspace/ during test runs, leaving
    artefacts like eva_workspace/u1/ on the developer's machine.

    The monkeypatch is scoped to each test and cleaned up automatically.
    """
    fake_root = tmp_path / "eva_workspace"
    fake_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ws, "WORKSPACE_ROOT", str(fake_root))


@pytest.fixture(autouse=True)
def redirect_execution_runtime_dir(tmp_path, monkeypatch):
    """
    Redirect core.execution.gate_markers's EXECUTION_RUNTIME_DIR to a
    throwaway temp directory for every test (B2-1.7/4.3).

    Without this, any test exercising gate_approval()/gate_step_extension()/
    _gate_plan_approval() would write real marker files into the
    developer's actual .runtime/executions/ dev directory.
    """
    fake_runtime_dir = tmp_path / "execution_runtime"
    monkeypatch.setattr(gate_markers, "EXECUTION_RUNTIME_DIR", str(fake_runtime_dir))


@pytest.fixture(autouse=True)
def isolated_credential_store():
    """
    Give every test its own empty in-memory credential store (V6.59 Phase 2).

    Provider API keys are resolved from the OS credential store
    (providers.credentials), never os.environ. Without this, any test touching
    key resolution would read -- and could write -- the developer's real
    Windows Credential Manager entries, making results depend on whichever
    keys happen to be configured on that machine. Tests that need a key
    configured use the `credential_store` fixture below.
    """
    from providers import credentials
    store = credentials.MemoryBackend()
    previous = credentials.set_backend(store)
    try:
        yield store
    finally:
        credentials.set_backend(previous)


@pytest.fixture
def credential_store(isolated_credential_store):
    """The active test credential store; `.set("GROQ_API_KEY", "x")` configures a provider."""
    return isolated_credential_store


@pytest.fixture
def stored_provider_keys(isolated_credential_store):
    """
    Store a placeholder key for every keyed provider in the (in-memory) credential store.

    Since V6.59 Phase 5 every run path starts with the usability gate
    (core/providers/usability.py), which refuses a provider that has no key.
    Tests that exercise the worker / daemon / delegation pipeline with a
    sessions naming e.g. anthropic or groq are testing plumbing, not
    credentials -- a configured provider is their precondition, exactly as in
    production. Apply with @pytest.mark.usefixtures("stored_provider_keys").
    """
    from providers import registry
    for spec in registry.PROVIDERS.values():
        if spec.env_key:
            isolated_credential_store.set(spec.env_key, f"test-key-{spec.name}")
    return isolated_credential_store
