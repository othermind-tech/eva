## Phase 5 — Commit and PR

Delegate to `git-agent` (SA-05) for the commit. Then use the
`commit-message-conventions` skill and `pr-description-writer` skill
for the message and PR body.

### Step 1 — Commit (delegate to git-agent)

```
delegate_to_agent(
  name="git-agent",
  task="Stage and commit the implementation",
  context={
    "files_changed": ["<list>"],
    "commit_summary": "<one sentence describing what was built>",
    "conventional_commit_type": "<feat|fix|refactor|test|docs>",
    "scope": "<optional: module or component name>"
  }
)
```

Commit message must follow Conventional Commits format:
```
<type>(<scope>): <short description>

<body — what changed and why, not how>

<footer — BREAKING CHANGE or issue refs if applicable>
```

### Step 2 — PR Description

Read `templates/pr-summary.md` for the PR body format.
Produce the PR description using:
- The implementation plan (what was built)
- The test results (what was verified)
- The review verdict (any deferred notes)

### After this phase

All changes are committed. Proceed to the final report.
