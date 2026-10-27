# tests/seed_data/seed_03_executions.py
"""
Seed: Execution Metrics + Execution Plans (PEV)
───────────────────────────────────────────────
15 execution_metrics rows covering conversation-triggered and daemon-triggered
runs across various statuses, models, and tool profiles.
5 execution_plans representing Plan→Execute→Verify loops with full step detail.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import (
    connect, find_db_path, get_user_id, jdump, new_id, ok, warn,
    info, head, count, ts,
)


def get_first_conv_id(conn, user_id: str, title_like: str = ""):
    """Return the first conversation id matching a title fragment.

    Falls back to any real conversation for this user — never returns a
    synthetic UUID, which would violate the FK constraint on execution_metrics.
    """
    if title_like:
        row = conn.execute(
            "SELECT id FROM conversations WHERE user_id=? AND title LIKE ? LIMIT 1",
            (user_id, f"%{title_like}%"),
        ).fetchone()
        if row:
            return row["id"]

    # No title match (or no hint given) — grab any real conversation
    row = conn.execute(
        "SELECT id FROM conversations WHERE user_id=? ORDER BY created_at LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return row["id"]

    # Absolute last resort: create a placeholder conversation so FK is satisfied
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,'[seed] placeholder',NULL,datetime('now'),datetime('now'))",
        (cid, user_id),
    )
    conn.commit()
    return cid


def get_project_id(conn, user_id, name):
    row = conn.execute(
        "SELECT id FROM projects WHERE user_id=? AND name=? LIMIT 1", (user_id, name)
    ).fetchone()
    return row["id"] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Execution Metrics
# ─────────────────────────────────────────────────────────────────────────────

def seed_execution_metrics(conn, user_id: str) -> int:
    EXECUTIONS = [
        # ── Conversation-triggered ──
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 3240, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 1234, "completion_tokens": 876, "reasoning_tokens": 0,
            "total_steps": 3, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "web_search", "call_count": 1, "avg_latency_ms": 820, "failure_count": 0, "success_count": 1},
                {"tool_name": "write_file",      "call_count": 1, "avg_latency_ms": 45,  "failure_count": 0, "success_count": 1},
            ],
            "triggered_by": "conversation", "title_hint": "async patterns",
            "days_ago": 28, "hours_ago": 0,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 11450, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 3876, "completion_tokens": 2341, "reasoning_tokens": 0,
            "total_steps": 8, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "read_file",    "call_count": 2, "avg_latency_ms": 32, "failure_count": 0, "success_count": 2},
                {"tool_name": "write_file",   "call_count": 2, "avg_latency_ms": 56, "failure_count": 0, "success_count": 2},
                {"tool_name": "shell_command","call_count": 2, "avg_latency_ms": 1245, "failure_count": 0, "success_count": 2},
            ],
            "triggered_by": "conversation", "title_hint": "authentication",
            "days_ago": 22, "hours_ago": 2,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 7820, "model": "groq/llama-3.3-70b-versatile", "provider": "groq",
            "prompt_tokens": 2198, "completion_tokens": 1534, "reasoning_tokens": 0,
            "total_steps": 5, "fallbacks": 0, "cost": 0.0032,
            "tool_metrics": [
                {"tool_name": "write_file", "call_count": 2, "avg_latency_ms": 67, "failure_count": 0, "success_count": 2},
            ],
            "triggered_by": "conversation", "title_hint": "retention dashboard",
            "days_ago": 18, "hours_ago": 5,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 4567, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 1812, "completion_tokens": 2234, "reasoning_tokens": 0,
            "total_steps": 2, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "web_search", "call_count": 2, "avg_latency_ms": 934, "failure_count": 0, "success_count": 2},
            ],
            "triggered_by": "conversation", "title_hint": "Transformer",
            "days_ago": 14, "hours_ago": 3,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 9123, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 2567, "completion_tokens": 1987, "reasoning_tokens": 0,
            "total_steps": 4, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "read_file",    "call_count": 1, "avg_latency_ms": 28, "failure_count": 0, "success_count": 1},
                {"tool_name": "write_file",   "call_count": 1, "avg_latency_ms": 49, "failure_count": 0, "success_count": 1},
                {"tool_name": "shell_command","call_count": 1, "avg_latency_ms": 876, "failure_count": 0, "success_count": 1},
            ],
            "triggered_by": "conversation", "title_hint": "payment",
            "days_ago": 10, "hours_ago": 6,
        },
        {
            "status": "failed", "termination_reason": "RuntimeError",
            "duration_ms": 2340, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 876, "completion_tokens": 234, "reasoning_tokens": 0,
            "total_steps": 2, "fallbacks": 1, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "shell_command", "call_count": 1, "avg_latency_ms": 1234, "failure_count": 1, "success_count": 0},
            ],
            "triggered_by": "conversation", "title_hint": "postgres",
            "days_ago": 8, "hours_ago": 4,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 6780, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 3234, "completion_tokens": 2678, "reasoning_tokens": 0,
            "total_steps": 4, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "write_file", "call_count": 1, "avg_latency_ms": 89, "failure_count": 0, "success_count": 1},
            ],
            "triggered_by": "conversation", "title_hint": "CI/CD",
            "days_ago": 5, "hours_ago": 9,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 4230, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 1678, "completion_tokens": 1345, "reasoning_tokens": 0,
            "total_steps": 2, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "write_file", "call_count": 2, "avg_latency_ms": 53, "failure_count": 0, "success_count": 2},
            ],
            "triggered_by": "conversation", "title_hint": "API docs",
            "days_ago": 12, "hours_ago": 2,
        },
        # ── Daemon-triggered ──
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 18450, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 4512, "completion_tokens": 2134, "reasoning_tokens": 0,
            "total_steps": 6, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "web_search", "call_count": 3, "avg_latency_ms": 1123, "failure_count": 0, "success_count": 3},
                {"tool_name": "http_get",         "call_count": 2, "avg_latency_ms": 456,  "failure_count": 0, "success_count": 2},
                {"tool_name": "memory_store",     "call_count": 1, "avg_latency_ms": 23,   "failure_count": 0, "success_count": 1},
            ],
            "triggered_by": "daemon", "daemon_name": "github-pr-monitor",
            "days_ago": 0, "hours_ago": 2,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 45230, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 8934, "completion_tokens": 4123, "reasoning_tokens": 0,
            "total_steps": 12, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "shell_command",   "call_count": 4, "avg_latency_ms": 2345, "failure_count": 0, "success_count": 4},
                {"tool_name": "read_file",        "call_count": 6, "avg_latency_ms": 34,   "failure_count": 0, "success_count": 6},
                {"tool_name": "memory_store",     "call_count": 3, "avg_latency_ms": 21,   "failure_count": 0, "success_count": 3},
                {"tool_name": "web_search",  "call_count": 2, "avg_latency_ms": 876,  "failure_count": 0, "success_count": 2},
            ],
            "triggered_by": "daemon", "daemon_name": "weekly-metrics-digest",
            "days_ago": 7, "hours_ago": 0,
        },
        {
            "status": "failed", "termination_reason": "TimeoutError",
            "duration_ms": 300000, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 1234, "completion_tokens": 456, "reasoning_tokens": 0,
            "total_steps": 5, "fallbacks": 2, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "http_get", "call_count": 5, "avg_latency_ms": 59876, "failure_count": 3, "success_count": 2},
            ],
            "triggered_by": "daemon", "daemon_name": "api-health-monitor",
            "days_ago": 1, "hours_ago": 3,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 23450, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 5678, "completion_tokens": 2890, "reasoning_tokens": 0,
            "total_steps": 9, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "read_file",    "call_count": 5, "avg_latency_ms": 29,   "failure_count": 0, "success_count": 5},
                {"tool_name": "shell_command","call_count": 3, "avg_latency_ms": 1876, "failure_count": 0, "success_count": 3},
                {"tool_name": "memory_store", "call_count": 2, "avg_latency_ms": 18,   "failure_count": 0, "success_count": 2},
            ],
            "triggered_by": "daemon", "daemon_name": "security-vulnerability-scanner",
            "days_ago": 1, "hours_ago": 0,
        },
        {
            "status": "cancelled", "termination_reason": "user",
            "duration_ms": 8900, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 2134, "completion_tokens": 987, "reasoning_tokens": 0,
            "total_steps": 3, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "web_search", "call_count": 2, "avg_latency_ms": 987, "failure_count": 0, "success_count": 2},
            ],
            "triggered_by": "conversation", "title_hint": "ML",
            "days_ago": 6, "hours_ago": 8,
        },
        # ── Subagent executions ──
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 5670, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 1890, "completion_tokens": 1234, "reasoning_tokens": 0,
            "total_steps": 4, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "web_search", "call_count": 2, "avg_latency_ms": 1023, "failure_count": 0, "success_count": 2},
                {"tool_name": "memory_store",    "call_count": 1, "avg_latency_ms": 19,   "failure_count": 0, "success_count": 1},
            ],
            "triggered_by": "conversation",
            "subagent_name": "deep-research", "title_hint": "Transformer",
            "days_ago": 14, "hours_ago": 2,
        },
        {
            "status": "completed", "termination_reason": "success",
            "duration_ms": 4320, "model": "qwen3:8b", "provider": "ollama",
            "prompt_tokens": 1456, "completion_tokens": 987, "reasoning_tokens": 0,
            "total_steps": 3, "fallbacks": 0, "cost": 0.0,
            "tool_metrics": [
                {"tool_name": "read_file",  "call_count": 2, "avg_latency_ms": 31, "failure_count": 0, "success_count": 2},
                {"tool_name": "git_status", "call_count": 1, "avg_latency_ms": 89, "failure_count": 0, "success_count": 1},
            ],
            "triggered_by": "conversation",
            "subagent_name": "coding-agent", "title_hint": "authentication",
            "days_ago": 22, "hours_ago": 4,
        },
    ]

    inserted = 0
    for ex in EXECUTIONS:
        eid = new_id()
        cid = get_first_conv_id(conn, user_id, ex.get("title_hint", ""))
        proj_id = None
        if ex.get("triggered_by") == "daemon":
            proj_id = get_project_id(conn, user_id, "EVA Backend v5")

        conn.execute(
            """
            INSERT INTO execution_metrics (
                execution_id, conversation_id, user_id,
                status, termination_reason, duration_ms,
                model_used, provider_used,
                prompt_tokens, completion_tokens, reasoning_tokens,
                total_steps, fallbacks_triggered, computed_cost_usd,
                tool_metrics, triggered_by, daemon_name, subagent_name,
                project_id, parent_execution_id,
                created_at, completed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                eid, cid, user_id,
                ex["status"], ex["termination_reason"], ex["duration_ms"],
                ex["model"], ex["provider"],
                ex["prompt_tokens"], ex["completion_tokens"], ex["reasoning_tokens"],
                ex["total_steps"], ex["fallbacks"], ex.get("cost"),
                jdump(ex.get("tool_metrics")),
                ex.get("triggered_by"), ex.get("daemon_name"), ex.get("subagent_name"),
                proj_id, None,
                ts(days_ago=ex["days_ago"], hours_ago=ex["hours_ago"]),
                ts(days_ago=ex["days_ago"], hours_ago=ex["hours_ago"],
                   seconds_ago=-(ex["duration_ms"] // 1000)),
            ),
        )
        inserted += 1
        label = ex.get("daemon_name") or ex.get("subagent_name") or ex.get("title_hint", "conv")
        ok(f"execution_metrics: [{ex['status']:10s}]  {label}  ({ex['total_steps']} steps)")

    conn.commit()
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Execution Plans (PEV - Plan → Execute → Verify)
# ─────────────────────────────────────────────────────────────────────────────

def seed_execution_plans(conn, user_id: str) -> int:
    PLANS = [
        {
            "title_hint": "authentication",
            "goal": "Implement refresh token rotation for the EVA authentication module with DB schema migration and full test coverage.",
            "status": "completed",
            "days_ago": 22,
            "steps": [
                {"index": 0, "title": "Read current auth module",       "description": "Read api/auth.py and core/db/models.py to understand the existing token structure.", "tool_hint": "read_file",     "status": "completed", "result_summary": "Found AuthSession model with no refresh_token column.", "verified": True,  "verification_notes": "File read successfully, schema understood."},
                {"index": 1, "title": "Design schema changes",          "description": "Define the two new columns: refresh_token (unique text) and refresh_expires_at (datetime).", "tool_hint": None, "status": "completed", "result_summary": "Schema design complete - two new columns identified.", "verified": True, "verification_notes": "Design is consistent with existing patterns."},
                {"index": 2, "title": "Write Alembic migration",        "description": "Create a new Alembic migration file that adds both columns with PRAGMA guard for idempotency.", "tool_hint": "write_file", "status": "completed", "result_summary": "Migration file written at alembic/versions/20260518_0004.", "verified": True, "verification_notes": "Migration syntax validated."},
                {"index": 3, "title": "Implement token rotation logic", "description": "Write generate_refresh_token() helper and POST /auth/refresh endpoint in api/auth.py.", "tool_hint": "write_file", "status": "completed", "result_summary": "Endpoint implemented with rotation and expiry validation.", "verified": True, "verification_notes": "Code review passed."},
                {"index": 4, "title": "Run test suite",                 "description": "Execute pytest on tests/test_auth.py and verify all existing tests still pass.", "tool_hint": "shell_command", "status": "completed", "result_summary": "3/3 tests passed in 1.24s.", "verified": True, "verification_notes": "All assertions green."},
                {"index": 5, "title": "Commit changes",                 "description": "Stage all changed files and commit with a descriptive message.", "tool_hint": "shell_command", "status": "completed", "result_summary": "Committed as a3f9c12.", "verified": True, "verification_notes": "Commit message follows conventional commits spec."},
            ],
        },
        {
            "title_hint": "CI/CD",
            "goal": "Create a complete GitHub Actions CI/CD pipeline for the FastAPI backend: lint → test → Docker build → ECR push → EKS deploy.",
            "status": "completed",
            "days_ago": 5,
            "steps": [
                {"index": 0, "title": "Audit existing CI config",     "description": "Check .github/workflows/ for any existing workflows to avoid conflicts.", "tool_hint": "list_files",    "status": "completed", "result_summary": "No existing workflows found.", "verified": True, "verification_notes": "Clean slate confirmed."},
                {"index": 1, "title": "Write lint + test job",        "description": "Create workflow with ruff lint and pytest steps with coverage reporting.", "tool_hint": "write_file", "status": "completed", "result_summary": "Workflow yaml written.", "verified": True, "verification_notes": "YAML syntax valid."},
                {"index": 2, "title": "Add Docker build with layer cache", "description": "Add docker/build-push-action step with GitHub Actions cache.", "tool_hint": "write_file", "status": "completed", "result_summary": "Build step added with cache-from/cache-to config.", "verified": True, "verification_notes": "Cache mode=max confirmed."},
                {"index": 3, "title": "Configure OIDC AWS auth",       "description": "Replace hard-coded AWS keys with OIDC role assumption via aws-actions/configure-aws-credentials.", "tool_hint": "write_file", "status": "completed", "result_summary": "OIDC step added, IAM trust policy documented.", "verified": True, "verification_notes": "No secrets stored in GitHub."},
                {"index": 4, "title": "Add EKS deploy step",           "description": "Use kubectl rollout after image push on main branch only.", "tool_hint": "write_file", "status": "completed", "result_summary": "Deploy step added with environment: staging protection.", "verified": True, "verification_notes": "Branch condition correct."},
            ],
        },
        {
            "title_hint": "payment",
            "goal": "Write comprehensive pytest unit tests for the payment service charge method covering success, decline, invalid input, and webhook firing scenarios.",
            "status": "completed",
            "days_ago": 10,
            "steps": [
                {"index": 0, "title": "Read payment service source",   "description": "Read services/payment.py to understand the charge method signature and dependencies.", "tool_hint": "read_file",  "status": "completed", "result_summary": "PaymentService.charge() identified - depends on stripe SDK, db session, and webhook client.", "verified": True, "verification_notes": "Dependencies fully mapped."},
                {"index": 1, "title": "Design test fixtures",          "description": "Define mock_stripe, mock_db, and mock_webhook_client pytest fixtures.", "tool_hint": None, "status": "completed", "result_summary": "Three fixtures designed using unittest.mock.AsyncMock.", "verified": True, "verification_notes": "Fixture design reviewed."},
                {"index": 2, "title": "Write test file",               "description": "Implement all four test cases in tests/test_payment_service.py.", "tool_hint": "write_file",  "status": "completed", "result_summary": "4 test cases written covering happy path and 3 error scenarios.", "verified": True, "verification_notes": "All assertions are deterministic."},
                {"index": 3, "title": "Run tests and verify",          "description": "Execute pytest and confirm 4/4 pass.", "tool_hint": "shell_command", "status": "completed", "result_summary": "4 passed in 0.87s.", "verified": True, "verification_notes": "Green. No warnings."},
            ],
        },
        {
            "title_hint": "mobile",
            "goal": "Design the offline sync architecture for the React Native mobile app including storage choice, conflict resolution strategy, and WatermelonDB integration plan.",
            "status": "completed",
            "days_ago": 3,
            "steps": [
                {"index": 0, "title": "Research offline-first patterns", "description": "Search for current best practices in React Native offline sync 2026.", "tool_hint": "web_search",  "status": "completed", "result_summary": "Event sourcing + WatermelonDB identified as the recommended stack.", "verified": True, "verification_notes": "Sources cross-checked."},
                {"index": 1, "title": "Define conflict resolution matrix", "description": "Document per-entity conflict strategy: LWW for profile, Server Wins for pricing, append-only for orders.", "tool_hint": None, "status": "completed", "result_summary": "Conflict matrix documented.", "verified": True, "verification_notes": "Covers all 3 entity types."},
                {"index": 2, "title": "Write architecture decision record", "description": "Create docs/decisions/002-offline-sync.md with rationale for WatermelonDB choice.", "tool_hint": "write_file", "status": "completed", "result_summary": "ADR written and saved.", "verified": True, "verification_notes": "ADR format follows template."},
                {"index": 3, "title": "Prototype sync endpoint",           "description": "Draft the backend GET /sync/pull and POST /sync/push endpoint signatures.", "tool_hint": "write_file", "status": "completed", "result_summary": "Endpoint signatures defined in OpenAPI 3.1.", "verified": True, "verification_notes": "Compatible with WatermelonDB sync protocol."},
            ],
        },
        {
            "title_hint": "postgres",
            "goal": "Diagnose and fix the slow order history query taking 4–8 seconds for customers with >500 orders.",
            "status": "failed",
            "days_ago": 8,
            "steps": [
                {"index": 0, "title": "EXPLAIN ANALYZE the query", "description": "Run EXPLAIN (ANALYZE, BUFFERS) on the order history query in staging.", "tool_hint": "shell_command", "status": "completed", "result_summary": "Confirmed Seq Scan on orders - missing index on user_id.", "verified": True,  "verification_notes": "Execution plan captured."},
                {"index": 1, "title": "Create composite index",    "description": "CREATE INDEX CONCURRENTLY idx_orders_user_created ON orders(user_id, created_at DESC).", "tool_hint": "shell_command", "status": "completed", "result_summary": "Index created successfully.", "verified": True,  "verification_notes": "CONCURRENTLY avoids table lock."},
                {"index": 2, "title": "Rewrite with LATERAL join", "description": "Refactor query to use LATERAL subquery to avoid Cartesian explosion from order_items join.", "tool_hint": "write_file", "status": "failed",    "result_summary": "LATERAL syntax error on PostgreSQL 13 (not supported in this version).", "verified": False, "verification_notes": "Need to target PG14+. Use CTEs as fallback."},
                {"index": 3, "title": "Deploy and benchmark",      "description": "Deploy index to production and measure query time improvement.", "tool_hint": "shell_command", "status": "skipped",   "result_summary": None, "verified": False, "verification_notes": None},
            ],
        },
    ]

    inserted = 0
    for plan_data in PLANS:
        eid = new_id()
        pid = new_id()
        cid = get_first_conv_id(conn, user_id, plan_data["title_hint"])

        # Insert a matching execution_metrics row for this plan
        conn.execute(
            """
            INSERT INTO execution_metrics (
                execution_id, conversation_id, user_id, status, termination_reason,
                duration_ms, model_used, provider_used,
                prompt_tokens, completion_tokens, reasoning_tokens,
                total_steps, fallbacks_triggered, computed_cost_usd,
                tool_metrics, triggered_by,
                created_at, completed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                eid, cid, user_id,
                plan_data["status"],
                "success" if plan_data["status"] == "completed" else "RuntimeError",
                12000, "qwen3:8b", "ollama",
                3200, 1800, 0,
                len(plan_data["steps"]), 0, 0.0,
                jdump([]),
                "conversation",
                ts(days_ago=plan_data["days_ago"], hours_ago=1),
                ts(days_ago=plan_data["days_ago"]),
            ),
        )

        completed_steps = sum(1 for s in plan_data["steps"] if s["status"] == "completed")
        current_idx = next(
            (s["index"] for s in plan_data["steps"] if s["status"] != "completed"),
            len(plan_data["steps"]) - 1,
        )

        conn.execute(
            """
            INSERT INTO execution_plans (
                id, execution_id, conversation_id, user_id,
                goal, steps, status, current_step_index,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pid, eid, cid, user_id,
                plan_data["goal"],
                jdump(plan_data["steps"]),
                plan_data["status"],
                current_idx,
                ts(days_ago=plan_data["days_ago"], hours_ago=1),
                ts(days_ago=plan_data["days_ago"]),
            ),
        )
        inserted += 1
        ok(f"execution_plan: [{plan_data['status']:10s}]  {plan_data['goal'][:60]}...  ({len(plan_data['steps'])} steps)")

    conn.commit()
    return inserted


# ── Main seeder ────────────────────────────────────────────────────────────
# NOTE: Daemon run history is seeded in script 04 (after daemon configs exist)
# so that execution_metrics.conversation_id can reference the real daemon.id
# from daemon_configs — matching the real backend's behaviour exactly.

def run(db_path=None) -> dict:
    head("03 : Execution Metrics & Plans")
    conn = connect(find_db_path(db_path))
    user_id = get_user_id(conn)

    # Idempotency: check for a known seed execution rather than raw count
    already = count(conn, "execution_metrics",
                    "user_id = ? AND daemon_name = 'weekly-metrics-digest' AND triggered_by = 'conversation'",
                    (user_id,))
    if already > 0:
        warn("seed execution_metrics already present - skipping conv/subagent metrics")
        metrics = 0
        plans   = 0
    else:
        metrics = seed_execution_metrics(conn, user_id)
        plans   = seed_execution_plans(conn, user_id)

    return {"execution_metrics": metrics, "execution_plans": plans}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db-path", default=None)
    args = p.parse_args()
    result = run(args.db_path)
    print(f"\n  Done: {result}")
