# AI Bridge Studio — Architecture

Status: Phase 1 (2026-10-06). Living document; decisions with lasting impact go to `docs/adr/`.

## Product goal

Tomáš submits a task and sees its progress, the diff, test evidence and an independent
review. Claude Code and Codex act as **leads and independent reviewers**. Bounded
implementation is done by cheaper / free / local models through a **worker** that talks to
providers via **OmniRoute**. A strong agent may take over implementation when the worker
fails or the task is too hard.

Success metric: less supervision by Tomáš and faster acceptance of a good change. Not the
number of models, agents or features.

## Shape: modular monolith

One Python 3.13 process (FastAPI + a single background runner thread) and one React/Vite
UI served by it. One SQLite database (WAL) owned by Studio. No microservices; module
boundaries are Python packages with narrow interfaces.

```
 UI (React/Vite)  ──HTTP poll──►  api/  (FastAPI, 127.0.0.1, Origin + custom-header CSRF guard)
                                    │
                                    ▼
                              control/runner.py      ← control plane: tasks, runs, steps, budgets,
                                    │                   interruption, restart recovery
        ┌──────────────┬────────────┼──────────────┬──────────────────┐
        ▼              ▼            ▼              ▼                  ▼
   agents/planner  worker/loop   verify.py     agents/reviewer   workspace.py
   (Claude/Codex   (tool loop)   (independent  (Claude/Codex      (isolated git clone,
    CLI, read-only)    │          acceptance    CLI, read-only)    diff, scope check)
                       ▼          commands)
                 routing.py  ── single owner of model choice + fallback (cooldowns in DB)
                       │
                       ▼
                 gateway.py  ── OmniRoute adapter: one HTTP call, one explicit model,
                       │        never retries, never substitutes; records actual model/provider
                       ▼
                 OmniRoute (separate install, 127.0.0.1:20128) ──► providers
```

### Responsibilities

1. **Control plane** (`control/`, `db.py`, `domain.py`): tasks, runs, steps, state machine,
   budgets, cancellation, restart recovery. All state lives in SQLite; the runner thread is
   stateless between steps.
2. **Memory** (Phase 2): selective context with source, age, conflicts and confirmation.
   Planned port of the AI Bridge memory kernel (`aibridge` repo, schema v2, FTS5, curator).
   Not in Phase 1 — the first loop gets context only from the task and the repository.
3. **Agent runtime** (`worker/`, `sandbox.py`, `workspace.py`): tools, isolation, working
   copy, limits, artifacts.
   **External implementer** (`implementer=external`, ADR 0001): the working copy is handed
   to Freebuff, which Tomáš runs by hand. Studio scans the copy for sensitive files first,
   writes the brief outside the copy and waits in `awaiting_external` (not an active state,
   so restart leaves it). Its output is a claim; verification and review are unchanged.
4. **Routing** (`routing.py`, derived from Prizma `routing/selector.py`): choose a model by
   capability, availability (cooldowns) and budget.
5. **OmniRoute** (`gateway.py`): gateway to providers. Integrated as an external dependency
   behind an adapter. Studio does not copy OmniRoute code and does not implement its own
   provider gateway.
6. **Claude Code / Codex adapters** (`agents/`): planning, review and optional takeover.
   Use the official CLIs (`claude -p`, `codex exec`) with their own authentication.
   A subscription is not assumed to grant API access.
7. **UI** (`frontend/`): task form, run state, plan, diff, evidence, review, decisions.

## Single owner of model choice and fallback

`routing.ModelRouter` is the **only** component that picks a model or switches to another.

- The worker asks the router for a model per gateway call and reports the outcome.
- `gateway.OmniRouteGateway` sends exactly one request with exactly the requested model id.
  It never retries and never substitutes.
- Studio sends only **explicit** model ids to OmniRoute, never `auto/*` combos, so OmniRoute
  runs `strategy=single` and does not perform its own fallback. Verified live 2026-10-06:
  the `auto/coding:free` combo stopped after one failed attempt (403 from an OpenCode
  free-tier provider), while an explicit model returned `x-omniroute-decision:
  strategy=single`.
- Every call stores requested model, actual model (`x-omniroute-model`), provider
  (`x-omniroute-provider`), tokens, cost, latency and error class in `model_call`.
- On a provider error the router records a cooldown (persisted, survives restart) and the
  worker's next call gets the next candidate. Retries are bounded by the run budget.

## Run lifecycle

```
queued → planning → working → verifying → reviewing → accepted
                       ▲                      │
                       └──── changes_requested (iteration < max) ┘
working (implementer=external) → awaiting_external → [user: "Hotovo"] → verifying
any active state → interrupted (restart / cancel)  → resume = re-run the interrupted step
any active state → needs_decision (budget exceeded, providers down, review reject,
                                    max iterations)  → user: rework / escalate / abandon
```

- Each step is a row in `step` with status `running|done|failed|interrupted`.
- On startup `recover()` marks every `running` step `interrupted` and its run
  `interrupted`. Nothing is resumed automatically.
- Resume re-executes the interrupted step from its recorded checkpoint. The `work` step
  resets the working copy to the iteration's base commit first (the working copy belongs to
  Studio, so this is safe). No step performs an irreversible external operation.
- Push, merge, publish, deploy and destructive actions are never automatic. Applying an
  accepted change to the source repository is a separate explicit action (Phase 1: patch
  file + branch in the source repo created only on an explicit request).

## Isolation and security

A git clone is not a security sandbox. Layers:

- **Working copy**: `git clone --no-hardlinks` of the source repository's `HEAD` into the
  Studio data directory. The user's checkout and its uncommitted work are never touched.
- **File policy** (`sandbox.py`, derived from Prizma `tools/sandbox.py`): every path resolves
  inside the working copy; deny `.git`, `.env*`, keys, credential files; no symlink escape.
- **Content policy**: files whose content matches the secret scanner (from AI Bridge
  `adapters/security.py`) are not sent to models.
- **Command policy**: allowlist of the first token, no shell, shell operators rejected,
  per-command timeout, output clipped.
- **Environment**: subprocesses get a minimal allowlisted environment; variables matching
  `KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL` are never forwarded to worker commands.
- **Scope**: each task declares allowed path globs. Writes outside them are refused by the
  tool and re-checked on the final diff by independent verification.
- **Out of scope for models**: personal documents and Recorder data are never placed in
  context automatically; Phase 1 sends only files from the task's working copy.
- **API**: binds `127.0.0.1`; mutating requests require the `X-Studio: 1` header and an
  allowed Origin (blocks browser CSRF). Loopback is not treated as authentication of the
  user — Phase 1 is a single-user local tool.

## Evidence model

- **Worker-run checks**: commands the worker ran itself; shown as claims.
- **Independent verification**: Studio runs the task's acceptance commands itself in the
  working copy after the worker stops, plus a scope check of the diff. Only these count.
- **Review**: Claude or Codex receives the goal, acceptance criteria, scope, the full diff
  and the independent verification output. The worker's summary is included only as an
  unverified claim. The reviewer returns structured JSON (`accept | request_changes |
  reject`, findings, per-criterion assessment).

## Budgets (per task, defaults)

| Limit | Default | On exceed |
|---|---|---|
| fix iterations | 2 | needs_decision |
| worker turns per iteration | 30 | iteration fails |
| worker wall time per iteration | 900 s | iteration fails |
| tokens per run (worker) | 400 000 | needs_decision |
| cost per run (USD, from OmniRoute) | 0.50 | needs_decision |
| command timeout | 120 s | command reported as timed out |
| review / plan timeout | 600 s | step fails, needs_decision |

## Stack decisions

- Python 3.13 + FastAPI + httpx + Pydantic; SQLite via stdlib `sqlite3`.
- React 19 + TypeScript + Vite, built and served by FastAPI.
- `uv` for Python, `npm` for the UI. Same as AI Bridge and Prizma; no reason to diverge.

## Source projects

AI Bridge (`../aibridge`) and Prizma (`../multiagent`) stay as independent sources. Nothing is
moved, deleted or merged; their databases are not touched. Ported code is listed in
`docs/PROVENANCE.md`.
