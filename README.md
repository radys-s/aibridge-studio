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

## Works with Refrakt

[Refrakt](https://github.com/radys-s/refrakt) is the layer below Studio: a chat app that holds
your provider keys and routes across free LLM APIs. Studio can use those keys through
Refrakt's OpenAI-compatible gateway, so they are stored once and Studio never holds one.

```powershell
$env:REFRAKT_GATEWAY_TOKEN = "<Refrakt's GATEWAY_TOKEN>"   # turns it on
$env:STUDIO_REFRAKT_URL = "http://127.0.0.1:8000"         # default
```

Models reached this way are listed as `refrakt/<provider>/<model>` and follow the same
privacy rules as the provider behind them. With OmniRoute and Refrakt both running, OmniRoute
goes first; with only Refrakt running, Studio uses the models Refrakt serves. Details:
`docs/adr/0007-refrakt-as-second-gateway.md`.
