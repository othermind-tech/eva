# Managing Your Skill Library

The **Skills** panel gives you full control over your skill library — creating, installing, browsing, editing, and deleting skills. Open it from the nav rail.

The panel has three levels you navigate through:

1. **Skills list** — all your skills, search, and entry points to create or install
2. **Skill folder view** — the file tree for a single skill
3. **File editor** — inline editor for any file in a skill directory

---

## Creating a skill from scratch

In the Skills list, click **New skill** → **Create new skill**.

An inline form opens with two fields:

**Skill Name** — a kebab-case identifier (`a-z`, `0-9`, `-`). This becomes the skill's directory name and its unique key. It cannot be changed after creation — if you need a different name, delete the skill and recreate it.

**SKILL.md Content** — the full file including frontmatter and body. The form pre-fills a minimal template. Replace it with your skill's content. The character limit is 10,000.

Once you save, the skill is created on disk, parsed, and added to the registry immediately. It is available to the agent from the next message.

---

## Installing a skill from a ZIP bundle

Skills can be packaged as `.skill` files — ZIP archives that contain a `SKILL.md` at the root or one subdirectory deep, plus any supporting files. This is the format used for sharing and distributing complex multi-file skills.

To install: click **New skill** → **Upload skill file**, then select the `.skill` ZIP from your file system. EVA unpacks it, validates the `SKILL.md`, and registers the skill. The skill name is taken from the frontmatter, not the filename.

If the archive is invalid or the `SKILL.md` fails validation, the upload is rejected with an error.

The file picker also accepts plain `.md`, `.yaml`, and `.yml` files for quick imports.

---

## Browsing a skill's files

Click any skill card in the list to open the **Skill folder view** — the full file tree for that skill.

The tree shows directories and files, sorted alphabetically with directories listed first. Click any file to open it in the editor. `SKILL.md` is always present and marked as the core file.

From the folder view you can:
- **Open any file** for editing — click it to go to the file editor
- **Delete any file** except `SKILL.md` — hover a file row to reveal the delete icon, then confirm

`SKILL.md` cannot be deleted from the file tree. To remove a skill entirely, use the delete action from the skills list (see below).

---

## Editing skill files

Click any file in the folder view to open the **file editor** — a full-width inline editor with the file's current content. Edit and save. For `SKILL.md`, saving automatically re-parses the frontmatter and updates the registry — changes to name, description, or `allowed-tools` take effect immediately without a reload.

For other files (phase files, templates, checklists), the change is written to disk and the skill's next execution will read the updated content.

---

## Adding files to a skill

To add a new file to an existing skill, use the editor on any file within the skill folder view and navigate away — or use the inline file path approach:

From within the folder view, the header shows the skill name and an **Add file** option. Enter a path relative to the skill root, including any subdirectory (e.g. `phases/06-deploy.md`). EVA creates the parent directories automatically if they don't exist.

You can also structure a multi-file skill by uploading an updated ZIP bundle under the same name — but note this will overwrite existing files if names collide.

---

## Reloading from disk

The **Reload** button in the Skills list header forces EVA to re-scan the entire skills directory and rebuild the registry from disk. Use this after:

- Manually editing or dropping files directly into `~/eva_workspace/{user_id}/skills/` using a file manager or terminal
- Resolving a failed upload where the registry may be out of sync with disk

Editing through the UI does not require a manual reload — file writes via the editor update the registry automatically.

---

## Deleting a skill

Deleting a skill removes both the registry entry and the entire directory on disk, including all files and subdirectories.

To delete: from the skills list, open the skill folder view for the skill you want to remove. The header contains a **Delete** action. A confirmation prompt appears before deletion proceeds.

There is no undo. If you want to preserve the skill content before deleting, copy the `SKILL.md` content from the editor first.

---

## Skill storage location

All skills live at:

```
~/eva_workspace/{your_username}/skills/{skill-name}/
```

Each user's skill library is fully isolated — skills you create are only visible to your account. The default skills shipped with EVA are copied into this directory on your first login and can be freely edited. Your edits are never overwritten by EVA updates.

---

## Troubleshooting

**Skill not appearing after creation** — click Reload to force a registry scan. If it still doesn't appear, check that the `name` in the frontmatter is valid: kebab-case, 2–64 characters, starts and ends with a letter or digit.

**Skill not triggering** — the description may be too vague. Rewrite it to include specific trigger phrases that match what you actually say. See [What are Skills?](01-what-are-skills.md#writing-effective-skill-descriptions) for guidance.

**Skill never shows up for some messages** — if the skill has a `trigger:` block, it is only offered when the message contains one of its keywords, as a whole word. Check the keyword list against what you actually type, or remove the `trigger:` block to make the skill always available. A `type: path` trigger never lists the skill at all — use `type: keyword` instead. See [Scoping a skill with a trigger](01-what-are-skills.md#scoping-a-skill-with-a-trigger).

**Upload rejected** — the ZIP archive must contain `SKILL.md` at its root or one directory level deep. Ensure the frontmatter is valid: `name` and `description` are required.

**Edit not taking effect** — if you edited `SKILL.md` and the registry shows old data, click Reload. This can happen if the server restarted between the write and the next request.

---

> **Next:** [What are Hooks?](../11-hooks/01-what-are-hooks.md) — scripts that intercept EVA's tool calls to allow, block, or modify them.
