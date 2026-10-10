# AI Bridge Studio — Phased Plan

Each phase ends only after: implementation → real verification → critical review → fixes →
commit → STATUS update → Obsidian note.

## Phase 1 — one complete verified loop (done 2026-10-06, see STATUS.md)

Goal: task → plan → worker via OmniRoute → change in working copy → tests → independent
review via Claude/Codex → accept or send back for rework. One worker, one reviewer.

1. Project skeleton, instructions, architecture, provenance. ✅
2. SQLite control plane: task/run/step/event/model_call/cooldown, state machine, recovery. ✅
3. Workspace: isolated clone, diff, scope check, reset to checkpoint. ✅
4. Sandbox: path/command/env policy (ported from Prizma), secret scan (from AI Bridge). ✅
5. Gateway adapter for OmniRoute + router (single owner of model choice and fallback). ✅
6. Worker tool loop with budgets. ✅
7. Independent verification. ✅
8. Claude / Codex adapters for plan and review. ✅ (Codex verified live; Claude blocked on
   CLI login, see STATUS.md)
9. Runner + FastAPI + recovery on startup. ✅
10. UI: task form, run list, run detail (state, plan, diff, evidence, review, model calls,
    decisions). ✅
11. Tests incl. failure paths: failing test, provider unavailable, timeout, cancel,
    restart/resume. Mocks only in tests. ✅
12. Live run on a synthetic repository. ✅
13. Live run on a copy of a real project without personal data. ✅
14. Measurement record (`docs/MEASUREMENTS.md`). ✅

## Phase 2a — Studio app (done 2026-10-06, ADR 0002)

Orchestration modes (cross / duo / solo), per-lead model and effort with heavy-effort warning,
lead-drafted spec with approval and chat, takeover by Claude/Codex with escalation policy,
DB v2 (messages, agent events, settings), SSE live view, rebuilt UI, `studio app` window and
shortcut. Open: Claude live runs (login), Codex isolation from global AGENTS.md (decision).

## Phase 2b — Autopilot (implemented 2026-10-07, ADR 0003)

Studio is the tool for after the Claude/Codex limits run out. The free-model orchestrator
is Autopilot, with a different-model review. Claude/Codex give an optional second opinion.
"Pokračovat projekt" works from Obsidian, memory write-back needs approval, and apply
creates a `studio/<run_id>` branch. Live run recorded in `MEASUREMENTS.md`.

## Phase 2c — private free Autopilot only (implemented 2026-10-08, ADR 0004)

No Claude/Codex routing at all. The router picks the strongest free model whose provider
does not train on inputs (Groq, Cloudflare), never below `privacy_floor`, and waits out
short cooldowns. A paid orchestrator later is configuration only. Memory proposals always
end with a card (model or fact-only fallback). Playwright E2E with a scripted gateway.
Live run on Groq accepted 2026-10-08.

## Phase 2d — more private providers (in progress, ADR 0005)

Providers from Prizma through OmniRoute, alias-based ids, per-provider privacy, key links and
a model probe in Settings, worker context trimming. GitHub Models removed (retired), Cerebras
skipped (needs a card). Next: live run with Cloudflare reviewing Groq's work once the Account
ID is set in OmniRoute, then router learning from outcomes.

## v0.3 — simple UI and router learning (implemented 2026-10-08, ADR 0006)

Apple-like UI with progressive disclosure and a six-stage run header; router demotes
unreliable models and favours accepted work for hard tasks; setup hint in Settings. Next:
live Groq run through the new UI; Cloudflare review run once the Account ID is set.

## v0.3.1 — hardening (completed 2026-10-09)

Bounded full-content vault scans, shared output withholding, complete review criterion
coverage and explicit independent-review provenance repair the v0.3 evidence boundaries.
Router reliability is role-specific; repeat reviews cannot inflate the accepted-work sample.
The UI preserves the simple design while guarding project-loading races and duplicate submit.
Accepted: 148 backend tests passed (two skipped), Ruff and mypy passed, frontend build
and 22 unit tests passed, 11 E2E tests passed, independent review approved, four
viewport/theme visual checks and a non-personal Groq fixture run passed. Cloudflare Account ID
remains a setup dependency. No new providers, OS sandbox or expanded second-brain scope.

## v0.4 — everyday MVP (in progress)

Review fixes on v0.3.1 (secret-scan precision, reviewer identity exclusion), "Spustit
OmniRoute" from the status line, "Přehled" of runs for the measurement protocol (done).

## Phase 2 — reliability and context

- Port the AI Bridge memory kernel (selective context, source, age, conflicts,
  confirmation) with its tests; context packet for planner, worker and reviewer.
- Escalation: strong agent takes over implementation in the same working copy; the other
  agent reviews. Done in Phase 2a.
- Apply flow: accepted change → branch in the source repository on explicit approval.
  Done in Phase 2b.
- Router learns from outcomes: done in v0.3 (ADR 0006).
- External implementer, manual (Freebuff): done 2026-10-06, see `docs/adr/0001-...`.
- Watch: headless Freebuff adapter in `agents/` once `--print` NDJSON or free SDK auth
  exists (github.com/CodebuffAI/freebuff/issues/947). Plugs in as an implementer, never via
  the router. Override for false positives of the pre-handoff sensitive scan.

## Phase 3 — scale and comfort

- Multiple workers in parallel (separate working copies, no shared files).
- Reusable workflows (task templates with acceptance criteria).
- Obsidian context source: done in Phase 2b (project note + handoffs, policy in `vault.py`).
- Cost overview across runs; comparison against direct Claude/Codex work.
- Desktop control (tray/launcher, notifications).

## Explicitly not now

Cloud sync, mobile app, own model, marketplace.

## Measurement protocol

For the first tasks record, per task, for Studio and for direct Claude/Codex work:
time to accepted change, number of fix rounds, total consumption (tokens, USD from
OmniRoute, CLI calls), and Tomáš's supervision time (decisions + reading). No savings are
claimed until measured.
