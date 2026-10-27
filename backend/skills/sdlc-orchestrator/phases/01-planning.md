## Phase 1 — Implementation Plan

Produce a structured plan BEFORE delegating to coding-agent. The plan is the
contract between phases — every subsequent specialist receives it as context.

### Steps

1. **Break down the task** into discrete deliverables:
   - New files to create (with proposed names)
   - Existing files to modify (with the change summary)
   - DB schema changes, if any
   - API contract changes, if any

2. **Define the test strategy:**
   - What unit tests are needed?
   - What integration tests are needed?
   - What edge cases must be covered?

3. **Identify risks:**
   - Anything that could break existing functionality
   - Any dependency on external services or data

4. **Produce the plan** using `templates/implementation-plan.md` as the format.

5. **Get user confirmation** — present the plan and wait for approval before
   moving to Phase 2. Do not proceed without explicit confirmation.

### What to pass to coding-agent

The full implementation plan, plus:
- The context block from Phase 0
- Specific file paths to create/modify
- The test strategy summary
