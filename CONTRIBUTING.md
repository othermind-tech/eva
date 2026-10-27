# Contributing to EVA

Thanks for helping improve EVA. Bug reports, fixes, documentation, and new features are all
welcome. This page explains how to get set up and what to expect.

## Before you start

- **Search first.** Check the existing issues and pull requests so you don't duplicate work.
- **For larger changes, open an issue first.** A short description of what you want to change and why
  saves everyone time before you write the code.
- **Security problems** go through the private process in [`SECURITY.md`](SECURITY.md), not a public
  issue.

## Contributor License Agreement (CLA)

Every contributor must agree to the [Contributor License Agreement](CLA.md) before a pull request
can be merged.

- **The maintainer owns the copyright** in EVA, including your contributions. The CLA assigns your
  copyright to the maintainer (and, where the law doesn't allow that, grants the broadest license it
  does allow). In return you get a limited license back, so you can reuse your contribution, as you
  submitted it, in your own projects (this doesn't include the EVA name or logo). The maintainer may use contributions commercially and may change the license of future
  versions, without any payment or royalty to you. If you're not comfortable with this, please don't
  submit a contribution.
- Versions of EVA already released under the [Apache License 2.0](LICENSE) stay available under it.
- When you open your first pull request, the CLA bot comments with instructions; reply with the
  sentence it gives you. You sign once and it applies to your future pull requests.
- If you contribute on behalf of an employer, make sure you have permission, or contact the
  maintainer first.
- If you used AI tools to write part of your change, you are still responsible for it: review it,
  and make sure you are entitled to submit it under the CLA.

## Development setup

Prerequisites: Python 3.11+, Node.js 22+, npm 10+, Git. See the [`README`](README.md) for the full
quick start. In short:

```bash
# Backend
cd backend
python -m venv .venv && .venv\Scripts\activate      # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend (in a second terminal)
cd frontend
npm install
npm run dev
```

## Running the checks

```bash
# Backend tests
cd backend
pytest

# Frontend type-check, lint, and build
cd frontend
npm run build
npm run lint
```

Please make sure these pass before you open a pull request, and add or update tests for the
behaviour you changed.

## Making a pull request

1. Fork the repository and create a branch from `main` (for example `fix/rag-pdf-encoding`).
2. Keep the change focused. One concern per pull request is easier to review.
3. Write clear commit messages. The project uses a conventional style: `feat(scope): ...`,
   `fix(scope): ...`, `docs: ...`, `chore: ...`.
4. Update documentation when behaviour changes: user docs live in `docs/`, API and architecture
   details in `backend/README.md` and `frontend/README.md`. A user-visible change should also get a
   line in `docs/CHANGELOG.md`.
5. Describe what changed and why in the pull request, and how you tested it.

## Code style

- **Python:** follow the style of the surrounding code; type hints and docstrings where the
  neighbouring code has them.
- **TypeScript/React:** the project's ESLint and TypeScript settings apply (`tsc -b` must pass).
- Match the existing naming and comment density rather than introducing a new style.

## What happens next

A maintainer reviews pull requests when time allows; there is no guaranteed response time, and no
promise that a contribution will be merged or released. We may ask for changes, or decline a change
that doesn't fit the project's direction. Declining is not a judgement of your work.

EVA is provided as-is. There is no support or service-level agreement, and no commitment to publish
future versions.

## License

By contributing, you agree to the terms of the [CLA](CLA.md), including the assignment of copyright,
and that the project as a whole is distributed under the [Apache License 2.0](LICENSE).
