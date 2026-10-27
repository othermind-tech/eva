# Activity and the Preview Pane

Some of what EVA does doesn't fit inside a single chat reply. A task can pause itself and pick up later, a dev server can keep running after the conversation moves on, and a daemon can be working while you're in a different chat. Two places in the app let you see and steer that work: the **Activity tab**, which shows everything running or paused right now, and the **Preview pane**, a browser built into EVA where you can watch the agent test the app it's building.

---

## The Activity tab

Click the **bell** icon in the left rail to open the notifications drawer. It has two tabs:

- **Notifications** — the history of things that happened: a daemon run finished, an execution failed, and so on.
- **Activity** — what is happening *right now*. It updates live; you don't need to refresh it.

The Activity tab has two sections, **Paused** and **Running**. When both are empty it reads *Nothing running or paused right now*.

### Paused

A paused task is one that stopped itself on purpose to resume later — for example, waiting twenty minutes before checking a build, or waiting for a background daemon to finish. See [Background work](../03-tools/01-what-are-tools.md#background-work) for how EVA does this.

Each paused row shows:

- the **conversation** the task belongs to,
- the **reason** EVA gave for pausing, and
- when it will continue — either a wake-up time, or *waiting on* the daemon it's watching along with that daemon's state. If EVA can't tell whether the thing it's waiting on has finished (after a restart, for example), the row says *check manually*.

Two actions are available:

- **Resume now** — continue right away instead of waiting.
- **Cancel** — drop the pause; the task will not continue. Cancelling only affects the paused task. If it was waiting on a background daemon, that daemon keeps running — stop it separately if you want it stopped.

Paused tasks are saved, so they survive EVA restarting.

### Running

Every task currently in progress, across all your conversations, appears here with when it started. Each row is labelled by kind:

| Label | What it is |
|-------|-----------|
| Chat execution | A normal reply being generated |
| Plan & Execute | A multi-step [PEV](../04-plan-execute-verify/01-what-is-pev.md) run |
| Daemon run | A [daemon](../09-daemons/01-what-are-daemons.md) currently executing |
| Background subagent | A subagent EVA started to work on its own while it carries on |
| Background process | A program EVA started and left running, such as a dev server |

**Background processes** show the command that started them (hover for the full text), a **Stop** button, and a **pin** button. Stop shuts the process down along with anything it started. Background processes deliberately keep running after the task that started them ends, so a dev server stays available for you to open — but they don't outlive EVA itself: by default, quitting EVA stops every background process along with it. If you want one to keep running after you close EVA — a server you want to leave up overnight, say — click its pin button before you quit; a pinned process (shown filled) survives, and everything else is stopped as EVA closes. Any process still running after several hours is stopped automatically regardless of pin state.

The other kinds don't have a Stop button here — stop a chat or plan from its own conversation with the Stop button, and a daemon from the Daemon Agents panel.

The same list is what EVA checks when you quit the app; see [Quitting EVA](../01-introduction/01-getting-started.md#quitting-eva) for what happens to paused tasks and background processes when you close EVA.

---

## The Preview pane

The Preview pane is a web browser inside EVA. It has two uses: looking at something yourself, and watching the agent work.

> **Desktop app only.** The Preview pane needs the EVA desktop app. In a browser-based build it shows a message saying so instead of a page.

### Opening it

Click **Preview** in the left rail. The pane opens on the right and shares the window roughly half-and-half with the conversation, so you can read chat and see the page together. Click **Preview** again, or the **×** in the pane, to close it.

At the top is an address bar. Type an address — `localhost:3000` is the suggested example — and press **Enter** or click the arrow. The bar follows the page as it navigates, so it always shows where the pane actually is.

### Watching the agent work

This is what the pane is really for. With the [Playwright browser MCP](../12-mcp/01-what-are-mcp-servers.md#default-mcp-browser-automation) turned on, the agent's browser tools drive *this* pane instead of opening a separate window. You watch it navigate, click, type and reload in real time — and can see for yourself what it's testing.

A typical request: *"Build a small to-do app, run it, and check it in the browser."* EVA writes the app, starts a dev server as a [background process](../03-tools/01-what-are-tools.md#background-work), opens the page in the pane, reads the browser console for errors, fixes what it finds, reloads to confirm, and stops the server.

Opening or closing the pane re-points the browser tools automatically. With the pane closed, the agent's browser opens as its own window instead.

### Screenshots in the chat

When the agent takes a screenshot, a small **thumbnail** appears in the conversation under that tool call. Click it to open the Preview pane.

### Good to know

- **It's private.** The pane uses its own temporary browser session. It doesn't share cookies, logins or history with EVA itself or with your regular browser, and nothing it stores outlives the session.
- **Closing keeps your place.** Closing the pane hides it without discarding the page, so reopening shows where you left off, with the address bar to match — and the agent's browser session carries on undisturbed.
- **One page at a time.** The pane shows a single page. A link that opens a new tab or popup window isn't shown in it.
- **The split is fixed.** The pane and the conversation share the space evenly; the divider can't be dragged yet.

---

> **Next:** [What are Tools?](../03-tools/01-what-are-tools.md) — how EVA interacts with your machine, the web, and external services.
