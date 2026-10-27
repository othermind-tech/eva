# Conversations

A conversation in EVA is a persistent, accumulating dialogue. Every message you send is answered in the context of everything that came before — previous questions, EVA's responses, decisions made, tools called, and documents shared. This means follow-up questions work naturally, work builds on itself, and you never have to re-explain what was already established.

This is different from tools that treat every message as an independent request. In EVA, a conversation has memory within the thread.

---

## How context accumulates

As a conversation grows, EVA has access to its complete history. If you spend several messages exploring a problem — trying different approaches, ruling things out, refining the goal — EVA's next response reflects that entire arc. Phrases like "do the same for the other file", "actually, try a different approach", or "why did you choose that?" work because EVA knows what "the other file" and "that" refer to.

There is no fixed limit on how long a conversation can be. When a conversation grows long enough to approach the model's context window, EVA automatically compresses the earlier portion into a structured summary and continues without interruption — nothing is lost. See [Long Conversations](06-long-conversations.md) for how this works.

---

## Standalone conversations vs project conversations

A standalone conversation is self-contained. It knows only what has been said within its own thread.

A conversation linked to a project gains access to the project's accumulated context on top of its own history — the project's goals, background documents, rules, and the knowledge that has built up across all previous work in that project. This is the difference between asking EVA a general question and asking an EVA that has been working inside your codebase, campaign, or research area for weeks.

Conversations can be moved into a project at any time. Once linked, the project's context becomes available from the next message onward.

---

## Starting fresh

Every new conversation begins with a clean slate. If you want EVA to carry context from a previous conversation into a new one, you can reference the earlier thread explicitly — covered in [Context & Attachments](02-context-and-attachments.md).

---

## Per-conversation system prompt

Each conversation in EVA can have its own **system prompt** — a set of instructions that defines how EVA should behave within that specific thread. This is distinct from tools like ChatGPT or Perplexity, where a system prompt (if it exists at all) is a global setting that applies to everything. In EVA, every conversation can have its own.

A system prompt sets the stage before EVA reads your first message: the tone it should take, the persona it should adopt, the constraints it must respect, or the focus it should maintain throughout. These instructions stay in effect for the entire conversation and can be updated at any time.

> *A conversation used for drafting investor communications instructs EVA to write in formal English, avoid speculation, and always quantify claims with numbers. A separate creative brainstorming conversation tells EVA to think laterally, ignore constraints, and prioritise originality over accuracy. Both run simultaneously — each with its own character.*

---

> **Next:** [Context & Attachments](02-context-and-attachments.md) — sharing documents, images, and past work with EVA.
