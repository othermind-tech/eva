---
name: python-best-practices
description: "Use when writing new Python modules, reviewing Python code for standards
  compliance, or setting up a new Python project. Triggers on requests to 'write
  a Python module', 'create a Python class', 'set up a Python project', or when
  establishing coding standards for a Python codebase. Complements python-helper
  (which focuses on debugging and advanced patterns); this skill focuses on
  project-level standards and consistency."
license: MIT
allowed-tools: [read_file, write_file, shell]
compatibility: "Python 3.10+. Some conventions require ruff or mypy."
---

## Python Best Practices

Apply these standards when writing or reviewing Python code for production use.
These are conventions, not suggestions — consistent application matters more than
individual brilliance.

---

### Module Structure

Every Python module follows this order:

```python
"""Module docstring — one paragraph explaining what this module does."""
from __future__ import annotations  # always first, enables PEP 563

# 1. Standard library imports (alphabetical within group)
import logging
from pathlib import Path
from typing import Optional

# 2. Third-party imports (alphabetical within group)
import httpx
from pydantic import BaseModel

# 3. Local imports (alphabetical within group)
from core.config import settings
from utils.helpers import format_date

logger = logging.getLogger(__name__)  # module-level logger, always last import
```

---

### Type Annotations

- **Every** public function signature must have type annotations.
- Use `X | Y` union syntax (not `Union[X, Y]`) — requires Python 3.10+.
- Use `X | None` (not `Optional[X]`) — cleaner and PEP 604 compliant.
- Use `from __future__ import annotations` to allow forward references.

```python
# Good
def get_user(user_id: str) -> User | None: ...

# Bad
def get_user(user_id, user=None): ...
```

---

### Docstrings

Use Google-style docstrings for all public functions and classes:

```python
def fetch_records(
    table: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Fetch paginated records from a database table.

    Args:
        table: Name of the database table to query.
        limit: Maximum number of records to return. Defaults to 100.
        offset: Number of records to skip. Defaults to 0.

    Returns:
        List of records as dictionaries, ordered by primary key.

    Raises:
        ValueError: If limit is less than 1 or greater than 1000.
        DatabaseError: If the query fails.
    """
```

Private/internal functions (prefixed `_`) only need a one-line docstring
if the name alone is not self-explanatory.

---

### Error Handling

```python
# Good — specific exception, context preserved
try:
    result = db.execute(query)
except sqlalchemy.exc.OperationalError as exc:
    logger.error("Database query failed", extra={"query": query}, exc_info=exc)
    raise DatabaseError("Failed to fetch records") from exc

# Bad — swallowing or hiding errors
try:
    result = db.execute(query)
except Exception:
    return None
```

Rules:
- Never use bare `except:` — always specify the exception type.
- Always use `raise ... from exc` to preserve exception chains.
- Log with `exc_info=exc` (not `exc_info=True`) for structured loggers.
- Define custom exception classes for domain errors — makes `except` clauses specific.

---

### Logging

```python
# Module-level setup (not per-function)
logger = logging.getLogger(__name__)

# Structured logging — use `extra` for machine-readable fields
logger.info("User created", extra={"user_id": user_id, "email": email})
logger.warning("Rate limit approaching", extra={"user_id": user_id, "count": count})
logger.error("Payment failed", extra={"order_id": order_id}, exc_info=exc)
```

Rules:
- Never use `print()` in production code — always `logger.*`.
- Use `extra={}` for structured fields (key-value), not f-strings.
- Choose levels consistently: DEBUG=internal state, INFO=business events,
  WARNING=unexpected but recoverable, ERROR=action failed, CRITICAL=system failure.

---

### Class Design

```python
from dataclasses import dataclass, field

@dataclass
class UserConfig:
    """Immutable configuration for a user account."""
    user_id: str
    email: str
    roles: list[str] = field(default_factory=list)
    max_tokens: int = 4096

    def has_role(self, role: str) -> bool:
        """Return True if the user has the given role."""
        return role in self.roles
```

Rules:
- Prefer `@dataclass` over plain classes for data holders.
- Use `@dataclass(frozen=True)` for value objects that should be immutable.
- Avoid mutable default arguments — use `field(default_factory=...)`.
- Keep `__init__` simple — complex setup belongs in a `@classmethod` factory.

---

### Testing Standards

```python
# tests/test_user_service.py
import pytest
from unittest.mock import AsyncMock, patch

from core.services.user import UserService

# Good fixture pattern
@pytest.fixture
def mock_db():
    return AsyncMock()

@pytest.fixture
def service(mock_db):
    return UserService(db=mock_db)

# Descriptive test names — what behaviour, under what condition
class TestUserService:
    async def test_create_user_returns_id_on_success(self, service):
        ...

    async def test_create_user_raises_conflict_on_duplicate_email(self, service):
        ...

    async def test_create_user_logs_creation_event(self, service, caplog):
        ...
```

Rules:
- Test names must describe behaviour, not implementation: `test_returns_404_when_user_not_found`, not `test_get_user_2`.
- One assertion per logical behaviour (not one assert per test).
- Use `pytest.raises()` to test exception cases.
- Mock at the boundary (DB, HTTP) — never mock the class under test.

---

### Tooling Checklist

For every new Python project, configure:

| Tool | Purpose | Config file |
|------|---------|-------------|
| `ruff` | Linting + formatting | `pyproject.toml [tool.ruff]` |
| `mypy` | Static type checking | `pyproject.toml [tool.mypy]` |
| `pytest` | Test runner | `pyproject.toml [tool.pytest.ini_options]` |
| `pytest-asyncio` | Async test support | `asyncio_mode = "auto"` in pytest config |

Minimum `pyproject.toml` snippet:
```toml
[tool.ruff]
line-length = 100
target-version = "py310"
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.10"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
```
