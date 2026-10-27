# What EVA Knows About You

Everything EVA has learned about you is stored in a structured profile. You can inspect it, edit individual entries, add facts manually, or wipe it entirely. Nothing is hidden.

---

## User Facts

User Facts are the core of EVA's long-term memory. They're extracted from your conversations and maintained as named fields. Some fields hold one value at a time; others accumulate multiple values.

| Field | Type | What it captures |
|-------|------|-----------------|
| name | Singular | Your casual name — what you want to be called in conversation |
| full_name | Singular | Your legal name — **profile-locked**, set in Profile settings only |
| about | Singular | A short description of who you are — **profile-locked** |
| occupation | Singular | Your job title or role |
| location | Singular | Your city or region, if mentioned in conversation |
| primary_language | Singular | Your primary programming language (BCP-47 for spoken language goes in Locale) |
| experience_level | Singular | Your self-described or inferred experience level |
| timezone | Singular | Your IANA timezone, if mentioned in conversation |
| skills | Plural | Skills and areas of expertise |
| tools | Plural | Software, frameworks, and tools you use |
| preferences | Plural | Working preferences you've expressed |
| style | Plural | Communication or work style observations |
| projects | Plural | Projects you've mentioned being involved in |
| misc | Plural | Facts that don't fit other categories (capped at 5 entries) |

> **Location and timezone** appear in both User Facts and Locale preferences because they have two sources: auto-extracted from what you say in conversation, and explicitly set in your profile. The explicit Locale settings take precedence — the auto-extracted values are a fallback for when no explicit setting exists.

---

## Locale preferences

Locale preferences are not memory facts — they are session configuration you set explicitly in Profile → Locale. They're listed here because they're part of what EVA knows about you, even though they work differently.

| Preference | What it is | Where it's set |
|------------|-----------|----------------|
| **Language** | The language EVA responds in (BCP-47 code, e.g. `en-US`, `de-DE`) | Profile → Locale |
| **Location** | Your city or region, as plain text you type, so EVA can give location-sensitive answers. It is not looked up on any map service | Profile → Locale |
| **Timezone** | Your IANA timezone for scheduling and datetime formatting (e.g. `Europe/London`) | Profile → Locale |

These apply immediately to every session and don't change unless you update them. They're injected into session configuration, not into the memory facts block.

---

## Agent Memory

Agent Memory entries capture what happened during PEV executions, scoped to a project or execution context rather than you personally.

| Entry type | What it records |
|------------|----------------|
| Error | An error encountered during execution and what resolved it |
| Success | An approach that worked well |
| Decision | An architectural or approach choice with the rationale |
| State | A checkpoint — where a long task left off |
| Artifact | An output produced and where it lives |
| Note | General observations worth carrying forward |

Agent entries let EVA recall what it tried, what failed, what succeeded, and what was decided — across sessions in the same project. When you return to a project after a week away and ask EVA to continue a task, it can consult these entries rather than starting fresh.

**Entries don't pile up.** Before saving anything new, EVA checks what it already remembers for that scope. If the new observation repeats an existing entry, it isn't saved again; if it updates one, the existing entry is revised in place rather than a near-copy being added. There is only ever one **State** entry per scope — a new checkpoint replaces the previous one. A recurring error or success reinforces the entry that already exists instead of creating another.

**Old errors expire.** An **Error** entry is removed automatically after 90 days (adjustable in the [Configuration Reference](../15-configuration/01-configuration-reference.md)), because a resolved error rarely stays relevant. Every other entry type is kept until you delete it.

You can view and manage entries in the Memory panel under the **Agent Memory** tab, or via the project browser.

---

## Sensitive memory

Any memory entry — user fact or agent entry — can be marked sensitive. A sensitive entry is stored and managed normally, but its value is not displayed in the UI until you explicitly grant access.

When you open the memory panel and an entry is sensitive, you see a placeholder: *Sensitive memory entry — access is not currently granted*. To view the value, click the entry and grant access. The grant lasts for one hour and is automatically revoked after that.

This is useful for entries containing information you wouldn't want visible on an unlocked screen — medical notes you've mentioned, financial details, or anything else that warranted explicit sensitivity.

---

## How the memory profile looks

EVA organises your User Facts into a structured summary that gets injected into your conversations. It's rebuilt within a few seconds after any memory write.

The summary is organised into sections by category:

1. **Identity** — name, full name, about
2. **Technical Stack** — primary language, tools
3. **Skills & Expertise** — skill entries
4. **Preferences** — preference entries
5. **Working Style** — style entries
6. **Projects & Context** — project entries
7. **Other** — miscellaneous entries

Each section only appears if there are facts to populate it. An empty memory profile contributes nothing to your conversations.

---

## Viewing and managing your memory

**In the UI** — open the Memory panel from the workspace sidebar. The **User Facts** tab lists all extracted facts by category. You can see when each fact was last updated.

**Editing a fact** — click any entry to open it. You can update the value directly. Manual edits are tagged differently from auto-extracted facts.

**Deleting a fact** — use the delete button on any individual entry. EVA's memory profile updates automatically.

**Bulk clear** — Settings → Memory → Clear all user facts removes every User Fact permanently. This is irreversible and requires explicit confirmation. Agent Memory entries are not affected; they can be cleared separately per project.

**Adding a fact manually** — you can also tell EVA directly in conversation: "remember that I prefer TypeScript over JavaScript" or "note that my billing currency is EUR". EVA will write the entry. Manually added facts are tagged to distinguish them from auto-extracted ones.

---

> **Next:** [Memory & Privacy](03-memory-and-privacy.md) — where the data lives, what your LLM provider can see, and how to back up or delete your memory.
