# Getting Started

You have EVA installed and you are logged in. This guide walks you through the five things to configure before your first real conversation — in the order that makes most sense.

> **Supported platform:** EVA is built and tested for **Windows 10 and 11 (64-bit)**. Other platforms aren't supported yet.

---

## 1. Session settings

Session settings control how EVA behaves across every conversation. Start here so EVA is configured the way you want it before you add providers and start chatting.

Click the **Settings icon** (the sliders/gear icon) in the left NavRail. A panel opens on the right side of the screen with two sections: **Behaviour** and **Tools**.

### Behaviour

The Behaviour section has six cards. Each saves automatically when you change it — there is no save button.

**Agent Mode**
Controls how EVA approaches your messages.

| Option | When to use |
|--------|-------------|
| **Auto** *(default)* | EVA decides — conversational mode for quick tasks, Plan-Execute-Verify for complex ones |
| **Chat** | Forces conversational mode. Good for quick Q&A where you don't need a structured plan |
| **Plan** | Forces Plan-Execute-Verify mode. EVA always generates a step plan before acting |

Leave this on **Auto** if you are unsure — EVA makes good decisions about which mode to use.

**Skill Mode**
Whether EVA is aware of your skill library during executions.

| Option | Behaviour |
|--------|-----------|
| **Auto** *(default)* | EVA uses skills when relevant |
| **On** | Skills always loaded |
| **Off** | Skills ignored entirely |

**Tool Approval**
How cautious EVA is before taking actions on your machine.

| Option | Behaviour |
|--------|-----------|
| **Auto** | EVA runs safe read-only tools without asking; pauses for write/delete actions |
| **Ask** | EVA asks before every single tool call |
| **Trust** | EVA runs all tools without pausing |

Start with **Auto**. Move to **Ask** if you want more oversight while you are learning what EVA can do.

**Allowed Authority**
Sets the maximum permission level for tools EVA can call.

| Option | What EVA can do |
|--------|----------------|
| **Read** | Read files, search the web, query documents — nothing is modified |
| **Mutating** | Can also write files, create items, send requests |
| **All** | Also allows destructive actions (delete, overwrite) |

Start with **Mutating**. Raise to **All** only when you explicitly need EVA to delete or overwrite things.

**Memory**
Two toggles in one card:
- *Chat memory* — EVA references your conversation history to personalise responses over time
- *Agent memory* — EVA maintains and reads its personal fact store about you

Both are recommended on for the best experience.

**Output & Safety**
Controls for how deep EVA thinks and how carefully it works:
- *Reasoning* — sets the reasoning depth for supported models. The control adapts to what the selected model actually supports: a level picker (Off / Minimal / Low / Medium / High / Xhigh) when the model supports multiple depths; a binary toggle when only on/off is meaningful; hidden entirely when the model has no reasoning capability. Some of the newest Claude models (Fable 5, Mythos 5) always reason and cannot be turned off — those show the picker without an Off option. The level is snapped to the nearest one the model supports if you switch to a model with a smaller range.
- *Shell safety* — when on, shell commands run in read-only mode by default
- *Plan approval* — pause multi-step tasks so you can review, edit or reject the plan before EVA runs it. See [Approvals](../02-conversations/04-approvals.md#plan-approval)
- *PII redaction* — replace emails, phone numbers, card numbers and similar in your messages with placeholders before they're stored or sent to a model. Off by default. See [Privacy & Security](../16-appendix/02-privacy-and-security.md#redacting-personal-information)
- *Skip completed steps* — in multi-step tasks, skip a later step when an earlier one already did its work. On by default. See [Plan-Execute-Verify](../04-plan-execute-verify/01-what-is-pev.md)
- *Step extensions* — how many times you can grant EVA more steps when it reaches its step limit (0 turns the prompt off)

### Tools

The Tools section shows all available tools in three groups: **Default**, **Custom**, and **MCP**. Use the search bar to find specific tools. Toggle any tool off to hide it from EVA entirely — a hidden tool is invisible to the model, not just restricted.

---

## 2. API keys

EVA needs at least one AI provider key to function. Keys are stored in Windows Credential Manager, your operating system's secure credential store — not in a file.

**To add a key:**

1. Click the **user pill** at the bottom-left of the workspace panel (your username)
2. The profile modal opens. Click the **Account** tab
3. Under *Provider Keys*, select a provider from the dropdown
4. Paste your API key into the key field. Click **Save Key**
5. EVA validates the key against the provider's live API and shows a green "Validated" badge if it was accepted

Keys are **write-only** — once saved, there is no way to read the key back. This is by design. You can remove a key at any time with **Remove key** in the same tab.

**Which provider to start with?**

| Provider | Cost | Good starting point? |
|----------|------|---------------------|
| Groq | Free tier available | Yes — fast models, no upfront cost |
| Anthropic | Pay-per-token | Yes — Claude is EVA's recommended default |
| OpenAI | Pay-per-token | Yes — GPT-4o is a strong all-rounder |
| Ollama | Free (local) | Yes — if you want fully offline, no API cost |
| Ollama Cloud | Pay-per-token (affordable) | Yes — open-weight models (Llama, Qwen, DeepSeek) without local hardware |

If you are not sure, add a Groq key to get started without spending anything. You can always add more providers later — EVA also works with Google Gemini, DeepSeek, Mistral, xAI and a dozen more; see [Providers & Models](../13-providers/01-configuring-providers.md).

> **Keys take effect immediately** — no restart required.

---

## 3. Setting your provider and model

Once you have at least one key saved, select which provider and model EVA should use by default.

1. Click the **user pill** again
2. Click the **Providers** tab
3. The *Configured Providers* grid at the top shows every provider with its status: a green checkmark means ready to use, a key icon means a key is still needed, a warning mark means a key is saved but that service can't be used for chat — typically a key saved in an earlier version for a service that needs more than a single key, or hasn't been verified to work with EVA's tools (nothing is wrong with your key; see [Services EVA doesn't support for chat](../13-providers/01-configuring-providers.md#services-eva-doesnt-support-for-chat)), and "local" labels appear on Ollama

Under *Provider & Model*:
- Select your **provider** from the left dropdown. It lists only providers you can actually use. EVA loads the available models live from that provider
- Select your **model** from the right dropdown. Capability badges appear below — these show whether the selected model supports tool calls, vision, streaming, file uploads, and reasoning

Click **Save changes** in the modal footer. This becomes your default for all new conversations.

> If you try to send a message before this step is done, EVA stops you with a prompt to configure a provider first, rather than silently failing or guessing one on your behalf.

---

## 4. Fallback chain (optional)

The fallback chain is a list of backup providers EVA will try automatically if your primary provider fails — due to a rate limit, outage, or quota exhaustion.

Still in the **Providers tab**, scroll to *Fallback Chain*:

1. Click **Add Fallback**
2. Select a provider and then a model for that row
3. Add as many fallback steps as you want — EVA tries them in order

A typical setup: primary Anthropic → fallback OpenAI → fallback Groq. If Anthropic hits a rate limit, EVA switches to OpenAI silently and continues. If OpenAI also fails, it moves to Groq.

If you only have one provider, skip this section and come back later.

---

## 5. Ollama (local models)

If you want to run models entirely on your machine with no API costs and no data leaving your device, EVA supports Ollama.

**Prerequisites:** [Ollama](https://ollama.com) must be installed and running on your machine, with at least one model pulled (e.g. `ollama pull llama3.2`).

**To configure:**

1. Click the **user pill** and go to the **Account tab**
2. Scroll down to *Local Models — Ollama*
3. The reachability indicator shows whether EVA can currently reach your Ollama server
4. If the default URL (`http://localhost:11434`) is correct, click **Save URL** — EVA will probe and confirm
5. If Ollama is running on a different host or port (e.g. in a Docker container), update the URL first then save

Once saved, go to the **Providers tab** and select **Ollama** as your provider. EVA will load the models you have pulled locally.

> **Your messages stay on your machine** — Ollama is the only mode where no message content is sent to an external AI service. If data privacy is a priority, Ollama is the right choice. (Web search and other internet tools still reach the internet if you use them; see [Privacy & Security](../16-appendix/02-privacy-and-security.md#network-activity-what-eva-contacts-on-its-own).)

---

## You are ready

With session settings configured, at least one provider key saved, and a provider and model selected, EVA is ready for your first conversation.

Start a new chat by clicking **New chat** at the top of the workspace panel (or pressing `⌘N` / `Ctrl+N`). Type a message and press Enter.

Building a web app? Turn on the Playwright browser in the MCP Servers panel and open the **Preview** pane from the left rail — EVA can then run your app and test it in a browser you can watch. See [Activity and the Preview Pane](../02-conversations/07-activity-and-preview.md).

---

## Quitting EVA

Closing EVA — from the tray icon's **Quit EVA** or the in-app Quit button — checks first whether anything is still running, across every conversation and every daemon, not just what's currently open. If something's active, you're asked to **Stop and Quit**, **Keep Running** (minimizes instead of closing), or **Cancel** (stays open, nothing touched). The window's own **×** button is unaffected by any of this — it always just minimizes EVA to the tray.

Choosing **Stop and Quit** also stops every background process (such as a dev server EVA started) along with EVA itself — except any you've pinned to keep running from the [Activity tab](../02-conversations/07-activity-and-preview.md#running), which survive the quit untouched.

---

> **Next:** [What is EVA?](02-what-is-eva.md) — what EVA is, how it differs from a chat app, and what it can do.
