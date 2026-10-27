---
name: testing-conventions
description: "Use when writing tests for any module, function, API endpoint, or
  feature. Triggers on phrases like 'write tests', 'add test coverage', 'write a
  unit test', 'test this function', or when the Coding Agent (SA-03) or Testing
  Agent (SA-04) needs to produce tests. Applies to Python (pytest) and TypeScript
  (Jest/Vitest) projects."
license: MIT
allowed-tools: [read_file, write_file, shell]
compatibility: "Primary examples use pytest. TypeScript examples use Jest/Vitest."
---

## Testing Conventions

Tests are first-class code. They must be readable, deterministic, fast, and
meaningful. A test suite that always passes but catches nothing is worse than
no test suite.

---

### Test Naming

**Pattern: `test_<unit>_<scenario>_<expected_result>`**

```python
# Good — self-documenting
def test_create_agent_missing_name_returns_422():
def test_get_agent_nonexistent_returns_404():
def test_seed_defaults_idempotent_skips_existing():
def test_memory_recall_empty_store_returns_empty_list():

# Bad — vague
def test_agent():
def test_error():
def test_case1():
```

---

### Arrange-Act-Assert (AAA)

Structure every test in three clearly separated phases:

```python
def test_upsert_agent_overwrites_existing(db_session, test_user):
    # Arrange — set up preconditions
    existing = AgentConfig(name="coder", max_steps=5, user_id=test_user.id)
    db_session.add(existing)
    db_session.commit()

    # Act — perform the operation under test
    result = registry.upsert(test_user.id, {"name": "coder", "max_steps": 10})

    # Assert — verify the outcome
    assert result.name == "coder"
    assert result.max_steps == 10
```

---

### Test Scope

| Test Type | What it tests | Mocking strategy |
|-----------|--------------|-----------------|
| Unit | Single function/class in isolation | Mock all dependencies |
| Integration | Multiple components working together | Mock only external services (DB, HTTP) |
| End-to-end | Full request-response cycle | No mocking (real DB, real HTTP client) |

**Prefer unit tests for business logic. Integration tests for DB interactions.
E2E tests for critical user journeys only.**

---

### Fixtures — pytest

```python
# conftest.py
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()

@pytest.fixture
def test_user(db):
    user = User(id="test-user-001", username="testuser")
    db.add(user)
    db.commit()
    return user
```

**Rule:** Fixtures should be the minimal setup needed for the test. Avoid
mega-fixtures that do too much.

---

### Mocking

**Mock at the correct boundary — the outermost layer you control:**

```python
# Mocking an HTTP client call (correct boundary)
@patch("core.providers.groq.httpx.AsyncClient.post")
async def test_groq_provider_handles_429(mock_post):
    mock_post.return_value = Mock(status_code=429, json=lambda: {"error": "rate limit"})
    result = await groq_provider.complete(messages=[...])
    assert result.error_code == "RATE_LIMITED"

# Mocking a DB call (correct boundary for unit tests)
@patch("core.agents.registry.subagent_registry.get")
async def test_delegate_uses_dynamic_config_when_agent_not_found(mock_get):
    mock_get.return_value = None
    ...
```

**Avoid:** Mocking private methods or internal implementation details — tests
that do this break on every refactor.

---

### Parametrize for Multiple Scenarios

```python
@pytest.mark.parametrize("max_steps,expected", [
    (0,  1),   # clamps to minimum
    (1,  1),   # boundary — valid
    (25, 25),  # mid-range
    (50, 50),  # boundary — valid
    (51, 50),  # clamps to maximum
    (99, 50),  # clamps to maximum
])
def test_build_dynamic_config_clamps_max_steps(max_steps, expected):
    cfg = _build_dynamic_config("test-agent", {"max_steps": max_steps})
    assert cfg.max_steps == expected
```

---

### Async Tests

```python
# Use pytest-asyncio
@pytest.mark.asyncio
async def test_seed_defaults_creates_three_agents(mock_db):
    result = await seed_user_defaults("user-001")
    assert result["agents_seeded"] == 3
    assert result["errors"] == []
```

Configure in `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

---

### Test Isolation

- Each test must be independent — no shared mutable state between tests.
- Database tests must roll back or use a fresh DB per test.
- Tests must not depend on execution order.
- File system tests must use `tmp_path` (pytest) or `tempfile.mkdtemp()`.

```python
def test_skill_seeder_copies_to_user_dir(tmp_path):
    user_id = "test-user"
    result = seed_skills(user_id, workspace_root=tmp_path)
    assert (tmp_path / user_id / "skills" / "web-search-methodology").exists()
```

---

### What NOT to Test

- The framework itself (don't test that FastAPI returns 200 for a route that
  exists — test your handler logic).
- Private/internal functions that are implementation details (test the public
  API; let internals be refactored freely).
- Third-party library behaviour (assume it works; test your use of it).

---

### TypeScript / Jest Conventions

```typescript
// Naming — same pattern
describe("AgentRegistry", () => {
  describe("get", () => {
    it("returns null when agent does not exist", async () => {
      // Arrange
      const registry = new AgentRegistry(mockDb);
      // Act
      const result = await registry.get("user-1", "nonexistent");
      // Assert
      expect(result).toBeNull();
    });
  });
});
```

```typescript
// Mocking with jest.fn()
const mockFetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
global.fetch = mockFetch;

afterEach(() => {
  jest.clearAllMocks();
});
```

---

### Coverage Targets

| Layer | Target |
|-------|--------|
| Business logic (services, validators) | ≥ 90% |
| API handlers | ≥ 80% |
| Utility functions | ≥ 95% |
| Configuration / bootstrapping | Best effort |

Coverage is a guide, not a goal. 100% coverage with trivial assertions is worse
than 80% coverage with meaningful tests.
