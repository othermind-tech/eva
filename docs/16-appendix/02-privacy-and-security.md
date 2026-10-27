# Privacy & Security

EVA is designed around two commitments: your data lives on your machine, and everything sensitive is protected at the layer that makes most sense for it. This page covers both — where your data lives, what leaves your device, how credentials are stored, and the specific mechanisms EVA uses to protect sensitive information.

Your conversations, memory, projects, documents, and settings are stored locally on your device. Your content leaves your machine only when you send it to an AI provider you chose, or when you use a tool that reaches the internet (web search, a fetched page, an MCP server you connected). EVA has no cloud account or server of its own that receives your data. A small number of requests EVA makes for its own operation — such as checking for an update — carry no user data; they are listed under [Network activity](#network-activity-what-eva-contacts-on-its-own).

---

## What leaves your device — and what doesn't

### What goes to an AI provider

When you send a message, EVA forwards it to whichever provider you have configured — Anthropic, OpenAI, Groq, or any of the others. The provider receives:

- The content of your message and any attached files you include — including images, which go to the model as real images when it can read them (see [Context and Attachments](../02-conversations/02-context-and-attachments.md))
- The system prompt that configures EVA's behaviour in that session
- Relevant context from the current conversation history

That is all. The provider does not receive your API key for other providers, your personal memory, your project files, your local workspace, your hook scripts, or any other EVA configuration. Each provider sees only the messages you send through EVA, just as they would if you used their product directly.

If some of what you type is personal — an email address, a phone number, a card number — you can have EVA replace it with a placeholder before it goes anywhere. See [Redacting personal information](#redacting-personal-information).

**What EVA's developers receive: no user data, conversation data or user logs.** EVA has no cloud account, no usage analytics, no crash reporting and no server that your messages, files, memory, logs or settings are sent to. The app runs on your machine. The only contact between EVA and its publisher is the update check described below, which sends no information about you or your use of EVA beyond what any web request inherently reveals (such as your IP address to the hosting service).

### Ollama — no message content leaves your device

If you use Ollama with a local model, your message content is not sent to any external service. Inference happens locally. This is the privacy-first mode. (EVA's own background requests, listed below, still apply — they carry no message content.)

---

## Network activity: what EVA contacts on its own

Apart from the AI provider you chose and the tools you use, EVA itself makes these requests. None of them includes your messages, files, memory, conversation history, logs or API keys.

| What | Where to | Why | When |
|------|----------|-----|------|
| **Update check** | GitHub Releases (this project's Releases page) | Find out whether a newer EVA is available. If one is, it downloads in the background and installs only when you choose to restart | On startup of the installed app (not in development builds). There is currently no setting to turn it off |
| **Model price and capability data** | LiteLLM's public data file on GitHub | Estimate costs and learn which features each model supports | At startup, and when the model library loads. Only the public data file is requested; nothing about you is sent |
| **Images in replies** | Wherever an image's link points | A reply that includes an image from a web address loads it, the way any web page would | Only when a reply contains such an image |

What EVA **does not** do: it doesn't send telemetry or analytics, doesn't phone home with a usage or install count, doesn't load fonts or scripts from third-party servers (fonts are bundled with the app), and doesn't look up your location — the Location field in your profile is just text you type. The embedding model used for document search is bundled with the installed app, so searching your files doesn't download anything.

**Requests you trigger.** Web search and page-fetch tools, MCP servers you connect, and hooks that call HTTP endpoints you wrote all contact the services they're pointed at, with whatever those tools are given. If you ask EVA to install an npm package or clone a repository, it reaches those services too. These happen because you asked for them; the [permission and approval settings](../02-conversations/04-approvals.md) control whether EVA asks first.

---

## Where your data lives

All EVA data is stored in a workspace folder on your local device. The default location is:

```
~/eva_workspace/{your_user_id}/
```

You can point EVA to any folder on your machine during setup, or change it later. The folder is yours — you can open it in your file explorer, back it up, move it, or delete individual items at any time.

```
~/eva_workspace/{user_id}/
  ├── files/              ← documents and images you upload
  ├── projects/           ← project context files (goals, tasks, knowledge, journal)
  ├── hooks/              ← your hook scripts
  ├── skills/             ← your personal skill library
  └── memory/
        └── facts         ← your auto-maintained personal fact summary
```

EVA also maintains a local database alongside your workspace that holds your conversation history, memory, agent configurations, and application state. This database stays on your machine and is included when you back up your data.

---

## Authentication — local only

When you create an account in EVA, your password is hashed with bcrypt and stored in your local database. It is never stored in plain text and is never sent anywhere.

When you log in, EVA issues two tokens:

| Token | Lifetime | What it does |
|-------|---------|--------------|
| Access token | 24 hours | Authenticates your requests to your local EVA server |
| Refresh token | 7 days | Issues a new access token when the current one expires |

Both tokens authenticate you to **your local EVA server** — not to Anthropic, OpenAI, or any other provider. They are never transmitted outside your machine. When a refresh token is used, it is immediately rotated: the old one is invalidated and a new one is issued, so a leaked token has a narrow window of use. Each token is bound to your account and your login session, so a token can't be replayed as a different user or after you sign out.

The key EVA uses to sign these tokens is generated on your machine and kept in the operating system credential store — not in a configuration file or in the environment of programs EVA launches.

An audit log of login and logout events (timestamp, event type) is kept in your local database. This log is yours — it is not sent anywhere. It is retained for 180 days by default (`AUTH_AUDIT_LOG_RETENTION_DAYS` — see [Configuration Reference](../15-configuration/01-configuration-reference.md)), after which old entries are purged automatically by the same daily background task that cleans up stale workspace files. Dead login sessions themselves (revoked, or past their refresh-token expiry) are purged immediately on the next cleanup pass, independent of that retention window.

To slow down repeated failed login attempts, EVA rate-limits `/auth/login` by both your IP address and account name — only failed attempts count, and a successful login clears the count immediately, so a few mistyped passwords never lock you out. `/auth/signup` and `/auth/refresh` are rate-limited too, as a general anti-abuse measure. This matters most if you choose to let other devices reach EVA (see [Network Exposure](#network-exposure) below), and see the [Configuration Reference](../15-configuration/01-configuration-reference.md) for the tunable limits.

---

## Network exposure

By default, the EVA server listens **only on your own machine** (`127.0.0.1`). Nothing else on your network — office Wi-Fi, a coffee shop, a hotel — can reach it. The desktop app talks to the server from the same computer, so nothing needs to be opened up for it to work.

You can change this deliberately, for example to reach EVA from another device, by setting `EVA_HOST` (see the [Configuration Reference](../15-configuration/01-configuration-reference.md)). Setting it to `0.0.0.0` makes the server reachable from every device on the same network. Even then, your conversations, files and API keys are not exposed — `/chat` and every other data-bearing endpoint requires a valid login token — but the login page itself becomes reachable, so someone on that network could try to guess your password or spam account signups. The login rate limiting described above is there for exactly this case. If you do open EVA up, also restrict inbound connections to its port at your operating system's firewall.

---

## How API keys are stored

When you enter an API key for any provider, EVA writes it to your operating system's secure credential store — not to a file and not to the database.

On Windows — the platform EVA currently supports — that store is **Windows Credential Manager** (DPAPI-encrypted). Entries appear there under names ending in `@eva` (for example `GROQ_API_KEY@eva`).

EVA reads a key from the credential store at the moment it needs it, through a single lookup used everywhere — so saving or removing a key takes effect immediately, with no restart. If there is no key for a provider in the credential store, EVA treats that provider as not configured rather than falling back to an environment variable that might happen to be set on your system — preventing accidental credential inheritance.

After saving a key, EVA validates it against the provider's live API and reports whether it was accepted. The key value itself is never returned in any response or log — write-only by design. Because each save makes a live call to the provider, saving keys is rate-limited per account (ten saves in five minutes by default) — plenty for normal key rotation, but it stops a stolen login from being used to run up your provider quota.

**Removing a key.** Profile → Account → API Keys → **Remove key** deletes the key from the credential store. The credential store is shared by every EVA account on the same machine, so the key is removed for all of them. This doesn't revoke the key at the provider — do that on the provider's website if the key may have been exposed.

**MCP server credentials get the same treatment.** When you add an MCP server whose settings include an API token or password — in an environment variable or a request header — EVA moves any value whose name suggests a secret (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `AUTH` and similar) into the same credential store and keeps only a masked placeholder in its own records. The real value is read back only at the moment EVA connects to the server, and is never returned to the interface. Deleting the server deletes its stored credentials. Servers added before this protection existed are migrated automatically the next time EVA starts. See [MCP Servers](../12-mcp/01-what-are-mcp-servers.md#credentials-and-secrets).

**Programs EVA starts don't receive your provider keys.** Shell commands EVA runs, background processes it starts, git commands, and hook scripts are launched with provider API keys stripped from their environment. A hook script that reads `$GROQ_API_KEY` or `$ANTHROPIC_API_KEY` finds it empty. This keeps a stray command — or a script EVA was asked to run — from reading or leaking the keys you saved for EVA. If a hook genuinely needs a credential, supply one of its own (for example in the script or an environment variable you define for that hook) rather than reusing your provider key.

---

## Redacting personal information

EVA can scrub personal details out of what you type before it is stored or sent to a model. Turn on **PII redaction** in Session settings → Output & Safety. It is **off by default**.

**What it catches** — email addresses, phone numbers, US Social Security numbers, credit card numbers, bank account and IBAN numbers, passport, driver's licence and medical licence numbers, and cryptocurrency addresses. Each is replaced with a labelled placeholder such as `<EMAIL_ADDRESS>` or `<PHONE_NUMBER>`.

**What it leaves alone** — names, locations and dates. This is deliberate: EVA needs your timezone and location to schedule things, dates to run daemons and reminders, and names for entirely ordinary requests like "email John about the meeting." Redacting them would break real functionality.

**When it happens** — before anything else. Your message is redacted first, and the redacted version is what gets saved in your conversation history, written to the conversation journal, and sent to the AI provider. Nothing sensitive reaches disk or the provider, not just the provider. (Your own screen briefly shows what you typed until the message is sent; reloading the conversation shows the redacted version.) EVA also tells the model that redaction is on, so it treats a placeholder as "a value that was removed" rather than as literal text or something to guess at.

**Private by construction** — detection runs entirely on your computer, using a pattern and language-analysis engine bundled with EVA. Your text is not sent anywhere to find the sensitive parts of it.

**Tool output too** — the same scrubbing is available for what tools return. In Hooks, add a post-tool hook with the **Redact PII** handler — it works exactly like the existing *Redact env vars* handler, and can be limited to particular tools. That is how you keep personal data in a file EVA reads, or a web page it fetches, from reaching the model.

**Know its limits.** Detection is pattern-based and best-effort. It is tuned to avoid false alarms, so an unusual number format, or a number that doesn't pass a validity check, can slip through, and it works best on English text. Treat it as a safety net rather than a guarantee — if something must never leave your machine, use a local model with Ollama. And if the detection engine ever can't start, EVA sends your message unredacted rather than blocking it, and records a warning in its logs.

---

## Sensitive memory access control

Agent memory entries can be marked **sensitive** when they are created. Sensitive entries are never automatically injected into conversation context — they appear only as placeholders until you explicitly grant access.

When you grant access, the sensitive entry becomes readable for that conversation. The grant expires automatically after one hour, after which the entry reverts to placeholder status. This allows you to store private notes, critical decisions, or security-relevant information in agent memory without any risk of it being silently included in a future prompt you did not intend.

> *Example: A sensitive architectural decision is stored in agent memory. In a later conversation, EVA acknowledges the entry exists but shows only a placeholder in its context. You grant access; EVA reads it and incorporates it. The session ends and the entry is protected again.*

---

## Inter-agent data encryption

When multiple agents collaborate on a task, they share an internal scratchpad called the **blackboard** — a structured space where agents write findings, planning outputs, and messages to one another during a single execution.

This scratchpad data is encrypted at rest using **AES-256-GCM** when blackboard encryption is enabled. Each execution receives its own unique encryption key derived via HKDF from the server's secret combined with the execution ID — so no two executions share a key, and the key is never stored anywhere. It is computed on demand and discarded when the execution ends.

Agents read and write in plain text; encryption and decryption happen transparently in the infrastructure layer. If someone accessed the in-memory broker directly, they would see only ciphertext.

> *Example: A researcher agent writes its findings to the blackboard. An orchestrator agent writes its draft plan. Both are stored as AES-GCM ciphertext in the broker. When the coding agent reads the researcher's findings as a tool result, it receives plain text — decryption happened transparently before the tool returned.*

---

## Backing up your data

To back up everything EVA knows:

1. Copy the `~/eva_workspace/` folder (or whichever directory you set as your workspace)
2. Copy the `eva.db` database file from the EVA application directory

Together these two items contain your complete EVA state. To restore, copy both back to the same locations and restart EVA.

---

## Deleting your data

You have three levels of deletion available:

**Delete specific items** — conversations, memory entries, projects, agents, and daemons can each be deleted individually from within the app. Deleted items are removed from the database immediately.

**Clear your workspace folder** — deleting the workspace folder removes all your project files, hook scripts, skills, uploaded documents, and file attachments. The database records pointing to those files remain; re-running EVA will find them missing and handle gracefully.

**Full wipe** — to remove all EVA data from your machine:
1. Stop the EVA application
2. Delete your workspace folder (`~/eva_workspace/` or your configured path)
3. Delete the `eva.db` database file from the EVA application directory
4. Remove your API keys — answer **Yes** to the "Delete all EVA data?" prompt when you uninstall (see below), or use **Remove key** in Profile → Account, or delete the entries ending in `@eva` from Windows Credential Manager (Control Panel → Credential Manager → Windows Credentials)

> **Uninstalling EVA and your saved keys.** When you uninstall EVA, the uninstaller asks **Delete all EVA data?** with **Yes** and **No** buttons, and **Yes** is the default. Choosing Yes removes your EVA application data folder (conversations, settings, vector database) and every EVA entry from Windows Credential Manager — your provider and MCP keys and EVA's sign-in key. Choose No to keep everything for a future reinstall. Updating EVA to a newer version never deletes anything. (This applies to v0.0.10 and later; the v0.0.9 uninstaller on an all-users install looks in the wrong folder, so choose No there.) The uninstaller doesn't touch your workspace folder, so delete that yourself if you want it gone (step 2).

After a full wipe, EVA starts fresh with no memory of previous conversations, projects, or configurations.

---

## A note on third-party providers

EVA is a local orchestration layer. When you use a cloud AI provider through EVA, you are still subject to that provider's own privacy policy and data retention practices. If data privacy with a specific provider is a concern, you can switch to Ollama or another local model — EVA supports this with no configuration beyond setting Ollama as your provider.

---

> *The data flows, storage mechanisms, and security properties in this document reflect EVA at the time of release. EVA's commitment to user privacy and data ownership extends to all future developments — any updates to storage, data flows, or privacy management will be reflected here promptly.*

---

> **Next:** [Technology Stack](03-technology-stack.md) — what EVA is built from.
