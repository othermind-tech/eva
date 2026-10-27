# EVA — Local-first AI Agent Harness

EVA is an open-source AI agent harness and desktop app that runs on your own machine. Bring your own model (Anthropic, OpenAI, Gemini, DeepSeek, Ollama and more) and EVA runs it through Plan-Execute-Verify orchestration, with long-term memory, RAG, subagents, background daemons, skills, MCP servers and human-in-the-loop approvals. Your conversations, memory and files stay in a local database, and no cloud infrastructure is required.

> **Status:** EVA is pre-1.0 and under active development (current version in [`docs/CHANGELOG.md`](docs/CHANGELOG.md)). Expect rough edges. It is developed and tested primarily on Windows; the backend and frontend are plain Python and Node.js, and the packaged installer currently targets Windows.

> **Privacy note:** EVA stores your data locally, but when you use a hosted model, your prompts are sent to that provider. Use a local model through Ollama to keep inference on your machine too. See [Privacy and security](docs/16-appendix/02-privacy-and-security.md).

---

## What EVA does

EVA is not a chat wrapper. It is an agent execution environment designed for people who want control, transparency, and extensibility.

| Capability | Description |
|---|---|
| Multi-provider AI | Anthropic, OpenAI, Gemini, DeepSeek, Mistral, Moonshot (Kimi), Z.AI (GLM), Groq, Ollama, Ollama Cloud, and any provider LiteLLM supports — one interface with fallback chains |
| ReAct tool loop | The model reasons and acts through a structured tool-call cycle with full traceability |
| Plan-Execute-Verify (PEV) | Three-phase orchestration: plan decomposition → execution → outcome verification, with an honest final verdict (success, pending, or not successful) |
| Long-term memory | Extracts and recalls facts, preferences, and past context across conversations |
| RAG (Knowledge Base) | Upload documents; EVA retrieves relevant context at inference time |
| Subagents | Spawn specialised agents to delegate subtasks during an execution |
| Daemons | Persistent background agents that run on a schedule or respond to triggers |
| Skills | Reusable instruction sets that shape how EVA approaches specific kinds of task |
| MCP servers | Connect Model Context Protocol servers for additional tools and resources |
| Hook scripts | Pre/post tool hooks for logging, redaction, approval gates, or custom logic |
| Projects | Workspace isolation — conversations, files, agents, and memory scoped to a project |
| Human-in-the-loop | Approval gates that pause execution and require your confirmation before a tool runs |
| PII redaction (optional) | Detects and replaces personal data in your messages locally, before they are stored or sent to a model |
| Analytics | Token usage, cost tracking, and execution metrics per provider and model |

---

## Download

Windows installers are published on the [Releases page](https://github.com/othermind-tech/eva/releases). Each release lists a SHA-256 checksum and carries a build provenance attestation (verify with `gh attestation verify EVA-Setup.exe --repo othermind-tech/eva`).

> **Windows SmartScreen:** installers are not yet code-signed, so Windows may show "Windows protected your PC". Check the checksum, then choose **More info → Run anyway**. Signing is planned.

---

## Repository structure

```
.
├── backend/    FastAPI execution engine (Python)
├── frontend/   React desktop UI (Vite + TypeScript)
├── build/      Electron shell, installer config, and build scripts
├── branding/   Logo and icon artwork (`build/scripts/make_icons.py` regenerates the app icons from it)
├── tests/      Tests for the Electron shell (the backend's tests live in backend/tests/)
└── docs/       User documentation (16 sections), also shown inside the app
```

The main directories each have their own README with details:

| Directory | README |
|---|---|
| `backend/` | [`backend/README.md`](backend/README.md) — API reference, architecture, configuration |
| `frontend/` | [`frontend/README.md`](frontend/README.md) — component map, state management, auth and SSE flow |
| `build/` | [`build/README.md`](build/README.md) — Electron main process, packaging, release pipeline |
| `docs/` | [`docs/README.md`](docs/README.md) — documentation index |

---

## Architecture overview

```
┌──────────────────────────────────────────────────────┐
│                 Electron shell (build/)               │
│   main.js: spawns backend binary + serves frontend   │
└────────────┬─────────────────────────┬───────────────┘
             │ http://127.0.0.1:8000   │ http://127.0.0.1:5174
             ▼                         ▼
┌────────────────────┐    ┌──────────────────────────┐
│  FastAPI backend   │◄───│   React frontend (SPA)   │
│  (backend/)        │    │   (frontend/)            │
│                    │    │                          │
│  • REST API        │    │  • Zustand state         │
│  • SSE streams     │    │  • TanStack Query v5     │
│  • ReAct loop      │    │  • Axios + JWT           │
│  • PEV orchestrator│    │  • Native EventSource    │
│  • Local broker    │    │  • Framer Motion UI      │
│  • SQLite          │    └──────────────────────────┘
│  • ChromaDB (RAG)  │
│  • OS keyring      │
└────────────────────┘
```

Key design decisions:

- **No Redis, no separate worker process.** Execution runs inside a single FastAPI async process through an internal `LocalBroker`.
- **SQLite by default.** Zero-config local database; the path is configurable with the `DATABASE_URL` environment variable.
- **Write-only secrets.** Provider API keys are stored in the OS keyring (Windows Credential Manager, macOS Keychain), never in the database, and no API endpoint returns them.
- **Docs as data.** `docs/` is the canonical documentation source. The frontend build copies it into the app so the in-app help viewer works offline. Navigation comes from `docs/manifest.json`: adding a page means adding the Markdown file and one manifest entry, with no frontend code change.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | Backend runtime |
| Node.js | 22+ | Frontend development and Electron build |
| npm | 10+ | Package management |
| Git | any | |
| uv | recent | Only to build the installer (`winget install astral-sh.uv`); it creates the Python build environment for you |

---

## Quick start (development)

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

On first run EVA creates `eva.db` and seeds the default agents, skills, and tools.

**Configuration:** copy `eva.env.example` to `eva.env` and fill in what you need. Provider API keys can also be set from the UI (**Settings → Providers**); they are written to the OS keyring, not to `eva.env`.

> Full configuration reference: [`docs/15-configuration/01-configuration-reference.md`](docs/15-configuration/01-configuration-reference.md)

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server runs at `http://localhost:5173`. Requests to `/api` are proxied to `http://127.0.0.1:8000` (the prefix is stripped). In-app docs are served from `../docs/` at `/docs/`.

### First run

EVA ships with no default account. Open the app and create one on the sign-up screen (username, email, password). Accounts are stored in your local database. A development instance is meant for your own machine, so do not expose it to a network you do not trust.

### Tests

```bash
cd backend
pytest
```

Most test files are fast, self-contained unit tests. A few scripts in `backend/tests/` run against a live server; `backend/tests/conftest.py` lists which ones are excluded from a normal `pytest` run.

---

## Building the desktop app

The PowerShell scripts in `build/scripts/` handle dependency installation, type-checking, and packaging in the right order:

```powershell
cd build\scripts
.\build-all.ps1                  # full build -> installer\EVA-Setup.exe
.\build-all.ps1 -SkipBackend     # frontend + Electron only (backend unchanged)
```

| Script | What it does |
|---|---|
| `build-frontend.ps1` | `tsc -b && vite build` → `frontend/dist/` |
| `build-backend.ps1` | Builds a disposable Python 3.11 environment from the hash-locked `backend/requirements.lock` and `backend/requirements-build.lock`, then PyInstaller → `backend/backend-dist/eva-backend/` |
| `build-electron.ps1` | `npm ci` when the lock changed, then `electron-builder` → `installer/EVA-Setup.exe` |

`electron-builder` bundles the backend binary and the frontend into one Windows installer; no Python or Node.js is needed on the end user's machine.

The backend is built from the lock files, so no pre-existing Python or conda environment is needed on the build machine, only `uv`. Renderer obfuscation is optional and off by default (`-Obfuscate`).

**Port overrides:** set `EVA_BACKEND_PORT` / `EVA_FRONTEND_PORT` before launching the dev servers or the packaged app to use ports other than 8000 / 5174.

More detail, including the release pipeline, is in [`build/README.md`](build/README.md).

---

## Security

- `eva.env` and `*.db` are gitignored. Never commit them.
- Provider API keys live in the OS keyring only. No API endpoint returns a key.
- Backend endpoints require a JWT, with three exceptions: `/health` and `/auth/login`, tokenised daemon webhook URLs (`/webhooks/{token}`), and a localhost-only internal shutdown endpoint used by the desktop app.
- Full security model: [`docs/16-appendix/02-privacy-and-security.md`](docs/16-appendix/02-privacy-and-security.md)

---

## Documentation

| Resource | Location |
|---|---|
| User documentation (16 sections) | [`docs/`](docs/) |
| Backend API reference | [`backend/README.md`](backend/README.md) |
| Frontend architecture | [`frontend/README.md`](frontend/README.md) |
| Build and release | [`build/README.md`](build/README.md) |
| Release notes | [`docs/CHANGELOG.md`](docs/CHANGELOG.md) |

---

## Contributing

Issues and pull requests are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request. Contributors sign a [Contributor License Agreement](CLA.md) that assigns copyright in their contributions to the maintainer. EVA is provided as-is, with no support or service-level agreement. Report security problems privately as described in [`SECURITY.md`](SECURITY.md).

## License

EVA is released under the Apache License 2.0. See [`LICENSE`](LICENSE).
