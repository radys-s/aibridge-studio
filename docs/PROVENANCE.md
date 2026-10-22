# Provenance of reused code and external dependencies

All source projects are by the same author (Tomáš Radek); nothing is copied verbatim
without adaptation. Source repositories are untouched.

| Studio file | Source | Source commit | What was taken | What changed |
|---|---|---|---|---|
| `backend/src/studio/sandbox.py` | Prizma `backend/app/tools/sandbox.py` | `49022f7` | Path resolution before root check, denylists, first-token command allowlist, no shell, operator rejection, output clipping | Single root = run working copy; per-task scope globs; read-only git subcommands; `.cmd/.bat` refused; minimal subprocess environment; English messages; no dependency on Prizma settings store |
| `backend/src/studio/sandbox.py` (`SECRET_PATTERNS`) | AI Bridge `backend/src/aibridge/adapters/security.py` | `953e0b9` | Secret regexes | Used to block file content from reaching models, not only memory writes |
| `backend/src/studio/routing.py` | Prizma `backend/app/routing/selector.py` | `49022f7` | Capability-first ordering, strength reorder for hard tasks, cooled-down candidates last | Cooldowns persisted in SQLite; explicit OmniRoute model ids; budget gate; per-turn exclusion |
| `backend/src/studio/agents/leads.py` | AI Bridge `backend/src/aibridge/agents/claude.py`, `codex.py` | `953e0b9` | `claude -p --output-format json --json-schema`, `codex exec --output-schema --output-last-message`, stdin prompt | Read-only tools / sandbox, Studio prompts and strict schemas, killable subprocess (cancel + timeout). Removed 2026-10-08 (ADR 0004), **restored 2026-10-22 (ADR 0007)** from `855d7ec^` as optional, per-role leads: adds `LeadConfig` (model, effort), `cli_version`, and a cross-platform fake-CLI test shim. |

Not ported in Phase 1 (planned): AI Bridge memory kernel (SQLite schema v2, FTS5, curator,
evidence verifier) — Phase 2, with its tests.

Not ported deliberately: Prizma provider registry/keystore/litellm client (provider access is
OmniRoute's job), Prizma auth/PWA/chat UI, AI Bridge FastAPI routes (fake-async `/v1/runs`).

## External dependencies

| Dependency | License | How used |
|---|---|---|
| OmniRoute (`npm i -g omniroute`, upstream `github.com/diegosouzapw/OmniRoute`) | MIT | Separate process; HTTP only via `gateway.py`. No code copied. Note: `github.com/dbegineer/omniroute` named in the brief is a stale fork (last push 2026-07-23); the installed package and docs match the diegosouzapw upstream. |
| FastAPI, uvicorn, httpx, pydantic | MIT / BSD-3 | Python dependencies via `uv` |
| React, Vite, TypeScript | MIT / Apache-2.0 | UI build |
