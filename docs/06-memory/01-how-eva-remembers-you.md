# How EVA Remembers You

EVA builds a picture of who you are over time. Not because you fill out a profile form, but because it reads your conversations and draws conclusions from them. This accumulated knowledge lives in two distinct stores: **User Facts** — things EVA has learned about you as a person — and **Agent Memory** — structured records of what happened during task executions. Both are visible and fully manageable in EVA's Memory panel.

User Facts are personal: your role, your skills, the tools you use, the way you like to work. Agent Memory is task-scoped: the errors encountered, decisions made, and approaches that succeeded on a given project. Together, they mean that when you return to a conversation or project, EVA already knows the relevant context — you don't re-introduce yourself, and you don't re-explain what was tried last time.

---

## How memory gets into your conversations

Before each conversation response, EVA assembles a memory block from your stored facts and inserts it into the system prompt. This happens automatically, before you send your first message. From your perspective, EVA simply seems to already know things — because it does.

The block is compact: a short, structured summary of what EVA knows about you. It's bounded by a token budget so it never crowds out the actual conversation. When a conversation is inside a project, relevant Agent Memory for that project is also included.

In some session configurations, rather than injecting facts automatically, EVA retrieves them on demand — looking up only what's relevant to the question at hand. EVA can also add new facts or remove existing ones during a conversation, in response to what you tell it. Everything it stores is visible and editable in the Memory panel.

---

## How User Facts are extracted

After each conversation response, EVA runs a memory extraction pass. It reviews the recent exchange and asks: does this contain meaningful facts about this person? If it does, it extracts them and adds them to your User Facts.

This runs asynchronously after the response is delivered — it doesn't slow down the conversation you're having. From your side, it's invisible.

Extraction happens at the conversation level, not in real time within a message. EVA won't update a fact mid-sentence; it waits until a response completes, then reviews what was learned.

**What counts as extractable:** statements about yourself — your role, where you work, what technologies you use, how you prefer things formatted, which projects you're running. Things that are true about you, not just things that were useful in this conversation.

**What doesn't trigger extraction:** greetings, single-message exchanges, factual lookups ("what does CORS stand for"), and sessions dominated by tool calls. EVA applies a signal quality check before running the extractor — low-signal conversations are skipped entirely.

A minimum number of messages must exist in the conversation before extraction runs at all. One-liner exchanges don't produce memory.

---

## What you set directly

Some things belong in explicit preferences rather than inferred memory. EVA distinguishes between what it learns from conversation and what you configure deliberately.

**Your Profile** — accessible from the workspace panel — has three identity fields that sit outside the memory extraction system:

- **Full Name** — your legal name, used in formal output when EVA generates documents, reports, or correspondence on your behalf
- **Preferred Name** — the name EVA will address you by in conversation
- **About** — a short description of yourself that frames the kind of work you do; written by you, not extracted

Full Name and About are **profile-locked**: the LLM extractor cannot overwrite them regardless of what you say in conversation. If EVA reads something that looks like your name in a message, it will not update Full Name. Only you can change these fields, in Profile settings.

**Locale preferences** — also set in your Profile, under the Locale section — cover language, location, and timezone. Unlike User Facts, these are not extracted from conversation; you set them explicitly and they apply to every session immediately. See [What EVA Knows About You](02-what-eva-knows.md) for the full Locale reference.

---

## Agent Memory in PEV mode

In conversational interactions, EVA extracts facts about you. In PEV mode — when it's executing a structured task — EVA also produces a different kind of memory: structured records of what happened during the task.

These entries span six categories — errors, decisions, state checkpoints, artifacts, successes, and notes — each capturing a different dimension of what happened during the execution. See [What EVA Knows About You](02-what-eva-knows.md) for the full breakdown.

Agent Memory is scoped: in a project conversation, entries are attached to that project. In a standalone execution, they're attached to a general scope. This means EVA can carry forward knowledge of "what we tried and what worked" across sessions on the same project without needing you to explain it again.

Agent Memory extraction only runs in plan_execute mode. Chat conversations do not produce structured task entries — those only make sense in the context of a real execution.

---

> **Next:** [What EVA Knows About You](02-what-eva-knows.md) — the full picture of what's stored, how to view it, and how to manage it.
