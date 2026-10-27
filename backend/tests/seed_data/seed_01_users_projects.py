# tests/seed_data/seed_01_users_projects.py
"""
Seed: User Preferences + 10 Projects
────────────────────────────────────
Creates / updates the UserPreference row for the existing user and inserts
10 realistic projects spanning different technology domains and statuses.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import (
    connect, find_db_path, get_user_id, jdump, new_id, ok, warn,
    info, head, already_seeded, ts,
)


# ── User Preferences ──────────────────────────────────────────────────────

def seed_preferences(conn, user_id: str) -> int:
    from _shared import table_exists, count
    if not table_exists(conn, "user_preferences"):
        warn("user_preferences table missing - skipping")
        return 0
    if count(conn, "user_preferences", "user_id = ?", (user_id,)) > 0:
        warn("user_preferences already exists - skipping")
        return 0

    conn.execute(
        """
        INSERT INTO user_preferences (
            user_id, provider, model, stream, reasoning, system_prompt,
            tools, fallback_chain, tool_approval_mode, allowed_authorities,
            agent_mode, require_plan_approval,
            user_memory_enabled, agent_scope_key, agent_memory_enabled,
            skills_mode, shell_readonly, tool_hooks, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            user_id,
            "ollama", "qwen3:8b",
            1, None,                        # stream=True, reasoning=None (V6.7: level string or NULL, was bool)
            "You are EVA - a highly capable AI assistant with access to tools. "
            "Be concise, precise, and proactive. Prefer working code over lengthy explanations.",
            jdump([
                "web_search", "read_file", "write_file", "shell_command",
                "git_status", "git_diff", "git_commit", "list_files",
                "rag_retrieve", "memory_recall", "memory_store",
                "delegate_to_agent", "http_get",
            ]),
            jdump([
                {"provider": "groq", "model": "llama-3.3-70b-versatile"},
                {"provider": "openai", "model": "gpt-4o-mini"},
            ]),
            "auto",                         # tool_approval_mode
            "read_only,mutating,destructive",
            "auto",                         # agent_mode
            0,                              # require_plan_approval
            1,                              # user_memory_enabled
            "eva-backend-dev",              # agent_scope_key
            1,                              # agent_memory_enabled
            "auto",                         # skills_mode
            0,                              # shell_readonly
            jdump([]),                      # tool_hooks
            ts(),
        ),
    )
    conn.commit()
    ok("user_preferences row created")
    return 1


# ── Projects ─────────────────────────────────────────────────────────────

PROJECTS = [
    {
        "name": "EVA Backend v5",
        "description": "Core AI agent backend - FastAPI, async SQLite, multi-provider LLM routing, subagent orchestration, and autonomous daemons.",
        "synopsis": "Build and maintain the EVA backend: a production-grade FastAPI service with multi-provider LLM routing, long-term memory, subagent delegation, Plan→Execute→Verify loops, and autonomous daemon scheduling.",
        "status": "active",
        "days_ago": 45,
    },
    {
        "name": "Customer Analytics Dashboard",
        "description": "Real-time React + D3.js analytics dashboard surfacing retention metrics, funnel drop-off, and cohort analysis from a Redshift warehouse.",
        "synopsis": "Replace the legacy Tableau dashboard with a custom React 18 app. Must support real-time WebSocket updates, drill-down cohort views, and CSV export. Target: <2 s initial load on 1M-row datasets.",
        "status": "active",
        "days_ago": 38,
    },
    {
        "name": "ML Training Pipeline Automation",
        "description": "End-to-end automated ML pipeline: data ingestion → feature engineering → distributed training (PyTorch DDP) → evaluation → model registry push.",
        "synopsis": "Automate the full ML lifecycle using Prefect 3 + Ray. Key goals: reproducible runs, automatic hyperparameter sweep via Optuna, and Slack alerts on metric regression.",
        "status": "active",
        "days_ago": 30,
    },
    {
        "name": "REST API Documentation v3",
        "description": "Complete rewrite of public API docs: OpenAPI 3.1 spec, interactive Stoplight UI, code samples in 6 languages, and a migration guide from v2.",
        "synopsis": "Publish authoritative API docs for the v3 release. Every endpoint needs a description, all error codes documented, and 3 working code examples (curl, Python SDK, JS SDK). Launch target: Q3 2026.",
        "status": "paused",
        "days_ago": 60,
    },
    {
        "name": "E-Commerce Platform v3",
        "description": "Major refactor of the monolithic Node.js/PostgreSQL e-commerce platform to an event-driven microservices architecture with Kafka and separate checkout, inventory, and notification services.",
        "synopsis": "Decompose the existing monolith into 5 bounded-context microservices. Phase 1 covers checkout flow. Strict requirement: zero downtime migration; feature flag all new paths.",
        "status": "active",
        "days_ago": 55,
    },
    {
        "name": "Security Audit & Hardening 2026",
        "description": "Annual penetration testing, OWASP Top-10 remediation, dependency vulnerability scanning, and secrets rotation across all production services.",
        "synopsis": "Achieve SOC 2 Type II readiness by end of Q3 2026. Scope includes all public-facing APIs, the internal admin panel, Kubernetes RBAC policies, and CI/CD pipeline secrets.",
        "status": "active",
        "days_ago": 20,
    },
    {
        "name": "Marketing Automation Suite",
        "description": "Python-based automation suite: email drip campaigns (SendGrid), dynamic landing page personalisation, A/B test orchestration, and Salesforce CRM sync.",
        "synopsis": "Cut manual campaign work by 80%. Build a campaign engine that reads audience segments from Segment, renders personalised emails via Jinja2 templates, and reports conversions back to Salesforce.",
        "status": "active",
        "days_ago": 25,
    },
    {
        "name": "Customer Mobile App MVP",
        "description": "React Native cross-platform mobile app (iOS + Android) with offline-first sync, push notifications, in-app purchases, and biometric auth.",
        "synopsis": "Deliver a working MVP to App Store and Play Store by end of Q2 2026. Core screens: onboarding, dashboard, order history, support chat. Offline sync must tolerate 48 h without connectivity.",
        "status": "active",
        "days_ago": 35,
    },
    {
        "name": "Data Warehouse Migration",
        "description": "Migrate 6 TB of historical data from on-prem Redshift to Snowflake. Includes dbt model rewrites, Fivetran connector setup, and BI tool (Metabase) reconnection.",
        "synopsis": "Complete zero-data-loss migration to Snowflake. All 140 dbt models must be ported, tested, and passing CI before cut-over. Target ETL latency: <5 min end-to-end.",
        "status": "paused",
        "days_ago": 70,
    },
    {
        "name": "DevOps Infrastructure Overhaul",
        "description": "Terraform-managed AWS infrastructure, EKS cluster upgrade (1.28→1.32), Helm chart consolidation, GitHub Actions CI/CD rewrite, and cost optimisation.",
        "synopsis": "Reduce infrastructure costs by 30% while improving deploy frequency from weekly to on-demand. All Terraform state migrated to S3 + DynamoDB locking. EKS node auto-scaling enabled.",
        "status": "archived",
        "days_ago": 90,
    },
]


def seed_projects(conn, user_id: str) -> int:
    from _shared import count
    existing = count(conn, "projects", "user_id = ?", (user_id,))
    if existing >= 10:
        warn(f"projects already has {existing} rows for this user - skipping")
        return 0

    inserted = 0
    for p in PROJECTS:
        pid = new_id()
        conn.execute(
            """
            INSERT OR IGNORE INTO projects
                (id, user_id, name, description, synopsis,
                 workspace_path, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                pid, user_id, p["name"], p["description"], p["synopsis"],
                f"/home/user/eva_workspace/projects/{pid}",
                p["status"],
                ts(days_ago=p["days_ago"]),
                ts(days_ago=p["days_ago"] // 3),
            ),
        )
        inserted += 1
        ok(f"project: {p['name']!r}  [{p['status']}]")

    conn.commit()
    return inserted


# ── Entry point ────────────────────────────────────────────────────────────

def run(db_path=None) -> dict:
    head("01 : Users & Projects")
    conn = connect(find_db_path(db_path))
    user_id = get_user_id(conn)

    prefs   = seed_preferences(conn, user_id)
    projects = seed_projects(conn, user_id)

    return {"preferences": prefs, "projects": projects}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db-path", default=None)
    args = p.parse_args()
    result = run(args.db_path)
    print(f"\n  Done: {result}")
