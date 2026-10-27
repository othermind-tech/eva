# Approvals

EVA can take actions beyond generating text — reading and writing files, running shell commands, calling external APIs, deleting records. Some of these actions are consequential and irreversible. Approvals are how EVA handles them safely: pausing before a significant action, showing you exactly what it plans to do, and waiting for your decision.

---

## What triggers an approval

Not every action requires approval. Read-only operations — searching the web, reading a file, querying a document, checking a status — run without interruption. Approvals are triggered by actions that change something: writing to a file, running a shell command, calling an external service, or deleting data.

When EVA reaches one of these actions, it pauses and presents the specific details — not a summary, but the actual file path, the exact shell command, or the precise API call it intends to make. You decide whether to proceed.

---

## Your options

Three responses are available when EVA pauses:

**Approve** — EVA takes this specific action and continues with the task.

**Deny** — EVA skips this action, notes the denial, and continues without it. EVA may find an alternative path, or it may tell you that it cannot complete the task without that step. Either way, the task does not stop — it adapts.

**Approve All** — EVA proceeds with this action and all remaining actions in the current task without pausing again. This is appropriate when you have reviewed EVA's plan and are confident in what follows.

---

## Configuring approval frequency

How often EVA pauses is controlled by the **Tool Approval** setting, available in Session settings and the Modes button in the conversation header.

| Setting | Behaviour |
|---------|-----------|
| **Auto** | Read-only actions run freely. Write, delete, and external actions pause for approval |
| **Ask** | Every tool call pauses for approval, regardless of type |
| **Trust** | All tool calls run without pausing |

**Auto** is the recommended default for most users — it stays out of the way for routine work while maintaining oversight over actions that matter.

**Ask** is useful when you are working in an unfamiliar codebase, running EVA on sensitive data, or want to understand exactly what EVA is doing at each step.

**Trust** removes all approval gates. EVA operates fully autonomously. This is appropriate when you have established confidence in EVA's behaviour for a specific task and want it to run without interruption — or when running in a sandboxed environment where the consequences of mistakes are low.

---

## Plan approval

Tool-call approvals, described above, gate individual actions once EVA has already started executing a task. Plan approval is a separate, optional gate earlier in the process — before EVA takes any action at all.

When you turn on **Plan approval** (Session settings → Output & Safety, off by default), EVA pauses immediately after generating a step-by-step plan in [PEV mode](../04-plan-execute-verify/01-what-is-pev.md) and waits for your decision before executing a single step.

Three options are available:

**Approve** — EVA executes the plan exactly as generated.

**Edit and approve** — change any step's title, description, or tool hint directly in the plan card, then submit. EVA executes your edited version, not the original. If an edit leaves a step incomplete — an empty title, for instance — EVA reports the problem inline immediately, and your other edits are preserved while you fix it.

**Reject** — with a reason, EVA treats it as feedback: it writes a revised plan that takes your comments into account and shows it to you for approval again. This can repeat for up to two rounds. If you reject without giving a reason, or the revision rounds are used up, the execution is cancelled before any step runs.

Plan approval is most useful when you want to review EVA's intended approach on unfamiliar or high-stakes tasks before any tool call — even a read-only one — happens, rather than catching problems one action at a time as they occur.

---

## Changes outside your workspace

Writing, editing or deleting a file outside your workspace always asks for approval, whatever your Tool Approval setting — including Trust. EVA can manage files anywhere on your machine, but "anywhere" is exactly what you'd want a say in if a task were ever steered wrong, for example by something malicious in a web page or document it read. So the first time a file tool tries to change something outside the active workspace (the conversation's folder, your project's workspace, or your EVA workspace), it pauses and shows you the exact path, the same way any other approval does. Approve to proceed, or deny to have EVA carry on without it.

Reading files is not affected: read-only actions still run freely.

---

## Shell safety

Shell access is one of EVA's most powerful capabilities and the one that warrants the most careful treatment. Shell Safety is a layered command analysis system that runs before every shell command executes — independently of and in addition to the approval system described above.

There are two tiers of protection.

**Absolute blocks — refused regardless of approval setting**

Some commands are never permitted, even if you have configured Trust mode or clicked Approve All. These include:

- **Fork bombs** — patterns that spawn uncontrolled processes and crash the machine
- **Interactive terminal commands** — editors (vim, nano), shells with no arguments, interactive database clients (psql, mysql with no args), pagers (less, more). These commands require a live terminal and hang indefinitely inside EVA's shell tool
- **Catastrophic destructive operations** — `rm -rf /`, filesystem formatting commands (`mkfs`, `fdisk --script`), raw disk writes (`dd of=/dev/sda`), disk shredding tools. These cannot be recovered from and are refused unconditionally, with no override

**Conditional blocks — blocked by default, unlockable with explicit approval**

A second tier of commands is blocked in Auto approval mode but can be permitted when you switch to Ask or Trust:

- **Outbound network commands** — `curl`, `wget`, `nc`, `ssh`, `scp` and similar. Network access from the shell requires explicit approval because it bypasses EVA's tool-level network controls
- **Commands targeting paths outside your workspace** — EVA enforces a workspace boundary. Any shell command that references an absolute path outside the active workspace is blocked until you approve it. This prevents EVA from accidentally modifying files in unrelated parts of your system
- **`sed -i` on external paths** — in-place file editing outside the workspace follows the same rule

On Windows, the workspace check understands Windows paths: drive-letter paths (`C:\Users\…`) and network shares (`\\server\share\…`) are recognised as outside the workspace, while the standard system locations (`C:\Windows`, `Program Files`, `ProgramData`) are not treated as suspicious just for running a program from them. Command-line switches such as `cmd /c` or `dir /s` are recognised as switches, not mistaken for paths. And if EVA can't work out where a path leads — a network share that isn't reachable, say — it treats the path as outside the workspace and blocks it, rather than letting it through.

**Read-only mode**

A separate toggle — Shell Read-Only — restricts shell access to inspection only. When enabled, EVA classifies every command before it runs and blocks anything that is not purely read-only. Commands that are permitted include file inspection (`cat`, `head`, `tail`, `diff`), directory listing (`ls`, `find`, `tree`), search (`grep`, `rg`, `awk`), system information (`ps`, `df`, `du`, `uname`), and read-only git operations (`git status`, `git log`, `git diff`). Everything that writes, modifies, installs, or deletes is blocked — `cp`, `mkdir`, `pip install`, `git commit`, `rm`, and all equivalents.

Read-only mode is useful when you want EVA to explore and analyse your environment freely but want a hard guarantee that nothing is changed without a deliberate mode switch.

**Background process detection**

If a shell command starts a background process using `&`, EVA surfaces a warning. Background processes are detached from EVA's tracking — their output, progress, and exit status are invisible to the conversation. For long-running background work, daemons are the correct mechanism.

---

> **Next:** [Conversation History](05-conversation-history.md) — finding, organising, and exporting past conversations.
