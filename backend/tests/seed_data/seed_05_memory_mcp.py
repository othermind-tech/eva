# tests/seed_data/seed_05_memory_mcp.py
"""
Seed: User Memory Facts + Agent Memory Entries + MCP Configs
─────────────────────────────────────────────────────────────
20 UserMemoryFacts covering preferences, skills, and working style.
15 AgentMemoryEntries across two scope keys reflecting real dev-session history.
3 MCP server configurations with tool metadata.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import (
    connect, find_db_path, get_user_id, jdump, new_id, ok, warn,
    info, head, count, ts,
)


# ─────────────────────────────────────────────────────────────────────────────
# User Memory Facts  (Scope 2 - persisted user preferences and skills)
# ─────────────────────────────────────────────────────────────────────────────

USER_MEMORY_FACTS = [
    # ── Preferences ──
    {"fact": "Prefers concise, direct responses. No filler phrases like 'Certainly!' or 'Great question!'.",
     "category": "preference", "slot_key": "user.response_style", "confidence": 1.0, "days_ago": 25},
    {"fact": "Wants working code, not pseudo-code. Always include full imports and runnable examples.",
     "category": "preference", "slot_key": "user.code_style", "confidence": 1.0, "days_ago": 25},
    {"fact": "Prefers TypeScript over plain JavaScript for all new frontend work.",
     "category": "preference", "slot_key": "user.lang_frontend", "confidence": 1.0, "days_ago": 20},
    {"fact": "Uses conventional commits format: feat(scope): message, fix(scope): message, etc.",
     "category": "preference", "slot_key": "user.git_convention", "confidence": 1.0, "days_ago": 22},
    {"fact": "Prefers pytest for Python testing. Uses pytest-asyncio for async tests and factories for fixtures.",
     "category": "preference", "slot_key": "user.test_framework", "confidence": 1.0, "days_ago": 18},
    {"fact": "Favours PostgreSQL for production, SQLite for development and testing environments.",
     "category": "preference", "slot_key": "user.db_preference", "confidence": 0.95, "days_ago": 15},
    {"fact": "Wants error handling that raises specific exception types - not bare `except Exception` catches.",
     "category": "preference", "slot_key": "user.error_handling", "confidence": 1.0, "days_ago": 12},
    {"fact": "Prefers Tailwind CSS over CSS modules or styled-components for frontend styling.",
     "category": "preference", "slot_key": "user.css_preference", "confidence": 0.9, "days_ago": 10},

    # ── Skills ──
    {"fact": "Expert in Python 3.11+ including async/await, dataclasses, type hints, and the standard library.",
     "category": "skill", "slot_key": "user.skill", "confidence": 1.0, "days_ago": 30},
    {"fact": "Strong FastAPI knowledge - including dependency injection, background tasks, SSE, and OpenAPI docs.",
     "category": "skill", "slot_key": "user.skill", "confidence": 1.0, "days_ago": 28},
    {"fact": "Proficient with React 18, TypeScript, TanStack Query v5, and Zustand state management.",
     "category": "skill", "slot_key": "user.skill", "confidence": 1.0, "days_ago": 25},
    {"fact": "Familiar with PostgreSQL, SQLAlchemy (async), Alembic, and database performance optimisation.",
     "category": "skill", "slot_key": "user.skill", "confidence": 0.9, "days_ago": 20},
    {"fact": "Working knowledge of AWS (EKS, ECR, S3, IAM, Secrets Manager), Terraform, and GitHub Actions.",
     "category": "skill", "slot_key": "user.skill", "confidence": 0.85, "days_ago": 18},
    {"fact": "Experienced with PyTorch for model training and profiling; familiar with distributed training (DDP).",
     "category": "skill", "slot_key": "user.skill", "confidence": 0.8, "days_ago": 15},

    # ── Context / working style ──
    {"fact": "Currently building EVA - a self-hosted AI agent backend written in Python/FastAPI.",
     "category": "context", "slot_key": "user.current_project", "confidence": 1.0, "days_ago": 45},
    {"fact": "Works on macOS locally; deploys to AWS EKS (us-east-1). Uses conda for Python envs.",
     "category": "context", "slot_key": "user.environment", "confidence": 0.95, "days_ago": 40},
    {"fact": "Sole developer and only user on the EVA instance during active development phase.",
     "category": "context", "slot_key": "user.occupation", "confidence": 1.0, "days_ago": 35},
    {"fact": "Uses qwen3:8b (Ollama, local) as the default model. Falls back to Groq for speed-critical tasks.",
     "category": "context", "slot_key": "user.preferred_model", "confidence": 1.0, "days_ago": 30},

    # ── Misc ──
    {"fact": "When debugging, always asks for the actual error message and stack trace before suggesting solutions.",
     "category": "style", "slot_key": "user.debug_style", "confidence": 0.9, "days_ago": 8},
    {"fact": "Prefers incremental implementation: get one layer working correctly before moving to the next.",
     "category": "style", "slot_key": "user.coding_approach", "confidence": 0.95, "days_ago": 5},
]


def seed_user_memory(conn, user_id: str) -> int:
    existing = count(conn, "user_memory_facts", "user_id = ?", (user_id,))
    if existing >= 15:
        warn(f"user_memory_facts already has {existing} rows - skipping")
        return 0

    inserted = 0
    for f in USER_MEMORY_FACTS:
        conn.execute(
            """
            INSERT INTO user_memory_facts
                (id, user_id, fact, category, slot_key, source, confidence, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                new_id(), user_id, f["fact"], f["category"],
                f["slot_key"], "auto_extracted", f["confidence"],
                ts(days_ago=f["days_ago"]),
            ),
        )
        inserted += 1
        ok(f"memory_fact [{f['category']:12s}]: {f['fact'][:65]}...")

    conn.commit()
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Agent Memory Entries  (Scope 3 - cross-conversation mission memory)
# ─────────────────────────────────────────────────────────────────────────────

AGENT_MEMORY_ENTRIES = [
    # ── Scope: eva-backend-dev ──
    {"scope": "eva-backend-dev", "type": "decision",
     "content": "Chose SQLite + aiosqlite for local development DB. Rationale: zero infrastructure, easy reset, async support. Swap to PostgreSQL via DATABASE_URL env var for production.",
     "tags": ["database", "architecture"], "days_ago": 40},

    {"scope": "eva-backend-dev", "type": "decision",
     "content": "Authentication uses JWT access tokens (24h) + refresh tokens (30d) stored in auth_sessions table. Token rotation invalidates old refresh token on every use.",
     "tags": ["auth", "security"], "days_ago": 30},

    {"scope": "eva-backend-dev", "type": "decision",
     "content": "SSE streaming uses two auth paths: (1) Authorization: Bearer header for WebSocket-capable clients, (2) short-lived sse_token UUID for browser EventSource (B-008). sse_token TTL is 30 seconds.",
     "tags": ["auth", "sse", "streaming"], "days_ago": 28},

    {"scope": "eva-backend-dev", "type": "artifact",
     "content": "Default subagent YAMLs located at default_agents/sa-01 through sa-12. Each YAML defines: name, description, system_prompt, tools (scoped list), max_steps. Seeded via POST /agents/seed-defaults.",
     "tags": ["subagents", "seeding", "yaml"], "days_ago": 25},

    {"scope": "eva-backend-dev", "type": "success",
     "content": "Token rotation refresh endpoint (POST /auth/refresh) implemented and tested. All 3 existing auth tests still pass. Committed as a3f9c12.",
     "tags": ["auth", "testing", "git"], "days_ago": 22},

    {"scope": "eva-backend-dev", "type": "error",
     "content": "YAML fallback parser (_simple_yaml_parse in seeder.py) did not handle list items (- item). Fixed by adding list_item_re regex and peek-ahead loop. Bug caused tools:null on all seeded agents.",
     "tags": ["bug", "seeder", "yaml", "subagents"], "days_ago": 20},

    {"scope": "eva-backend-dev", "type": "state",
     "content": "Current sprint (V5.7): subagent tool scoping complete. is_default flag added to subagent_configs table via Alembic migration 0005. Reset + clone endpoints added to /agents API.",
     "tags": ["sprint", "progress", "subagents"], "days_ago": 15},

    {"scope": "eva-backend-dev", "type": "decision",
     "content": "is_default flag on subagent_configs: seeder sets True for YAML-backed agents; user-created agents default to False. PUT /agents/{name} blocks tool list changes on default agents (HTTP 400).",
     "tags": ["subagents", "api", "protection"], "days_ago": 14},

    {"scope": "eva-backend-dev", "type": "artifact",
     "content": "Frontend lives in the frontend/ folder of the repository. Vite proxy maps /api → http://127.0.0.1:8000. Auth state in Zustand authStore. Server state via TanStack Query v5. SSE via native EventSource.",
     "tags": ["frontend", "architecture"], "days_ago": 35},

    {"scope": "eva-backend-dev", "type": "note",
     "content": "Health endpoint GET /health/ready has get_current_auth dependency. Frontend healthApi.checkReady() catches all errors and returns {status:'degraded'} silently. The 401 interceptor still fires before the catch.",
     "tags": ["health", "auth", "frontend"], "days_ago": 5},

    # ── Scope: ecommerce-refactor ──
    {"scope": "ecommerce-refactor", "type": "decision",
     "content": "Chose Kafka (via Confluent Cloud) as the event bus between microservices. Topics: orders.created, orders.fulfilled, inventory.reserved, payments.processed. Dead-letter topic: events.dlq.",
     "tags": ["kafka", "architecture", "microservices"], "days_ago": 50},

    {"scope": "ecommerce-refactor", "type": "error",
     "content": "LATERAL join syntax for order history query not supported on PostgreSQL 13. Workaround: use CTE with ROW_NUMBER() window function instead. Target cluster must be upgraded to PG14+ for LATERAL.",
     "tags": ["postgres", "sql", "bug"], "days_ago": 8},

    {"scope": "ecommerce-refactor", "type": "success",
     "content": "Added composite index idx_orders_user_created ON orders(user_id, created_at DESC). Order history query reduced from 4–8s to <50ms for customers with 500+ orders. Deployed to staging 2026-05-11.",
     "tags": ["performance", "postgres", "index"], "days_ago": 8},

    {"scope": "ecommerce-refactor", "type": "state",
     "content": "Phase 1 (checkout service) is in progress. Payment service unit tests complete (4/4 passing). Still need: inventory reservation saga, dead-letter queue UI, load testing at 1k RPS.",
     "tags": ["sprint", "progress", "checkout"], "days_ago": 3},

    {"scope": "ecommerce-refactor", "type": "artifact",
     "content": "Payment service tests at tests/test_payment_service.py. 4 test cases: successful charge, declined card, zero amount, webhook fired. All pass via mock_stripe + mock_db fixtures.",
     "tags": ["testing", "payment", "artifact"], "days_ago": 10},
]


def seed_agent_memory(conn, user_id: str) -> int:
    existing = count(conn, "agent_memory_entries", "user_id = ?", (user_id,))
    if existing >= 10:
        warn(f"agent_memory_entries already has {existing} rows - skipping")
        return 0

    inserted = 0
    for e in AGENT_MEMORY_ENTRIES:
        conn.execute(
            """
            INSERT INTO agent_memory_entries
                (id, agent_scope_key, user_id, entry_type, content,
                 tags, sensitive, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                new_id(), e["scope"], user_id, e["type"], e["content"],
                jdump(e["tags"]), 0,
                ts(days_ago=e["days_ago"]),
            ),
        )
        inserted += 1
        ok(f"agent_memory [{e['scope']:22s}] [{e['type']:10s}]: {e['content'][:55]}...")

    conn.commit()
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# MCP Configs + Tool Metadata
# ─────────────────────────────────────────────────────────────────────────────

MCP_SERVERS = [
    {
        # github-mcp: stdio transport (npx @modelcontextprotocol/server-github)
        # Corrected 2026-05-21: was SSE (localhost:3001/sse) — now stdio with PAT.
        # Uses GITHUB_PERSONAL_ACCESS_TOKEN in env (replace with real PAT for live tests).
        "name": "github-mcp",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_placeholder_replace_with_real_pat"},
        "enabled": True,
        "tools": [
            {"name": "list_pull_requests",  "description": "List open pull requests for a repository", "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}, "state": {"type": "string", "enum": ["open", "closed", "all"]}}}},
            {"name": "get_pull_request",    "description": "Get details of a specific pull request",  "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}, "number": {"type": "integer"}}}},
            {"name": "create_issue",        "description": "Create a new GitHub issue",                "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}}}},
            {"name": "list_commits",        "description": "List recent commits on a branch",          "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}, "branch": {"type": "string"}, "since": {"type": "string"}}}},
            {"name": "get_file_contents",   "description": "Read a file from a GitHub repository",    "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}, "ref": {"type": "string"}}}},
        ],
    },
    {
        "name": "filesystem-mcp",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
        "enabled": True,
        "tools": [
            {"name": "read_file",    "description": "Read the complete contents of a file from the filesystem",      "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "write_file",   "description": "Create a new file or overwrite an existing file",              "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "list_dir",     "description": "List files and directories in a specified path",               "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "search_files", "description": "Search for files matching a pattern in a directory",           "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}}, "required": ["path", "pattern"]}},
            {"name": "get_file_info","description": "Retrieve metadata about a file: size, modified time, permissions", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        ],
    },
    {
        "name": "slack-mcp",
        "transport": "http",
        "url": "http://localhost:3002/mcp",
        "enabled": False,    # disabled - OAuth not yet configured
        "headers": {"X-Slack-Bot-Token": "xoxb-placeholder"},
        "tools": [
            {"name": "post_message",    "description": "Post a message to a Slack channel",                          "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "text": {"type": "string"}}, "required": ["channel", "text"]}},
            {"name": "list_channels",   "description": "List all public channels in the workspace",                  "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 50}}}},
            {"name": "get_messages",    "description": "Retrieve recent messages from a channel",                    "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["channel"]}},
            {"name": "search_messages", "description": "Search for messages matching a query across all channels",   "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
        ],
    },
]


def seed_mcp(conn, user_id: str) -> int:
    existing = count(conn, "mcp_configs", "user_id = ?", (user_id,))
    if existing >= 3:
        warn(f"mcp_configs already has {existing} rows - skipping")
        return 0

    # Detect whether new columns from migration 0006 are present
    cols = table_columns(conn, "mcp_configs")
    has_project_cols = "project_id" in cols and "project_paused" in cols

    inserted = 0
    for server in MCP_SERVERS:
        config_id = new_id()

        if has_project_cols:
            conn.execute(
                """
                INSERT OR IGNORE INTO mcp_configs
                    (id, user_id, name, enabled, transport,
                     command, args, env, cwd, env_file,
                     url, headers, created_at, updated_at,
                     project_id, project_paused)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    config_id, user_id, server["name"],
                    1 if server["enabled"] else 0,
                    server["transport"],
                    server.get("command"), jdump(server.get("args")),
                    jdump(server.get("env")), None, None,
                    server.get("url"), jdump(server.get("headers")),
                    ts(days_ago=10), ts(days_ago=1),
                    server.get("project_id"), 0,
                ),
            )
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO mcp_configs
                    (id, user_id, name, enabled, transport,
                     command, args, env, cwd, env_file,
                     url, headers, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    config_id, user_id, server["name"],
                    1 if server["enabled"] else 0,
                    server["transport"],
                    server.get("command"), jdump(server.get("args")),
                    jdump(server.get("env")), None, None,
                    server.get("url"), jdump(server.get("headers")),
                    ts(days_ago=10), ts(days_ago=1),
                ),
            )

        # Insert tool metadata
        for tool in server.get("tools", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO mcp_tool_meta
                    (id, config_id, name, description, input_schema, last_synced_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    new_id(), config_id, tool["name"], tool["description"],
                    jdump(tool["input_schema"]),
                    ts(days_ago=1),
                ),
            )

        inserted += 1
        status = "enabled " if server["enabled"] else "disabled"
        ok(f"mcp_config: {server['name']!r}  [{server['transport']} | {status}| {len(server['tools'])} tools]")

    conn.commit()
    return inserted


# ── Main seeder ────────────────────────────────────────────────────────────

def run(db_path=None) -> dict:
    head("05 : Memory Facts, Agent Memory & MCP Configs")
    conn = connect(find_db_path(db_path))
    user_id = get_user_id(conn)

    user_facts    = seed_user_memory(conn, user_id)
    agent_entries = seed_agent_memory(conn, user_id)
    mcp           = seed_mcp(conn, user_id)

    return {
        "user_memory_facts":    user_facts,
        "agent_memory_entries": agent_entries,
        "mcp_configs":          mcp,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db-path", default=None)
    args = p.parse_args()
    result = run(args.db_path)
    print(f"\n  Done: {result}")
