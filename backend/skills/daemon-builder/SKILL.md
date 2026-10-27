---
name: daemon-builder
description: "Use when the user asks you to set up, create, or schedule a daemon (a recurring automated task) -- an alternative to the daemon panel, done entirely in conversation. Walks through gathering the schedule and behaviour, drafting the configuration, getting explicit confirmation, and saving it via create_daemon_config."
license: MIT
allowed-tools: [create_daemon_config, get_daemon_config, edit_daemon_config, delete_daemon_config]
metadata:
  type: builder
  tier: platform
  version: "1.0"
---

# Daemon Builder

Lets a user create, inspect, modify, and remove scheduled daemons entirely
through conversation, instead of the /daemons panel.

## Important limitation -- be upfront about this, don't silently misrepresent it

EVA's daemons run on a **fixed interval since their last run** (seconds,
minimum 60), not a wall-clock/calendar schedule. There is no way to specify
"every day at 9am" or "every weekday" today -- only "every N seconds."

If the user asks for something like "every morning" or "every day at 9am",
**say so explicitly** rather than quietly approximating it as
`run_interval_seconds=86400` and letting them assume it's calendar-accurate
-- an interval daemon created at 3pm and re-triggered every 24 hours will
keep firing at 3pm, not 9am, and will drift further if it's ever delayed by
a retry or a period the app wasn't running. Offer the closest interval
approximation, name the caveat, and let the user decide if that's good
enough for their use case.

## Creating a new daemon

1. **Gather what's needed:**
   - `name` -- suggest a short slug (e.g. `daily-digest`, `pr-watcher`);
     confirm or let the user rename it.
   - `description` -- one sentence, what it does.
   - `trigger_message` -- the instruction sent to the daemon every time it
     wakes up. Draft this based on what the user wants it to do each run.
   - `system_prompt` -- optional, only needed if the daemon should have a
     distinct persona/behaviour beyond the trigger message alone.
   - `run_interval_seconds` -- ask how often, convert to seconds, apply the
     wall-clock caveat above if relevant. If the user wants manual-trigger-
     only (no automatic schedule), leave this unset.
   - `tools` -- only what the daemon actually needs to do its job each run;
     verify names the same way as the agent-builder skill (Available Tools
     list / `describe_tool`) -- `create_daemon_config` rejects unknown
     names outright.
   - `model` / `provider` -- default to inheriting the session's, unless
     asked for something specific.
   - `enabled` -- default true (starts scheduled immediately) unless the
     user wants to review it disabled first.

2. **Show the full draft before saving** -- name, description, trigger
   message, interval (in human terms too, e.g. "every 2 hours", plus the
   wall-clock caveat if it applies), tools, model/provider, enabled state.
   Take any edits.

3. **Only call `create_daemon_config` after explicit confirmation.**

4. If the tool call returns an error:
   - **Name already exists** -- offer a different name, or switch to the
     edit flow if the user actually wants to replace the existing one.
   - **Unknown tool name(s)** -- re-check and retry.
   - **Unrecognised model/provider (warning, not a hard error)** -- config
     still saves; mention it in case it was a typo.

5. On success, tell the user it's created and, if scheduled, when it'll
   first run.

## Modifying an existing daemon

1. Call `get_daemon_config(name=...)` first -- this also returns
   `lifecycle_status` and `next_run_at`, useful context before proposing a
   change (e.g. don't casually change the interval of something that's
   about to fire).
2. Show old value → new value before calling `edit_daemon_config`. Only
   pass the fields actually changing.

## Removing a daemon

Confirm which daemon and that the user actually wants it gone -- permanent,
and stops its schedule immediately -- then call
`delete_daemon_config(name=...)`. If it reports `was_running: true`, tell
the user its in-flight run will finish on its own but won't be recorded
anywhere, since the config it belongs to no longer exists. Fine to use for
your own self-correction (e.g. you created it moments ago and the config
was wrong), including mid-run, without necessarily re-confirming first --
that's cleaning up your own immediate mistake, not touching something
established.
