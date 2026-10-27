---
name: commit-message-conventions
description: "Use when writing a git commit message. Triggers on phrases like
  'write a commit message', 'commit these changes', 'git commit', or when the
  Git Agent (SA-05) is about to create a commit. Ensures commits follow
  Conventional Commits format and produce a clean, readable history."
license: MIT
allowed-tools: [shell]
compatibility: "Applies to any git project. Follows Conventional Commits 1.0.0."
---

## Commit Message Conventions

Commits are the permanent record of a codebase's evolution. A well-written
commit message tells you what changed, why it changed, and what impact it has —
without reading the diff.

---

### The Format

```
<type>(<scope>): <subject>

[optional body]

[optional footer(s)]
```

**Every field has a specific role:**
- **Type** — what kind of change is this?
- **Scope** — which part of the codebase is affected?
- **Subject** — what changed, in one line?
- **Body** — why was this change made? (not what — the diff already shows that)
- **Footer** — breaking changes, issue references, co-authors

---

### Type Vocabulary

| Type | When to use |
|------|-------------|
| `feat` | A new feature or capability |
| `fix` | A bug fix |
| `refactor` | Code restructure with no behaviour change |
| `test` | Adding or improving tests only |
| `docs` | Documentation changes only |
| `chore` | Build system, tooling, dependency updates, config |
| `perf` | Performance improvement |
| `style` | Formatting, whitespace, missing semicolons — no logic change |
| `ci` | CI/CD pipeline changes |
| `revert` | Reverting a previous commit |

**Rule:** If a commit does more than one type of change, split it into multiple
commits. A commit that is both a `feat` and a `fix` is two commits.

---

### Scope

The scope is the affected subsystem, module, or area. Use the actual name from
the codebase:

```
feat(agents): ...          # api/agents.py
fix(delegate-tool): ...    # tools/default/delegate_tool.py
test(defaults): ...        # tests/ covering core/defaults/
chore(deps): ...           # requirements.txt, package.json
docs(adr): ...             # docs/decisions/
```

**Rules:**
- Lowercase
- Use the module name, not the file name
- Omit scope only for truly global changes (e.g., `chore: update Python to 3.12`)

---

### Subject Line Rules

- **Imperative mood:** "add", "fix", "remove" — not "added", "fixes", "removing"
- **Lowercase after the colon**
- **No period at the end**
- **Maximum 72 characters** (50 is ideal)
- **Do not repeat the type in the subject:** `fix(auth): fix the auth bug` is redundant

**Bad subject lines:**
```
Fixed the bug with agents
Added new feature for dynamic subagents
Changes to delegate tool to support dynamic config
```

**Good subject lines:**
```
fix(auth): prevent token expiry race on concurrent refresh
feat(agents): add dynamic subagent spawning without pre-registration
refactor(worker): extract system message assembly into build_system_message()
```

---

### Body — The "Why"

The body explains **why** the change was made, not what (the diff shows the what).

Include the body when:
- The reason for the change is not obvious from the subject
- The change fixes a non-obvious bug
- The change has implications for future development
- An alternative approach was rejected

```
feat(delegate-tool): fall back to dynamic subagent when name not registered

Previously, delegate_to_agent returned an error if the subagent name was not
in the database. This forced callers to pre-register every subagent they needed,
adding friction for one-off delegation tasks.

The new dynamic path constructs a minimal SubagentConfig from the inline call
arguments (system_prompt, allowed_tools, max_steps) so ad-hoc delegation works
without DB overhead. The subagent is ephemeral — no DB row is created.
```

**Body formatting:**
- Blank line between subject and body
- Wrap at 72 characters
- Use present tense: "add" not "added"

---

### Footer — Breaking Changes and References

**Breaking changes:**
```
feat(api)!: change GET /providers response to flat array

BREAKING CHANGE: Response shape changed from {total_models, providers: []}
to a plain array []. Update all callers.
```

The `!` after the type/scope signals a breaking change. The `BREAKING CHANGE:`
footer provides migration guidance.

**Issue references:**
```
fix(daemon): resolve pause race condition during Groq 429 retry

Closes #142
See also: #138 (related daemon lifecycle issues)
```

---

### Complete Examples

**Simple bug fix:**
```
fix(seed-defaults): resolve 405 on POST /agents/seed-defaults

FastAPI matches routes in declaration order. The /{name} parameterised route
was registered before /seed-defaults, causing Starlette to match the static
path as a name parameter and return 405 Method Not Allowed.

Moved seed-defaults registration above all /{name} routes and added a comment
to prevent future regressions.

Closes #189
```

**New feature:**
```
feat(parallel-delegate): support dynamic subagents in parallel calls

parallel_delegate previously only worked with pre-registered subagents.
Adds the same _build_dynamic_config() bypass used in delegate_to_agent so
registered and dynamic subagents can be mixed in a single parallel call.
```

**Chore:**
```
chore(deps): upgrade pydantic from 2.6.4 to 2.7.1

Picks up fix for validator ordering in nested models (pydantic/pydantic#8901).
No API changes required.
```

---

### Checking Your Commit Before Pushing

```bash
# Review staged changes before committing
git diff --staged

# Verify the commit message after writing it
git log -1 --format="%s%n%n%b%n%n%N"
```

A commit message is good when a colleague who has not seen the code can
understand what changed and why from the message alone.
