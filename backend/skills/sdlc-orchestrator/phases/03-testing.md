## Phase 3 — Testing

Delegate to `testing-agent` (SA-04).

### Delegate call

```
delegate_to_agent(
  name="testing-agent",
  task="Write and run tests for the implementation described below",
  context={
    "implementation_summary": "<summary from Phase 2>",
    "test_strategy": "<test strategy from Phase 1>",
    "files_created": ["<list from coding-agent>"],
    "test_runner": "<pytest / jest / etc.>"
  }
)
```

### What testing-agent should deliver

- Unit tests for all new functions/methods
- Integration tests for any new API endpoints or DB interactions
- All tests passing (exit code 0)
- Coverage report (if the project has a coverage config)
- A clear pass/fail summary

### If tests fail

- If testing-agent can fix the issue itself: allow it to do so (one retry)
- If tests reveal a logic error in the implementation: return to Phase 2
  with a clear description of what failed and why
- If tests reveal a design problem: escalate to the user before proceeding

### After testing-agent completes

Note the test count and pass rate, then proceed to Phase 4.
