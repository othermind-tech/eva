# tests/seed_data/seed_02_conversations.py
"""
Seed: Conversations, Messages, Tool Calls, Usage Logs, Summaries
────────────────────────────────────────────────────────────────
12 realistic conversations - some standalone, some linked to projects.
Each conversation has between 6 and 14 messages with realistic tool calls
and per-assistant-message usage logs.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import (
    connect, find_db_path, get_user_id, jdump, new_id, ok, warn,
    info, head, count, ts, table_exists,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: look up a project id by name
# ─────────────────────────────────────────────────────────────────────────────

def get_project_id(conn, user_id: str, name: str):
    row = conn.execute(
        "SELECT id FROM projects WHERE user_id=? AND name=? LIMIT 1",
        (user_id, name),
    ).fetchone()
    return row["id"] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Helper: insert a single message with optional tool-call and usage-log rows
# ─────────────────────────────────────────────────────────────────────────────

def insert_message(
    conn,
    conversation_id: str,
    user_id: str,
    seq: int,
    role: str,
    content: str,
    days_ago: int = 0,
    hours_ago: int = 0,
    minutes_ago: int = 0,
    tool_name: str = None,
    tool_args: dict = None,
    tool_result: dict = None,
    prompt_tokens: int = None,
    completion_tokens: int = None,
    model: str = "qwen3:8b",
    provider: str = "ollama",
    finish_reason: str = "stop",
) -> str:
    msg_id = new_id()
    conn.execute(
        """
        INSERT INTO messages
            (id, conversation_id, role, content, type, tool_name, tool_args,
             tool_result, finish_reason, sequence, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            msg_id, conversation_id, role, content,
            "tool_call" if tool_name else "text",
            tool_name, jdump(tool_args), jdump(tool_result),
            finish_reason, seq,
            ts(days_ago=days_ago, hours_ago=hours_ago, minutes_ago=minutes_ago),
        ),
    )

    # Tool call record
    if tool_name:
        conn.execute(
            """
            INSERT INTO tool_calls
                (id, call_id, message_id, tool_name, arguments, result, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                new_id(), f"call_{msg_id[:8]}", msg_id,
                tool_name, jdump(tool_args), jdump(tool_result),
                ts(days_ago=days_ago, hours_ago=hours_ago, minutes_ago=minutes_ago),
            ),
        )

    return msg_id


# ─────────────────────────────────────────────────────────────────────────────
# Conversation builders
# ─────────────────────────────────────────────────────────────────────────────

def conv_python_async(conn, user_id, project_id=None):
    """Conv 1 - Python async patterns in FastAPI"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Python async patterns in FastAPI", project_id,
         ts(days_ago=28), ts(days_ago=28, hours_ago=-2)),
    )
    d = 28
    insert_message(conn, cid, user_id, 0, "user",
        "Can you explain how async/await works in FastAPI compared to traditional Flask? "
        "I keep seeing async def everywhere but I'm not sure when it actually matters.",
        days_ago=d, minutes_ago=30)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Great question. FastAPI is built on **Starlette** (ASGI), which means every request "
        "runs inside an event loop. When you declare `async def` on a path function, FastAPI "
        "awaits it directly on the event loop - no thread pool involved.\n\n"
        "Flask, by contrast, is WSGI-synchronous: each request ties up an OS thread. "
        "Even with `gevent` or `eventlet` patching you're still faking concurrency.\n\n"
        "**When `async def` actually matters:**\n"
        "- You're calling an `await`-able I/O operation (DB query via `asyncpg`, HTTP call via `httpx`, etc.)\n"
        "- You're reading/writing files with `aiofiles`\n"
        "- You have a WebSocket handler\n\n"
        "**When it doesn't matter (and might hurt):**\n"
        "- CPU-bound work - the event loop is blocked either way; use `run_in_executor` or a worker pool\n"
        "- Calling a sync library - FastAPI automatically runs sync `def` handlers in a thread pool, "
        "so mixing is safe\n\n"
        "Rule of thumb: if every I/O call inside your handler is `await`-able, use `async def`. "
        "If you're calling a synchronous ORM like plain SQLAlchemy Core, keep `def` and let FastAPI "
        "push it to a thread.",
        days_ago=d, minutes_ago=28, prompt_tokens=312, completion_tokens=487,
        model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "What about background tasks? I need to send a welcome email after a signup "
        "response is returned to the user - I don't want them waiting for the email.",
        days_ago=d, minutes_ago=20)
    insert_message(conn, cid, user_id, 3, "assistant",
        "FastAPI's `BackgroundTasks` is perfect for this - it runs the task after the "
        "response is sent, inside the same worker process.\n\n"
        "```python\nfrom fastapi import BackgroundTasks, FastAPI\n\napp = FastAPI()\n\n"
        "def send_welcome_email(email: str, username: str):\n"
        "    # This runs AFTER the response is returned\n"
        "    print(f'Sending welcome email to {email}')\n"
        "    # your SendGrid / SMTP call here\n\n"
        "@app.post('/signup')\nasync def signup(\n"
        "    data: SignupRequest, background_tasks: BackgroundTasks\n):\n"
        "    user = await create_user(data)\n"
        "    background_tasks.add_task(send_welcome_email, user.email, user.username)\n"
        "    return {'id': user.id, 'message': 'Account created'}\n```\n\n"
        "For heavier workloads (bulk emails, PDF generation, video processing) switch to "
        "**Celery + Redis** - `BackgroundTasks` won't survive a pod restart.",
        days_ago=d, minutes_ago=18,
        tool_name="web_search",
        tool_args={"query": "FastAPI BackgroundTasks vs Celery 2026 best practices"},
        tool_result={"results": [{"title": "FastAPI Background Tasks docs", "url": "https://fastapi.tiangolo.com/tutorial/background-tasks/"}]},
        prompt_tokens=421, completion_tokens=389, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 4, "user",
        "Write me a complete working signup endpoint with welcome email background task "
        "and proper error handling.",
        days_ago=d, minutes_ago=10)
    insert_message(conn, cid, user_id, 5, "assistant",
        "Here's a production-ready signup endpoint with email background task:\n\n"
        "```python\n# api/auth.py  (relevant section)\nfrom fastapi import APIRouter, BackgroundTasks, HTTPException\n"
        "from pydantic import BaseModel, EmailStr\nimport smtplib, ssl\nfrom email.message import EmailMessage\n\n"
        "router = APIRouter()\n\nclass SignupRequest(BaseModel):\n    username: str\n    email: EmailStr\n    password: str\n\n"
        "async def _send_welcome(email: str, username: str) -> None:\n    msg = EmailMessage()\n"
        "    msg['Subject'] = f'Welcome to EVA, {username}!'\n    msg['From'] = 'noreply@example.com'\n"
        "    msg['To'] = email\n    msg.set_content(f'Hi {username},\\n\\nYour account is ready.\\n\\nTeam EVA')\n"
        "    ctx = ssl.create_default_context()\n    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx) as s:\n"
        "        s.login(SMTP_USER, SMTP_PASS)\n        s.send_message(msg)\n\n"
        "@router.post('/signup', status_code=201)\nasync def signup(body: SignupRequest, bg: BackgroundTasks):\n"
        "    if await user_exists(body.email):\n        raise HTTPException(409, 'Email already registered')\n"
        "    user = await create_user(body)\n    bg.add_task(_send_welcome, user.email, user.username)\n"
        "    return {'id': user.id, 'username': user.username}\n```",
        days_ago=d,
        tool_name="write_file",
        tool_args={"path": "api/signup_example.py", "content": "# see above"},
        tool_result={"success": True, "bytes_written": 847},
        prompt_tokens=538, completion_tokens=612, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Python async patterns in FastAPI'  [6 msgs]")
    return cid


def conv_auth_refactor(conn, user_id, project_id=None):
    """Conv 2 - Refactor authentication module (EVA Backend project)"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Refactor authentication module - add refresh tokens", project_id,
         ts(days_ago=22), ts(days_ago=21)),
    )
    d = 22
    insert_message(conn, cid, user_id, 0, "user",
        "I need to add refresh token support to our auth module. Right now access tokens expire "
        "in 1 hour and users are getting logged out constantly. What's the best approach?",
        days_ago=d, hours_ago=5)
    insert_message(conn, cid, user_id, 1, "assistant",
        "The standard pattern is **token rotation**: short-lived access tokens (15–60 min) paired "
        "with long-lived refresh tokens (7–30 days). On expiry the client POSTs the refresh token "
        "to `/auth/refresh` and receives a fresh access + refresh pair (old refresh token is "
        "immediately invalidated).\n\nLet me read the current auth module first.",
        days_ago=d, hours_ago=4, minutes_ago=58,
        tool_name="read_file",
        tool_args={"path": "api/auth.py"},
        tool_result={"content": "# api/auth.py\nfrom fastapi import APIRouter\n# ... existing auth code ..."},
        prompt_tokens=289, completion_tokens=198, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "What DB schema changes do I need?",
        days_ago=d, hours_ago=4, minutes_ago=30)
    insert_message(conn, cid, user_id, 3, "assistant",
        "You need two new columns on `auth_sessions`:\n\n"
        "```sql\nALTER TABLE auth_sessions ADD COLUMN refresh_token TEXT UNIQUE;\n"
        "ALTER TABLE auth_sessions ADD COLUMN refresh_expires_at DATETIME;\n```\n\n"
        "Create an index on `refresh_token` for fast lookup:\n"
        "```sql\nCREATE UNIQUE INDEX idx_auth_sessions_refresh ON auth_sessions(refresh_token);\n```\n\n"
        "For Alembic wrap both in a migration with PRAGMA table_info guard to make it idempotent.",
        days_ago=d, hours_ago=4, minutes_ago=20,
        tool_name="read_file",
        tool_args={"path": "core/db/models.py"},
        tool_result={"content": "class AuthSession(Base):\n    __tablename__ = 'auth_sessions'\n    # ..."},
        prompt_tokens=356, completion_tokens=312, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 4, "user",
        "Write the token rotation implementation including the /auth/refresh endpoint.",
        days_ago=d, hours_ago=3, minutes_ago=50)
    insert_message(conn, cid, user_id, 5, "assistant",
        "Here's the complete token rotation implementation:\n\n"
        "```python\nimport secrets\nfrom datetime import timedelta\n\n"
        "REFRESH_TOKEN_EXPIRE_DAYS = 30\n\n"
        "def generate_refresh_token() -> str:\n    return secrets.token_urlsafe(64)\n\n"
        "@router.post('/refresh')\nasync def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)):\n"
        "    session = await db.execute(\n        select(AuthSession).where(AuthSession.refresh_token == body.refresh_token)\n    )\n"
        "    session = session.scalar_one_or_none()\n    if not session or session.refresh_expires_at < utcnow():\n"
        "        raise HTTPException(401, 'Refresh token expired or invalid')\n\n"
        "    # Rotate: invalidate old, issue new\n"
        "    new_refresh = generate_refresh_token()\n"
        "    session.refresh_token = new_refresh\n"
        "    session.refresh_expires_at = utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)\n"
        "    await db.commit()\n\n"
        "    new_access = create_access_token({'sub': session.user_id})\n"
        "    return {'access_token': new_access, 'refresh_token': new_refresh}\n```",
        days_ago=d, hours_ago=3, minutes_ago=30,
        tool_name="write_file",
        tool_args={"path": "api/auth.py", "content": "# token rotation endpoint added"},
        tool_result={"success": True, "bytes_written": 1243},
        prompt_tokens=445, completion_tokens=698, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 6, "user",
        "Run the existing auth tests to make sure nothing broke.",
        days_ago=d, hours_ago=3)
    insert_message(conn, cid, user_id, 7, "assistant",
        "Running the test suite now.",
        days_ago=d, hours_ago=2, minutes_ago=58,
        tool_name="shell_command",
        tool_args={"command": "python -m pytest tests/test_auth.py -v --tb=short 2>&1 | tail -30"},
        tool_result={
            "exit_code": 0,
            "stdout": "tests/test_auth.py::test_login PASSED\ntests/test_auth.py::test_token_expiry PASSED\n"
                      "tests/test_auth.py::test_invalid_credentials PASSED\n\n3 passed in 1.24s",
        },
        prompt_tokens=198, completion_tokens=87, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 8, "user",
        "All green! Commit this with a good commit message.",
        days_ago=d, hours_ago=2, minutes_ago=40)
    insert_message(conn, cid, user_id, 9, "assistant",
        "Staging and committing the changes.",
        days_ago=d, hours_ago=2, minutes_ago=35,
        tool_name="shell_command",
        tool_args={"command": "git add api/auth.py core/db/models.py alembic/versions/ && git commit -m 'feat(auth): add refresh token rotation (B-010)\n\n- Add refresh_token + refresh_expires_at columns to auth_sessions\n- Implement POST /auth/refresh with token rotation\n- Access token TTL unchanged (24h); refresh TTL 30 days'"},
        tool_result={"exit_code": 0, "stdout": "[main a3f9c12] feat(auth): add refresh token rotation (B-010)\n 3 files changed, 87 insertions(+), 4 deletions(-)"},
        prompt_tokens=167, completion_tokens=134, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Refactor authentication module'  [10 msgs]")
    return cid


def conv_analytics_dashboard(conn, user_id, project_id=None):
    """Conv 3 - Customer retention dashboard"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Customer retention dashboard - cohort analysis component", project_id,
         ts(days_ago=18), ts(days_ago=17)),
    )
    d = 18
    insert_message(conn, cid, user_id, 0, "user",
        "I need to build a cohort retention chart in React using D3. "
        "Users are grouped by signup month and I need to show what % are still active in month 1, 2, 3 etc.",
        days_ago=d, hours_ago=6)
    insert_message(conn, cid, user_id, 1, "assistant",
        "A cohort retention heatmap is the canonical viz for this. Here's the architecture:\n\n"
        "**Data shape** (from your API):\n```json\n[\n"
        "  {\"cohort\": \"2026-01\", \"month_0\": 1200, \"month_1\": 876, \"month_2\": 701, \"month_3\": 589},\n"
        "  {\"cohort\": \"2026-02\", \"month_0\": 980,  \"month_1\": 712, \"month_2\": 601}\n]\n```\n\n"
        "**D3 approach**: `d3.scaleSequential` with a blue colour range, each cell shows the % "
        "retention relative to `month_0`. Hover tooltip shows absolute counts + percentage.\n\n"
        "Want me to write the full React + D3 component?",
        days_ago=d, hours_ago=5, minutes_ago=45,
        prompt_tokens=378, completion_tokens=312, model="groq/llama-3.3-70b-versatile", provider="groq")
    insert_message(conn, cid, user_id, 2, "user",
        "Yes please - full component with TypeScript types and Tailwind styling.",
        days_ago=d, hours_ago=5, minutes_ago=30)
    insert_message(conn, cid, user_id, 3, "assistant",
        "Here's the complete `RetentionHeatmap` component:\n\n"
        "```tsx\n// src/components/RetentionHeatmap.tsx\nimport { useEffect, useRef } from 'react';\nimport * as d3 from 'd3';\n\n"
        "interface CohortRow {\n  cohort: string;\n  [key: `month_${number}`]: number;\n}\n\n"
        "interface Props { data: CohortRow[]; maxMonths?: number; }\n\n"
        "export function RetentionHeatmap({ data, maxMonths = 6 }: Props) {\n"
        "  const svgRef = useRef<SVGSVGElement>(null);\n\n"
        "  useEffect(() => {\n    if (!svgRef.current || !data.length) return;\n"
        "    const svg = d3.select(svgRef.current);\n    svg.selectAll('*').remove();\n"
        "    const colour = d3.scaleSequential(d3.interpolateBlues).domain([0, 100]);\n"
        "    // ... cell rendering with tooltips ...\n"
        "  }, [data, maxMonths]);\n\n"
        "  return (\n    <div className='overflow-x-auto rounded-xl border border-white/10 bg-bg-surface p-4'>\n"
        "      <svg ref={svgRef} className='w-full' />\n    </div>\n  );\n}\n```\n\n"
        "I'll write the full D3 rendering logic to a file.",
        days_ago=d, hours_ago=5,
        tool_name="write_file",
        tool_args={"path": "src/components/RetentionHeatmap.tsx", "content": "// full component"},
        tool_result={"success": True, "bytes_written": 2341},
        prompt_tokens=512, completion_tokens=891, model="groq/llama-3.3-70b-versatile", provider="groq")
    insert_message(conn, cid, user_id, 4, "user",
        "What's the best way to fetch this data from our FastAPI backend with TanStack Query?",
        days_ago=d, hours_ago=4, minutes_ago=30)
    insert_message(conn, cid, user_id, 5, "assistant",
        "Use `useQuery` with a typed fetcher:\n\n"
        "```ts\n// src/api/analytics.ts\nexport const analyticsApi = {\n"
        "  getCohortRetention: (months = 6) =>\n"
        "    client.get<CohortRow[]>(`/analytics/cohort-retention?months=${months}`)\n"
        "      .then(r => r.data),\n};\n\n"
        "// In component\nconst { data = [], isLoading } = useQuery({\n"
        "  queryKey: ['cohort-retention', months],\n"
        "  queryFn: () => analyticsApi.getCohortRetention(months),\n"
        "  staleTime: 5 * 60 * 1000,  // 5 min cache\n});\n```\n\n"
        "Add `placeholderData: keepPreviousData` so the chart doesn't flash during month-filter changes.",
        days_ago=d, hours_ago=4,
        prompt_tokens=389, completion_tokens=334, model="groq/llama-3.3-70b-versatile", provider="groq")
    conn.commit()
    ok("conv: 'Customer retention dashboard'  [6 msgs]")
    return cid


def conv_transformer_explainer(conn, user_id, project_id=None):
    """Conv 4 - Transformer architecture explained (standalone)"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Transformer architecture deep dive - attention mechanism", project_id,
         ts(days_ago=14), ts(days_ago=14, hours_ago=-3)),
    )
    d = 14
    insert_message(conn, cid, user_id, 0, "user",
        "Can you walk me through the transformer architecture? Specifically the attention mechanism - "
        "I keep reading 'queries, keys, values' but the intuition never clicks.",
        days_ago=d, hours_ago=4)
    insert_message(conn, cid, user_id, 1, "assistant",
        "The **attention** mechanism is best understood as a soft database lookup.\n\n"
        "Imagine you have a library (the keys + values) and a search query. "
        "Traditional lookup: binary - the query either matches a key or it doesn't. "
        "Attention: **fuzzy** - the query has a similarity score with every key simultaneously, "
        "and you get back a *weighted blend* of all the values.\n\n"
        "**Formally** (Scaled Dot-Product Attention):\n"
        "```\nAttention(Q, K, V) = softmax(QKᵀ / √dₖ) : V\n```\n\n"
        "- Q (Query): 'What am I looking for?'\n"
        "- K (Key):   'What do I advertise having?'\n"
        "- V (Value): 'What do I actually give when selected?'\n\n"
        "The √dₖ scale prevents dot-products from growing so large that softmax saturates.\n\n"
        "**Concrete example** (translating 'The cat sat'):\n"
        "When processing 'sat', the Q vector asks 'what subject performed this action?' "
        "The K vectors for 'cat' and 'The' respond with high/low similarity respectively, "
        "so 'sat' attends most strongly to 'cat'.",
        days_ago=d, hours_ago=3, minutes_ago=50,
        prompt_tokens=298, completion_tokens=567, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "What's multi-head attention then? Why have multiple heads?",
        days_ago=d, hours_ago=3, minutes_ago=20)
    insert_message(conn, cid, user_id, 3, "assistant",
        "**Multi-head attention** runs the attention operation in parallel across `h` independent "
        "subspaces (heads), each with its own Q/K/V projection matrices.\n\n"
        "```\nMultiHead(Q,K,V) = Concat(head₁, ..., headₕ) : Wᴼ\nwhere headᵢ = Attention(Q:Wᵢᵠ, K:Wᵢᴷ, V:Wᵢᵛ)\n```\n\n"
        "**Why multiple heads?** Each head can specialise in a different type of relationship:\n"
        "- Head 1 might capture syntactic agreement (subject↔verb)\n"
        "- Head 2 might track coreference (pronoun→antecedent)\n"
        "- Head 3 might encode positional proximity\n\n"
        "GPT-3 uses 96 heads with d_model=12288 - each head sees a 128-dim subspace. "
        "In practice heads often do specialise, though interpretability research shows it's messier than the clean story.",
        days_ago=d, hours_ago=3,
        tool_name="web_search",
        tool_args={"query": "multi-head attention head specialization interpretability 2024 paper"},
        tool_result={"results": [{"title": "Are Sixteen Heads Really Better than One? - NeurIPS", "url": "https://arxiv.org/abs/1905.10650"}]},
        prompt_tokens=412, completion_tokens=489, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 4, "user",
        "How does positional encoding work? The model doesn't see token order, right?",
        days_ago=d, hours_ago=2, minutes_ago=30)
    insert_message(conn, cid, user_id, 5, "assistant",
        "Exactly - pure attention is **permutation-invariant**. Without positional encoding "
        "'cat sat mat' and 'mat sat cat' would produce identical representations.\n\n"
        "**Sinusoidal Encoding** (original paper):\n"
        "```\nPE(pos, 2i)   = sin(pos / 10000^(2i/d_model))\nPE(pos, 2i+1) = cos(pos / 10000^(2i/d_model))\n```\n\n"
        "Each position gets a unique vector of alternating sin/cos at different frequencies - "
        "like a binary counter but continuous. These are **added** to the token embeddings.\n\n"
        "**Modern variants:**\n"
        "- **RoPE** (Rotary Position Embedding) - used in LLaMA, Qwen: rotates Q&K vectors by position angle, "
        "baking relative position into the attention score itself. Better for long contexts.\n"
        "- **ALiBi** - adds a position-dependent bias to attention logits; no learned parameters.\n\n"
        "RoPE is now the dominant choice for LLMs >7B because it generalises to sequences longer than training context.",
        days_ago=d, hours_ago=2,
        prompt_tokens=389, completion_tokens=534, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Transformer architecture deep dive'  [6 msgs]")
    return cid


def conv_payment_tests(conn, user_id, project_id=None):
    """Conv 5 - Unit tests for payment service"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Unit tests for the payment service checkout flow", project_id,
         ts(days_ago=10), ts(days_ago=9)),
    )
    d = 10
    insert_message(conn, cid, user_id, 0, "user",
        "I need to write pytest unit tests for our payment service. It calls Stripe's API, "
        "updates an orders DB table, and fires a webhook. What's the best testing strategy?",
        days_ago=d, hours_ago=8)
    insert_message(conn, cid, user_id, 1, "assistant",
        "For payment service tests you want three layers:\n\n"
        "1. **Unit tests** - mock Stripe SDK entirely (`unittest.mock.patch`), test business logic in isolation\n"
        "2. **Integration tests** - use Stripe's test mode with real API calls against `stripe.com/v1`\n"
        "3. **Contract tests** - assert your webhook handler accepts the exact payload shape Stripe sends\n\n"
        "For unit tests the key fixtures are:\n- `mock_stripe_charge` - returns a canned `stripe.Charge` object\n"
        "- `mock_db_session` - `AsyncMock` of your database session\n- `mock_webhook_client` - captures outbound calls\n\n"
        "Let me read your existing payment module first.",
        days_ago=d, hours_ago=7, minutes_ago=50,
        tool_name="read_file",
        tool_args={"path": "services/payment.py"},
        tool_result={"content": "class PaymentService:\n    async def charge(self, order_id, amount_cents, card_token): ..."},
        prompt_tokens=312, completion_tokens=378, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "Write the full pytest test file for the charge method.",
        days_ago=d, hours_ago=7, minutes_ago=30)
    insert_message(conn, cid, user_id, 3, "assistant",
        "Here's the complete test file:\n\n"
        "```python\n# tests/test_payment_service.py\nimport pytest\nfrom unittest.mock import AsyncMock, MagicMock, patch\n"
        "from services.payment import PaymentService\nfrom core.exceptions import PaymentDeclinedError, InvalidAmountError\n\n"
        "@pytest.fixture\ndef mock_stripe():\n    with patch('services.payment.stripe') as m:\n"
        "        m.Charge.create = AsyncMock(return_value=MagicMock(\n"
        "            id='ch_test_abc123', status='succeeded', amount=2999\n        ))\n        yield m\n\n"
        "@pytest.fixture\nasync def payment_svc(mock_db):\n    return PaymentService(db=mock_db)\n\n"
        "class TestChargeMethod:\n    async def test_successful_charge(self, payment_svc, mock_stripe, mock_db):\n"
        "        result = await payment_svc.charge('order_42', 2999, 'tok_visa')\n"
        "        assert result.charge_id == 'ch_test_abc123'\n"
        "        assert result.status == 'succeeded'\n        mock_db.commit.assert_awaited_once()\n\n"
        "    async def test_declined_card_raises(self, payment_svc, mock_stripe):\n"
        "        mock_stripe.Charge.create.side_effect = stripe.error.CardError(\n"
        "            'Your card was declined', None, 'card_declined'\n        )\n"
        "        with pytest.raises(PaymentDeclinedError, match='card_declined'):\n"
        "            await payment_svc.charge('order_43', 5000, 'tok_chargeDeclined')\n\n"
        "    async def test_zero_amount_raises(self, payment_svc):\n"
        "        with pytest.raises(InvalidAmountError):\n"
        "            await payment_svc.charge('order_44', 0, 'tok_visa')\n\n"
        "    async def test_webhook_fired_on_success(self, payment_svc, mock_stripe, mock_webhook):\n"
        "        await payment_svc.charge('order_45', 1999, 'tok_visa')\n"
        "        mock_webhook.post.assert_awaited_once()\n```",
        days_ago=d, hours_ago=7,
        tool_name="write_file",
        tool_args={"path": "tests/test_payment_service.py", "content": "# full test file"},
        tool_result={"success": True, "bytes_written": 1876},
        prompt_tokens=478, completion_tokens=923, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 4, "user",
        "Run the tests",
        days_ago=d, hours_ago=6, minutes_ago=50)
    insert_message(conn, cid, user_id, 5, "assistant",
        "Running the payment tests now.",
        days_ago=d, hours_ago=6, minutes_ago=45,
        tool_name="shell_command",
        tool_args={"command": "python -m pytest tests/test_payment_service.py -v 2>&1"},
        tool_result={
            "exit_code": 0,
            "stdout": "tests/test_payment_service.py::TestChargeMethod::test_successful_charge PASSED\n"
                      "tests/test_payment_service.py::TestChargeMethod::test_declined_card_raises PASSED\n"
                      "tests/test_payment_service.py::TestChargeMethod::test_zero_amount_raises PASSED\n"
                      "tests/test_payment_service.py::TestChargeMethod::test_webhook_fired_on_success PASSED\n\n"
                      "4 passed in 0.87s",
        },
        prompt_tokens=156, completion_tokens=89, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Unit tests for payment service'  [6 msgs]")
    return cid


def conv_postgres_query(conn, user_id, project_id=None):
    """Conv 6 - PostgreSQL query optimisation"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "PostgreSQL query optimisation - slow order history query", project_id,
         ts(days_ago=8), ts(days_ago=7)),
    )
    d = 8
    insert_message(conn, cid, user_id, 0, "user",
        "Our order history query is taking 4–8 seconds for customers with >500 orders. "
        "Here's the query:\n\n"
        "```sql\nSELECT o.*, u.email, p.name as product_name\nFROM orders o\nJOIN users u ON o.user_id = u.id\n"
        "JOIN order_items oi ON oi.order_id = o.id\nJOIN products p ON oi.product_id = p.id\n"
        "WHERE o.user_id = $1\nORDER BY o.created_at DESC\nLIMIT 20 OFFSET $2;\n```",
        days_ago=d, hours_ago=6)
    insert_message(conn, cid, user_id, 1, "assistant",
        "There are several likely culprits. Let me EXPLAIN ANALYZE this.\n\n"
        "Paste the output of:\n```sql\nEXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)\nSELECT o.*, ...\n```\n\n"
        "While you do that, here are my immediate suspicions:\n\n"
        "1. **Missing index on `orders.user_id`** - full table scan for each customer\n"
        "2. **N+1 on `order_items`** - one row per item means the join explodes before LIMIT\n"
        "3. **OFFSET pagination** - `OFFSET 500` still reads 520 rows then discards 500\n\n"
        "Quick wins in order of impact:\n"
        "```sql\n-- Index if missing:\nCREATE INDEX CONCURRENTLY idx_orders_user_created ON orders(user_id, created_at DESC);\n\n"
        "-- Keyset pagination instead of OFFSET:\nWHERE o.user_id = $1 AND o.created_at < $2 -- cursor\nORDER BY o.created_at DESC LIMIT 20;\n```",
        days_ago=d, hours_ago=5, minutes_ago=45,
        prompt_tokens=445, completion_tokens=512, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "EXPLAIN output shows Seq Scan on orders (cost=0.00..48233.12 rows=487 width=156) "
        "Filter: (user_id = $1) - yeah definitely missing index.",
        days_ago=d, hours_ago=5, minutes_ago=20)
    insert_message(conn, cid, user_id, 3, "assistant",
        "Confirmed - sequential scan over the entire orders table for each user lookup. "
        "At 48K estimated rows that's ~4 MB of data read per query.\n\n"
        "**Fix 1 - Composite index** (covers the WHERE + ORDER BY):\n"
        "```sql\nCREATE INDEX CONCURRENTLY idx_orders_user_created\n    ON orders(user_id, created_at DESC);\n```\n\n"
        "**Fix 2 - Aggregate items before joining** (avoids row explosion):\n"
        "```sql\nSELECT o.*, u.email,\n       agg.product_names\nFROM orders o\nJOIN users u ON o.user_id = u.id\n"
        "JOIN LATERAL (\n    SELECT array_agg(p.name ORDER BY oi.id) AS product_names\n    FROM order_items oi\n"
        "    JOIN products p ON oi.product_id = p.id\n    WHERE oi.order_id = o.id\n) agg ON true\n"
        "WHERE o.user_id = $1\nORDER BY o.created_at DESC LIMIT 20;\n```\n\n"
        "After the index, expect <50 ms for 500-order customers. The LATERAL avoids the Cartesian explosion.",
        days_ago=d, hours_ago=5,
        prompt_tokens=389, completion_tokens=478, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'PostgreSQL query optimisation'  [4 msgs]")
    return cid


def conv_cicd_setup(conn, user_id, project_id=None):
    """Conv 7 - CI/CD with GitHub Actions"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "GitHub Actions CI/CD pipeline - build, test, deploy to EKS", project_id,
         ts(days_ago=5), ts(days_ago=4)),
    )
    d = 5
    insert_message(conn, cid, user_id, 0, "user",
        "Write a GitHub Actions workflow for our FastAPI backend: lint → test → build Docker → "
        "push to ECR → deploy to EKS staging. Must run on PR and main branch push.",
        days_ago=d, hours_ago=10)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Here's a complete workflow covering all stages with proper caching and environment separation:",
        days_ago=d, hours_ago=9, minutes_ago=45,
        tool_name="write_file",
        tool_args={"path": ".github/workflows/ci-cd.yml", "content": "# GitHub Actions workflow"},
        tool_result={"success": True, "bytes_written": 3241},
        prompt_tokens=523, completion_tokens=1124, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "The Docker build step is slow - taking 8 minutes on a fresh runner. How do I cache layers?",
        days_ago=d, hours_ago=8)
    insert_message(conn, cid, user_id, 3, "assistant",
        "Use GitHub Actions cache with `docker/build-push-action` and `cache-from`/`cache-to`:\n\n"
        "```yaml\n- name: Build and push\n  uses: docker/build-push-action@v5\n  with:\n"
        "    context: .\n    push: ${{ github.ref == 'refs/heads/main' }}\n"
        "    tags: ${{ steps.meta.outputs.tags }}\n"
        "    cache-from: type=gha\n    cache-to: type=gha,mode=max\n```\n\n"
        "With `mode=max` every layer is cached, not just the final image. Subsequent builds skip unchanged "
        "layers - typically reduces build time from 8 min → 90 sec for a Python service.",
        days_ago=d, hours_ago=7, minutes_ago=30,
        prompt_tokens=312, completion_tokens=389, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 4, "user",
        "How do I safely store the AWS credentials in GitHub?",
        days_ago=d, hours_ago=7)
    insert_message(conn, cid, user_id, 5, "assistant",
        "**Use OIDC (OpenID Connect) - not long-lived access keys.** GitHub can assume an IAM role "
        "directly via token federation:\n\n"
        "```yaml\n- name: Configure AWS credentials\n  uses: aws-actions/configure-aws-credentials@v4\n  with:\n"
        "    role-to-assume: arn:aws:iam::123456789:role/GitHubActionsEKSDeployRole\n"
        "    aws-region: us-east-1\n```\n\n"
        "**IAM Trust Policy** (allows GitHub's OIDC provider to assume the role):\n"
        "```json\n{\n  \"Effect\": \"Allow\",\n  \"Principal\": {\"Federated\": \"arn:aws:iam::123456789:oidc-provider/token.actions.githubusercontent.com\"},\n"
        "  \"Action\": \"sts:AssumeRoleWithWebIdentity\",\n"
        "  \"Condition\": {\"StringLike\": {\"token.actions.githubusercontent.com:sub\": \"repo:your-org/your-repo:*\"}}\n}\n```\n\n"
        "No secrets in GitHub at all - credentials expire after each workflow run.",
        days_ago=d, hours_ago=6, minutes_ago=30,
        prompt_tokens=398, completion_tokens=567, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'GitHub Actions CI/CD'  [6 msgs]")
    return cid


def conv_api_docs(conn, user_id, project_id=None):
    """Conv 8 - Draft API documentation"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Write OpenAPI docs for /v3/orders endpoint", project_id,
         ts(days_ago=12), ts(days_ago=12, hours_ago=-2)),
    )
    d = 12
    insert_message(conn, cid, user_id, 0, "user",
        "Write the OpenAPI 3.1 spec for our GET /v3/orders/{order_id} endpoint. "
        "It returns order details with line items, accepts an ?expand=customer query param.",
        days_ago=d, hours_ago=3)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Here's the complete OpenAPI 3.1 spec for the endpoint:\n\n"
        "```yaml\npaths:\n  /v3/orders/{order_id}:\n    get:\n"
        "      operationId: getOrderById\n      summary: Retrieve a single order\n"
        "      description: |\n        Returns full order details including line items and pricing.\n"
        "        Use `?expand=customer` to inline the customer object instead of returning just the ID.\n"
        "      tags: [Orders]\n      parameters:\n"
        "        - name: order_id\n          in: path\n          required: true\n"
        "          schema: {type: string, pattern: '^ord_[a-zA-Z0-9]{16}$'}\n"
        "          example: ord_A3x9Km2pQrNvYt7W\n"
        "        - name: expand\n          in: query\n          required: false\n"
        "          schema: {type: array, items: {type: string, enum: [customer, shipping]}}\n"
        "          style: form\n          explode: false\n      responses:\n"
        "        '200':\n          description: Order found\n          content:\n"
        "            application/json:\n              schema:\n                $ref: '#/components/schemas/Order'\n"
        "        '404':\n          description: Order not found\n          content:\n"
        "            application/json:\n              schema:\n                $ref: '#/components/schemas/ErrorResponse'\n"
        "        '401':\n          $ref: '#/components/responses/Unauthorised'\n```",
        days_ago=d, hours_ago=2, minutes_ago=40,
        tool_name="write_file",
        tool_args={"path": "docs/openapi/orders.yaml", "content": "# openapi spec"},
        tool_result={"success": True, "bytes_written": 2134},
        prompt_tokens=423, completion_tokens=789, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "Now write the Python SDK example for this endpoint in the docs.",
        days_ago=d, hours_ago=2)
    insert_message(conn, cid, user_id, 3, "assistant",
        "```python\n# Python SDK example - GET /v3/orders/{order_id}\nimport orders_sdk\n\nclient = orders_sdk.Client(api_key='sk_live_...')\n\n"
        "# Basic retrieval\norder = client.orders.get('ord_A3x9Km2pQrNvYt7W')\nprint(order.status)          # 'fulfilled'\nprint(order.total_cents)     # 4999\n\n"
        "# With customer expansion\norder = client.orders.get(\n    'ord_A3x9Km2pQrNvYt7W',\n    expand=['customer']\n)\nprint(order.customer.email)  # 'alice@example.com'\n\n"
        "# Iterate line items\nfor item in order.line_items:\n    print(f'{item.product_name} x{item.quantity} @ ${item.unit_price / 100:.2f}')\n```",
        days_ago=d, hours_ago=1, minutes_ago=40,
        prompt_tokens=312, completion_tokens=389, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'OpenAPI docs for /v3/orders'  [4 msgs]")
    return cid


def conv_ml_debug(conn, user_id, project_id=None):
    """Conv 9 - Debug memory leak in ML training loop"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Debug GPU memory leak in PyTorch training loop", project_id,
         ts(days_ago=6), ts(days_ago=5)),
    )
    d = 6
    insert_message(conn, cid, user_id, 0, "user",
        "My PyTorch training loop leaks GPU memory - after epoch 3 it OOMs. "
        "I'm using `loss.backward()` and `optimizer.step()`. What am I missing?",
        days_ago=d, hours_ago=9)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Classic PyTorch memory leak - almost certainly one of these three culprits:\n\n"
        "**1. Accumulating loss for logging without `.item()`**\n"
        "```python\n# ❌ Keeps computation graph alive\ntotal_loss += loss\n\n"
        "# ✅ Detaches to Python float\ntotal_loss += loss.item()\n```\n\n"
        "**2. Storing tensors that hold a reference to the graph**\n"
        "```python\n# ❌ In a list across batches\nlosses.append(loss)\n\n"
        "# ✅ Detach first\nlosses.append(loss.detach().cpu())\n```\n\n"
        "**3. Forgetting `optimizer.zero_grad()` before backward**\n"
        "```python\noptimizer.zero_grad()  # ← must come before loss.backward()\nloss.backward()\noptimizer.step()\n```\n\n"
        "Check GPU usage mid-epoch with `torch.cuda.memory_allocated()` - if it grows linearly you've confirmed the leak.",
        days_ago=d, hours_ago=8, minutes_ago=45,
        prompt_tokens=334, completion_tokens=456, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "It was the `.item()` issue - I was summing loss tensors in a list for tensorboard logging. "
        "Fixed that, but now I'm getting 'CUDA error: device-side assert triggered'. How do I debug this?",
        days_ago=d, hours_ago=7)
    insert_message(conn, cid, user_id, 3, "assistant",
        "Device-side asserts are notoriously hard to debug because CUDA executes asynchronously - "
        "the error surfaces at the wrong point in your code.\n\n"
        "**Debugging steps:**\n\n"
        "1. **Enable synchronous execution** to get accurate stack traces:\n"
        "```bash\nCUDA_LAUNCH_BLOCKING=1 python train.py\n```\n\n"
        "2. **Common causes:**\n"
        "   - `NLLLoss` / `CrossEntropyLoss` receiving class indices outside `[0, num_classes-1]`\n"
        "   - `index_select` with out-of-bounds indices\n"
        "   - `-1` labels in your dataset (background class not handled)\n\n"
        "3. **Check your labels:**\n"
        "```python\nfor batch in train_loader:\n    _, labels = batch\n    assert labels.min() >= 0, f'Negative label: {labels.min()}'\n"
        "    assert labels.max() < num_classes, f'Label {labels.max()} >= {num_classes}'\n```",
        days_ago=d, hours_ago=6, minutes_ago=30,
        prompt_tokens=389, completion_tokens=512, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Debug GPU memory leak in PyTorch'  [4 msgs]")
    return cid


def conv_mobile_sync(conn, user_id, project_id=None):
    """Conv 10 - Mobile offline sync architecture"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Mobile offline sync architecture - conflict resolution strategy", project_id,
         ts(days_ago=3), ts(days_ago=2)),
    )
    d = 3
    insert_message(conn, cid, user_id, 0, "user",
        "I need to design offline-first sync for our React Native app. Users should be able to create "
        "orders and update their profile while offline, then sync when reconnected. How do I handle conflicts?",
        days_ago=d, hours_ago=12)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Offline-first sync is one of the hardest distributed systems problems in mobile. Here's the canonical approach:\n\n"
        "**Architecture: Event Sourcing + Operational Transformation**\n\n"
        "Don't sync *state*, sync *events* (mutations). Each change on the device produces an event:\n"
        "```json\n{\"id\": \"evt_uuid\", \"type\": \"ORDER_CREATED\", \"payload\": {...}, "
        "\"device_id\": \"d42\", \"created_at\": \"2026-05-15T10:22:00Z\", \"synced\": false}\n```\n\n"
        "**Conflict resolution options (pick one):**\n\n"
        "| Strategy | Best for | Trade-off |\n|---|---|---|\n"
        "| Last-Write-Wins (LWW) | Non-critical updates (profile name) | Data loss on concurrent edits |\n"
        "| Server Wins | Pricing/inventory | User changes silently overwritten |\n"
        "| Client Wins | Local-first UX | Server state ignored |\n"
        "| CRDT | Collaborative editing (notes, lists) | Complex implementation |\n\n"
        "For an order app: **LWW for profile fields, Server Wins for price/stock, append-only for order line items**.",
        days_ago=d, hours_ago=11, minutes_ago=40,
        prompt_tokens=456, completion_tokens=623, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "Which local storage should I use on device - SQLite, MMKV, or AsyncStorage?",
        days_ago=d, hours_ago=10, minutes_ago=30)
    insert_message(conn, cid, user_id, 3, "assistant",
        "**WatermelonDB** (built on SQLite) for your use case - it's specifically designed for "
        "offline-first React Native apps and has built-in sync primitives.\n\n"
        "| Option | Reads | Writes | Querying | Sync Support | Verdict |\n"
        "|---|---|---|---|---|---|\n"
        "| AsyncStorage | Slow | Slow | None | Manual | ❌ Too limited |\n"
        "| MMKV | ⚡ Fast | ⚡ Fast | Key-only | Manual | ✅ Config/tokens only |\n"
        "| SQLite (expo-sqlite) | Fast | Fast | Full SQL | Manual | ✅ Fine for small apps |\n"
        "| **WatermelonDB** | ⚡ Lazy | ⚡ Fast | Reactive | **Built-in** | ✅✅ Best for sync |\n\n"
        "WatermelonDB's sync protocol gives you a `pullChanges` / `pushChanges` API that maps "
        "directly to your backend sync endpoint - saves weeks of conflict logic.",
        days_ago=d, hours_ago=10,
        prompt_tokens=378, completion_tokens=445, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Mobile offline sync architecture'  [4 msgs]")
    return cid


def conv_sprint_planning(conn, user_id, project_id=None):
    """Conv 11 - Sprint planning"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Sprint 23 planning - scope and story point estimation", project_id,
         ts(days_ago=2), ts(days_ago=1)),
    )
    d = 2
    insert_message(conn, cid, user_id, 0, "user",
        "Help me plan sprint 23. We have these candidate stories:\n"
        "1. Add dark mode toggle to settings page\n"
        "2. Fix order history pagination bug\n3. Implement webhook retry queue\n"
        "4. Migrate auth to PKCE flow\n5. Write API docs for /v4/payments\n"
        "6. Performance audit of dashboard load time\n\nTeam capacity: 34 points.",
        days_ago=d, hours_ago=6)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Here's my estimate breakdown with recommended sprint scope:\n\n"
        "| Story | Points | Rationale |\n|---|---|---|\n"
        "| Fix order history pagination bug | 3 | Localised frontend bug - likely a keyset cursor issue |\n"
        "| Add dark mode toggle | 5 | CSS variable swap + localStorage persistence + test across 8 screens |\n"
        "| Write API docs /v4/payments | 5 | OpenAPI spec + 3 SDK examples; parallelisable |\n"
        "| Performance audit dashboard | 8 | Lighthouse run + waterfall analysis + at least 2 fix PRs |\n"
        "| Webhook retry queue | 8 | New Redis-backed queue + dead-letter handling + admin UI |\n"
        "| Migrate auth to PKCE | 13 | Security-sensitive, requires auth server changes + mobile SDK update |\n\n"
        "**Recommended sprint scope** (34 pts): items 1–5 = **29 pts** - leaves 5 pts buffer for unplanned work.\n\n"
        "PKCE migration (13 pts) should be its own sprint - it touches auth, mobile, and requires security review.",
        days_ago=d, hours_ago=5, minutes_ago=45,
        prompt_tokens=389, completion_tokens=512, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "Agreed. Can you write the Jira-style story descriptions for the webhook retry queue?",
        days_ago=d, hours_ago=5)
    insert_message(conn, cid, user_id, 3, "assistant",
        "**Story: Implement Webhook Retry Queue**\n\n"
        "**As a** platform operator,\n**I want** failed webhooks to be automatically retried with "
        "exponential backoff,\n**So that** transient downstream failures don't result in lost events.\n\n"
        "**Acceptance Criteria:**\n"
        "- [ ] Failed webhook deliveries (non-2xx response or timeout) are queued in Redis with a TTL of 72h\n"
        "- [ ] Retry schedule: 1 min → 5 min → 30 min → 2h → 8h → 24h (max 6 attempts)\n"
        "- [ ] After 6 failures the event moves to the dead-letter queue\n"
        "- [ ] `GET /admin/webhooks/dead-letter` lists failed events with error details\n"
        "- [ ] `POST /admin/webhooks/dead-letter/{id}/retry` manually re-queues a dead-letter event\n"
        "- [ ] Prometheus counter `webhook_retries_total{status}` is incremented on each attempt\n\n"
        "**Technical Notes:**\nUse `rq` (Redis Queue) with custom retry middleware. "
        "Store the original payload as JSON blob; do not mutate on retry.",
        days_ago=d, hours_ago=4, minutes_ago=30,
        prompt_tokens=356, completion_tokens=489, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Sprint 23 planning'  [4 msgs]")
    return cid


def conv_security_owasp(conn, user_id, project_id=None):
    """Conv 13 - OWASP API security audit"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "OWASP Top 10 API security audit - findings and remediation plan", project_id,
         ts(days_ago=9), ts(days_ago=8)),
    )
    d = 9
    insert_message(conn, cid, user_id, 0, "user",
        "Run an OWASP Top 10 audit on our FastAPI backend. I want a prioritised findings list "
        "with severity ratings and concrete fixes for anything you find.",
        days_ago=d, hours_ago=10)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Starting the audit. Let me read the main API surface first.",
        days_ago=d, hours_ago=9, minutes_ago=55,
        tool_name="read_file",
        tool_args={"path": "api/", "recursive": True},
        tool_result={"files": ["api/auth.py", "api/conversations.py", "api/executions.py", "api/memory.py", "api/rag.py"]},
        prompt_tokens=198, completion_tokens=87, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "assistant",
        "**OWASP API Security Audit - EVA Backend**\n\n"
        "| # | Finding | Severity | OWASP Category |\n|---|---|---|---|\n"
        "| 1 | SSE token not rate-limited - can enumerate valid tokens | HIGH | API2: Broken Auth |\n"
        "| 2 | `/rag/upload` missing file type validation - accepts any MIME | HIGH | API8: Security Misconfiguration |\n"
        "| 3 | User ID taken from JWT but not validated against DB on every request | MEDIUM | API1: Broken Object Auth |\n"
        "| 4 | Error responses leak internal paths in stack traces | MEDIUM | API3: Excessive Data Exposure |\n"
        "| 5 | No rate limiting on `/auth/login` - brute-force possible | MEDIUM | API4: Rate Limiting |\n"
        "| 6 | `Access-Control-Allow-Origin: *` on SSE endpoint | LOW | API7: Security Misconfiguration |\n\n"
        "**Immediate fixes (HIGH severity):**\n\n"
        "```python\n# Fix 1: Rate limit SSE token endpoint\nfrom slowapi import Limiter\nlimiter = Limiter(key_func=get_remote_address)\n\n"
        "@router.post('/auth/sse-token')\n@limiter.limit('10/minute')\nasync def get_sse_token(request: Request, ...):\n    ...\n\n"
        "# Fix 2: Validate file types on upload\nALLOWED_TYPES = {'application/pdf', 'text/plain', 'text/markdown'}\n\n"
        "async def upload_file(file: UploadFile):\n    if file.content_type not in ALLOWED_TYPES:\n"
        "        raise HTTPException(415, f'Unsupported media type: {file.content_type}')\n```",
        days_ago=d, hours_ago=9, minutes_ago=30,
        prompt_tokens=1234, completion_tokens=987, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 3, "user",
        "Fix #3 and #5 now - validate user from DB on every request and add login rate limiting.",
        days_ago=d, hours_ago=8)
    insert_message(conn, cid, user_id, 4, "assistant",
        "Implementing both fixes:",
        days_ago=d, hours_ago=7, minutes_ago=45,
        tool_name="write_file",
        tool_args={"path": "api/deps.py", "content": "# get_current_user now validates against DB + login rate limiter added"},
        tool_result={"success": True, "bytes_written": 1456},
        prompt_tokens=567, completion_tokens=823, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 5, "assistant",
        "Both fixes applied and tests passing. Here's the summary:\n\n"
        "**Fix 3 - DB validation on every request** (`api/deps.py`):\n"
        "```python\nasync def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):\n"
        "    payload = decode_jwt(token)\n    user_id = payload.get('sub')\n"
        "    # Previously: returned payload directly - no DB check\n"
        "    # Now: validate user still exists and is not suspended\n"
        "    user = await db.get(User, user_id)\n"
        "    if not user or user.suspended:\n        raise HTTPException(401, 'User not found or suspended')\n"
        "    return user\n```\n\n"
        "**Fix 5 - Login rate limiting** (`api/auth.py`):\n"
        "```python\n@router.post('/login')\n@limiter.limit('5/minute;20/hour')\nasync def login(request: Request, ...):\n    ...\n```\n\n"
        "Run `pytest tests/test_auth.py tests/test_security.py -v` to verify.",
        days_ago=d, hours_ago=7, minutes_ago=20,
        prompt_tokens=445, completion_tokens=612, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'OWASP Top 10 API security audit'  [6 msgs]")
    return cid


def conv_security_cve(conn, user_id, project_id=None):
    """Conv 14 - CVE remediation in dependency chain"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Remediate CVE-2025-44228 in dependency chain - log4j equivalent in Python", project_id,
         ts(days_ago=4), ts(days_ago=3)),
    )
    d = 4
    insert_message(conn, cid, user_id, 0, "user",
        "Safety check flagged CVE-2025-44228 in our `structlog` dependency. CVSS 9.1 CRITICAL. "
        "How serious is this and what do we do?",
        days_ago=d, hours_ago=7)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Checking the CVE details and your dependency tree now.",
        days_ago=d, hours_ago=6, minutes_ago=55,
        tool_name="shell_command",
        tool_args={"command": "pip show structlog && pip-audit --vulnerability-service osv -r requirements.txt 2>&1"},
        tool_result={"exit_code": 0, "stdout": "structlog 23.1.0\nCVE-2025-44228: structlog <23.3.0 - unsafe deserialization in JSONRenderer when STRUCTLOG_UNSAFE_EVAL=1 is set"},
        prompt_tokens=234, completion_tokens=156, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "assistant",
        "**Severity Assessment: HIGH but mitigated in your environment.**\n\n"
        "The CVE requires `STRUCTLOG_UNSAFE_EVAL=1` to be set in the environment - this is an opt-in "
        "flag that enables `eval()` in the JSON renderer. Your `.env` doesn't set it, so you are "
        "**not currently exploitable**.\n\n"
        "However: pin it anyway - defence in depth.\n\n"
        "```bash\n# requirements.txt - bump to patched version\nstructlog>=23.3.0  # was ==23.1.0\n```\n\n"
        "Then rebuild and re-scan:\n"
        "```bash\npip install --upgrade structlog\npip-audit -r requirements.txt\ndocker build -t eva-backend:patched .\ntrivy image eva-backend:patched\n```",
        days_ago=d, hours_ago=6, minutes_ago=30,
        prompt_tokens=389, completion_tokens=456, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 3, "user",
        "Done - trivy shows clean. Update the requirements.txt and commit.",
        days_ago=d, hours_ago=5)
    insert_message(conn, cid, user_id, 4, "assistant",
        "Updating requirements.txt and committing with a security-tagged commit message.",
        days_ago=d, hours_ago=4, minutes_ago=55,
        tool_name="shell_command",
        tool_args={"command": "sed -i 's/structlog==23.1.0/structlog>=23.3.0/' requirements.txt && git add requirements.txt && git commit -m 'fix(security): patch CVE-2025-44228 - bump structlog to >=23.3.0\n\nCVSS 9.1 CRITICAL - unsafe eval in JSONRenderer.\nNot exploitable in our env (STRUCTLOG_UNSAFE_EVAL not set)\nbut patching as defence in depth.\n\nRefs: CVE-2025-44228'"},
        tool_result={"exit_code": 0, "stdout": "[main b8f2e34] fix(security): patch CVE-2025-44228 - bump structlog to >=23.3.0\n 1 file changed, 1 insertion(+), 1 deletion(-)"},
        prompt_tokens=189, completion_tokens=134, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Remediate CVE-2025-44228 in dependency chain'  [5 msgs]")
    return cid


def conv_marketing_drip(conn, user_id, project_id=None):
    """Conv 15 - Email drip campaign automation"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Email drip campaign automation - onboarding sequence design", project_id,
         ts(days_ago=11), ts(days_ago=10)),
    )
    d = 11
    insert_message(conn, cid, user_id, 0, "user",
        "Design a 7-email onboarding drip sequence for new SaaS signups. "
        "Each email should move users toward their first 'aha moment' - completing their first project.",
        days_ago=d, hours_ago=9)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Here's a 7-email onboarding sequence built around the activation journey:\n\n"
        "| Day | Subject | Goal | CTA |\n|---|---|---|---|\n"
        "| 0 | Welcome to [Product] - let's get you started | Immediate value, no friction | Create first project |\n"
        "| 1 | Your project is one step away | Nudge incomplete signups | Resume setup |\n"
        "| 3 | How [similar company] saved 4h/week with [feature] | Social proof + feature discovery | Watch 2-min demo |\n"
        "| 5 | You're missing this (most users skip it) | Highlight power feature | Enable integrations |\n"
        "| 7 | Quick check-in - any questions? | Human touch, reduce churn signal | Reply to this email |\n"
        "| 10 | 3 teams seeing results this week | FOMO + community | Read case study |\n"
        "| 14 | Last onboarding tip (then we'll leave you alone) | Final activation push | Invite a teammate |\n\n"
        "**Key principles:**\n- Day 0 email sends within 2 minutes of signup (use webhook trigger)\n"
        "- Exit sequence immediately when user completes first project\n"
        "- Personalise subject lines with first name + company when available",
        days_ago=d, hours_ago=8, minutes_ago=30,
        prompt_tokens=456, completion_tokens=612, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "Write the full copy for the Day 0 and Day 1 emails.",
        days_ago=d, hours_ago=7, minutes_ago=30)
    insert_message(conn, cid, user_id, 3, "assistant",
        "**Day 0 Email - Sent within 2 minutes of signup:**\n\n"
        "---\n**Subject:** Welcome, {first_name} - your workspace is ready\n\n"
        "Hi {first_name},\n\nYour account is live. Here's the fastest path to your first result:\n\n"
        "1. **Create a project** (30 seconds) - give it a name, pick a template\n"
        "2. **Start a conversation** - describe what you're building\n"
        "3. **Watch EVA plan it** - you'll see the Plan→Execute→Verify loop in action\n\n"
        "Most users have their first working output in under 10 minutes.\n\n"
        "[Create your first project →]\n\n---\n\n"
        "**Day 1 Email - Sent if no project created:**\n\n"
        "---\n**Subject:** Still getting set up? Here's a shortcut\n\n"
        "Hi {first_name},\n\nYou signed up yesterday but haven't created a project yet - "
        "that's usually the bit that trips people up.\n\n"
        "The most common question we get: *'What should my first project be?'*\n\n"
        "Answer: **whatever you're actually working on right now.**\n\n"
        "You don't need to structure it perfectly. Just paste in what you're trying to build "
        "and EVA will help you figure out the rest.\n\n"
        "[Continue where you left off →]\n\n"
        "If you're stuck on something specific, just reply to this email - I read everything.\n\n"
        "— The EVA team\n---",
        days_ago=d, hours_ago=7,
        tool_name="write_file",
        tool_args={"path": "marketing/emails/onboarding_d0_d1.md", "content": "# Day 0 and Day 1 onboarding emails"},
        tool_result={"success": True, "bytes_written": 1876},
        prompt_tokens=523, completion_tokens=934, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Email drip campaign - onboarding sequence'  [4 msgs]")
    return cid


def conv_marketing_segmentation(conn, user_id, project_id=None):
    """Conv 16 - Audience segmentation for re-engagement"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Audience segmentation strategy - re-engagement campaign for churned users", project_id,
         ts(days_ago=7), ts(days_ago=6)),
    )
    d = 7
    insert_message(conn, cid, user_id, 0, "user",
        "We have ~1200 users who signed up 30-90 days ago but haven't logged in for 21+ days. "
        "Design a re-engagement segmentation strategy and campaign.",
        days_ago=d, hours_ago=8)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Re-engagement works best when you treat different churn reasons differently. "
        "Here's a segmentation framework for your 1,200 lapsed users:\n\n"
        "**Segment A - Never activated (signup → 0 projects created): ~35%**\n"
        "- Root cause: didn't understand value prop\n"
        "- Message: 'See what EVA can do in 60 seconds' - video-first email\n\n"
        "**Segment B - Activated but stalled (1 project, no conversations in 21d): ~40%**\n"
        "- Root cause: got stuck, hit a wall\n"
        "- Message: 'Stuck? Here are 3 things your project is perfect for' - personalised to their project type\n\n"
        "**Segment C - Active then ghost (was using weekly, stopped cold): ~25%**\n"
        "- Root cause: competing tool, price objection, or job change\n"
        "- Message: 'We've shipped X improvements since you left' - changelog highlight\n\n"
        "**Timing:** Send all segments on Tuesday 10am in their timezone. "
        "Follow up 5 days later only to non-openers. After 2 misses, suppress for 60 days.",
        days_ago=d, hours_ago=7, minutes_ago=30,
        prompt_tokens=512, completion_tokens=678, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "Write the SQL to pull each segment from our DB, assuming we have users, projects, and conversations tables.",
        days_ago=d, hours_ago=6)
    insert_message(conn, cid, user_id, 3, "assistant",
        "```sql\n-- Segment A: Signed up 30-90 days ago, zero projects created\nSELECT u.id, u.email, u.created_at\nFROM users u\nLEFT JOIN projects p ON p.user_id = u.id\nWHERE u.created_at BETWEEN NOW() - INTERVAL '90 days' AND NOW() - INTERVAL '30 days'\n  AND p.id IS NULL\n  AND u.last_active_at < NOW() - INTERVAL '21 days';\n\n"
        "-- Segment B: Has projects but no recent conversations\nSELECT u.id, u.email, p.name AS project_name, p.created_at AS project_created\nFROM users u\nJOIN projects p ON p.user_id = u.id\nLEFT JOIN conversations c ON c.project_id = p.id\n  AND c.created_at > NOW() - INTERVAL '21 days'\nWHERE u.created_at BETWEEN NOW() - INTERVAL '90 days' AND NOW() - INTERVAL '30 days'\n  AND c.id IS NULL\nGROUP BY u.id, u.email, p.name, p.created_at\nHAVING COUNT(p.id) BETWEEN 1 AND 3;\n\n"
        "-- Segment C: Was active (3+ convs per week), then stopped cold\nWITH weekly_activity AS (\n  SELECT c.user_id,\n         DATE_TRUNC('week', c.created_at) AS week,\n         COUNT(*) AS conv_count\n  FROM conversations c\n  GROUP BY 1, 2\n)\nSELECT DISTINCT u.id, u.email\nFROM users u\nJOIN weekly_activity wa ON wa.user_id = u.id AND wa.conv_count >= 3\nWHERE NOT EXISTS (\n  SELECT 1 FROM conversations c\n  WHERE c.user_id = u.id AND c.created_at > NOW() - INTERVAL '21 days'\n);\n```",
        days_ago=d, hours_ago=5, minutes_ago=30,
        tool_name="write_file",
        tool_args={"path": "marketing/queries/re_engagement_segments.sql", "content": "-- segmentation queries"},
        tool_result={"success": True, "bytes_written": 1234},
        prompt_tokens=489, completion_tokens=712, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Audience segmentation - re-engagement campaign'  [4 msgs]")
    return cid


def conv_dw_etl_pipeline(conn, user_id, project_id=None):
    """Conv 17 - Data warehouse ETL pipeline design"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Snowflake migration - ETL pipeline design with dbt and Airbyte", project_id,
         ts(days_ago=15), ts(days_ago=14)),
    )
    d = 15
    insert_message(conn, cid, user_id, 0, "user",
        "We're migrating our analytics from a home-grown PostgreSQL warehouse to Snowflake. "
        "Currently using hand-rolled SQL scripts for ETL. What's the modern stack for this?",
        days_ago=d, hours_ago=7)
    insert_message(conn, cid, user_id, 1, "assistant",
        "The canonical modern data stack for a Snowflake migration is:\n\n"
        "```\nSource DBs  →  Airbyte (ingestion)  →  Snowflake raw layer\n                                          ↓\n                                     dbt (transform)  →  Snowflake marts\n                                          ↓\n                                     Metabase / Superset (BI)\n```\n\n"
        "**Why this stack:**\n"
        "- **Airbyte**: 300+ connectors (Postgres, Stripe, Salesforce, S3), CDC support, runs as Docker/K8s\n"
        "- **dbt**: SQL-first transformation with tests, lineage, and documentation built in\n"
        "- **Snowflake**: Compute/storage separation - scale warehouse up for transforms, down to zero at idle\n\n"
        "**Migration phases:**\n"
        "1. Set up Airbyte to replicate source tables into `RAW` schema (full-refresh first, then CDC)\n"
        "2. Port your existing SQL scripts to dbt models layer by layer (staging → intermediate → marts)\n"
        "3. Run old and new pipelines in parallel for 2 weeks - compare row counts and key metrics\n"
        "4. Cut over BI tool to Snowflake marts and decommission old warehouse",
        days_ago=d, hours_ago=6, minutes_ago=30,
        prompt_tokens=512, completion_tokens=712, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "Write a sample dbt model for our orders mart - we have raw orders, order_items, and customers tables.",
        days_ago=d, hours_ago=5, minutes_ago=30)
    insert_message(conn, cid, user_id, 3, "assistant",
        "Here's a layered dbt model set for the orders mart:\n\n"
        "```sql\n-- models/staging/stg_orders.sql\nSELECT\n    order_id,\n    customer_id,\n"
        "    status,\n    created_at::TIMESTAMP_TZ AS ordered_at,\n"
        "    updated_at::TIMESTAMP_TZ AS updated_at\nFROM {{ source('raw', 'orders') }}\nWHERE order_id IS NOT NULL\n\n"
        "---\n-- models/staging/stg_order_items.sql\nSELECT\n    item_id,\n    order_id,\n    product_id,\n"
        "    quantity,\n    unit_price_cents,\n    quantity * unit_price_cents AS line_total_cents\nFROM {{ source('raw', 'order_items') }}\n\n"
        "---\n-- models/marts/fct_orders.sql\nSELECT\n    o.order_id,\n    o.customer_id,\n    o.ordered_at,\n    o.status,\n"
        "    SUM(oi.line_total_cents) AS order_total_cents,\n    COUNT(oi.item_id)       AS item_count,\n"
        "    MAX(oi.unit_price_cents) AS max_item_price_cents\nFROM {{ ref('stg_orders') }} o\nJOIN {{ ref('stg_order_items') }} oi USING (order_id)\nGROUP BY 1,2,3,4\n```\n\n"
        "Add to `schema.yml` for built-in tests:\n"
        "```yaml\nmodels:\n  - name: fct_orders\n    columns:\n      - name: order_id\n        tests: [unique, not_null]\n"
        "      - name: order_total_cents\n        tests:\n          - dbt_utils.expression_is_true:\n              expression: '>= 0'\n```",
        days_ago=d, hours_ago=5,
        tool_name="write_file",
        tool_args={"path": "dbt/models/marts/fct_orders.sql", "content": "-- fct_orders dbt model"},
        tool_result={"success": True, "bytes_written": 987},
        prompt_tokens=578, completion_tokens=834, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Snowflake migration - ETL pipeline design with dbt'  [4 msgs]")
    return cid


def conv_dw_data_quality(conn, user_id, project_id=None):
    """Conv 18 - Data quality checks and schema validation"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "Data quality framework - schema validation and anomaly detection for warehouse", project_id,
         ts(days_ago=13), ts(days_ago=12)),
    )
    d = 13
    insert_message(conn, cid, user_id, 0, "user",
        "After our first week running dbt on Snowflake, we're seeing silent data quality issues "
        "- wrong row counts, null values where we don't expect them. How do we build a proper DQ framework?",
        days_ago=d, hours_ago=8)
    insert_message(conn, cid, user_id, 1, "assistant",
        "Silent DQ failures are the worst - they corrupt dashboards for days before anyone notices. "
        "Here's a three-layer defence:\n\n"
        "**Layer 1 - dbt tests (schema-level, runs on every pipeline):**\n"
        "```yaml\n# schema.yml\n- name: fct_orders\n  columns:\n"
        "    - name: order_id\n      tests: [unique, not_null]\n"
        "    - name: order_total_cents\n      tests:\n        - dbt_utils.expression_is_true:\n            expression: '>= 0'\n"
        "    - name: status\n      tests:\n        - accepted_values:\n            values: ['pending','paid','cancelled','refunded']\n```\n\n"
        "**Layer 2 - dbt-expectations (statistical, runs on mart refresh):**\n"
        "```yaml\n- dbt_expectations.expect_column_values_to_be_between:\n    column_name: order_total_cents\n    min_value: 0\n    max_value: 100000000  # $1M sanity cap\n"
        "- dbt_expectations.expect_table_row_count_to_be_between:\n    min_value: 1000  # alert if orders drop below 1k rows\n```\n\n"
        "**Layer 3 - Great Expectations on the raw Airbyte layer (catches upstream issues before dbt runs):**\n"
        "Run `ge checkpoint run raw_orders_checkpoint` as a pre-step in your pipeline.",
        days_ago=d, hours_ago=7, minutes_ago=20,
        prompt_tokens=567, completion_tokens=789, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "Write a Python script that queries Snowflake and alerts on anomalies - "
        "things like row count drops >20% day-over-day or null rate spikes.",
        days_ago=d, hours_ago=6)
    insert_message(conn, cid, user_id, 3, "assistant",
        "Here's an anomaly detection script that runs as a daily scheduled job:\n\n"
        "```python\n# scripts/dq_anomaly_check.py\nimport snowflake.connector\nfrom datetime import date, timedelta\n\n"
        "CHECKS = [\n    {\n        'table': 'MARTS.FCT_ORDERS',\n        'metric': 'row_count',\n        'query': 'SELECT COUNT(*) FROM MARTS.FCT_ORDERS WHERE ordered_at::DATE = CURRENT_DATE - 1',\n"
        "        'baseline_query': 'SELECT COUNT(*) FROM MARTS.FCT_ORDERS WHERE ordered_at::DATE = CURRENT_DATE - 2',\n"
        "        'threshold_pct': 20,\n    },\n"
        "    {\n        'table': 'MARTS.FCT_ORDERS',\n        'metric': 'null_customer_id_rate',\n"
        "        'query': 'SELECT 100.0 * SUM(CASE WHEN customer_id IS NULL THEN 1 ELSE 0 END) / COUNT(*) FROM MARTS.FCT_ORDERS',\n"
        "        'baseline_query': None,  # static threshold\n        'static_max': 0.5,       # alert if >0.5% nulls\n    },\n]\n\n"
        "def run_checks(conn):\n    alerts = []\n    for check in CHECKS:\n        cur = conn.cursor()\n        cur.execute(check['query'])\n        current = cur.fetchone()[0]\n"
        "        if check.get('baseline_query'):\n            cur.execute(check['baseline_query'])\n            baseline = cur.fetchone()[0]\n"
        "            pct_change = abs((current - baseline) / max(baseline, 1)) * 100\n"
        "            if pct_change > check['threshold_pct']:\n                alerts.append(f\"{check['table']}.{check['metric']}: {pct_change:.1f}% change (current={current}, baseline={baseline})\")\n"
        "        elif check.get('static_max') and current > check['static_max']:\n"
        "            alerts.append(f\"{check['table']}.{check['metric']}: {current:.2f}% (max allowed: {check['static_max']}%)\")\n"
        "    return alerts\n```",
        days_ago=d, hours_ago=5, minutes_ago=30,
        tool_name="write_file",
        tool_args={"path": "scripts/dq_anomaly_check.py", "content": "# data quality anomaly detection"},
        tool_result={"success": True, "bytes_written": 2134},
        prompt_tokens=612, completion_tokens=978, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'Data quality framework - schema validation and anomaly detection'  [4 msgs]")
    return cid


def conv_react_concurrent(conn, user_id, project_id=None):
    """Conv 12 - React 18 concurrent rendering"""
    cid = new_id()
    conn.execute(
        "INSERT INTO conversations (id, user_id, session_id, title, project_id, created_at, updated_at) "
        "VALUES (?,?,NULL,?,?,?,?)",
        (cid, user_id, "React 18 concurrent features - useTransition and Suspense", project_id,
         ts(days_ago=1), ts(hours_ago=4)),
    )
    d = 1
    insert_message(conn, cid, user_id, 0, "user",
        "Explain React 18's concurrent rendering. What's useTransition actually for? "
        "I've read the docs but I'm not clear on when to use it vs just useState.",
        days_ago=d, hours_ago=8)
    insert_message(conn, cid, user_id, 1, "assistant",
        "**Concurrent React** lets React interrupt, pause, and resume renders. Before React 18, "
        "every state update was synchronous and blocking - a slow render would freeze the UI.\n\n"
        "`useTransition` marks a state update as **non-urgent**. React can interrupt it to handle "
        "urgent updates (like typing) first.\n\n"
        "```tsx\nconst [isPending, startTransition] = useTransition();\n\n"
        "function handleSearch(query: string) {\n  // Urgent - update input immediately\n  setQuery(query);\n\n"
        "  // Non-urgent - filter 10k items, can be interrupted\n  startTransition(() => {\n"
        "    setFilteredResults(bigList.filter(item => item.name.includes(query)));\n  });\n}\n```\n\n"
        "**The key insight:** without `startTransition`, every keystroke waits for the full filter. "
        "With it, React keeps the input snappy and shows `isPending` while the list catches up.",
        days_ago=d, hours_ago=7, minutes_ago=45,
        prompt_tokens=312, completion_tokens=489, model="qwen3:8b", provider="ollama")
    insert_message(conn, cid, user_id, 2, "user",
        "How does Suspense fit with data fetching in React 18? Is it production-ready now?",
        days_ago=d, hours_ago=6, minutes_ago=30)
    insert_message(conn, cid, user_id, 3, "assistant",
        "**Suspense for data fetching** is production-ready in React 18 via framework integration "
        "(Next.js App Router, Remix) - using it directly in CRA/Vite still requires a Suspense-compatible "
        "data library like TanStack Query v5.\n\n"
        "```tsx\n// TanStack Query v5 + Suspense\nfunction OrderList({ userId }: { userId: string }) {\n"
        "  // useSuspenseQuery throws a Promise until data is ready\n"
        "  const { data } = useSuspenseQuery({\n"
        "    queryKey: ['orders', userId],\n    queryFn: () => ordersApi.list(userId),\n  });\n\n"
        "  return <ul>{data.map(o => <OrderRow key={o.id} order={o} />)}</ul>;\n}\n\n"
        "// In parent - Suspense boundary catches the Promise\n"
        "<Suspense fallback={<OrderSkeleton />}>\n  <OrderList userId={userId} />\n</Suspense>\n```\n\n"
        "**Benefits over `isLoading` checks:**\n"
        "- `data` is guaranteed non-null inside the component - no `if (!data) return null`\n"
        "- Loading states compose naturally up the tree\n"
        "- Works with `useTransition` for route transitions without flash-of-spinner",
        days_ago=d, hours_ago=6,
        tool_name="web_search",
        tool_args={"query": "React 18 Suspense data fetching TanStack Query v5 production 2026"},
        tool_result={"results": [{"title": "TanStack Query v5 Suspense guide", "url": "https://tanstack.com/query/latest/docs/react/guides/suspense"}]},
        prompt_tokens=423, completion_tokens=612, model="qwen3:8b", provider="ollama")
    conn.commit()
    ok("conv: 'React 18 concurrent features'  [4 msgs]")
    return cid


# ── Conversation summaries ─────────────────────────────────────────────────

def seed_summaries(conn, user_id, conv_ids_with_last_msg):
    inserted = 0
    for (cid, last_msg_id, summary_text, days_ago) in conv_ids_with_last_msg:
        conn.execute(
            """
            INSERT OR IGNORE INTO conversation_summaries
                (id, user_id, conversation_id, summary_text,
                 last_message_id, token_estimate, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (new_id(), user_id, cid, summary_text,
             last_msg_id, len(summary_text) // 4,
             ts(days_ago=days_ago)),
        )
        inserted += 1
    conn.commit()
    ok(f"conversation_summaries: {inserted} rows")
    return inserted


# ── Main seeder ────────────────────────────────────────────────────────────

def run(db_path=None) -> dict:
    head("02 : Conversations, Messages, Tool Calls & Usage Logs")
    conn = connect(find_db_path(db_path))
    user_id = get_user_id(conn)

    # Idempotency: check for a known seed title, not row count
    # (user may have other test conversations - don't skip based on count alone)
    already = count(conn, "conversations",
                    "user_id = ? AND title = ?",
                    (user_id, "Python async patterns in FastAPI"))
    if already > 0:
        warn("seed conversations already present - skipping")
        return {"conversations": 0, "messages": 0, "tool_calls": 0}

    # Resolve project IDs (may be None if projects not seeded yet)
    def pid(name): return get_project_id(conn, user_id, name)

    conv_ids = []
    conv_ids.append(conv_python_async(conn, user_id))
    conv_ids.append(conv_auth_refactor(conn, user_id, pid("EVA Backend v5")))
    conv_ids.append(conv_analytics_dashboard(conn, user_id, pid("Customer Analytics Dashboard")))
    conv_ids.append(conv_transformer_explainer(conn, user_id))
    conv_ids.append(conv_payment_tests(conn, user_id, pid("E-Commerce Platform v3")))
    conv_ids.append(conv_postgres_query(conn, user_id, pid("E-Commerce Platform v3")))
    conv_ids.append(conv_cicd_setup(conn, user_id, pid("DevOps Infrastructure Overhaul")))
    conv_ids.append(conv_api_docs(conn, user_id, pid("REST API Documentation v3")))
    conv_ids.append(conv_ml_debug(conn, user_id, pid("ML Training Pipeline Automation")))
    conv_ids.append(conv_mobile_sync(conn, user_id, pid("Customer Mobile App MVP")))
    conv_ids.append(conv_sprint_planning(conn, user_id))
    conv_ids.append(conv_react_concurrent(conn, user_id))
    conv_ids.append(conv_security_owasp(conn, user_id, pid("Security Audit & Hardening 2026")))
    conv_ids.append(conv_security_cve(conn, user_id, pid("Security Audit & Hardening 2026")))
    conv_ids.append(conv_marketing_drip(conn, user_id, pid("Marketing Automation Suite")))
    conv_ids.append(conv_marketing_segmentation(conn, user_id, pid("Marketing Automation Suite")))
    conv_ids.append(conv_dw_etl_pipeline(conn, user_id, pid("Data Warehouse Migration")))
    conv_ids.append(conv_dw_data_quality(conn, user_id, pid("Data Warehouse Migration")))

    # Add a few conversation summaries for the older conversations
    # We need a last_message_id - just grab one per conv
    summaries_data = []
    for cid in conv_ids[:5]:
        row = conn.execute(
            "SELECT id FROM messages WHERE conversation_id=? ORDER BY sequence DESC LIMIT 1", (cid,)
        ).fetchone()
        if row:
            summaries_data.append((
                cid, row["id"],
                "Multi-turn technical conversation covering implementation details, "
                "code examples, and tool-assisted development tasks.",
                14,
            ))
    seed_summaries(conn, user_id, summaries_data)

    msg_count  = count(conn, "messages",    "conversation_id IN (SELECT id FROM conversations WHERE user_id=?)", (user_id,))
    tool_count = count(conn, "tool_calls",  "message_id IN (SELECT id FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id=?))", (user_id,))

    info(f"Total messages:   {msg_count}")
    info(f"Total tool_calls: {tool_count}")

    return {
        "conversations": len(conv_ids),
        "messages":   msg_count,
        "tool_calls": tool_count,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db-path", default=None)
    args = p.parse_args()
    result = run(args.db_path)
    print(f"\n  Done: {result}")
