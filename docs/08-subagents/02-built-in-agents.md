# Built-in Agents

EVA ships with thirteen specialised agents, pre-configured and ready to use from day one. They cover the most common categories of technical work — plus one that runs your skills for you — and are available in any Plan mode task without any setup.

---

## Quick reference

| Agent | Max steps | What it does |
|-------|-----------|-------------|
| deep-research | 15 | Multi-step web research, source synthesis, structured reporting with citations |
| code-review-agent | 15 | Structured code review: correctness, security, quality, performance — with actionable findings and a verdict |
| coding-agent | 20 | End-to-end implementation: writes code, runs tests, verifies output, handles multi-file changes |
| testing-agent | 20 | Writes and runs test suites; reports coverage and results |
| git-agent | 10 | Git workflow specialist: commits, branches, rebases, conflict resolution, release tagging |
| file-system-analyst | 10 | Explores and maps directory structures, identifies project layout and conventions |
| documentation-writer | 15 | Generates and updates README files, API docs, inline comments, and changelogs from source code |
| security-reviewer | 20 | OWASP-aligned security audit with CWE references and remediation guidance |
| data-analyst | 20 | Analyses CSV, JSON, and database data using Python; computes statistics, identifies patterns |
| error-diagnostician | 15 | Diagnoses runtime errors and stack traces; identifies root cause and produces a specific fix |
| deployment-agent | 15 | Generates Dockerfiles, docker-compose configs, and CI/CD pipelines; never runs destructive production commands without confirmation |
| self-improvement-agent | 15 | Reviews EVA's memory quality and behaviour patterns; produces advisory improvement suggestions |
| skill-runner | 30 | Finds and faithfully follows a skill, start to finish, for a delegated task — built for automated runs such as daemons |

---

## Agent descriptions

### deep-research

Conducts comprehensive research on any topic: breaks the question into sub-questions, runs multiple independent searches, cross-references sources, and produces a structured report with citations. Designed for questions where a single web lookup is insufficient — market research, technical background reading, competitive analysis, academic surveys.

Uses web search, news search, HTTP fetch, file read/write, and the Knowledge Base.

---

### code-review-agent

Reviews code files or diffs with the rigour of a senior engineer. Works through five phases: understand context, check correctness (edge cases, error paths, type safety), audit security (injection, secrets, auth), assess quality and maintainability, and flag performance concerns. Produces a structured report with critical issues, suggestions, style notes, and a clear verdict.

Can review individual files, whole modules, or a git diff. Frequently used as a self-review step within PEV coding tasks before they report completion.

Uses file read, directory listing, shell (for linters), and web search (for CVE lookups).

---

### coding-agent

A senior software engineer for implementation tasks. Reads existing code to understand conventions, writes the change, runs it via the shell, verifies the output is correct, and reports what was done. Handles multi-file changes, bug fixes, feature additions, and refactoring. Does not stop at pseudocode or partial solutions.

Can read git history (status, diff, log) for context but delegates commits to the git-agent.

Uses file read/write, shell, web search, HTTP fetch, and the Knowledge Base.

---

### testing-agent

Writes comprehensive test suites for a given module, class, or feature. Analyses the code under test, identifies happy paths, edge cases, and error paths, writes tests using the project's existing framework and conventions, executes them via the shell, and reports pass/fail and coverage.

Does not modify existing passing tests unless explicitly instructed.

Uses file read/write, shell, and the Knowledge Base.

---

### git-agent

Handles git operations safely and with clear audit trails. Before taking any action, it checks current state with status and log. Stages changes selectively, writes commit messages following the project's existing convention, manages branches, performs rebases, and resolves conflicts. Never force-pushes or pushes to main/master without explicit instruction.

Uses git tools (status, diff, log, add, commit, checkout, branch, push), file read, and shell for edge-case operations.

---

### file-system-analyst

Explores and maps directory structures, identifies project type and conventions, counts files by extension, flags oversized files, and produces a structured project map. Designed to be called by the orchestrator before assigning coding or documentation tasks — it establishes what is where so other agents can work efficiently.

Read-only: it never modifies the filesystem.

Uses file listing and read tools.

---

### documentation-writer

Generates and updates developer documentation: README files, API reference docs, inline code comments, docstrings, and changelogs. Reads the source code before writing anything, verifies every claim against the code, and flags anything that requires runtime verification. Follows the project's existing documentation format and conventions.

Uses file read/write, git log (for changelogs), shell, and the Knowledge Base.

---

### security-reviewer

Audits code for vulnerabilities aligned to the OWASP Top 10: access control, cryptographic failures, injection, insecure design, security misconfiguration, vulnerable dependencies, authentication failures, and security logging. Produces a structured report categorised by severity (critical, high, medium, low, informational) with CWE IDs and specific remediation guidance.

Can run dependency scanners (bandit, safety, npm audit) via the shell and look up CVEs via web search.

Read-only: it never modifies code.

---

### data-analyst

Analyses structured data from CSV files, JSON datasets, or databases. Computes real statistics using Python and pandas via the shell — it never estimates or fabricates numbers. Profiles columns, identifies patterns and anomalies, answers specific analysis questions, and produces a structured findings report with recommendations.

Uses file read/write, shell, and the Knowledge Base.

---

### error-diagnostician

Diagnoses runtime errors, exceptions, and stack traces. Reads the complete stack trace from bottom to top to identify the origin (not just where the error surfaced), reads the relevant source files, forms an explicit hypothesis about the root cause, verifies it where possible by reproducing the error, and produces a specific fix with prevention guidance.

Read-only: it diagnoses and recommends, but does not modify code. Hand the fix to the coding-agent for implementation.

Uses file read, shell, and the Knowledge Base.

---

### deployment-agent

Generates production-ready deployment artefacts: Dockerfiles with multi-stage builds, docker-compose configurations with health checks, and CI/CD pipeline definitions for GitHub Actions, GitLab CI, and Jenkinsfile. Reads the project structure first to produce accurate configuration. Validates generated files where tooling is available.

Has a built-in safety constraint: it never runs destructive production commands (kubectl apply, terraform destroy, docker system prune) without explicit user confirmation.

Uses file read/write, shell, web search, HTTP fetch, and git read tools.

---

### self-improvement-agent

An introspective agent that audits EVA's memory quality and identifies gaps in its configuration. It reads your stored memory entries to surface problems — stale facts that are no longer true, contradictory entries that need reconciling, and vague entries that are too imprecise to be useful. It also scans your configured agents and skills to identify capability gaps: task types you request repeatedly that no specialist agent handles well.

**Example:** After several weeks of use, you ask EVA to run a self-review. The agent reads your memory and finds three issues — a deadline entry from three months ago that is now past, two conflicting preference entries ("prefers concise responses" vs "always provide full explanations"), and a note from a past PEV task recording that data analysis requests frequently required extra clarification. It reads your agent list and notes you have no customised variant of the data-analyst agent. Its report recommends: delete the expired deadline, decide which response-length preference is current and remove the other, and consider cloning the data-analyst agent with a system prompt tuned to the kind of analysis you do most. You act on those suggestions through the Memory panel and Subagents panel — the agent itself changes nothing.

Its output is always advisory: a structured report saved to memory for future reference. It has no ability to delete entries, edit agent definitions, or modify skill files.

Uses memory recall and write tools, file read, and the Knowledge Base.

---

### skill-runner

Identifies and carries out a [skill](../10-skills/01-what-are-skills.md) as a self-contained piece of work. If the task names a skill or points at its file, the agent uses that one; otherwise it checks the skills available to it for a match. It reads the skill in full first, then follows every phase in order, using whichever tools the skill directs it to — it doesn't summarise or skip steps for speed.

It exists mainly for **automated runs**. In a normal conversation, EVA reaches for a skill on its own when one fits. A daemon running unattended doesn't get that organic triggering, so a daemon (or any agent, through delegation) can hand the work to skill-runner instead and be sure the skill is actually followed. The agent is deliberately generic: it carries no project or task knowledge of its own — that lives in the skill it's pointed at.

It remembers nothing beyond the task it was given, so include the skill name or path and anything it needs to know in the task itself. Its step limit is 30 to leave room for reading project context before writing.

Uses file listing and read, file search, project file read/write, and the Knowledge Base.

---

> **Next:** [Creating Custom Agents](03-creating-custom-agents.md) — build your own specialised agents with custom personas, tool sets, and project scope.
