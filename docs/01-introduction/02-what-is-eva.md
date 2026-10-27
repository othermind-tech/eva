# What is EVA?

**EVA** is an AI agent — extensible with external tools and libraries, capable of performing multiple tasks, catering to multiple use cases, and designed for a wide variety of users.

EVA is a provider-agnostic AI agent with a desktop-first design. It works with virtually any AI model — from the latest Claude and GPT to free locally-running models — and keeps your data in your own workspace rather than on someone else's server.

The interface EVA presents to you is intentionally familiar: a conversation window, just like any chat app. Under the hood, however, there is an intelligence engine making decisions that go far beyond generating a reply.

---

## What makes EVA different from a chat app?

Most AI products work with a single provider. You use ChatGPT with OpenAI, Claude with Anthropic, Gemini with Google. The product and the model are one package. EVA is built on a different principle.

### 1. Provider-agnostic — freedom to use any model

EVA is not tied to any single AI company. It works with 18 providers — Anthropic, OpenAI, Groq, Google Gemini, DeepSeek, Mistral AI, xAI, Moonshot (Kimi), Z.AI (GLM), Alibaba Qwen, MiniMax, Cerebras, Together AI, Fireworks AI, DeepInfra, Novita AI, Ollama, and Ollama Cloud — and presents a consistent experience regardless of which one you choose.

You can switch providers at any time to find the best balance of cost, speed, and capability for your task. Want to run entirely offline at zero cost? Point EVA at a local Ollama model. Need maximum reasoning for a complex task? Switch to Claude or GPT-4o. The conversation, your memory, your projects, and your tools work identically across all of them.

### 2. Automatic fallback — no more provider failures

Providers go down. Rate limits hit at the worst moments. With most AI tools, that means a failed task and a frustrated user.

EVA lets you configure a **fallback chain** — an ordered list of backup providers. If your primary provider fails for any reason (rate limit, outage, timeout), EVA automatically switches to the next one and continues the response without interruption. You may never even notice it happened.

> *Example: Primary is Anthropic. Fallback chain: OpenAI → Groq → Ollama. Anthropic hits a rate limit during your morning daemon run. EVA switches to OpenAI, completes the task, and logs which provider was used. No error, no retry, no manual intervention.*

### 3. An intelligence engine, not just a chat box

EVA's interface is deliberately designed to look like a familiar chat app — because most people, technical or not, are comfortable with that pattern. But the engine underneath is doing much more.

**When you send a message, EVA's execution engine:**
- Detects the intent: is this a quick question, or a complex multi-step task?
- For simple requests: responds conversationally, calling tools as needed
- For complex tasks: generates a structured plan, executes each step with tools, and verifies the result before moving on
- Decides which tools to use, in what order, and whether any action needs your approval first
- Decides whether to store anything useful in long-term memory

You see a streaming response. Behind it is an orchestrator reasoning, acting, checking its own work, and adapting — not a text predictor guessing the next word.

### 4. Background agents that work while you don't

Daemons are autonomous agents that run on a schedule, triggered by internal events, or activated by an external system calling a webhook. You configure them once and they run indefinitely in the background.

Time-based daemons fire on a schedule — every morning, every hour, every week. Event-based daemons react to what EVA is doing — for instance, running a summary agent every time a long task completes. Webhook-based daemons connect EVA to the outside world — a CI system, a monitoring tool, any service that can make an HTTP call.

> *Example: "Every weekday at 7:45am, read my open task list and draft a standup summary." EVA runs this every morning without you touching the keyboard. The summary is ready when you open the app.*

### 5. Your data stays in your workspace

Your conversations, memory, projects, uploaded documents, agent configurations, and hook scripts all live in a local workspace folder on your machine. You own it. You can inspect it, back it up, or move it. Nothing is locked away in a cloud account you don't control.

### 6. Extensible by design

EVA is built to be extended, not just configured. You can:
- Write **hook scripts** that run before and after tools or executions (shell or HTTP)
- Use or create **skills** — install ready-made skills from libraries (including Anthropic's published skill catalog) or build your own named, reusable workflow bundles
- Build **custom subagents** with their own model, system prompt, and tool permissions
- Connect **MCP servers** to give EVA access to any external tool via an open protocol

---

## Who is EVA for?

EVA is for everyone.

If you are a **non-technical user** who has always felt locked out of AI tools because of their complexity — EVA is for you. There are no terminals to open, no configuration files to edit, no technical knowledge required to get started. You open the app, describe what you need, and EVA gets it done. The interface is a conversation window. The work happens underneath it.

If you are a **power user** already comfortable with tools like Claude Code, advanced APIs, or complex automation pipelines — EVA is equally for you. You will appreciate the fine-grained control: switching providers and models mid-session, monitoring execution traces and costs, wiring up hook scripts and custom agents, building skills, and integrating external tools via MCP. EVA gives you levers that other products simply do not expose.

EVA is designed so that both can sit in the same application. If you are the non-technical user, you never see the complexity. If you are the power user, you are never constrained by its absence.

---

## How EVA looks from the outside

When you open EVA's desktop app, you see a conversation interface — familiar and immediate. You type, EVA responds. But behind every message, the execution engine is at work:

- Is this a quick question or a complex task that needs a plan?
- Which tools should be invoked, and in what order?
- Which provider and model best suits this request?
- Does any action need your approval before it runs?
- Is there anything worth saving to memory for next time?

You receive a streaming response, watching EVA's reasoning and tool use unfold in real time. The simplicity of the interface is intentional — EVA's sophistication lives in the engine, not the chrome.

---

> **Next:** [Key Concepts Glossary](03-key-concepts-glossary.md) — plain-English definitions for every term EVA uses.
