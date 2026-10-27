#!/usr/bin/env python3
"""
A-04 Phase C — one-time memory reconciliation (backfill).

Reviews stored memory (Scope 2 user facts, and/or one or more Scope 3
agent-memory scopes) for redundant history that accumulated before the
dedupe/supersedes pipeline in core/memory/extractor.py existed (Scope 3 had
no such pipeline until A-04 Phase A; Scope 2 has had one since B-041, so its
yield here is expected to be much smaller).

This is a standalone maintenance script, not an API endpoint -- it is meant
to be run occasionally by whoever operates this EVA instance, not called by
the frontend. It uses core/memory/reconciler.py's propose_*/apply_*
functions directly against the real DB.

Always proposes first and prints what it found; deletes nothing until you
confirm (or pass --yes to skip the prompt for a non-interactive run).

Usage:
    python scripts/reconcile_memory.py --user-id <id> --provider ollama_cloud --model <model>
        [--scope-key <key> | --all-scopes] [--yes] [--dry-run]

Examples:
    # Review user facts only, interactive confirmation per batch
    python scripts/reconcile_memory.py --user-id abc123 --provider ollama_cloud --model llama3.1

    # Review user facts + one specific agent scope, no prompts
    python scripts/reconcile_memory.py --user-id abc123 --provider ollama_cloud --model llama3.1 \\
        --scope-key eva-backend-dev --yes

    # Review user facts + every agent scope this user has, dry-run (never deletes)
    python scripts/reconcile_memory.py --user-id abc123 --provider ollama_cloud --model llama3.1 \\
        --all-scopes --dry-run
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db.session import AsyncSessionLocal
from core.db.repository import MemoryRepo
from core.memory import reconciler
from core.memory.fact_summary import schedule_summary_rebuild


def _print_proposals(label: str, proposals: list) -> None:
    if not proposals:
        print(f"  {label}: already clean, nothing to do.")
        return
    print(f"  {label}: {len(proposals)} proposed deletion(s):")
    for p in proposals:
        print(f"    [{p['delete_id']}] {p['reason']}")


def _confirm(count: int, yes: bool) -> bool:
    if yes:
        return True
    reply = input(f"  Delete these {count} item(s)? [y/N] ").strip().lower()
    return reply in ("y", "yes")


async def reconcile_user_facts(user_id: str, session: dict, yes: bool, dry_run: bool) -> None:
    print("\n== User facts (Scope 2) ==")
    proposals = await reconciler.propose_user_fact_reconciliation(user_id=user_id, session=session)
    _print_proposals("User facts", proposals)
    if not proposals or dry_run:
        return
    if _confirm(len(proposals), yes):
        ids = [p["delete_id"] for p in proposals]
        deleted = await reconciler.apply_user_fact_reconciliation(user_id, ids)
        schedule_summary_rebuild(user_id)
        print(f"  Deleted {deleted} fact(s).")
    else:
        print("  Skipped.")


async def reconcile_agent_scope(user_id: str, scope_key: str, session: dict, yes: bool, dry_run: bool) -> None:
    print(f"\n== Agent scope '{scope_key}' (Scope 3) ==")
    proposals = await reconciler.propose_agent_reconciliation(
        user_id=user_id, scope_key=scope_key, session=session,
    )
    _print_proposals(scope_key, proposals)
    if not proposals or dry_run:
        return
    if _confirm(len(proposals), yes):
        ids = [p["delete_id"] for p in proposals]
        deleted = await reconciler.apply_agent_reconciliation(user_id, ids)
        print(f"  Deleted {deleted} entry(ies).")
    else:
        print("  Skipped.")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", required=True, help="The user whose memory to reconcile.")
    parser.add_argument("--provider", required=True, help="Provider to use for the reconciliation LLM call (e.g. ollama_cloud).")
    parser.add_argument("--model", required=True, help="Model to use for the reconciliation LLM call.")
    parser.add_argument("--scope-key", help="Reconcile this one agent-memory scope (in addition to user facts).")
    parser.add_argument("--all-scopes", action="store_true", help="Reconcile every agent-memory scope this user has (in addition to user facts).")
    parser.add_argument("--yes", action="store_true", help="Apply proposed deletions without prompting for confirmation.")
    parser.add_argument("--dry-run", action="store_true", help="Only print proposals, never delete anything (overrides --yes).")
    args = parser.parse_args()

    if args.scope_key and args.all_scopes:
        parser.error("--scope-key and --all-scopes are mutually exclusive.")

    session = {"provider": args.provider, "model": args.model}

    await reconcile_user_facts(args.user_id, session, args.yes, args.dry_run)

    if args.all_scopes:
        async with AsyncSessionLocal() as db:
            scopes = await MemoryRepo.list_agent_scopes(db, user_id=args.user_id)
        if not scopes:
            print("\n== Agent scopes (Scope 3) ==\n  No agent-memory scopes found for this user.")
        for s in scopes:
            await reconcile_agent_scope(args.user_id, s["scope_key"], session, args.yes, args.dry_run)
    elif args.scope_key:
        await reconcile_agent_scope(args.user_id, args.scope_key, session, args.yes, args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
