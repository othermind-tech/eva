# Technology Stack

EVA is a desktop application that runs entirely on your own machine. This page lists what it's built from — useful if you're evaluating EVA, wondering why it works offline, or deciding whether it fits your environment. It names technologies rather than version numbers, because the exact versions change with each release; the authoritative lists are the dependency files that ship with the source (`requirements.txt` for the backend, `package.json` for the interface and desktop shell).

For what these choices mean for your data, see [Privacy & Security](02-privacy-and-security.md).

---

## In one glance

| Part | Technology |
|------|-----------|
| Desktop shell | Electron |
| Core engine (backend) | Python, FastAPI |
| Interface (frontend) | React, TypeScript |
| Database | SQLite |
| Vector search | ChromaDB |
| Embeddings | ONNX Runtime (local model) |
| AI providers | Anthropic, OpenAI, Groq, Ollama, and more via LiteLLM — 18 in all |
| Tool protocol | Model Context Protocol (MCP) |
| Credentials | Windows Credential Manager (your operating system's secure credential store) |

Everything above runs in a single local process tree. EVA needs no Docker, no Redis, no message queue and no cloud service of its own.

---

## Desktop shell

| Technology | What it does for you |
|------------|---------------------|
| **Electron** | Packages EVA as a normal Windows application with a window, tray icon and installer. |
| **electron-builder** | Builds the installer. |
| **electron-updater** | Checks for new releases and updates the app. |
| **Electron web views and a CDP relay** | Power the built-in [Preview pane](../02-conversations/07-activity-and-preview.md), so the agent's browser runs inside EVA. |

The backend is bundled into the installer as a standalone program (built with PyInstaller), so you don't need Python installed to run EVA.

## Core engine

| Technology | What it does for you |
|------------|---------------------|
| **Python** (3.11) | The language the whole engine — agents, planning, tools, memory — is written in. |
| **FastAPI** and **Uvicorn** | The local web server the interface talks to. By default it listens on your machine only. |
| **Pydantic** | Validates everything that goes in and out, including tool arguments. |
| **asyncio** | Runs many things at once — several conversations, background subagents, daemons — in one process. EVA's task queue is built in, not an external service. |
| **httpx** | Calls AI providers and MCP servers. |
| **tiktoken** | Counts tokens, so EVA can manage context budgets. |

## Interface

| Technology | What it does for you |
|------------|---------------------|
| **React** and **TypeScript** | The interface itself. |
| **Vite** | Builds it. |
| **Tailwind CSS** and **Radix UI** | Styling and accessible building blocks such as dialogs, menus and tabs. |
| **Zustand** and **TanStack Query** | Keep the interface in step with the engine — live status, data caching. |
| **Framer Motion** | Animation. |
| **react-markdown** with syntax highlighting | Renders EVA's replies and this documentation. |

## Data and search

| Technology | What it does for you |
|------------|---------------------|
| **SQLite** (via SQLAlchemy and Alembic) | Stores your conversations, memory, projects and settings in one local file. No database server to install. Alembic upgrades its structure safely between releases. |
| **PostgreSQL** *(optional)* | Supported for multi-user or server deployments in place of SQLite; not used on a normal desktop install. |
| **ChromaDB** | The local vector database behind the [Knowledge Base](../07-rag/01-knowledge-base.md), embedded in EVA. |
| **ONNX Runtime** with a small bundled embedding model (all-MiniLM-L6-v2) | Turns text into searchable form on your CPU. This is why the Knowledge Base works offline. |
| **pypdf**, **docx2txt**, **pandas** | Read PDFs, Word documents and spreadsheets so they can be indexed. |

## AI providers and tools

| Technology | What it does for you |
|------------|---------------------|
| **Anthropic, OpenAI, Groq and Ollama SDKs** | Talk to each provider natively. See [Providers & Models](../13-providers/01-configuring-providers.md). |
| **LiteLLM** | Broadens provider coverage and supplies model capability and pricing data. Its version is pinned, so a provider's behaviour doesn't shift under you between EVA releases. |
| **MCP SDK** | Connects [MCP servers](../12-mcp/01-what-are-mcp-servers.md) — local processes and remote services — to EVA's agents. |
| **Node.js** *(only if you use it)* | Runs MCP servers distributed as npm packages, including the default Playwright browser server. Not needed otherwise. |
| **Playwright** | The browser automation behind the default browser MCP, driving Microsoft Edge (Windows) or Chrome. |
| **DuckDuckGo search** | The web search tool — no account or API key. |

## Security and privacy

| Technology | What it does for you |
|------------|---------------------|
| **OS credential store** (via `keyring`) | Holds your provider API keys, MCP credentials and EVA's token-signing key — Windows Credential Manager on the supported Windows platform. |
| **JWT** (python-jose) and **bcrypt** | Sign you in to your own local server; passwords are stored only as bcrypt hashes. |
| **Hash-locked dependencies** | The backend's Python packages are installed from a lock file that pins each package to an exact version and checksum, so what's built is exactly what was tested. |
| **cryptography** | Provides the encryption used for the shared scratchpad agents coordinate through, when you turn that encryption on. |
| **Presidio** and **spaCy** | Find personal information such as emails and card numbers for [PII redaction](02-privacy-and-security.md#redacting-personal-information), entirely on your machine. |

## Where the parts run

- **Your machine:** the interface, the engine, the database, the vector index, embeddings, PII detection, credential storage, and any local Ollama model.
- **Only when you choose:** an AI provider (receives the messages you send through EVA), a remote MCP server you connect, web search and web fetch when EVA uses those tools.
- **EVA's own background requests:** an update check against EVA's releases on GitHub, and a download of LiteLLM's public model price and capability file. Neither carries your data. See [Network activity](02-privacy-and-security.md#network-activity-what-eva-contacts-on-its-own).
- **Nowhere:** EVA has no server of its own that receives your data, and no telemetry or analytics.

---

> **Next:** [Open Source](04-open-source.md) — the licence, how to contribute, and what support to expect.
