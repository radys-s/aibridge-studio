# AI Bridge Studio

Local workspace for AI-assisted app development. You submit a task with a goal, allowed
paths and verifiable acceptance criteria; Studio plans it with Claude Code or Codex, lets a
cheap/free model implement it through OmniRoute in an isolated copy of the repo, verifies it
independently and has the other lead review the diff. You see progress, diff, tests and the
review, and decide.

- Architecture: `docs/ARCHITECTURE.md`
- Plan: `docs/PLAN.md` · Status: `docs/STATUS.md` · Measurements: `docs/MEASUREMENTS.md`
- Ported code and licences: `docs/PROVENANCE.md`

## Quick start

```powershell
uv sync
cd frontend; npm install; npm run build; cd ..
omniroute serve          # separate terminal; OmniRoute on 127.0.0.1:20128
uv run studio serve      # http://127.0.0.1:8765
```

Requires Python 3.13, uv, Node 22+, OmniRoute with at least one provider connection, and
logged-in `claude` and/or `codex` CLIs for planning and review.
