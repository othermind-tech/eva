# EVA — Test Database Seed Scripts

Populates `eva.db` with realistic dummy data for frontend E2E testing.
No changes to production code — these scripts only **read** from the codebase
and **write** directly to the database via the sqlite3 stdlib module.

---

## Prerequisites

1. The EVA backend must have been started **at least once** and a user must
   have registered/logged in. The seeder uses the first user it finds in the
   `users` table.
2. Python 3.10+ (same env as the backend — no extra packages required).

---

## Quick Start

```bash
# From the project root:
python tests/seed_data/run_all.py
```

`eva.db` is auto-discovered by walking up from the script location.
Supply `--db-path` if your DB is elsewhere.

---

## Scripts

| Script | What it seeds |
|--------|--------------|
| `seed_01_users_projects.py` | `user_preferences` (1 row) + `projects` (10 rows) |
| `seed_02_conversations.py`  | `conversations` (12) + `messages` (~80) + `tool_calls` (~20) + `conversation_summaries` (5) |
| `seed_03_executions.py`     | `execution_metrics` (15) + `execution_plans` (5 with full step detail) |
| `seed_04_agents_daemons.py` | `subagent_configs` (5 custom) + `daemon_configs` (10) + `daemon_webhooks` (3) |
| `seed_05_memory_mcp.py`     | `user_memory_facts` (20) + `agent_memory_entries` (15) + `mcp_configs` (3) + `mcp_tool_meta` (~19) |

Each script is **idempotent** — re-running it skips tables that already have rows
for this user.

---

## Options

```
python run_all.py --help

  --db-path PATH    Path to eva.db (default: auto-discovered)
  --reset           Delete existing seed data then re-seed (prompts for confirmation)
  --dry-run         Show what would be done without touching the DB
  --only N [N ...]  Run only specific scripts by number, e.g. --only 02 04
```

### Run a single script

Each domain script is also standalone:

```bash
python tests/seed_data/seed_02_conversations.py --db-path eva.db
python tests/seed_data/seed_04_agents_daemons.py
```

### Re-seed after wiping the DB

```bash
# 1. Start backend (creates fresh schema + seeds default agents)
# 2. Register / log in once
# 3. Run the full seed
python tests/seed_data/run_all.py

# Or reset + re-seed without wiping the DB:
python tests/seed_data/run_all.py --reset
```

---

## What's seeded

### Projects (10)
Across active / paused / archived statuses:
- EVA Backend v5, Customer Analytics Dashboard, ML Training Pipeline
- REST API Documentation v3, E-Commerce Platform v3, Security Audit & Hardening 2026
- Marketing Automation Suite, Customer Mobile App MVP, Data Warehouse Migration, DevOps Infrastructure Overhaul

### Conversations (12)
- Standalone technical Q&A (Python async, Transformer architecture, React 18, Sprint planning)
- Project-linked (auth refactor → EVA Backend, payment tests → E-Commerce, CI/CD → DevOps, etc.)
- Rich message content with tool calls, code blocks, and realistic assistant responses

### Execution Metrics (15)
- Mix of `completed`, `failed`, `cancelled` statuses
- Conversation-triggered, daemon-triggered, and subagent executions
- Per-tool breakdowns with latencies and call counts

### Execution Plans (5)
- Full Plan→Execute→Verify step detail with `completed`, `failed`, `skipped` step statuses
- Linked to matching `execution_metrics` rows

### Daemons (10)
Ranging from 5-minute health checks to weekly digest generators, plus event-driven daemons:
- `daily-standup-reporter`, `github-pr-monitor`, `error-log-watcher`
- `weekly-metrics-digest`, `dependency-update-checker`, `api-health-monitor`
- `code-review-assistant`, `customer-feedback-analyzer`
- `security-vulnerability-scanner`, `deployment-notifier`

### Custom Subagents (5)
Non-default, user-created subagents with scoped tool lists:
- `sql-query-optimizer`, `tech-writer`, `security-reviewer`, `performance-profiler`, `devops-helper`

### Memory (35 rows)
- 20 `user_memory_facts`: preferences, skills, working context
- 15 `agent_memory_entries`: two scope keys (`eva-backend-dev`, `ecommerce-refactor`)
  covering decisions, errors, successes, artifacts, and state notes

### MCP Configs (3)
- `github-mcp` (SSE, enabled) — 5 tools
- `filesystem-mcp` (stdio, enabled) — 5 tools
- `slack-mcp` (HTTP, disabled) — 4 tools + OAuth pending note

---

## Notes

- **Default subagents** (deep-research, coding-agent, etc.) are seeded by the backend
  on first login via `POST /agents/seed-defaults`. The seed scripts add only *custom*
  subagents and will not conflict with defaults.
- Timestamps are spread realistically over the past 45 days.
- All IDs are UUID4 strings generated fresh on each run — safe to run multiple times
  on different users or fresh DBs.
- The `is_default` column on `subagent_configs` is detected via `PRAGMA table_info`
  and included only if present (handles both migrated and fresh DBs gracefully).
