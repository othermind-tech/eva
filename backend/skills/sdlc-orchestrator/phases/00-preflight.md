## Phase 0 — Preflight

Before writing a single line of code, establish context. This prevents breaking
conventions, duplicating existing work, or missing constraints.

### Steps

1. **Check for project context**
   - Is there an active project attached to this conversation?
   - If yes: read `CLAUDE.md` (if present), then `README.md`
   - Note the language, framework, and test runner in use

2. **Locate the relevant area of the codebase**
   - Run `list_files` on the repo root to understand structure
   - Identify which directory / module the new feature belongs in
   - Find 1–2 existing files similar to what will be created — sample their style

3. **Clarify the task scope**
   - Restate the user's request in one sentence
   - Identify any ambiguities and resolve them before proceeding
   - If the task touches an API contract, note whether backward compatibility is required

4. **Check for existing tests**
   - Locate the test directory
   - Note the test runner and naming convention (`test_*.py`, `*.spec.ts`, etc.)

### Output of this phase

A brief context block (3–5 bullet points) that you will carry into Phase 1:

```
## Context
- **Project:** [name or "no active project"]
- **Stack:** [language, framework, key deps]
- **Feature area:** [directory / module]
- **Test runner:** [pytest / jest / etc.]
- **Key constraint:** [any gotchas to be aware of]
```
