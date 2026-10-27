# Providers & Models

EVA is provider-agnostic. It has no fixed AI model — you choose which provider and model it uses, and you can change it at any time without restarting anything. The same agent, the same memory, the same tools, but a different brain.

EVA works with **18 providers** across two tiers — native and gateway. This includes Ollama for fully local inference and Ollama Cloud for hosted open-weight models. A provider appears in EVA's pickers only when EVA can chat with it *and* you have added its key (local Ollama needs no key), so everything you can select is something that will work.

---

## Native vs gateway providers

Not all providers are equal in EVA's eyes. Understanding this distinction helps you know what to expect when selecting a model.

EVA is designed to support the widest possible range of AI providers. For the most widely used providers — the ones where it's worth investing in deep, first-class support — EVA builds a dedicated native handler: custom code that speaks that provider's official SDK, exposes every feature the provider offers, and keeps EVA's core logic clean and unified regardless of which provider is active. For the long tail of less common providers, writing and maintaining a custom handler for each one isn't practical. Instead, EVA routes those through the LiteLLM SDK, a unified compatibility layer that already speaks dozens of provider APIs. This is the gateway tier.

### Native providers

Native providers have dedicated handlers built into EVA's core. EVA uses each provider's official SDK, supports their full feature set, and maintains a hand-curated capability database for their models. EVA's internal agent loop doesn't need to know which native provider is active — the handler abstracts the difference.

| Provider | Notes |
|----------|-------|
| **Groq** | Native. Known for extremely high token throughput. |
| **OpenAI** | Native. GPT-4o, o3, and other OpenAI models. |
| **Anthropic** | Native. Claude family. |
| **Ollama** | Native. Fully local — no API cost, no data ever leaves your machine. |
| **Ollama Cloud** | Native. Ollama's hosted cloud service — open-weight models without local hardware requirements. |

For native providers, EVA can:
- Fetch a live model list directly from the provider's API
- Validate your API key immediately on save
- Show curated, high-accuracy capability data for each model

### Gateway providers

Gateway providers are accessed through the LiteLLM SDK, which EVA uses as a compatibility layer. You configure them the same way — select the provider, paste your key, select a model — but EVA routes the request through LiteLLM internally.

| Provider | Notes |
|----------|-------|
| **DeepSeek** | DeepSeek's models. |
| **Google Gemini** | Google's Gemini models. |
| **Mistral AI** | Mistral and Mixtral models. |
| **xAI (Grok)** | xAI's Grok models. |
| **Moonshot (Kimi)** | Moonshot's Kimi models. |
| **Z.AI (GLM)** | Z.AI's GLM models. |
| **Alibaba DashScope (Qwen)** | Alibaba's Qwen models. |
| **MiniMax** | MiniMax models. |
| **Cerebras** | Hosted open models on Cerebras hardware. |
| **Together AI** | Hosted open-source models (Llama, Qwen, etc.). |
| **Fireworks AI** | Hosted open-source models. |
| **DeepInfra** | Hosted open-source models. |
| **Novita AI** | Hosted open-source models. |

For gateway providers, EVA:
- Reads the model list from LiteLLM's dataset (no live provider API call needed for listing)
- Cannot validate API keys with a live test call at the point of saving
- Uses LiteLLM's capability data for feature flags and pricing — broader coverage, but lower fidelity than the hand-curated native registry

The distinction is visible in the provider selector: native providers show a **"native"** badge, gateway providers show **"gateway"**, and Ollama shows **"local"**.

### Services EVA doesn't support for chat

EVA only offers the providers listed above. Other AI services are left out for one of two reasons:

- **They need more than a single API key.** Some services also need an endpoint address, a region, a project or a service account, and EVA's key field can't carry those yet.
- **They haven't been verified to work with EVA's agent loop.** EVA relies on a model being able to call tools, and each provider has its own quirks there, so a provider is only added once it has been checked.

These services won't appear in the provider pickers. If an earlier version of EVA let you save a key for one of them, that key is kept but isn't used: the Configured Providers grid marks it with a warning, and if it was your selected provider it shows as unavailable until you choose a supported one. Nothing is wrong with your key, and no action is needed.

---

## What EVA knows about each model

When you select a provider and model, EVA fetches that model's capability profile and displays it directly in the interface. This isn't cosmetic — EVA uses this data internally to decide how to interact with the model.

EVA uses **LiteLLM's public benchmark dataset** as its primary capability reference. LiteLLM maintains a continuously updated database of 2,000+ models across all major providers, covering context windows, output limits, feature support, and per-token pricing. This means EVA stays current with new model releases and capability changes without requiring a software update — the data is refreshed automatically. For native providers (Groq, OpenAI, Anthropic), EVA overlays its own hand-curated entries on top of the LiteLLM data to capture provider-specific details that LiteLLM doesn't track, such as tool-call reliability and file upload support.

### Feature flags

In the **User Profile → Providers tab → Provider & Model section**, once you select a model, EVA fetches that model's full capability profile from its database and shows a row of capability badges directly below the model selector. Each badge shows ✓ (supported) or ✗ (not supported):

| Badge | What it means |
|-------|--------------|
| **Tool Calls** | The model can call tools (use EVA's built-in tools, MCP tools, etc.). If this is ✗, the model will respond in plain text only. |
| **Reasoning** | The model supports reasoning / chain-of-thought. When ✓, EVA also reads which specific levels the model accepts and whether it can be turned off — this drives the Reasoning control in Output & Safety. |
| **Vision** | The model can process images in messages and attachments. EVA sends images to the model as real images only when this shows ✓. Models EVA has no vision data for — which includes many Ollama and Ollama Cloud models — are treated as not supporting it, so images are replaced by a text placeholder. |
| **Streaming** | EVA can stream the response token-by-token as the model generates it. |
| **JSON Schema** | The model reliably produces output matching a JSON schema. |
| **File Upload** | The model accepts native file uploads (PDFs, documents). |

A ✗ badge doesn't mean the feature is blocked — it means the model doesn't support it and EVA won't attempt it.

### Reasoning levels

When a model's Reasoning badge is ✓, EVA fetches two additional fields alongside the badge:

- **`reasoning_levels`** — the ordered set of depths this model accepts, from the unified vocabulary: `Minimal / Low / Medium / High / Xhigh`. Not every model supports the full range. Anthropic's adaptive-thinking models (Sonnet 5, Opus 4.7/4.8) accept three levels (Low / Medium / High); EVA maps Minimal → Low and Xhigh → High automatically when building the request. OpenAI, Groq, and Ollama models are offered the full range optimistically — the provider's own API is the source of truth for what a specific model actually accepts.

- **`reasoning_can_disable`** — whether the model allows reasoning to be turned off. A handful of the newest Claude models (Fable 5, Mythos 5, Mythos Preview) run reasoning permanently on; sending a disable request to these models is rejected by Anthropic's API. For these models, EVA forces a minimum level and omits the Off option from the picker.

**Level snapping:** if you save a session with reasoning set to `Xhigh` and then switch to a model that only supports up to `High`, EVA snaps to the nearest supported level and adds a warning to the save response. The UI reflects the actual level that was saved, not what was requested.

### Token limits

Below the badges, EVA shows the model's token limits:

- **Context** — the total number of tokens the model can process in a single turn (your messages + the agent's working memory + tool results)
- **Output** — the maximum number of tokens the model can generate in a single response

For example: `200K context · 8K output`. These figures come from EVA's capability database and are the limits the provider documents.

### Pricing

The model selector shows per-model pricing inline, as `$X.XX/M` — the cost per million input tokens. This is drawn from LiteLLM's public pricing data and is kept reasonably current. Ollama always shows no cost.

### Data source

EVA's capability database is built from two sources:

**Registry** — hand-curated data for native providers (Groq, OpenAI, Anthropic). Highest accuracy. Feature flags, token limits, pricing, and tool-call reliability are all verified.

**LiteLLM** — auto-imported from LiteLLM's public dataset, which covers 2,000+ models. Broader coverage, but only has fields LiteLLM tracks: pricing, context window, and basic feature flags.

**Merged** — both sources contributed to this model's profile.

The source is shown in small text next to the token limit display (e.g., `via registry`, `via litellm`).

### Tool-call reliability

EVA tracks one additional field that isn't visible as a badge but influences agent behavior: **tool-call reliability**. Some models intermittently generate malformed tool calls — wrong argument types, hallucinated function names — that the provider's own API rejects. EVA flags these models as `unreliable`. You can still use them, but EVA is aware that tool-call failures from these models may be model errors rather than tool errors.

---

## Configuring providers

### Cloud provider API keys

Open the **User Profile** (bottom of the nav rail) → **Account** tab. The API Keys section lists every cloud provider EVA supports.

1. Select the provider from the dropdown.
2. Paste your API key into the key field (it shows as a password by default; toggle the eye icon to reveal).
3. Click **Save Key**.

Keys take effect immediately — no restart is required. The key is written to **Windows Credential Manager** and activated in the running EVA process instantly.

**Validation** happens automatically for Anthropic, OpenAI, and Groq. After saving, EVA makes a lightweight test call to the provider's API and shows one of three statuses:

| Status | Meaning |
|--------|---------|
| **Validated** | The provider accepted the key. |
| **Key rejected** | The key was saved, but the provider returned an authentication error. Check that the key is correct and has the required permissions. |
| **Saved** | The key was stored, but live validation was skipped. This applies to gateway providers (DeepSeek, Mistral, Gemini, etc.) where EVA doesn't yet have a validation path. The key is active — you'll know it's wrong when you first try to use the provider. |

Keys are **write-only**. There is no way to read a saved key back — not in the UI, not via any endpoint. Once saved, the only options are to overwrite it with a new key, remove it (see below), or confirm a provider is configured by the green checkmark in the Configured Providers grid. This is an intentional security design, consistent with industry standards for credential storage: a system that can return a secret in plaintext creates an additional attack surface. EVA stores keys in Windows Credential Manager precisely so they never need to travel over any interface again after the initial write.

#### Removing a key

To delete a stored key, open **User Profile → Account**, select the provider in the API Keys section and click **Remove key**. An inline confirmation explains what will happen:

- The key is deleted from this computer's credential store **for every EVA account on the machine**.
- It is **not** revoked at the provider. If a key may have leaked, revoke it in the provider's own dashboard too.

Afterwards the provider is no longer offered in the pickers. EVA also tells you which of *your own* settings still point at it — your main provider, fallback entries, subagents and daemons — and a reminder to choose a new provider if it was your main one. Nothing is changed for you: silently re-pointing your agents would be worse than a message you can see and act on. Removing a key that isn't stored is harmless.

#### Providers that stop being usable

A saved provider can go stale — a key is removed, or a model turns out not to be a chat model. EVA checks this at three points rather than letting a run fail halfway:

- **When you save.** Choosing a provider for your session, a fallback, a subagent or a daemon is checked immediately. An unknown or unsupported provider is refused with a clear message; a provider with no key stored for it asks you to add one (with an *Add a key* shortcut). Choosing a provider for an agent or daemon also requires choosing a model, because otherwise it would be paired with your session's model from a different provider. A fallback entry whose key is missing is simply dropped with a warning, so removing a key later never blocks unrelated saves.
- **When you send a message.** If your chosen provider or model can't be used and no fallback can, the message is refused immediately — nothing is queued — and the composer shows why, with a shortcut to the API Keys tab or the provider picker.
- **While a task runs.** If your main provider has become unusable but a fallback is usable, the fallback answers and a quiet notice says so. Your saved settings are not changed. If nothing is usable, the task fails at once with the reason instead of hanging. A subagent or daemon in the same position uses its own fallback or reports the reason.

EVA only blocks a model when it has positive evidence the model can't work (it isn't a chat model, or it can't call tools). A brand-new model EVA doesn't know yet is never blocked.

### Ollama — local vs cloud

EVA supports two distinct Ollama providers, and it's worth understanding the difference before configuring either.

**Ollama (Local)** runs entirely on your machine. You install the Ollama server (`ollama serve`), pull models locally (`ollama pull llama3.3`), and EVA connects to it over localhost. Your messages are never sent to an external AI service, there is no API cost, and inference speed depends on your hardware. This is the right choice if you have capable local hardware (a modern GPU helps significantly) and want the most privacy — the model itself runs entirely on your computer.

The practical constraint is hardware. Very large open-weight models — 70B+ parameter models, or the high-quality variants of Llama, Qwen, Mistral, and similar families — require significant VRAM to run at useful speeds. Most consumer machines can handle smaller models (7B–13B) comfortably.

**Ollama Cloud** is Ollama's hosted inference service. It gives you access to the same open-weight model ecosystem — Llama 3.3, Qwen, Mistral, DeepSeek, and many others — without needing the hardware to run them locally. Models that would demand a server-grade GPU run in the cloud and stream back to EVA like any other provider. Ollama Cloud uses an API key and is priced per token, but significantly more affordably than closed models from Anthropic or OpenAI — making it a practical option for power users who want open-weight model capability without local infrastructure.

#### Configuring Ollama (Local)

Ollama Local doesn't use an API key. Instead, EVA connects to the Ollama HTTP server process running on your machine.

In the **Account** tab, scroll past the Provider Keys section to **Local Models — Ollama**.

The current Ollama base URL is shown with a reachability indicator:
- **Green dot** — Ollama server responded. EVA can reach it.
- **Red dot** — Server not reachable. Check that Ollama is running (`ollama serve`).

The default URL is `http://localhost:11434`. If you're running Ollama on a different port or on another machine on your local network, update it here and click **Save URL**. The URL is saved to EVA's environment config and takes effect immediately.

The Ollama base URL is not a secret — it's a host address, not a credential. It can be read back and is visible in the settings panel.

#### Configuring Ollama Cloud

Ollama Cloud requires an API key from your Ollama account. Once you have it, it is configured the same way as any other cloud provider key — stored securely and takes effect immediately.

---

## Selecting a provider and model

Once a provider is configured, open the **User Profile → Providers tab**. The **Provider & Model** section has two side-by-side dropdowns.

**Provider** — lists the providers you can use right now: those EVA can chat with and that have a key stored (plus local Ollama). To add another, save its key in the Account tab. If your saved provider is no longer usable — for example you removed its key — it stays visible, flagged as unavailable, so you can see what changed.

**Model** — after selecting a provider, EVA fetches the live model list directly from the provider's API (or from LiteLLM's dataset for gateway providers). Models appear with their pricing shown inline. Select one.

Once a model is selected, the capability badges and token limits appear below the dropdowns.

**Refresh button** — the small refresh icon next to each dropdown forces a fresh fetch from the source, bypassing EVA's 5-minute model list cache. Use this if you've just added a new model to your Ollama library or if a provider has released new models since EVA last checked.

The selected provider and model become the defaults for all new conversations. You can also change the model mid-conversation without losing context.

---

## Fallback chain

The **Providers tab** also has a **Fallback Chain** section.

If the primary provider fails, EVA works through the fallback chain automatically. The response arrives from whatever provider succeeded — no interruption to the conversation.

Click **Add Fallback** to add a provider + model pair. Fallbacks are tried in the listed order. The chain supports up to **5 entries**; the Add Fallback button is disabled once that limit is reached.

Fallbacks are optional. With no fallback configured, a primary provider failure surfaces as an error in the conversation.

### How failure handling works

EVA distinguishes between two classes of failure:

**Tier A — transient failures** (rate limits, timeouts, provider outages): EVA retries the same provider with exponential backoff first, a configurable number of times, before falling over to the next entry in the chain. The retry counts and backoff bounds are tunable in `eva.env` — see [Configuration Reference](../15-configuration/01-configuration-reference.md#provider-fallback-chain).

**Tier B — structural failures** (invalid model name, content policy rejection, authentication error, context window exceeded): EVA moves to the next fallback immediately. There is no value in retrying the same provider for errors that won't resolve on their own — a context-window overflow in particular won't shrink on retry, but a different model in the chain may have more headroom to work with.

### Cooldown

After a provider exhausts its retry budget (Tier A only), it enters a **cooldown period** and is skipped automatically by all subsequent executions until the cooldown expires. The cooldown starts at 30 seconds and doubles with each consecutive failure, up to a maximum of 15 minutes. Once a request succeeds, the streak resets and the cooldown returns to the base interval.

Cooldown state is in-memory and resets when EVA restarts.

### Credential validation at save time

When you add or reorder a fallback entry, EVA validates that the selected provider has a key configured. Entries with missing credentials are dropped with a warning before saving. This prevents silent failures at execution time from entries that would never succeed.

### Live notices

When EVA falls back to a different provider mid-execution, two notices appear:

- **Live pill** — during streaming, a teal pill in the execution status bar shows `↔ Now using {model}` for the duration of that turn.
- **Persisted chip** — after the turn completes, a small `via {model}` chip appears in the message's token usage footer, so you can identify which provider answered when reviewing earlier messages.

---

## The Configured Providers grid

At the top of the **Providers tab**, a grid shows every supported provider at a glance. Each card shows:

- **Provider name** with its canonical identifier (e.g., `together_ai`)
- **Status**: green checkmark (configured and ready), key icon (needs API key), or `local` badge (Ollama — no key required)
- **Type tags**: `native`, `gateway`, or `no cost`

Providers are sorted: configured and ready ones appear first.

---

## Troubleshooting

**Provider shows "⚠ key needed" after saving** — the key was saved but the `is_configured` status refresh hasn't propagated. Click the small Refresh button next to the Provider dropdown in the Providers tab to reload the provider list.

**"Key rejected" after saving** — the key was stored successfully but the provider returned an authentication error. Common causes: the key was copied with leading/trailing spaces (they're trimmed automatically, but check for invisible characters), the key has been revoked, or the key doesn't have the required scope (some providers issue read-only or restricted keys).

**Ollama reachability shows red** — EVA can't reach the server at the configured URL. Confirm Ollama is running (`ollama serve`), then check the base URL matches exactly the host and port Ollama is listening on.

**Model list won't load** — if you've selected a native provider and the model list fails with a key error, go to Account → API Keys and re-save the key. If it fails with a connectivity error, the provider's API may be temporarily unreachable — the refresh button will retry.

**Capability badges all show "unknown"** — this model isn't in EVA's registry or LiteLLM's dataset. EVA will still use it — it just has no capability metadata. Tool calls and features work if the model supports them; EVA discovers this through normal use.

---

> **Next:** [Usage & Costs](../14-analytics/01-usage-and-costs.md) — tracking token usage and spend across providers.
