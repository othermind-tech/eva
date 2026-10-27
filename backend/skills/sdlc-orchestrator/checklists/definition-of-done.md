## Definition of Done

A feature is NOT complete until every item below is checked.
Read this checklist before producing the final report.

---

### Code Quality
- [ ] Code follows existing style conventions (naming, imports, structure)
- [ ] No dead code, commented-out blocks, or TODO left in production files
- [ ] All functions have docstrings / type hints (if the project uses them)
- [ ] No hardcoded secrets, credentials, or local paths

### Tests
- [ ] Unit tests written for all new functions
- [ ] All tests passing (zero failures, zero errors)
- [ ] No existing tests broken by this change
- [ ] Edge cases covered (empty input, null values, boundary conditions)

### Review
- [ ] No 🔴 Critical findings from code-review-agent
- [ ] Security: no injection, no exposed data, no auth bypass
- [ ] Error handling: all exception paths handled or explicitly documented

### Git
- [ ] Commit message follows Conventional Commits
- [ ] Only relevant files staged (no accidental .env, cache, or binary files)
- [ ] Branch name is descriptive (feat/*, fix/*, etc.)

### Handoff
- [ ] PR description written and covers: what, why, how to test
- [ ] Final report produced with complete file change list
- [ ] Any deferred items noted for follow-up

---

**If any item is unchecked:** note it explicitly in the final report under "Notes".
Do not silently skip checklist items.
