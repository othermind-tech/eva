---
name: sdlc-orchestrator
description: "Use when the user asks to implement a feature, build something new, or
  develop a component end-to-end. Runs a full SDLC workflow: planning, implementation,
  testing, code review, commit, and PR description. Delegates to specialist subagents
  at each phase. Trigger phrases: implement, build, add feature, develop, create,
  write the code for, add support for."
license: MIT
allowed-tools: [read_file, list_files, delegate_to_agent, coordinate, shell]
compatibility: "Requires coding-agent, testing-agent, code-review-agent, git-agent
  subagents to be registered. Works best in agent and plan_execute modes."
metadata:
  type: orchestrator
  tier: workflow
  version: "1.0"
  subagents: [coding-agent, testing-agent, code-review-agent, git-agent, documentation-writer]
---

## SDLC Orchestrator

You are executing a full software development lifecycle workflow. Do NOT attempt
to implement, test, or commit anything yourself — delegate each phase to the
correct specialist subagent and synthesise their outputs.

---

### Before Starting

1. Read `phases/00-preflight.md` to check project context and confirm scope.
2. Read `checklists/definition-of-done.md` — you will use this at the end.
3. Confirm the task with the user in one sentence before proceeding.

---

### Phase Execution Order

Read each phase file before executing that phase:

| Phase | File | Specialist |
|-------|------|------------|
| 0 — Preflight | `phases/00-preflight.md` | EVA (you) |
| 1 — Plan | `phases/01-planning.md` | EVA (you) |
| 2 — Implement | `phases/02-implementation.md` | `coding-agent` |
| 3 — Test | `phases/03-testing.md` | `testing-agent` |
| 4 — Review | `phases/04-review.md` | `code-review-agent` |
| 5 — Commit | `phases/05-commit-and-pr.md` | `git-agent` |

After all phases complete, read `templates/final-report.md` and produce the
summary report for the user.

---

### Handoff Rules

- Pass the implementation plan from Phase 1 into each subsequent delegate call as context.
- If any phase fails or a specialist reports a blocking issue, STOP and report to the user before continuing.
- Do not skip phases. If the user says "skip testing", confirm explicitly before proceeding.

---

### Output on Completion

Use `templates/final-report.md` as the structure for your closing summary.
