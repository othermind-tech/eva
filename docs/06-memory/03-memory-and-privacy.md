# Memory & Privacy

EVA's memory is yours. It lives on your machine, it's readable in plain text, and you have complete control over what stays and what goes.

---

## Where memory is stored

All memory data is stored in EVA's local database — the same database that holds your conversations and project files. Nothing is uploaded to a server. Nothing is synced to a cloud account.

The database is a single file on your machine. Memory facts, agent entries, session configuration — all of it is local. If you delete the file, the data is gone. If you copy the file, you have a complete backup. There is no remote copy.

---

## What your LLM provider can see

When you send a message, EVA constructs a prompt that includes your conversation messages and, usually, a compact memory block derived from your stored facts. That prompt goes to your configured LLM provider.

The provider sees:
- Your current conversation messages
- The injected memory block (a compact plain-text summary of your stored facts)
- Project context, if you're in a project conversation

The provider does **not** have access to your database. It receives only what's included in the prompt at the time of the call — nothing more. Your stored facts reach the model as formatted text in the system prompt, the same way any other context does.

This means EVA's memory system has no special exposure compared to anything else in your prompt. If you're comfortable with your conversations going to a given provider, memory injection doesn't change that calculus.

---

## Inspecting your memory directly

The rendered version of your memory profile lives at:

```
blob_storage/memory/{user_id}_facts.md
```

This is a plain Markdown file, readable in any text editor. It's the exact text injected into your system prompt — no transformation, no hidden formatting. What you see in the file is what EVA sees when it constructs a prompt.

The file is rebuilt automatically within a few seconds after any memory write. If you've just added or deleted a fact through the UI and want to verify the change, reading this file confirms exactly what EVA will use.

---

## Backing up your memory

To back up everything EVA knows about you:

1. Copy your EVA workspace folder (`~/eva_workspace/` or whichever directory you set as your workspace)
2. Copy the `eva.db` database file from the EVA application directory

Both together give you a complete snapshot. To restore, copy them back to the same locations and restart EVA.

For full backup and deletion instructions — including how to remove API keys from the credential store — see [Privacy & Security](../16-appendix/02-privacy-and-security.md).

---

## Deleting your memory

**Individual facts** — open the Memory panel in the workspace sidebar, find the entry, and click the delete button. Your memory profile updates automatically.

**All user facts at once** — Settings → Memory → Clear all user facts. This removes every User Fact permanently. Agent Memory entries, which are task-scoped rather than personal, are not affected.

**Agent Memory for a project** — in the project browser, find the Memory section for that project and clear it. This requires confirmation and removes all Agent Memory entries associated with that project.

**Everything** — if you want a complete reset, delete `eva.db`. This removes all memory, all conversations, and all project state. The application creates a fresh database on next launch.

---

## Going completely airgapped

If you configure EVA to use a local Ollama model, your conversations and memory never go to an external AI service. Your conversations go to a local process. Your memory is stored locally. (Tools that reach the internet — such as web search, if EVA uses it — still contact those services; see [Privacy & Security](../16-appendix/02-privacy-and-security.md#network-activity-what-eva-contacts-on-its-own).)

This is the configuration for users who require complete data locality — air-gapped workstations, regulated environments, or simply a preference that no information touches an external service. EVA's full feature set, including memory, projects, daemons, and PEV mode, works identically with a local model.

---

> **Next:** [Knowledge Base](../07-rag/01-knowledge-base.md) — how EVA searches documents and indexes knowledge for retrieval.
