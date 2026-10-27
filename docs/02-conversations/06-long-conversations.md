# Long Conversations

Every AI model has a finite context window — a limit on how much text it can hold in view at once. Most tools simply break or lose the beginning of a conversation when they approach that limit. EVA doesn't. It compresses the earlier portion of the conversation into a structured summary, preserves the most recent exchange in full, and continues without interruption.

You never have to manage this. There is no "start a new thread" step, no context limit warning, and no lost work.

---

## What happens when a conversation gets long

As a conversation grows, EVA tracks how much of the model's context window the history is consuming. When that approaches capacity, EVA automatically summarises the earlier portion of the conversation before sending the next response.

The summary is comprehensive: it captures what was discussed, decisions that were made, files created or modified, code written, commands run, and the precise state of any work in progress. Nothing that matters is discarded — it is condensed, not deleted.

The summary is injected as context at the start of the next prompt, followed by the most recent messages verbatim. From the model's perspective, the conversation is still fully coherent. From your perspective, nothing changes.

---

## The summary adapts to your model

The trigger point scales with the context window of the model you are using. A model with a smaller context window will begin compressing earlier; a model with a very large context window can hold much more history before compression kicks in. EVA reads the model's actual capability at runtime — there are no fixed limits hardcoded per provider.

This means switching to a larger model gives you proportionally longer conversations before any compression happens. Switching to a smaller or local model compresses sooner but otherwise behaves identically.

When EVA cannot determine a model's context window — which can happen with local Ollama models or any model not in EVA's capability registry — it falls back to a built-in conservative limit rather than guessing. This default is intentionally cautious: it triggers compression earlier than a large model would need, but ensures the conversation never overflows regardless of what model is in use.

---

## How the summary stays current

Compression is not a one-time event. As the conversation continues growing after the first summary, EVA updates the summary to incorporate the new messages — producing a single integrated document each time, not a growing stack of appended segments. The summary at any point reflects the full history of the conversation up to the compression boundary, coherently merged.

Tool calls and their results — which can be very large in their raw form — are represented as compact references in the summary rather than reproduced in full. The substance is retained without the bulk.

---

## What the summary preserves

The summariser is instructed to treat the summary as the only persistent record of the compressed portion. Specifically, it preserves:

- **Decisions and conclusions** — architectural choices, agreed approaches, ruled-out options and why
- **Work completed** — files created or modified, functions or classes written, commands executed and their outcomes
- **Critical code** — short, important snippets reproduced verbatim (a key fix, a config change, a schema definition)
- **Current state** — precisely where things stand at the point of compression
- **Pending work** — tasks in progress, tasks agreed but not started, and the exact next step

The last point is intentional: the summary does not say "several items remain". It names each one.

---

## Nothing to configure

Conversation compression is always on and requires no setup. If you prefer a model with a larger context window to defer compression further, that is the only relevant choice — the compression system itself has no user-facing settings.

---

> **Next:** [Activity and the Preview Pane](07-activity-and-preview.md) — seeing and steering background work, and watching EVA test a web app in a built-in browser.
