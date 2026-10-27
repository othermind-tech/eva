---
name: agent-builder
description: "Use when the user asks you to create, build, or save a new subagent for a task. Walks through gathering requirements, drafting the configuration, getting explicit confirmation, and saving it via create_subagent_config."
license: MIT
allowed-tools: [create_subagent_config, get_subagent_config, edit_subagent_config, delete_subagent_config]
metadata:
  type: builder
  tier: platform
  version: "1.0"
---

# Agent Builder

Lets a user create, inspect, modify, and remove reusable subagents entirely
through conversation, instead of the /agents panel.

## When this applies

Only on an explicit request -- the user asks you to create/build/save a
subagent ("make me an agent that reviews PRs", "save this as a subagent I
can reuse"). Don't propose creating one on your own initiative just
because you notice a repeated pattern; that's a deliberate choice left to
each user's own setup (e.g. a project's system prompt can ask for that
behaviour explicitly if wanted) rather than something this skill does by
default.

## Creating a new subagent

1. **Gather what's needed**, asking only for what you don't already know
   from context:
   - `name` -- suggest a short, descriptive slug yourself (e.g.
     `pr-reviewer`, `changelog-writer`); confirm or let the user rename it.
   - `description` -- one sentence, what it's for.
   - `system_prompt` -- draft one based on the task the user described.
     Show it to the user; this is the part most worth them reviewing
     closely, since it's what actually shapes the subagent's behaviour.
   - `tools` -- which tools does this subagent actually need? Keep the
     list narrow and specific to the task rather than granting everything
     -- if you're not sure a tool name is exactly right, check the
     Available Tools list or call `describe_tool` before including it.
     `create_subagent_config` will reject unknown tool names outright.
   - `model` / `provider` -- default to leaving both unset (inherits the
     user's current session settings) unless the user asks for something
     specific.
   - `max_steps` -- default 5 is fine for most tasks; suggest a higher
     number only if the task genuinely needs many tool calls per run.

2. **Show the full draft before saving it** -- name, description, system
   prompt, tools, model/provider (or "inherits session"), max_steps -- in
   plain readable form, not raw JSON. Take any edits.

3. **Only call `create_subagent_config` after explicit confirmation.**
   Never save silently.

4. If the tool call returns an error:
   - **Name already exists** -- offer to pick a different name, or (if the
     user actually wants to replace/update the existing one) switch to the
     edit flow below instead.
   - **Unknown tool name(s)** -- re-check the exact name via the Available
     Tools list or `describe_tool`, fix, and retry.
   - **Unrecognised model/provider (warning, not a hard error)** -- the
     config still saves; mention the warning to the user in case it was a
     typo, but don't treat it as a failure.

5. On success, tell the user the subagent was created and how to use it:
   `delegate_to_agent(agent_name="<name>", task="...")`.

## Modifying an existing subagent

1. Call `get_subagent_config(name=...)` first, so you know the current
   values before proposing a change -- `edit_subagent_config` only changes
   the fields you actually pass; everything else stays exactly as it was,
   so working from stale assumptions about the current state risks
   confusing the user about what actually changed.
2. Show the user what will change (old value → new value) before calling
   `edit_subagent_config`. Only pass the fields that are actually changing.
3. Note: a *default* agent's `tools` list can't be changed this way (EVA
   manages it) -- the tool will tell you if that's the case; the user would
   need to clone it first via the panel.

## Removing a subagent

Confirm which subagent and that the user actually wants it gone -- this is
permanent -- then call `delete_subagent_config(name=...)`. This is also
fine to use for your own self-correction (e.g. you created one moments ago
and realised the config was wrong) without necessarily re-confirming with
the user first, since it's cleaning up your own immediate mistake rather
than touching something established.
