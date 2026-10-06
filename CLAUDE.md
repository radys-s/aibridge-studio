# AI Bridge Studio — instructions for coding agents

Read first: `docs/ARCHITECTURE.md`, `docs/PLAN.md`, `docs/STATUS.md`.
Shared memory with Tomáš and the other agent: Obsidian vault
`C:\Users\tomas\Documents\Obsidian Vault\AI paměť` (note `02 Projekty/AI Bridge Studio.md`,
handoffs in `08 Předávání`).

## Hard rules

- `routing.ModelRouter` is the only owner of model choice and fallback. The gateway never
  retries or substitutes; nothing sends `auto/*` combos to OmniRoute.
- OmniRoute is an external dependency behind `gateway.py`. Do not vendor its code.
- The worker only touches the run's working copy, only inside the task scope, only via
  allowlisted commands with a scrubbed environment. A git clone is not a sandbox.
- Never send secrets, personal documents or Recorder data to models.
- Independent verification and review are separate from the worker's claims. Never mark a run
  accepted without a real review result. No fake "success" for missing live integrations.
- Push, merge, publish, deploy and destructive actions need Tomáš's explicit approval.
- Source projects `../aibridge` and `../multiagent` are read-only references. Ported code
  must be listed in `docs/PROVENANCE.md` with commit and changes.
- Claude Code and Codex are peers: one owns a task, the other reviews. Never edit the same
  files at the same time.

## Commands

```powershell
uv sync
uv run pytest -q                 # unit + control-plane tests (no live services)
$env:STUDIO_LIVE='1'; uv run pytest -q -m live   # opt-in live OmniRoute check
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
cd frontend; npm install; npm run build; npm test
uv run studio serve              # http://127.0.0.1:8765
```

## Conventions

- Docs and plans in English; UI text and commit messages in Czech.
- Python 3.13, strict mypy, ruff line length 100.
- Every phase: implement → verify for real → critical review → fix → commit → update
  `docs/STATUS.md` and the Obsidian note.
