# What are Daemons?

A daemon is a background AI agent that runs on its own schedule — no conversation required, no message from you needed. You configure it once: give it a system prompt, tell it what to do each time it wakes, decide when it should run. From that point, it operates independently, using tools, making decisions, and producing results whether you're at your desk or not.


---

## Daemons vs subagents

Subagents are called by an orchestrating agent to handle a specific subtask within a running PEV execution — they exist for the duration of that task, then disappear. Daemons are entirely independent. They are not summoned by an orchestrator; they wake on their own, run a full reasoning loop, and go back to sleep. They persist across sessions and accumulate a history of their runs.

---

## How daemons get triggered

A daemon can wake up in four ways.

**Scheduled** — you set a run interval (minimum 60 seconds). Practical presets in the UI are 1 hour, 6 hours, 12 hours, 24 hours, and 7 days. The daemon scheduler runs as part of EVA's backend — so the EVA app must be running for scheduled daemons to fire. You don't need to be in a conversation or doing anything actively; the scheduler runs in the background as long as the app is open.

**Manual** — you click the trigger button from the Daemon Agents panel. Useful for daemons where timing is irregular or you want to fire a one-off run on demand.

**Webhook** — each daemon can be assigned a secret webhook URL from within its settings. Any external system that can make an HTTP POST request can trigger that daemon: a CI pipeline, a GitHub Action, a monitoring alert, a cron job on another server. An optional JSON payload in the POST body is forwarded to the daemon as part of its activation context. The URL acts as the credential — keep it secret; if compromised, revoke it and generate a new one.

**Event-based** — a daemon can subscribe to internal EVA events and fire automatically when they occur. Currently supported events: `execution.completed` (fires when any EVA execution finishes) and `execution.failed` (fires when any execution fails). Use this to build automatic responses to things happening in EVA. A daemon linked to a project only reacts to events from that project; a daemon with no project reacts to events from anywhere.

A daemon can combine trigger modes — for example, a scheduled daily run plus a webhook for on-demand firing.

---

## What a daemon does when it runs

Each time a daemon wakes, it runs as a full AI agent: its configured system prompt sets its persona and purpose, its trigger message is sent as the opening instruction, and it executes its reasoning loop using whichever tools it has access to. It can read and write files, search the web, call external APIs, query the Knowledge Base, store things in memory, and update project artifact files.

Two things it cannot do: interact with a live conversation while it's running, or wait for your input mid-execution. Daemons run to completion on their own. That includes the step limit: a conversation can ask you whether to continue when it runs out of steps, but nobody is there to answer a daemon, so a daemon that reaches its **Max steps** stops cleanly right there — with a best-effort answer if it has one, and an error if it doesn't. If a job routinely needs more room, raise the daemon's own Max steps.

While a daemon is running it shows in the [Activity tab](../02-conversations/07-activity-and-preview.md), and you can cancel it — cancelling genuinely interrupts the run.

---

## Daemon lifecycle

| Status | What it means |
|--------|--------------|
| **Idle** | Waiting for the next scheduled run or trigger |
| **Running** | Currently executing — a new trigger during this window is skipped |
| **Paused** | Auto-scheduling suspended; you must resume it manually |
| **Permanently failed** | Exceeded the maximum retry attempts; requires a manual failure reset before it can run again |

If a daemon's linked project is paused or archived, the daemon is automatically disabled and cannot be triggered until the project is resumed.

---

## Retry policy

When a daemon run fails, EVA can automatically retry it. The retry delay starts at the configured base value and doubles with each attempt (exponential backoff). If the run still fails after the maximum number of retries, the daemon moves to permanently failed status and stops until you reset it.

You configure three things:

- **Retry on failure** — whether to retry at all (on by default)
- **Max retries** — how many consecutive failures before permanent failure (0–10, default 3)
- **Retry delay** — base delay before the first retry, in seconds (minimum 60s; doubles each attempt)

---

## Configuration

| Setting | What it controls |
|---------|-----------------|
| **Name** | Unique slug for this daemon. Cannot be changed after creation. |
| **Kind** | **Standard** (the default) or **Supervisor**. A supervisor is a daemon whose job is to manage long-running background processes — start a dev server, read its output, stop it when it's no longer needed — using the `background_process` tool. Both kinds run exactly the same way; the label just makes the purpose clear in your list of daemons. A third kind, *system*, is reserved for daemons EVA creates itself (see below) and can't be chosen. |
| **Description** | What this daemon is for. |
| **System prompt** | The daemon's persona, purpose, and operating rules. An **Enhance** button next to the field expands a rough draft into a fuller prompt using AI — available whenever a provider is configured. |
| **Trigger message** | The instruction sent to the daemon each time it wakes. Think of it as the "what to do now" message. |
| **Provider / Model** | Which AI provider and model to use. Leave blank to inherit from your session. |
| **Max steps** | Maximum reasoning iterations per run (1–20). |
| **Tools** | Which tools this daemon can use. Leave empty to inherit all your enabled tools. |
| **Schedule** | Run interval in seconds, or manual-only (no automatic scheduling). |
| **Event subscriptions** | Internal EVA events that trigger this daemon automatically. |
| **Project** | Optional project scope. If set, the daemon is linked to that project's context and appears only in the project browser — not in the global Daemon Agents panel. |
| **Enabled** | Whether automatic scheduling and triggering are active. |
| **Retry policy** | Retry on failure, max retries, retry delay. |

---

## Execution history

Every daemon run is recorded. The history tab on any daemon shows the last 50 executions — status, duration, which model and provider handled the run, token usage, and any error messages. This lets you audit what the daemon did, diagnose failures, and verify that scheduled runs are completing successfully.

---

## Integration with PEV tasks

Daemons are not isolated from EVA's planning system. An orchestrating agent running a PEV task can directly trigger a daemon, either to kick off background work or to use a daemon's output as part of the task itself. This is how standing infrastructure and on-demand task work connect.

There are two modes.

**Signal (fire and forget)** — the orchestrator triggers a daemon and immediately continues with its own work, without waiting for the daemon to finish. The daemon runs independently in the background. Use this when you want a side effect — notify someone, log a result, kick off a monitoring sweep — without blocking the main task.

**Await (blocking call)** — the orchestrator triggers a daemon and waits for it to finish, receiving its output as a result. The orchestrator uses that output to continue the task. Wait time is bounded by a configurable timeout (default 60 seconds, maximum 5 minutes). If the daemon doesn't finish within the timeout, the orchestrator gets an error and the daemon continues running in the background. Use this when you need the daemon's output as an input to the current task.

**Pause and resume** — for a daemon that takes longer than the wait limit, the orchestrator can trigger it as a signal and then *pause the whole task* until the daemon finishes (or a timer runs out, whichever comes first), resuming on its own afterwards. No connection is held open in the meantime. The paused task shows in the [Activity tab](../02-conversations/07-activity-and-preview.md), where you can resume or cancel it. Cancelling the pause does not stop the daemon it was waiting on.

**Example:** You have a `slack-notifier` daemon configured to send a formatted message to a Slack channel via webhook, and a `data-fetcher` daemon that calls an internal API and returns a JSON report.

A PEV task receives the goal: *"Fetch this week's sales figures, analyse them, and notify the team."*

The orchestrator's plan:
1. Invoke the data-fetcher daemon and wait for its result — blocks until the daemon returns the sales figures
2. Analyse the returned data (orchestrator's own reasoning step)
3. Write a summary report to a project file
4. Signal the Slack notifier daemon to run in the background — the task completes immediately without waiting for delivery

The key insight: the daemons in this example are persistent, reusable agents. The Slack notifier can be triggered by any PEV task, by a schedule, or by a webhook — it's one piece of infrastructure serving many workflows.

---

## Project sync daemons

Every project has its own daemon, created automatically the moment the project is created — it explores the project's workspace and keeps `knowledge.eva` current. This is the *system* kind of daemon. You don't create or configure it yourself, and its Kind shows as a read-only "system (reserved)" badge if you ever open it. It doesn't appear in the Daemon Agents panel or fire on any schedule: it's manual-trigger-only by design, invisible to the scheduler. You control it entirely from the project itself — automatically at creation, or on demand via the project's **Sync now** button. See [What are Projects?](../05-projects/01-what-are-projects.md#automatic-project-sync) for what it actually does.

---

## Managing daemons

The **Daemon Agents** panel lists your global daemons — those not assigned to any project — with their current status. Each card shows the daemon's name, description, lifecycle status, and last/next run times. Project-scoped daemons are not shown here; they appear only in the project browser under the Daemons tab.

**Creating** — click **New Daemon**, fill in the system prompt and trigger message at minimum. Everything else has defaults.

**Or just ask EVA** — in any conversation, tell EVA what you want: *"Set up a daemon that checks my inbox every hour and summarises anything urgent."* EVA gathers the details it needs, shows you the full draft configuration, and saves it only after you confirm. The same conversational path also works for editing an existing daemon and for deleting one. One limitation worth knowing: EVA's scheduling is interval-only (see Configuration above) — if you describe a wall-clock time like "every morning at 9am," EVA will flag that it can't represent that directly and propose the closest interval-based equivalent instead of silently approximating it.

**Editing** — click any daemon to open its config. All fields except the name are editable.

**Triggering manually** — the trigger button fires an immediate run regardless of schedule.

**Pausing and resuming** — pause suspends auto-scheduling without deleting the daemon; resume restarts it from the current time.

**Cancelling a run** — if a daemon is currently running, you can send a cancellation signal to stop it.

**Resetting failures** — clears the failure counter on a permanently-failed daemon so it can be resumed.

**Cloning** — creates a disabled copy of the daemon under a new name. Useful for creating variants. The clone starts disabled so it does not fire immediately.

**Webhooks** — generate a secret webhook URL for the daemon. External systems POST to this URL to trigger a run. The URL is shown once at creation and cannot be retrieved afterwards — if lost, delete and regenerate.

---

> **Next:** [What are Skills?](../10-skills/01-what-are-skills.md) — reusable structured procedures that give EVA a consistent methodology for recurring tasks.
