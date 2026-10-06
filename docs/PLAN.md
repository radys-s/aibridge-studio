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

## Phase 2 — reliability and context

- Port the AI Bridge memory kernel (selective context, source, age, conflicts,
  confirmation) with its tests; context packet for planner, worker and reviewer.
- Escalation: strong agent takes over implementation in the same working copy; the other
  agent reviews.
- Apply flow: accepted change → branch in the source repository on explicit approval.
- Router learns from outcomes (success rate per model and task kind).
- External implementer, manual (Freebuff): done 2026-10-06, see `docs/adr/0001-...`.
- Watch: headless Freebuff adapter in `agents/` once `--print` NDJSON or free SDK auth
  exists (github.com/CodebuffAI/freebuff/issues/947). Plugs in as an implementer, never via
  the router. Override for false positives of the pre-handoff sensitive scan.

## Phase 3 — scale and comfort

- Multiple workers in parallel (separate working copies, no shared files).
- Reusable workflows (task templates with acceptance criteria).
- Obsidian context source (read-only, explicit selection, no personal notes by default).
- Cost overview across runs; comparison against direct Claude/Codex work.
- Desktop control (tray/launcher, notifications).

## Explicitly not now

Cloud sync, mobile app, own model, marketplace.

## Measurement protocol

For the first tasks record, per task, for Studio and for direct Claude/Codex work:
time to accepted change, number of fix rounds, total consumption (tokens, USD from
OmniRoute, CLI calls), and Tomáš's supervision time (decisions + reading). No savings are
claimed until measured.
