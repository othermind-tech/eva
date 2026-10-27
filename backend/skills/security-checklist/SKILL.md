---
name: security-checklist
description: "Apply when writing or reviewing any code that handles user input,
  authentication, file access, database queries, or external API calls. Triggers
  automatically in agent and plan_execute modes when the task involves writing new
  endpoints, handling user data, or changing authentication logic. Use this as a
  always-on security lens during implementation — not just during dedicated security
  reviews."
license: MIT
allowed-tools: [read_file, web_search]
compatibility: "Language-agnostic. Examples use Python/FastAPI but principles apply universally."
---

## Security Checklist

Apply this checklist as a continuous lens during all coding tasks. Security is
not a separate pass — it is part of writing correct code.

---

### Input Validation

Before any user-supplied value is used, it must be validated:

- [ ] Is the type correct? (string, int, UUID, etc.)
- [ ] Is the length bounded? (no unbounded string inputs)
- [ ] Does the value match an expected format? (regex, enum, range)
- [ ] Is whitelist validation preferred over blacklist where possible?
- [ ] Are validation errors returned as 400/422 — not 500?

```python
# FastAPI example — Pydantic validates automatically
class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9\-]+$')
    max_steps: int = Field(ge=1, le=50)
```

---

### SQL Injection Prevention

**Always use parameterised queries — never string concatenation:**

```python
# NEVER do this
query = f"SELECT * FROM users WHERE name = '{user_input}'"

# Always do this
result = await db.execute(
    select(User).where(User.name == user_input)
)
# Or with raw SQL:
await db.execute(text("SELECT * FROM users WHERE name = :name"), {"name": user_input})
```

---

### Command Injection Prevention

**Never pass unsanitised user input to shell commands:**

```python
# NEVER do this
os.system(f"git clone {user_url}")

# Safe alternative — use subprocess with a list (not shell=True)
import subprocess, shlex
# Validate URL format first, then:
subprocess.run(["git", "clone", "--", validated_url], check=True)
```

---

### Path Traversal Prevention

**Resolve and validate paths before file operations:**

```python
import pathlib

BASE_DIR = pathlib.Path("/app/user_files").resolve()

def safe_open(user_path: str) -> pathlib.Path:
    target = (BASE_DIR / user_path).resolve()
    if not str(target).startswith(str(BASE_DIR)):
        raise ValueError("Path traversal attempt detected")
    return target
```

---

### Authentication & Authorisation

- [ ] Is every sensitive endpoint behind an authentication check?
- [ ] Is the authenticated user's ID taken from the verified token — never from request body/params?
- [ ] Are resource ownership checks performed (user A cannot access user B's data)?
- [ ] Are admin-only endpoints protected by a role check, not just authentication?

```python
# Correct — user_id from verified JWT, not from request
async def get_agent(name: str, auth = Depends(get_current_auth)):
    user_id = auth["user_id"]  # from verified token
    # NOT: user_id = request.query_params["user_id"]  ← spoofable
    agent = await registry.get(user_id, name)
```

---

### Secrets Management

- [ ] No secrets, passwords, or API keys in source code.
- [ ] No secrets in log output (mask tokens, passwords in log statements).
- [ ] No secrets in error messages returned to clients.
- [ ] Secrets loaded from environment variables or a secrets manager.
- [ ] `.env` files in `.gitignore` — never committed.

```python
# Load from environment, never hardcode
import os
API_KEY = os.environ["PROVIDER_API_KEY"]  # raises KeyError if missing — intentional

# Mask in logs
logger.info("Connecting to provider", extra={"api_key": "***"})
```

---

### Cryptography

- [ ] Passwords hashed with bcrypt, argon2, or scrypt — never MD5/SHA-1/plain.
- [ ] JWT tokens validated: signature, expiry (`exp`), issuer (`iss`), audience (`aud`).
- [ ] TLS certificate validation enabled — never `verify=False`.
- [ ] Sensitive data encrypted at rest if stored (PII, tokens, keys).

```python
# Password hashing — bcrypt via passlib
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
hashed = pwd_context.hash(plain_password)
valid = pwd_context.verify(plain_password, hashed)
```

---

### HTTP Security Headers

For web APIs, ensure responses include:

| Header | Recommended Value |
|--------|-------------------|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Content-Security-Policy` | `default-src 'self'` (adjust per app) |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |

---

### CORS Policy

- [ ] CORS origins are explicitly listed — never `allow_origins=["*"]` in production.
- [ ] CORS credentials (`allow_credentials=True`) only when required and origins are explicit.

```python
# FastAPI — explicit origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

---

### Error Handling

- [ ] Stack traces are never returned in API responses.
- [ ] Error messages do not leak implementation details (DB schema, file paths, internal IDs).
- [ ] All unhandled exceptions are caught at the framework level and return a generic 500.

```python
# Return opaque error — log details internally
@app.exception_handler(Exception)
async def generic_handler(request, exc):
    logger.exception("Unhandled error", extra={"path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
```

---

### Dependency Security

- [ ] Pin dependency versions in `requirements.txt` / `package.json`.
- [ ] Run `pip-audit` or `npm audit` periodically.
- [ ] Do not install packages from untrusted sources.
- [ ] Review changelogs before upgrading major versions.
