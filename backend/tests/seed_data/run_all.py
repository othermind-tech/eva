#!/usr/bin/env python3
# tests/seed_data/run_all.py
"""
EVA Database Seed - Master Orchestrator
════════════════════════════════════════
Runs all five seed scripts in dependency order, reports per-script results,
and prints a final summary table.

Usage
─────
  # From project root (auto-discovers eva.db):
  python tests/seed_data/run_all.py

  # Explicit DB path:
  python tests/seed_data/run_all.py --db-path /path/to/eva.db

  # Reset: drop all seeded rows then re-seed (CAUTION - destructive):
  python tests/seed_data/run_all.py --reset

  # Dry-run: print what would be done without touching the DB:
  python tests/seed_data/run_all.py --dry-run

Dependency order
────────────────
  01_users_projects    ← no dependencies
  02_conversations     ← references projects
  03_executions        ← references conversations
  04_agents_daemons    ← references projects
  05_memory_mcp        ← no hard dependencies
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path
from typing import Any

# ── ensure seed_data/ is on sys.path so sub-modules can import _shared ───────
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _shared import (
    connect, find_db_path, get_user_id,
    head, ok, warn, err, info, dim,
    _BOLD, _RESET, _GREEN, _RED, _YELLOW, _CYAN,
)

# ─────────────────────────────────────────────────────────────────────────────
# Module registry - (module_name, human_label)
# ─────────────────────────────────────────────────────────────────────────────

SCRIPTS = [
    ("seed_01_users_projects",  "User Preferences & Projects"),
    ("seed_02_conversations",   "Conversations, Messages & Usage"),
    ("seed_03_executions",      "Execution Metrics & Plans"),
    ("seed_04_agents_daemons",  "Custom Subagents & Daemons"),
    ("seed_05_memory_mcp",      "Memory Facts & MCP Configs"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Reset helpers
# ─────────────────────────────────────────────────────────────────────────────

RESET_ORDER = [
    # Delete leaf tables first to respect FK constraints
    "conversation_attachments",
    "conversation_summaries",
    "tool_calls",
    "messages",
    "conversations",
    "execution_plans",
    "execution_metrics",
    "daemon_webhooks",
    "daemon_configs",
    "subagent_configs",
    "agent_memory_entries",
    "user_memory_facts",
    "mcp_tool_meta",
    "mcp_resource_meta",
    "mcp_prompt_meta",
    "mcp_configs",
    "user_preferences",
    "projects",
]


def reset_seed_data(conn, user_id: str, dry_run: bool = False) -> None:
    """Delete all rows belonging to this user from seed-managed tables."""
    head("RESET - removing existing seed data")
    from _shared import table_exists

    # Temporarily disable FK enforcement so we can delete in any order
    conn.execute("PRAGMA foreign_keys=OFF")

    # Tables that need subquery deletion (no direct user_id column)
    # Map: table -> SQL to delete rows belonging to this user
    SUBQUERY_DELETES = {
        "tool_calls": (
            "DELETE FROM tool_calls WHERE message_id IN "
            "(SELECT m.id FROM messages m JOIN conversations c ON m.conversation_id=c.id WHERE c.user_id=?)"
        ),
        "messages": (
            "DELETE FROM messages WHERE conversation_id IN "
            "(SELECT id FROM conversations WHERE user_id=?)"
        ),
        "mcp_tool_meta": (
            "DELETE FROM mcp_tool_meta WHERE config_id IN "
            "(SELECT id FROM mcp_configs WHERE user_id=?)"
        ),
        "mcp_resource_meta": (
            "DELETE FROM mcp_resource_meta WHERE config_id IN "
            "(SELECT id FROM mcp_configs WHERE user_id=?)"
        ),
        "mcp_prompt_meta": (
            "DELETE FROM mcp_prompt_meta WHERE config_id IN "
            "(SELECT id FROM mcp_configs WHERE user_id=?)"
        ),
    }

    for table in RESET_ORDER:
        if not table_exists(conn, table):
            dim(f"skip {table} (not found)")
            continue

        if table in SUBQUERY_DELETES:
            sql = SUBQUERY_DELETES[table]
            if not dry_run:
                conn.execute(sql, (user_id,))
                conn.commit()
                ok(f"cleared {table} (via subquery)")
            else:
                dim(f"[DRY-RUN] would clear {table}")
        else:
            sql = f"DELETE FROM {table} WHERE user_id = ?"
            if not dry_run:
                conn.execute(sql, (user_id,))
                conn.commit()
                ok(f"cleared {table}")
            else:
                n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (user_id,)).fetchone()[0]
                dim(f"[DRY-RUN] would delete {n:4d} rows from {table}")

    conn.execute("PRAGMA foreign_keys=ON")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_script(module_name: str, db_path: str | None, dry_run: bool) -> dict[str, Any]:
    """Import a seed module and call its run() function. Returns result dict."""
    mod = importlib.import_module(module_name)
    if dry_run:
        # In dry-run mode we still call run() but with a read-only connection
        # We simply report what the module would do without mutating
        return {"dry_run": True}
    return mod.run(db_path)


def summarise(results: list[tuple[str, str, dict, float, str | None]]) -> None:
    """Print a formatted summary table."""
    print(f"\n{'=' * 70}")
    print(f"{_BOLD}  SEED SUMMARY{_RESET}")
    print(f"{'=' * 70}")
    print(f"  {'Script':<40}  {'Status':<10}  {'Time':>6}  {'Rows'}")
    print(f"  {'-' * 38}  {'-' * 8}  {'-' * 6}  {'-' * 20}")

    total_rows = 0
    for (module, label, result, elapsed, error) in results:
        if error:
            status = f"{_RED}FAILED{_RESET}"
            row_str = "-"
        else:
            status = f"{_GREEN}OK{_RESET}"
            rows = sum(v for v in result.values() if isinstance(v, int))
            total_rows += rows
            row_str = f"{rows} rows" if rows else f"{_YELLOW}skipped{_RESET}"
        print(f"  {label:<40}  {status:<20}  {elapsed:5.1f}s  {row_str}")
        if error:
            print(f"    {_RED}{error}{_RESET}")

    print(f"{'-' * 70}")
    print(f"  Total new rows inserted: {_BOLD}{total_rows}{_RESET}")
    print(f"{'=' * 70}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="EVA seed data master runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run_all.py\n"
            "  python run_all.py --db-path ../../eva.db\n"
            "  python run_all.py --reset\n"
            "  python run_all.py --dry-run\n"
            "  python run_all.py --only 02 04\n"
        ),
    )
    parser.add_argument("--db-path", default=None,
                        help="Path to eva.db (auto-discovered if omitted)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete existing seed data before re-seeding (per-user)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without modifying the DB")
    parser.add_argument("--only", nargs="+", metavar="N",
                        help="Run only specific scripts by number prefix, e.g. --only 02 04")
    args = parser.parse_args()

    # ── Resolve DB ──────────────────────────────────────────────────────────
    try:
        db_path = find_db_path(args.db_path)
    except FileNotFoundError as e:
        err(str(e))
        return 1

    print(f"\n{_BOLD}EVA Database Seed{_RESET}")
    print(f"  DB path : {_CYAN}{db_path}{_RESET}")
    print(f"  Mode    : {'DRY RUN - no changes will be made' if args.dry_run else 'LIVE'}")

    # ── Resolve user ─────────────────────────────────────────────────────────
    try:
        conn = connect(db_path)
        user_id = get_user_id(conn)
        conn.close()
    except RuntimeError as e:
        err(str(e))
        return 1

    # ── Optional reset ───────────────────────────────────────────────────────
    if args.reset and not args.dry_run:
        print(f"\n  {_YELLOW}WARNING: --reset will delete all seeded rows for user {user_id[:8]}...{_RESET}")
        confirm = input("  Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            print("  Reset cancelled.")
            return 0
        conn = connect(db_path)
        reset_seed_data(conn, user_id, dry_run=False)
        conn.close()

    # ── Filter scripts if --only ──────────────────────────────────────────────
    scripts_to_run = SCRIPTS
    if args.only:
        prefixes = {n.zfill(2) for n in args.only}
        scripts_to_run = [
            (m, l) for (m, l) in SCRIPTS
            if any(m.startswith(f"seed_{p}") for p in prefixes)
        ]
        if not scripts_to_run:
            err(f"No scripts matched --only {args.only}")
            return 1
        info(f"Running {len(scripts_to_run)}/{len(SCRIPTS)} scripts")

    # ── Run each script ──────────────────────────────────────────────────────
    results = []
    all_ok = True

    for module_name, label in scripts_to_run:
        t0 = time.monotonic()
        error = None
        result = {}
        try:
            if args.dry_run:
                result = {"dry_run": True}
                ok(f"[DRY-RUN] {label}")
            else:
                result = run_script(module_name, str(db_path), args.dry_run)
        except Exception as e:
            error = str(e)
            all_ok = False
            import traceback
            err(f"FAILED: {label}")
            traceback.print_exc()

        elapsed = time.monotonic() - t0
        results.append((module_name, label, result, elapsed, error))

    # ── Summary ──────────────────────────────────────────────────────────────
    summarise(results)

    if args.dry_run:
        info("Dry run complete - no data was modified.")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
