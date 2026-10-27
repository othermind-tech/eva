# tests/seed_data/_shared.py
"""
Shared utilities for all EVA seed scripts.
──────────────────────────────────────────
Provides DB connection, timestamp helpers, UUID generation, and
user-resolution so every domain script stays focused on its own data.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


# ── Colour codes (works on any modern terminal, graceful fallback) ─────────
_RESET  = "\033[0m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"


def ok(msg: str)    -> None: print(f"  {_GREEN}[ok]{_RESET}   {msg}")
def warn(msg: str)  -> None: print(f"  {_YELLOW}[--]{_RESET}   {msg}")
def err(msg: str)   -> None: print(f"  {_RED}[!!]{_RESET}   {msg}")
def info(msg: str)  -> None: print(f"  {_CYAN}[>>]{_RESET}   {msg}")
def head(msg: str)  -> None: print(f"\n{_BOLD}{msg}{_RESET}")
def dim(msg: str)   -> None: print(f"  {_DIM}      {msg}{_RESET}")


# ── DB path resolution ─────────────────────────────────────────────────────

def find_db_path(override: Optional[str] = None) -> Path:
    """
    Return the path to eva.db.
    Priority: --db-path CLI arg → SEED_DB_PATH env var → auto-discover.
    Auto-discovery walks up from this file to find the project root.
    """
    if override:
        p = Path(override)
        if not p.exists():
            raise FileNotFoundError(f"DB not found at supplied path: {p}")
        return p

    env = os.getenv("SEED_DB_PATH")
    if env:
        p = Path(env)
        if not p.exists():
            raise FileNotFoundError(f"SEED_DB_PATH points to missing file: {p}")
        return p

    # Auto: no machine-specific locations are hard-coded. Use SEED_DB_PATH
    # (above) for an explicit DB, otherwise fall through to the walk-up search.
    known: list[Path] = []
    for p in known:
        if p.exists():
            # Prefer a DB that has the full migrated schema (has 'projects' table)
            try:
                import sqlite3 as _sq
                c = _sq.connect(str(p))
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                c.close()
                if "projects" in tables and "subagent_configs" in tables:
                    return p
            except Exception:
                pass

    # Fallback: walk up from this script's location
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent]:
        p = candidate / "eva.db"
        if p.exists():
            return p

    raise FileNotFoundError(
        "Cannot locate eva.db. Pass --db-path or set SEED_DB_PATH env var."
    )


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a synchronous SQLite connection with useful pragmas set."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── ID / timestamp helpers ─────────────────────────────────────────────────

def new_id() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


def ts(
    days_ago: int = 0,
    hours_ago: int = 0,
    minutes_ago: int = 0,
    seconds_ago: int = 0,
) -> str:
    """
    Return an ISO-8601 UTC timestamp string offset from now.

    Example:
        ts(days_ago=3, hours_ago=2)  →  "2026-05-16T08:34:22+00:00"
    """
    dt = datetime.now(timezone.utc) - timedelta(
        days=days_ago,
        hours=hours_ago,
        minutes=minutes_ago,
        seconds=seconds_ago,
    )
    return dt.isoformat()


def jdump(obj: Any) -> Optional[str]:
    """Serialise a Python object to a JSON string, or return None if None."""
    if obj is None:
        return None
    return json.dumps(obj)


# ── Table introspection ────────────────────────────────────────────────────

def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names for a given table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return True if the table exists in the DB."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# ── Seed user config ──────────────────────────────────────────────────────

SEED_USERNAME = "eva-seed"
SEED_EMAIL    = "eva-seed@eva.local"
SEED_PASSWORD = "eva-seed"   # login with eva-seed / eva-seed


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt (same algorithm as the EVA backend)."""
    try:
        import bcrypt  # type: ignore
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
    except ImportError:
        pass
    try:
        from passlib.context import CryptContext
        return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(password)
    except Exception:
        pass
    # Hard-coded fallback: pre-computed bcrypt hash of "eva-seed" (cost 12)
    # Valid for login — regenerated if either library becomes available.
    return "$2b$12$TKRF6bGALsxZB.P1VVfKuuHaERbdFqF4bgqhjWMUzUiz0ZTERnPNa"


# ── User resolution ────────────────────────────────────────────────────────

def get_user_id(conn: sqlite3.Connection) -> str:
    """
    Return the user_id for the dedicated seed user (eva-seed).
    Creates the user if it does not exist yet — no backend startup required.
    Login credentials: eva-seed / eva-seed
    """
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?", (SEED_USERNAME,)
    ).fetchone()

    if row:
        info(f"Using user: {_BOLD}{SEED_USERNAME}{_RESET} (id: {row['id'][:8]}...)")
        return row["id"]

    # User doesn't exist yet — create it
    user_id = new_id()
    pwd_hash = _hash_password(SEED_PASSWORD)
    conn.execute(
        "INSERT INTO users (id, username, email, password_hash, created_at, updated_at) "
        "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
        (user_id, SEED_USERNAME, SEED_EMAIL, pwd_hash),
    )
    conn.commit()
    ok(f"Created seed user '{_BOLD}{SEED_USERNAME}{_RESET}' "
       f"(id: {user_id[:8]}...)  login: {SEED_USERNAME} / {SEED_PASSWORD}")
    return user_id


# ── Row counting helpers ───────────────────────────────────────────────────

def count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    """Return row count, or 0 if the table does not exist."""
    if not table_exists(conn, table):
        return 0
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return conn.execute(sql, params).fetchone()[0]


def already_seeded(conn: sqlite3.Connection, table: str, user_id: str) -> bool:
    """True if the table already has >0 rows for this user_id (skip re-seeding)."""
    if not table_exists(conn, table):
        return False
    # Some tables use user_id, others don't - handle gracefully
    cols = table_columns(conn, table)
    if "user_id" in cols:
        n = count(conn, table, "user_id = ?", (user_id,))
    else:
        n = count(conn, table)
    return n > 0
