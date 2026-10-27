# tests/seed_data/seed_04_agents_daemons.py
"""
Seed: Custom Subagent Configs + Daemon Configs + Daemon Webhooks
────────────────────────────────────────────────────────────────
Inserts 5 custom (non-default) subagent configurations and
10 realistic daemon agents covering scheduled monitoring, reporting,
and event-driven automation - plus 3 daemon webhooks.

NOTE: Default subagents (deep-research, coding-agent, etc.) are already
seeded by the backend's seed_defaults call on first login. This script
only adds user-custom subagents that do NOT conflict with those names.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import (
    connect, find_db_path, get_user_id, jdump, new_id, ok, warn,
    info, head, count, ts, table_columns,
)


def get_project_id(conn, user_id, name):
    row = conn.execute(
        "SELECT id FROM projects WHERE user_id=? AND name=? LIMIT 1", (user_id, name)
    ).fetchone()
    return row["id"] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Custom Subagent Configs
# ─────────────────────────────────────────────────────────────────────────────

CUSTOM_SUBAGENTS = [
    {
        "name": "sql-query-optimizer",
        "description": "Specialised in analysing slow SQL queries, reading EXPLAIN plans, and rewriting them for maximum performance. Expert in PostgreSQL, SQLite, and MySQL index strategies.",
        "system_prompt": (
            "You are a database performance specialist. Your job is to:\n"
            "1. Read EXPLAIN ANALYZE output and identify bottlenecks\n"
            "2. Suggest missing indexes and schema changes\n"
            "3. Rewrite queries using CTEs, window functions, and LATERAL joins where appropriate\n"
            "4. Always explain WHY a change improves performance\n\n"
            "Be concise and actionable. Always provide the corrected SQL, not just advice."
        ),
        "model": None,
        "provider": None,
        "tools": ["read_file", "write_file", "shell_command", "web_search", "memory_recall"],
        "max_steps": 8,
        "allowed_mcp_servers": None,
        "project_id_name": "Data Warehouse Migration",
    },
    {
        "name": "tech-writer",
        "description": "Technical documentation specialist - writes clear, accurate API docs, README files, migration guides, and architecture decision records.",
        "system_prompt": (
            "You are a senior technical writer with deep engineering knowledge. You:\n"
            "- Write clear, scannable documentation with good heading hierarchy\n"
            "- Always include working code examples in at least 2 languages\n"
            "- Follow the Diátaxis framework (tutorials / how-tos / reference / explanation)\n"
            "- Avoid passive voice and marketing fluff\n"
            "- Review existing docs for accuracy before writing new sections\n\n"
            "Output format: Markdown unless instructed otherwise. Always include a TL;DR at the top for reference docs."
        ),
        "model": None,
        "provider": None,
        "tools": ["read_file", "write_file", "list_files", "web_search", "rag_retrieve", "memory_recall"],
        "max_steps": 10,
        "allowed_mcp_servers": None,
        "project_id_name": "REST API Documentation v3",
    },
    {
        "name": "security-reviewer",
        "description": "Code security analysis specialist - reviews code for OWASP Top 10 vulnerabilities, secrets leakage, injection flaws, and authentication weaknesses.",
        "system_prompt": (
            "You are a senior application security engineer. For each code review:\n"
            "1. Scan for OWASP Top 10 vulnerabilities\n"
            "2. Check for hardcoded secrets, insecure defaults, and missing auth checks\n"
            "3. Identify injection vectors: SQL, command, SSTI, path traversal\n"
            "4. Rate each finding: CRITICAL / HIGH / MEDIUM / LOW / INFO\n"
            "5. Provide a minimal code fix for each finding\n\n"
            "Be thorough but avoid false positives - only flag real risks. "
            "Reference OWASP and CVE numbers where applicable."
        ),
        "model": None,
        "provider": None,
        "tools": ["read_file", "list_files", "shell_command", "web_search", "memory_recall", "memory_store"],
        "max_steps": 15,
        "allowed_mcp_servers": None,
        "project_id_name": "Security Audit & Hardening 2026",
    },
    {
        "name": "performance-profiler",
        "description": "Application performance specialist - profiles Python, Node.js, and React apps, identifies bottlenecks, and delivers optimised implementations.",
        "system_prompt": (
            "You are a performance engineering expert. Your workflow:\n"
            "1. Establish a baseline - measure before changing anything\n"
            "2. Profile with the right tool (cProfile, py-spy, React DevTools, Lighthouse)\n"
            "3. Identify the top 3 bottlenecks by impact\n"
            "4. Apply one fix at a time and re-measure\n"
            "5. Document the improvement with before/after numbers\n\n"
            "Never optimise without measuring first. Always report % improvement in latency or throughput."
        ),
        "model": None,
        "provider": None,
        "tools": ["read_file", "write_file", "shell_command", "list_files", "web_search", "memory_recall"],
        "max_steps": 12,
        "allowed_mcp_servers": None,
        "project_id_name": "ML Training Pipeline Automation",
    },
    {
        "name": "devops-helper",
        "description": "DevOps and infrastructure specialist - Terraform, Kubernetes, GitHub Actions, Docker, and AWS. Writes IaC, debugs deployments, and optimises CI/CD pipelines.",
        "system_prompt": (
            "You are a senior DevOps engineer. You work with:\n"
            "- Terraform (AWS provider, modules, state management)\n"
            "- Kubernetes (EKS, Helm charts, RBAC, HPA)\n"
            "- GitHub Actions (workflows, OIDC, matrix builds)\n"
            "- Docker (multi-stage builds, layer caching, security scanning)\n\n"
            "Always use least-privilege IAM. Prefer OIDC over long-lived credentials. "
            "Validate Terraform plans before applying. Use `kubectl diff` before applying manifests. "
            "All secrets go in AWS Secrets Manager or GitHub Secrets - never in code."
        ),
        "model": None,
        "provider": None,
        "tools": ["read_file", "write_file", "shell_command", "list_files", "web_search", "memory_recall", "memory_store"],
        "max_steps": 10,
        "allowed_mcp_servers": None,
        "project_id_name": "DevOps Infrastructure Overhaul",
    },
]


def seed_subagents(conn, user_id: str) -> int:
    cols = table_columns(conn, "subagent_configs")
    has_is_default = "is_default" in cols

    existing = count(conn, "subagent_configs", "user_id = ? AND name = 'sql-query-optimizer'", (user_id,))
    if existing > 0:
        warn("custom subagents already seeded - skipping")
        return 0

    inserted = 0
    for sa in CUSTOM_SUBAGENTS:
        proj_id = get_project_id(conn, user_id, sa["project_id_name"]) if sa["project_id_name"] else None

        if has_is_default:
            conn.execute(
                """
                INSERT OR IGNORE INTO subagent_configs
                    (id, user_id, name, description, system_prompt, model, provider,
                     tools, max_steps, allowed_mcp_servers, project_id, is_default,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    new_id(), user_id, sa["name"], sa["description"], sa["system_prompt"],
                    sa["model"], sa["provider"],
                    jdump(sa["tools"]), sa["max_steps"], jdump(sa["allowed_mcp_servers"]),
                    proj_id, 0,
                    ts(days_ago=15), ts(days_ago=5),
                ),
            )
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO subagent_configs
                    (id, user_id, name, description, system_prompt, model, provider,
                     tools, max_steps, allowed_mcp_servers, project_id,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    new_id(), user_id, sa["name"], sa["description"], sa["system_prompt"],
                    sa["model"], sa["provider"],
                    jdump(sa["tools"]), sa["max_steps"], jdump(sa["allowed_mcp_servers"]),
                    proj_id,
                    ts(days_ago=15), ts(days_ago=5),
                ),
            )
        inserted += 1
        ok(f"subagent: {sa['name']!r}  [{len(sa['tools'])} tools]")

    conn.commit()
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Daemon Configs
# ─────────────────────────────────────────────────────────────────────────────

DAEMONS = [
    {
        "name": "daily-standup-reporter",
        "description": "Generates a daily standup report by scanning recent git commits, open PRs, and completed tasks. Posts a Markdown summary to memory for the team.",
        "system_prompt": (
            "You are a daily standup automation agent. Every morning:\n"
            "1. Run `git log --since=yesterday --oneline` to list yesterday's commits\n"
            "2. Check for open pull requests via the GitHub API\n"
            "3. Retrieve any open blocking issues from memory\n"
            "4. Generate a concise standup in this format:\n"
            "   **Yesterday:** [bullet list of commits]\n   **Today:** [planned tasks]\n   **Blockers:** [any blockers]\n"
            "5. Store the report in agent memory with tag 'standup'"
        ),
        "trigger_message": "Generate today's standup report. Check recent commits, open PRs, and blockers.",
        "model": None, "provider": None,
        "tools": ["shell_command", "http_get", "memory_recall", "memory_store"],
        "max_steps": 8,
        "run_interval_seconds": 86400,   # daily
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": None,
        "project_id_name": "EVA Backend v5",
        "last_run_hours_ago": 14,
        "failure_count": 0,
    },
    {
        "name": "github-pr-monitor",
        "description": "Monitors open pull requests every 30 minutes, flags PRs older than 48 hours without review, and posts a reminder summary to agent memory.",
        "system_prompt": (
            "You monitor GitHub pull requests. On each run:\n"
            "1. Fetch all open PRs from the repository\n"
            "2. Flag any PR older than 48 hours without a review comment\n"
            "3. Check if any PR has unresolved requested changes\n"
            "4. Store a summary in agent memory: count of stale PRs, PR numbers, authors\n"
            "5. If >3 stale PRs, increase urgency in the summary\n\n"
            "Format: JSON stored under key 'pr_monitor_summary'."
        ),
        "trigger_message": "Scan open pull requests and report on stale reviews.",
        "model": None, "provider": None,
        "tools": ["http_get", "memory_recall", "memory_store"],
        "max_steps": 6,
        "run_interval_seconds": 1800,    # 30 min
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": None,
        "project_id_name": "EVA Backend v5",
        "last_run_hours_ago": 0,
        "failure_count": 0,
    },
    {
        "name": "error-log-watcher",
        "description": "Scans application error logs every 15 minutes for new ERROR or CRITICAL entries. Groups similar errors, tracks frequency, and alerts on new error patterns.",
        "system_prompt": (
            "You monitor application error logs. On each run:\n"
            "1. Read the last 200 lines of /var/log/eva/app.log\n"
            "2. Extract all ERROR and CRITICAL lines since the last run timestamp\n"
            "3. Group similar errors by message pattern\n"
            "4. Compare with previously seen patterns stored in memory\n"
            "5. Report NEW error patterns (not seen before) with occurrence count\n"
            "6. Update the error pattern registry in memory\n\n"
            "Only report truly new patterns - suppress known recurring errors to avoid noise."
        ),
        "trigger_message": "Scan error logs for new ERROR/CRITICAL patterns since last check.",
        "model": None, "provider": None,
        "tools": ["read_file", "shell_command", "memory_recall", "memory_store"],
        "max_steps": 7,
        "run_interval_seconds": 900,     # 15 min
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": None,
        "project_id_name": "EVA Backend v5",
        "last_run_hours_ago": 0,
        "failure_count": 2,
    },
    {
        "name": "weekly-metrics-digest",
        "description": "Runs every Sunday evening to compile a full weekly metrics digest: API usage, token consumption, execution counts, error rates, and project activity.",
        "system_prompt": (
            "You compile weekly engineering metrics. Each Sunday:\n"
            "1. Query execution_metrics for the past 7 days: count, avg duration, error rate, top models\n"
            "2. Summarise token consumption by provider\n"
            "3. List the most active projects and conversations\n"
            "4. Identify any daemons that failed more than 3 times\n"
            "5. Generate a Markdown digest report\n"
            "6. Store the report in agent memory under 'weekly_digest_YYYY-WW'\n\n"
            "Keep the report scannable: use tables for numbers, bullet points for insights."
        ),
        "trigger_message": "Compile this week's metrics digest and store it in memory.",
        "model": None, "provider": None,
        "tools": ["shell_command", "memory_recall", "memory_store", "read_file"],
        "max_steps": 12,
        "run_interval_seconds": 604800,  # weekly
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": None,
        "project_id_name": "Customer Analytics Dashboard",
        "last_run_hours_ago": 7 * 24,
        "failure_count": 0,
    },
    {
        "name": "dependency-update-checker",
        "description": "Daily check for outdated Python and Node.js dependencies across all projects. Reports packages with security advisories as HIGH priority.",
        "system_prompt": (
            "You check for dependency updates. On each run:\n"
            "1. Run `pip list --outdated` for Python dependencies\n"
            "2. Run `npm outdated` for Node.js dependencies\n"
            "3. Run `safety check` to identify packages with known CVEs\n"
            "4. Categorise findings: SECURITY (CVE), MAJOR (breaking), MINOR (safe to update)\n"
            "5. Store a prioritised update list in memory\n\n"
            "Focus on SECURITY findings first - these need immediate attention.\n"
            "Skip internal packages and packages pinned for compatibility reasons."
        ),
        "trigger_message": "Check all dependencies for outdated versions and security advisories.",
        "model": None, "provider": None,
        "tools": ["shell_command", "web_search", "memory_store", "memory_recall"],
        "max_steps": 8,
        "run_interval_seconds": 86400,
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": None,
        "project_id_name": "Security Audit & Hardening 2026",
        "last_run_hours_ago": 20,
        "failure_count": 0,
    },
    {
        "name": "api-health-monitor",
        "description": "Checks critical API endpoints every 5 minutes. Tracks response times, detects degradation, and stores alert history in agent memory.",
        "system_prompt": (
            "You monitor API health. On each run:\n"
            "1. Send HTTP GET to each endpoint in the health check list\n"
            "2. Record response time and HTTP status code\n"
            "3. Flag any endpoint with: status != 2xx, response time >2000ms, or 3+ consecutive failures\n"
            "4. Update the health status in memory (key: 'api_health_status')\n"
            "5. If status changed from healthy to degraded or down, create a new alert entry\n\n"
            "Endpoints to monitor:\n"
            "- GET /health (liveness)\n- GET /health/ready (readiness)\n"
            "- GET /providers (should return 200)\n- GET /session (should return 200 with valid token)"
        ),
        "trigger_message": "Run health checks on all monitored API endpoints.",
        "model": None, "provider": None,
        "tools": ["http_get", "memory_recall", "memory_store"],
        "max_steps": 5,
        "run_interval_seconds": 300,     # 5 min
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": None,
        "project_id_name": "EVA Backend v5",
        "last_run_hours_ago": 0,
        "failure_count": 1,
    },
    {
        "name": "code-review-assistant",
        "description": "Triggered after each completed execution. Reviews any newly written files for code quality, test coverage gaps, and style consistency.",
        "system_prompt": (
            "You perform automated code reviews on newly written files. When triggered:\n"
            "1. Identify files written in the most recent execution (check agent memory)\n"
            "2. For each Python file: run ruff and check for type annotation coverage\n"
            "3. For each TypeScript/React file: check for missing prop types and key props\n"
            "4. Flag any function >50 lines without a docstring\n"
            "5. Check test coverage: does a corresponding test file exist?\n"
            "6. Write a brief review comment to memory under 'code_review_{execution_id}'\n\n"
            "Be constructive, not critical. Focus on impact > 5 minute fix time only."
        ),
        "trigger_message": "Review code written in the most recent execution for quality and coverage.",
        "model": None, "provider": None,
        "tools": ["read_file", "list_files", "shell_command", "memory_recall", "memory_store"],
        "max_steps": 10,
        "run_interval_seconds": None,    # event-driven only
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": ["execution.completed"],
        "project_id_name": "EVA Backend v5",
        "last_run_hours_ago": 1,
        "failure_count": 0,
    },
    {
        "name": "customer-feedback-analyzer",
        "description": "Every 6 hours, fetches new customer support tickets and NPS survey responses, performs sentiment analysis, and surfaces emerging themes.",
        "system_prompt": (
            "You analyse customer feedback. On each run:\n"
            "1. Fetch recent support tickets from the API (last 6 hours)\n"
            "2. Classify sentiment: positive / neutral / negative\n"
            "3. Extract the top 3 themes from negative tickets (bug report, feature request, confusion)\n"
            "4. Compare theme frequency with the previous run - flag newly emerging themes\n"
            "5. Update the feedback summary in memory: 'customer_feedback_summary'\n\n"
            "Focus on actionable patterns. If negative tickets spike >20% vs baseline, flag as urgent."
        ),
        "trigger_message": "Analyse customer feedback from the past 6 hours and update the themes summary.",
        "model": None, "provider": None,
        "tools": ["http_get", "web_search", "memory_recall", "memory_store"],
        "max_steps": 8,
        "run_interval_seconds": 21600,   # 6h
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": None,
        "project_id_name": "Customer Analytics Dashboard",
        "last_run_hours_ago": 5,
        "failure_count": 0,
    },
    {
        "name": "security-vulnerability-scanner",
        "description": "Daily scan of all Python packages and Docker base images for known CVEs. Aggregates findings and stores a prioritised remediation list.",
        "system_prompt": (
            "You run daily security scans. On each run:\n"
            "1. Run `safety check --json` on requirements.txt\n"
            "2. Run `trivy image --format json` on the production Docker image\n"
            "3. Cross-reference findings with NVD for CVSS scores\n"
            "4. Filter to CVEs with CVSS ≥ 7.0 (HIGH and CRITICAL only)\n"
            "5. Group by affected package with fix version and patch availability\n"
            "6. Store the remediation plan in memory: 'vuln_scan_{date}'\n\n"
            "Always include the CVE ID, CVSS score, affected version, and fixed-in version."
        ),
        "trigger_message": "Run security vulnerability scan on all packages and Docker images.",
        "model": None, "provider": None,
        "tools": ["shell_command", "web_search", "memory_recall", "memory_store", "read_file"],
        "max_steps": 10,
        "run_interval_seconds": 86400,
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": None,
        "project_id_name": "Security Audit & Hardening 2026",
        "last_run_hours_ago": 23,
        "failure_count": 0,
    },
    {
        "name": "deployment-notifier",
        "description": "Triggered after each successful main-branch execution. Checks if a Docker image was built, summarises the changes, and posts a deployment notification to memory.",
        "system_prompt": (
            "You handle post-deployment notifications. When triggered after an execution:\n"
            "1. Check if a Docker image was built or a git commit was made in this execution\n"
            "2. If yes: retrieve the commit message and list of changed files from memory\n"
            "3. Compose a deployment notification:\n"
            "   - Service name and version\n   - Summary of changes (3 bullet points max)\n"
            "   - Any breaking changes flagged\n   - Rollback procedure if needed\n"
            "4. Store the notification in memory: 'deployment_notification_{timestamp}'\n\n"
            "Keep the notification human-readable. Assume the audience is non-technical ops staff."
        ),
        "trigger_message": "Check if a deployment occurred in the last execution and send a notification if so.",
        "model": None, "provider": None,
        "tools": ["memory_recall", "memory_store", "shell_command"],
        "max_steps": 5,
        "run_interval_seconds": None,    # event-driven only
        "enabled": True,
        "lifecycle_status": "idle",
        "event_subscriptions": ["execution.completed"],
        "project_id_name": "DevOps Infrastructure Overhaul",
        "last_run_hours_ago": 3,
        "failure_count": 0,
    },
]


def seed_daemons(conn, user_id: str) -> tuple[int, list[tuple]]:
    """Returns (inserted_count, [(daemon_id, daemon_name), ...]) for webhook seeding."""
    already = count(conn, "daemon_configs",
                    "user_id = ? AND name = 'daily-standup-reporter'", (user_id,))
    if already > 0:
        warn("seed daemon_configs already present - skipping")
        return 0, []

    inserted = 0
    daemon_id_map = []

    for d in DAEMONS:
        did = new_id()
        proj_id = get_project_id(conn, user_id, d["project_id_name"]) if d["project_id_name"] else None
        last_run = ts(hours_ago=d["last_run_hours_ago"]) if d["last_run_hours_ago"] is not None else None
        next_run = None
        if d["run_interval_seconds"] and last_run:
            from datetime import datetime, timedelta, timezone
            lr = datetime.now(timezone.utc) - timedelta(hours=d["last_run_hours_ago"])
            nr = lr + timedelta(seconds=d["run_interval_seconds"])
            next_run = nr.isoformat()

        conn.execute(
            """
            INSERT OR IGNORE INTO daemon_configs (
                id, user_id, name, description, system_prompt, trigger_message,
                model, provider, tools, max_steps,
                run_interval_seconds, enabled,
                last_run_at, next_run_at,
                lifecycle_status, current_execution_id,
                retry_on_failure, max_retries, retry_delay_seconds,
                failure_count, last_error, last_failure_at,
                event_subscriptions, project_id,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                did, user_id, d["name"], d["description"], d["system_prompt"],
                d["trigger_message"],
                d["model"], d["provider"],
                jdump(d["tools"]), d["max_steps"],
                d["run_interval_seconds"], 1 if d["enabled"] else 0,
                last_run, next_run,
                d["lifecycle_status"], None,
                1, 3, 60,                # retry_on_failure, max_retries, retry_delay_seconds
                d["failure_count"],
                "Connection timeout after 300s" if d["failure_count"] > 0 else None,
                ts(hours_ago=d["failure_count"] * 2) if d["failure_count"] > 0 else None,
                jdump(d["event_subscriptions"]),
                proj_id,
                ts(days_ago=20), ts(days_ago=1),
            ),
        )
        daemon_id_map.append((did, d["name"]))
        inserted += 1
        interval = f"{d['run_interval_seconds']}s" if d["run_interval_seconds"] else "event-driven"
        ok(f"daemon: {d['name']!r}  [{interval}]")

    conn.commit()
    return inserted, daemon_id_map


# ─────────────────────────────────────────────────────────────────────────────
# Daemon Webhooks
# ─────────────────────────────────────────────────────────────────────────────

WEBHOOK_DAEMONS = ["github-pr-monitor", "api-health-monitor", "deployment-notifier"]


def seed_webhooks(conn, user_id: str, daemon_id_map: list[tuple]) -> int:
    name_to_id = {name: did for did, name in daemon_id_map}
    inserted = 0

    for daemon_name in WEBHOOK_DAEMONS:
        daemon_id = name_to_id.get(daemon_name)
        if not daemon_id:
            warn(f"  daemon '{daemon_name}' not found - skipping webhook")
            continue

        existing = conn.execute(
            "SELECT id FROM daemon_webhooks WHERE daemon_id=?", (daemon_id,)
        ).fetchone()
        if existing:
            warn(f"  webhook for '{daemon_name}' already exists - skipping")
            continue

        import uuid as _uuid
        secret = str(_uuid.uuid4())
        conn.execute(
            """
            INSERT INTO daemon_webhooks
                (id, daemon_id, user_id, daemon_name, secret_token, description,
                 created_at, last_triggered_at, trigger_count)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                new_id(), daemon_id, user_id, daemon_name, secret,
                f"External trigger webhook for {daemon_name}",
                ts(days_ago=15),
                ts(hours_ago=6),
                3 if daemon_name == "github-pr-monitor" else 1,
            ),
        )
        inserted += 1
        ok(f"webhook: {daemon_name!r}  (token: {secret[:8]}...)")

    conn.commit()
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Daemon Run History
# Seeded here (after daemon configs exist) so each execution_metrics row can
# use the real daemon_configs.id as conversation_id — exactly as the live
# backend does.  FK enforcement is temporarily disabled because daemon.id is
# NOT a conversations row; the real backend runs SQLite with FK enforcement
# off by default.
# ─────────────────────────────────────────────────────────────────────────────

DAEMON_RUN_HISTORY = {
    "daily-standup-reporter": [
        {"status": "completed", "duration_ms": 12340, "steps": 6, "days_ago": 1,  "hours_ago": 8,  "p_tok": 3210, "c_tok": 1450, "tools": [{"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 1200, "failure_count": 0, "success_count": 2}, {"tool_name": "memory_store", "call_count": 1, "avg_latency_ms": 22, "failure_count": 0, "success_count": 1}]},
        {"status": "completed", "duration_ms": 11890, "steps": 5, "days_ago": 2,  "hours_ago": 8,  "p_tok": 3105, "c_tok": 1320, "tools": [{"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 1150, "failure_count": 0, "success_count": 2}, {"tool_name": "memory_store", "call_count": 1, "avg_latency_ms": 19, "failure_count": 0, "success_count": 1}]},
        {"status": "completed", "duration_ms": 13200, "steps": 7, "days_ago": 3,  "hours_ago": 8,  "p_tok": 3450, "c_tok": 1670, "tools": [{"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 1340, "failure_count": 0, "success_count": 3}, {"tool_name": "http_get", "call_count": 1, "avg_latency_ms": 456, "failure_count": 0, "success_count": 1}]},
        {"status": "failed",    "duration_ms": 4500,  "steps": 2, "days_ago": 4,  "hours_ago": 8,  "p_tok": 980,  "c_tok": 230,  "tools": [{"tool_name": "shell_command", "call_count": 1, "avg_latency_ms": 4200, "failure_count": 1, "success_count": 0}]},
        {"status": "completed", "duration_ms": 11200, "steps": 5, "days_ago": 5,  "hours_ago": 8,  "p_tok": 2980, "c_tok": 1280, "tools": [{"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 1100, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 12780, "steps": 6, "days_ago": 7,  "hours_ago": 8,  "p_tok": 3320, "c_tok": 1510, "tools": [{"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 1230, "failure_count": 0, "success_count": 2}]},
    ],
    "github-pr-monitor": [
        {"status": "completed", "duration_ms": 8450,  "steps": 4, "days_ago": 0,  "hours_ago": 0,  "p_tok": 2100, "c_tok": 890,  "tools": [{"tool_name": "http_get", "call_count": 2, "avg_latency_ms": 670, "failure_count": 0, "success_count": 2}, {"tool_name": "memory_store", "call_count": 1, "avg_latency_ms": 18, "failure_count": 0, "success_count": 1}]},
        {"status": "completed", "duration_ms": 7820,  "steps": 4, "days_ago": 0,  "hours_ago": 1,  "p_tok": 1980, "c_tok": 820,  "tools": [{"tool_name": "http_get", "call_count": 2, "avg_latency_ms": 590, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 9120,  "steps": 5, "days_ago": 0,  "hours_ago": 2,  "p_tok": 2340, "c_tok": 1050, "tools": [{"tool_name": "http_get", "call_count": 3, "avg_latency_ms": 720, "failure_count": 0, "success_count": 3}]},
        {"status": "failed",    "duration_ms": 30200, "steps": 3, "days_ago": 0,  "hours_ago": 4,  "p_tok": 1200, "c_tok": 340,  "tools": [{"tool_name": "http_get", "call_count": 3, "avg_latency_ms": 9800, "failure_count": 2, "success_count": 1}]},
        {"status": "completed", "duration_ms": 8100,  "steps": 4, "days_ago": 1,  "hours_ago": 0,  "p_tok": 2050, "c_tok": 870,  "tools": [{"tool_name": "http_get", "call_count": 2, "avg_latency_ms": 645, "failure_count": 0, "success_count": 2}]},
    ],
    "error-log-watcher": [
        {"status": "completed", "duration_ms": 5230,  "steps": 4, "days_ago": 0,  "hours_ago": 0,  "p_tok": 1870, "c_tok": 720,  "tools": [{"tool_name": "read_file", "call_count": 1, "avg_latency_ms": 45, "failure_count": 0, "success_count": 1}, {"tool_name": "memory_store", "call_count": 1, "avg_latency_ms": 21, "failure_count": 0, "success_count": 1}]},
        {"status": "completed", "duration_ms": 6100,  "steps": 5, "days_ago": 0,  "hours_ago": 1,  "p_tok": 2340, "c_tok": 980,  "tools": [{"tool_name": "read_file", "call_count": 1, "avg_latency_ms": 52, "failure_count": 0, "success_count": 1}, {"tool_name": "shell_command", "call_count": 1, "avg_latency_ms": 890, "failure_count": 0, "success_count": 1}]},
        {"status": "failed",    "duration_ms": 2100,  "steps": 2, "days_ago": 0,  "hours_ago": 3,  "p_tok": 780,  "c_tok": 210,  "tools": [{"tool_name": "read_file", "call_count": 1, "avg_latency_ms": 1980, "failure_count": 1, "success_count": 0}]},
        {"status": "completed", "duration_ms": 4890,  "steps": 4, "days_ago": 1,  "hours_ago": 0,  "p_tok": 1720, "c_tok": 680,  "tools": [{"tool_name": "read_file", "call_count": 1, "avg_latency_ms": 38, "failure_count": 0, "success_count": 1}]},
    ],
    "weekly-metrics-digest": [
        {"status": "completed", "duration_ms": 45230, "steps": 12, "days_ago": 7,  "hours_ago": 0,  "p_tok": 8934, "c_tok": 4123, "tools": [{"tool_name": "shell_command", "call_count": 4, "avg_latency_ms": 2345, "failure_count": 0, "success_count": 4}, {"tool_name": "read_file", "call_count": 6, "avg_latency_ms": 34, "failure_count": 0, "success_count": 6}, {"tool_name": "memory_store", "call_count": 3, "avg_latency_ms": 21, "failure_count": 0, "success_count": 3}]},
        {"status": "completed", "duration_ms": 42100, "steps": 11, "days_ago": 14, "hours_ago": 0,  "p_tok": 8120, "c_tok": 3890, "tools": [{"tool_name": "shell_command", "call_count": 4, "avg_latency_ms": 2100, "failure_count": 0, "success_count": 4}, {"tool_name": "read_file", "call_count": 5, "avg_latency_ms": 31, "failure_count": 0, "success_count": 5}]},
        {"status": "failed",    "duration_ms": 15000, "steps": 4,  "days_ago": 21, "hours_ago": 0,  "p_tok": 3400, "c_tok": 890,  "tools": [{"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 6200, "failure_count": 1, "success_count": 1}]},
        {"status": "completed", "duration_ms": 39800, "steps": 10, "days_ago": 28, "hours_ago": 0,  "p_tok": 7650, "c_tok": 3540, "tools": [{"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 1980, "failure_count": 0, "success_count": 3}]},
    ],
    "dependency-update-checker": [
        {"status": "completed", "duration_ms": 22100, "steps": 7, "days_ago": 1,  "hours_ago": 4,  "p_tok": 4320, "c_tok": 1890, "tools": [{"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 3400, "failure_count": 0, "success_count": 3}, {"tool_name": "web_search", "call_count": 2, "avg_latency_ms": 890, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 19800, "steps": 6, "days_ago": 2,  "hours_ago": 4,  "p_tok": 3980, "c_tok": 1720, "tools": [{"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 3100, "failure_count": 0, "success_count": 3}]},
        {"status": "completed", "duration_ms": 21500, "steps": 8, "days_ago": 3,  "hours_ago": 4,  "p_tok": 4560, "c_tok": 2010, "tools": [{"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 3250, "failure_count": 0, "success_count": 3}, {"tool_name": "web_search", "call_count": 3, "avg_latency_ms": 920, "failure_count": 0, "success_count": 3}]},
        {"status": "completed", "duration_ms": 18900, "steps": 6, "days_ago": 5,  "hours_ago": 4,  "p_tok": 3780, "c_tok": 1640, "tools": [{"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 3050, "failure_count": 0, "success_count": 2}]},
    ],
    "api-health-monitor": [
        {"status": "completed", "duration_ms": 1890,  "steps": 3, "days_ago": 0,  "hours_ago": 0,  "p_tok": 890,  "c_tok": 310,  "tools": [{"tool_name": "http_get", "call_count": 3, "avg_latency_ms": 245, "failure_count": 0, "success_count": 3}]},
        {"status": "completed", "duration_ms": 2100,  "steps": 3, "days_ago": 0,  "hours_ago": 0,  "p_tok": 910,  "c_tok": 320,  "tools": [{"tool_name": "http_get", "call_count": 3, "avg_latency_ms": 267, "failure_count": 0, "success_count": 3}]},
        {"status": "failed",    "duration_ms": 300000,"steps": 5, "days_ago": 1,  "hours_ago": 3,  "p_tok": 1234, "c_tok": 456,  "tools": [{"tool_name": "http_get", "call_count": 5, "avg_latency_ms": 59876, "failure_count": 3, "success_count": 2}]},
        {"status": "completed", "duration_ms": 1750,  "steps": 3, "days_ago": 0,  "hours_ago": 1,  "p_tok": 870,  "c_tok": 300,  "tools": [{"tool_name": "http_get", "call_count": 3, "avg_latency_ms": 220, "failure_count": 0, "success_count": 3}]},
        {"status": "completed", "duration_ms": 2340,  "steps": 3, "days_ago": 0,  "hours_ago": 2,  "p_tok": 930,  "c_tok": 340,  "tools": [{"tool_name": "http_get", "call_count": 3, "avg_latency_ms": 290, "failure_count": 0, "success_count": 3}]},
        {"status": "completed", "duration_ms": 1980,  "steps": 3, "days_ago": 0,  "hours_ago": 3,  "p_tok": 895,  "c_tok": 315,  "tools": [{"tool_name": "http_get", "call_count": 3, "avg_latency_ms": 255, "failure_count": 0, "success_count": 3}]},
    ],
    "code-review-assistant": [
        {"status": "completed", "duration_ms": 14200, "steps": 8, "days_ago": 1,  "hours_ago": 2,  "p_tok": 3450, "c_tok": 1780, "tools": [{"tool_name": "read_file", "call_count": 4, "avg_latency_ms": 38, "failure_count": 0, "success_count": 4}, {"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 1234, "failure_count": 0, "success_count": 2}, {"tool_name": "memory_store", "call_count": 1, "avg_latency_ms": 22, "failure_count": 0, "success_count": 1}]},
        {"status": "completed", "duration_ms": 11800, "steps": 6, "days_ago": 2,  "hours_ago": 4,  "p_tok": 2980, "c_tok": 1450, "tools": [{"tool_name": "read_file", "call_count": 3, "avg_latency_ms": 35, "failure_count": 0, "success_count": 3}, {"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 1100, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 16500, "steps": 9, "days_ago": 5,  "hours_ago": 0,  "p_tok": 4120, "c_tok": 2100, "tools": [{"tool_name": "read_file", "call_count": 5, "avg_latency_ms": 41, "failure_count": 0, "success_count": 5}, {"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 1320, "failure_count": 0, "success_count": 3}]},
        {"status": "failed",    "duration_ms": 3200,  "steps": 2, "days_ago": 8,  "hours_ago": 0,  "p_tok": 1100, "c_tok": 290,  "tools": [{"tool_name": "read_file", "call_count": 2, "avg_latency_ms": 1450, "failure_count": 1, "success_count": 1}]},
    ],
    "customer-feedback-analyzer": [
        {"status": "completed", "duration_ms": 18900, "steps": 7, "days_ago": 0,  "hours_ago": 5,  "p_tok": 4560, "c_tok": 2230, "tools": [{"tool_name": "http_get", "call_count": 2, "avg_latency_ms": 567, "failure_count": 0, "success_count": 2}, {"tool_name": "memory_store", "call_count": 2, "avg_latency_ms": 24, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 17200, "steps": 6, "days_ago": 1,  "hours_ago": 5,  "p_tok": 4120, "c_tok": 2010, "tools": [{"tool_name": "http_get", "call_count": 2, "avg_latency_ms": 534, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 21300, "steps": 8, "days_ago": 2,  "hours_ago": 5,  "p_tok": 5230, "c_tok": 2560, "tools": [{"tool_name": "http_get", "call_count": 3, "avg_latency_ms": 612, "failure_count": 0, "success_count": 3}, {"tool_name": "web_search", "call_count": 1, "avg_latency_ms": 890, "failure_count": 0, "success_count": 1}]},
        {"status": "completed", "duration_ms": 16800, "steps": 6, "days_ago": 3,  "hours_ago": 5,  "p_tok": 3980, "c_tok": 1890, "tools": [{"tool_name": "http_get", "call_count": 2, "avg_latency_ms": 545, "failure_count": 0, "success_count": 2}]},
    ],
    "security-vulnerability-scanner": [
        {"status": "completed", "duration_ms": 23450, "steps": 9, "days_ago": 1,  "hours_ago": 0,  "p_tok": 5678, "c_tok": 2890, "tools": [{"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 4560, "failure_count": 0, "success_count": 3}, {"tool_name": "web_search", "call_count": 2, "avg_latency_ms": 890, "failure_count": 0, "success_count": 2}, {"tool_name": "memory_store", "call_count": 2, "avg_latency_ms": 20, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 21800, "steps": 8, "days_ago": 2,  "hours_ago": 0,  "p_tok": 5230, "c_tok": 2670, "tools": [{"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 4200, "failure_count": 0, "success_count": 3}, {"tool_name": "web_search", "call_count": 2, "avg_latency_ms": 870, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 25100, "steps": 10,"days_ago": 5,  "hours_ago": 0,  "p_tok": 6120, "c_tok": 3100, "tools": [{"tool_name": "shell_command", "call_count": 4, "avg_latency_ms": 4780, "failure_count": 0, "success_count": 4}, {"tool_name": "web_search", "call_count": 3, "avg_latency_ms": 910, "failure_count": 0, "success_count": 3}]},
        {"status": "failed",    "duration_ms": 8900,  "steps": 3, "days_ago": 10, "hours_ago": 0,  "p_tok": 2100, "c_tok": 560,  "tools": [{"tool_name": "shell_command", "call_count": 2, "avg_latency_ms": 3900, "failure_count": 1, "success_count": 1}]},
        {"status": "completed", "duration_ms": 22300, "steps": 9, "days_ago": 15, "hours_ago": 0,  "p_tok": 5450, "c_tok": 2780, "tools": [{"tool_name": "shell_command", "call_count": 3, "avg_latency_ms": 4300, "failure_count": 0, "success_count": 3}]},
    ],
    "deployment-notifier": [
        {"status": "completed", "duration_ms": 6780,  "steps": 4, "days_ago": 1,  "hours_ago": 14, "p_tok": 1890, "c_tok": 980,  "tools": [{"tool_name": "memory_recall", "call_count": 2, "avg_latency_ms": 19, "failure_count": 0, "success_count": 2}, {"tool_name": "memory_store", "call_count": 1, "avg_latency_ms": 22, "failure_count": 0, "success_count": 1}]},
        {"status": "completed", "duration_ms": 5900,  "steps": 3, "days_ago": 3,  "hours_ago": 8,  "p_tok": 1650, "c_tok": 870,  "tools": [{"tool_name": "memory_recall", "call_count": 2, "avg_latency_ms": 17, "failure_count": 0, "success_count": 2}]},
        {"status": "completed", "duration_ms": 7200,  "steps": 4, "days_ago": 5,  "hours_ago": 10, "p_tok": 2010, "c_tok": 1050, "tools": [{"tool_name": "memory_recall", "call_count": 2, "avg_latency_ms": 21, "failure_count": 0, "success_count": 2}, {"tool_name": "shell_command", "call_count": 1, "avg_latency_ms": 670, "failure_count": 0, "success_count": 1}]},
    ],
}


def seed_daemon_run_history(conn, user_id: str) -> int:
    """
    Insert execution_metrics rows for past daemon runs using each daemon's
    own config ID as conversation_id — matching real backend behaviour.
    No conversations rows are created; FK enforcement is disabled for these
    inserts so the pseudo-conversation-id pattern works in SQLite.
    """
    # Idempotency: check execution_metrics for known daemon history row
    already = count(conn, "execution_metrics",
                    "user_id = ? AND triggered_by = 'daemon' AND daemon_name = 'daily-standup-reporter'",
                    (user_id,))
    if already > 0:
        warn("daemon run history already present - skipping")
        return 0

    # Build daemon_name → daemon.id map from the configs we just seeded
    daemon_id_map = {}
    for row in conn.execute(
        "SELECT id, name FROM daemon_configs WHERE user_id = ?", (user_id,)
    ).fetchall():
        daemon_id_map[row["name"]] = row["id"]

    if not daemon_id_map:
        warn("no daemon configs found - cannot seed run history")
        return 0

    # Disable FK enforcement: conversation_id = daemon.id (not a real conversation)
    conn.execute("PRAGMA foreign_keys=OFF")

    inserted = 0
    for daemon_name, runs in DAEMON_RUN_HISTORY.items():
        daemon_id = daemon_id_map.get(daemon_name)
        if not daemon_id:
            warn(f"daemon '{daemon_name}' not in daemon_configs - skipping run history")
            continue

        for run in runs:
            eid = new_id()
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
                    eid, daemon_id, user_id,
                    run["status"],
                    "success" if run["status"] == "completed" else (
                        "TimeoutError" if run["duration_ms"] > 60000 else "RuntimeError"
                    ),
                    run["duration_ms"],
                    "qwen3:8b", "ollama",
                    run["p_tok"], run["c_tok"], 0,
                    run["steps"], 0, 0.0,
                    jdump(run["tools"]),
                    "daemon", daemon_name, None,
                    None, None,
                    ts(days_ago=run["days_ago"], hours_ago=run["hours_ago"]),
                    ts(days_ago=run["days_ago"], hours_ago=run["hours_ago"],
                       seconds_ago=-(run["duration_ms"] // 1000)),
                ),
            )
            inserted += 1

        total_runs = len(runs)
        completed  = sum(1 for r in runs if r["status"] == "completed")
        failed     = sum(1 for r in runs if r["status"] == "failed")
        ok(f"daemon history: {daemon_name!r}  [{total_runs} runs: {completed} ok, {failed} failed]")

    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    return inserted


# ── Main seeder ────────────────────────────────────────────────────────────

def run(db_path=None) -> dict:
    head("04 : Custom Subagents & Daemons")
    conn = connect(find_db_path(db_path))
    user_id = get_user_id(conn)

    subagents = seed_subagents(conn, user_id)
    daemons, daemon_id_map = seed_daemons(conn, user_id)
    webhooks = seed_webhooks(conn, user_id, daemon_id_map)
    daemon_runs = seed_daemon_run_history(conn, user_id)

    return {"subagents": subagents, "daemons": daemons, "webhooks": webhooks, "daemon_run_history": daemon_runs}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db-path", default=None)
    args = p.parse_args()
    result = run(args.db_path)
    print(f"\n  Done: {result}")
