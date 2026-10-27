---
name: pr-description-writer
description: "Use when creating a pull request description from a git diff, commit
  history, or a summary of changes. Triggers on phrases like 'write a PR description',
  'create a pull request', 'write a PR for these changes', or when the Git Agent
  (SA-05) needs to produce a PR. Produces a professional, reviewer-friendly PR
  description."
license: MIT
allowed-tools: [shell, read_file]
compatibility: "Applies to any git-based workflow. Output is Markdown."
---

## PR Description Writer

A pull request description is a contract between the author and the reviewer.
It must explain what changed, why, how to test it, and what risks to be aware of.

---

### Before Writing — Gather Information

Always gather these before writing:

```bash
# Changes in this branch vs base
git diff main...HEAD --stat
git log main...HEAD --oneline

# Full diff for context
git diff main...HEAD
```

Read the diff fully before writing the title or description.

---

### PR Title Formula

**Format:** `<type>(<scope>): <short description in imperative mood>`

| Type | When to use |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructure without behaviour change |
| `test` | Adding or improving tests |
| `docs` | Documentation only |
| `chore` | Build, tooling, dependency updates |
| `perf` | Performance improvement |
| `security` | Security fix |

**Examples:**
- `feat(agents): add dynamic subagent spawning without pre-registration`
- `fix(auth): prevent token expiry race condition on concurrent requests`
- `refactor(worker): extract system message assembly into dedicated function`
- `test(defaults): add idempotency coverage for seed_user_defaults`

**Rules:**
- Maximum 72 characters
- Imperative mood: "add", "fix", "remove" — not "added", "fixing", "removes"
- No period at the end
- Scope is the affected module/layer, not a file name

---

### PR Description Template

```markdown
## Summary

<!-- 2–4 bullet points: what changed and why. Focus on the "why". -->
- Adds X because Y
- Fixes Z which was causing W
- Removes deprecated A in favour of B

## Changes

<!-- Optional: more detail for complex changes. Skip for simple PRs. -->

### <component/area>
- <specific change and its purpose>

## Test Plan

<!-- How to verify this works. Reviewers should be able to follow these steps. -->
- [ ] Run `pytest tests/` — all N tests pass
- [ ] `POST /agents/seed-defaults` returns `agents_seeded >= 3`
- [ ] Dynamic subagent: send chat message triggering `delegate_to_agent` with
      unregistered name — verify execution completes, agent not in `/agents` after

## Breaking Changes

<!-- If none, write "None." If present, be explicit. -->
None.

<!-- OR: -->
**Breaking:** `GET /providers` response shape changed from `{total, providers}`
to a plain array `[]`. Frontend API client must be updated.

## Screenshots / Recordings

<!-- Optional — include for UI changes. Delete section if not applicable. -->

## Checklist

- [ ] Tests added or updated
- [ ] Documentation updated (if applicable)
- [ ] No secrets or credentials in the diff
- [ ] Breaking changes documented above
```

---

### Writing the Summary Section

The summary is the most-read part. Make it worth reading:

**Bad:**
> - Changes made to agents.py
> - Fixed some bugs
> - Added tests

**Good:**
> - Adds dynamic subagent spawning so the orchestrator can create subagents
>   on-the-fly without pre-registration in the DB
> - Fixes FastAPI route ordering issue where `POST /agents/seed-defaults` was
>   matched by the `/{name}` parameterised route (405 regression from Sprint 5)
> - Extends `parallel_delegate` with the same dynamic path for consistent behaviour

---

### Writing the Test Plan

The test plan must be actionable — a reviewer who hasn't read your code must
be able to verify the change works:

**Bad:**
> - Tests pass

**Good:**
> - [ ] `pytest tests/test_features.py::TestDynamicSubagents` — all 8 tests pass
> - [ ] Start the server and send:
>   ```
>   POST /chat {"message": "delegate to researcher agent", "session_id": "..."}
>   ```
>   Verify: execution reaches `completed` state; `GET /agents/researcher` returns 404

---

### Breaking Changes

If the PR changes a public API contract (response shape, endpoint path, required
fields, behaviour), document it explicitly and add migration guidance:

```markdown
## Breaking Changes

**`GET /providers` response shape changed:**

Before: `{"total_models": 5, "providers": [...]}`
After: `[...]` (flat array)

Migration: Update all callers to use the array directly.
Affected: `src/api/client.ts` line 42 — already updated in this PR.
```

---

### Reviewer Checklist (for authors to complete before requesting review)

- [ ] Self-reviewed the diff — no debug code, commented-out code, or TODOs left
- [ ] All CI checks passing
- [ ] No new lint warnings
- [ ] Test coverage not decreased
- [ ] PR is focused — a single logical change (split if it covers multiple concerns)
