## Phase 4 — Code Review

Delegate to `code-review-agent` (SA-02).

### Delegate call

```
delegate_to_agent(
  name="code-review-agent",
  task="Review the implementation for quality, security, and correctness",
  context={
    "files_to_review": ["<list of files created/modified>"],
    "implementation_plan": "<plan from Phase 1>",
    "test_results": "<summary from Phase 3>"
  }
)
```

### What code-review-agent should deliver

A structured report with findings grouped by severity:

- 🔴 **Critical** — must fix before commit (security issues, data loss risk, broken logic)
- 🟡 **Important** — should fix (performance, error handling, missing validation)
- 🔵 **Suggestion** — optional improvements (naming, structure, comments)

### Acting on review findings

| Severity | Action |
|----------|--------|
| 🔴 Critical | Return to Phase 2. Fix before proceeding. |
| 🟡 Important | Use judgement — fix if quick, note if deferred |
| 🔵 Suggestion | Note in final report. Do not delay the commit. |

### After code-review-agent completes

Summarise the review verdict (approved / approved with minor notes / blocked).
Proceed to Phase 5 only if no 🔴 Critical findings remain.
