## Phase 2 — Implementation

Delegate to `coding-agent` (SA-03). Do not implement the code yourself.

### Delegate call

```
delegate_to_agent(
  name="coding-agent",
  task="<task description>",
  context={
    "implementation_plan": "<plan from Phase 1>",
    "project_context": "<context block from Phase 0>",
    "files_to_create": ["<path1>", "<path2>"],
    "files_to_modify": ["<path3>"],
    "conventions": "<key style notes from preflight>"
  }
)
```

### What coding-agent should deliver

- All new files created at the correct paths
- All existing files modified per the plan
- Code that compiles / runs without syntax errors
- Docstrings / type hints consistent with the existing codebase style
- No TODO comments left in production code

### If coding-agent reports a blocker

Stop. Report the issue to the user. Do not attempt to fix it yourself unless
it is a trivial clarification (wrong path, naming ambiguity). Substantial
implementation problems must be fed back to the user for a decision.

### After coding-agent completes

Summarise in one paragraph what was built, then proceed to Phase 3.
