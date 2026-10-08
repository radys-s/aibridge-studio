# Status

Last update: 2026-10-08 (Claude Code). Phase 2c "private free Autopilot only" (ADR 0004)
implemented and tested (pytest, vitest, Playwright E2E). Studio no longer calls Claude or
Codex. The live run on Groq is still open: OmniRoute was not running at the end of the
session.

## Phase 2c — private free Autopilot only (2026-10-08, ADR 0004)

- Claude/Codex routing removed: no second opinion, no takeover by Claude/Codex, no CLI
  probe, no lead picker. Takeover goes to a stronger free model. Old runs still load.
  Claude/Codex remain only as handoff targets in vault notes.
- Model catalog with privacy classes (`no_train` / `unknown` / `may_train`) and a `paid`
  flag. The router never goes below `privacy_floor` (default `no_train`) and never picks a
  paid model unless `STUDIO_ALLOW_PAID=1`. It skips ids OmniRoute does not expose.
  Default order: Groq `gpt-oss-120b`, Cloudflare `kimi-k2.6` and `gpt-oss-120b`, Groq
  `gpt-oss-20b`; OpenRouter `:free` only with `STUDIO_OPENROUTER_NO_TRAIN=1`.
- Rate limits: 429 cools for 60 s or `Retry-After`, daily caps for 1 h, 5xx/empty for 30 s.
  The router waits up to `STUDIO_ROUTE_WAIT` s (default 45) for a cooling model; cancel
  interrupts the wait.
- Memory proposals: `submit` only, strongest model; when the model fails, Studio builds a
  fact-only fallback proposal (`author: studio`). "Navrhnout znovu" on the latest proposal.
- Life-upgrades: privacy line in the composer, per-provider budget for today (warn at 80 %),
  last project preselected, "Zkusit nezávislou recenzi znovu" on `self_review`.
- E2E: `npm run e2e` (Playwright) starts Studio with `STUDIO_FAKE_GATEWAY=1` (scripted
  gateway, test-only, labelled in the UI) on a fixture repo and vault. 3 specs: settings,
  composer, full run to accepted with "Zapsat" and apply-to-branch.
- Tests: 99 backend passed (1 live skipped), 14 vitest, 3 Playwright. ruff and mypy clean.

## Phase 2b — Autopilot, the after-the-limit tool (2026-10-07)

- Default orchestration is `autopilot`: a free model through OmniRoute drafts, plans and
  reviews. The review runs on a different model from the work; if none is free, the run
  needs Tomáš's acceptance (`self_review`).
- Claude/Codex are optional second-opinion reviewers (Settings, off by default). A limit
  or login error skips them and the run continues. They also remain takeover targets.
- Composer: "Pokračovat projekt" picks a project note from Obsidian, shows the open
  handoffs that will be sent and fills the repo from the note's path. "Nové zadání" is the
  free prompt. The free-pool line shows the lead models and requests today.
- Vault policy: shared folders only, no private/`osobni`/secret notes, nothing whose path
  names Recorder, contacts redacted. Reads appear as agent events.
- After acceptance, Autopilot proposes a project-note append and a handoff to Claude/Codex.
  The thread shows "Zapsat" / "Zahodit"; nothing is written before the click.
- "Použít do repozitáře" creates branch `studio/<run_id>` in the source repo without
  checkout, merge or push. Live-checked on the demo repo (run `4b9fa0d87ecb`): the branch
  holds one squashed commit on the source commit, and `main` and the working tree are
  unchanged.
- Tests: 108 backend + 14 frontend. A browser-pane click-through covered the project picker,
  repo hint, settings and apply.

## Phase 2 — Studio app (2026-10-06)

- Composer: free-text prompt + repo; mode Křížově / Oba / Jen Claude / Jen Codex; per-lead
  model and effort (`xhigh`/`max` asks for confirmation and keeps a warning badge);
  implementer Auto / Worker / Freebuff / Claude / Codex; escalation off / ask / auto.
- Lead drafts the spec; `awaiting_approval` until the user approves or edits it (or
  auto-approve with no questions). Replies in the run thread go to the lead.
- Duo: both plan (primary merges), both review, accept only if both accept.
- Takeover: Claude/Codex implement in the working copy; solo self-review needs the user's
  acceptance (`pending=self_review`).
- Thread + inspector (Diff / Testy / Review / Zadání / Modely / Události), settings with model
  catalog and live probe, light/dark theme, SSE stream with reconnect and polling fallback.
- `uv run studio app` opens an Edge app window; closing it stops the server.
  `scripts/install-shortcut.ps1` adds desktop and Start menu shortcuts.
- Live: Codex draft → approval in UI → plan → implement → verify → self-review (see
  `docs/MEASUREMENTS.md`). Claude paths covered by fake-CLI tests only.

## What works

- Submit a task in the UI (goal, allowed paths, acceptance criteria and commands, planner,
  reviewer, budget). Studio clones the repo into its data dir; the user's checkout is never
  touched.
- Plan (Claude or Codex CLI, read-only) → worker tool loop through OmniRoute in the working
  copy → independent verification (acceptance commands, scope check, empty diff) → review by
  the other lead against the diff and criteria → accept, rework round, or `needs_decision`.
- Accepted change is exported as `<data dir>/workspaces/<run_id>.patch`. Applying it to the
  source repo is manual for now (Phase 2 apply flow, behind explicit approval).
- Run detail in the UI: state, plan, diff, verification evidence, review findings and
  criteria, every model call with requested vs actual model, provider, tokens, cost, latency.
- Cancel, resume, rework with a note, abandon. Restart-safe: active runs become
  `interrupted`, nothing auto-resumes, resume resets the copy to the iteration base.

- External implementer (Freebuff, manual): task option "Kdo implementuje". Studio hands the
  working copy over, waits, then verifies and reviews (ADR 0001). Live-checked in the UI with
  simulated agent edits and a real Codex review → accepted. Not yet tried with Freebuff
  itself (not installed; needs Tomáš's login).

## Live-verified (2026-10-06)

See `docs/MEASUREMENTS.md`. Four accepted runs (3 synthetic, 1 on a copy of AI Bridge),
gateway down, gateway timeout with fallback across all models, server kill + resume.
Failing acceptance tests → rework/`needs_decision` is covered by tests with real git and
real unittest, not yet observed live (the live worker never failed).

## How to run

```powershell
omniroute serve                    # OmniRoute must be running on 127.0.0.1:20128
cd frontend; npm install; npm run build; cd ..
uv run studio serve                # http://127.0.0.1:8765
```

Environment: `STUDIO_DATA_DIR`, `STUDIO_OMNIROUTE_URL`, `OMNIROUTE_API_KEY`,
`STUDIO_LEAD_MODELS`, `STUDIO_WORKER_MODELS` (comma-separated explicit OmniRoute ids),
`STUDIO_PRIVACY` (`no_train` default, `unknown` relaxes), `STUDIO_OPENROUTER_NO_TRAIN`,
`STUDIO_PAID_MODELS`, `STUDIO_ALLOW_PAID`, `STUDIO_ROUTE_WAIT`, `STUDIO_GATEWAY_TIMEOUT`,
`STUDIO_VAULT`, `STUDIO_PORT`. `STUDIO_FAKE_GATEWAY=1` is for E2E tests only.
Provider keys live only in the OmniRoute dashboard; Studio never reads them.

## Known blockers and gaps

- **Live Groq run not done**: OmniRoute was not running on 20128 at the end of Phase 2c.
  Start `omniroute serve`, then run Pokračovat projekt on the demo project.
- Cloudflare Workers AI is in the catalog but not connected in OmniRoute; with Groq only,
  review independence is `gpt-oss-20b` reviewing `gpt-oss-120b` (same provider).
- Superseded memory proposals still show "Zapsat" (only "Navrhnout znovu" is limited to the
  latest one).
- Worker resends the whole history each turn; tokens grow fast on real repos.
- Lead (CLI) token use is not recorded.
- Narrow crash window: if Studio dies between the pre-iteration commit and the DB write of
  `iteration_base`, resume resets to the previous base and the previous iteration's changes
  are lost (still reachable via the copy's reflog). Low risk; fix with a step marker.
- Memory proposals run synchronously after acceptance in the runner thread (one extra
  free call per accepted project run).

## Pitfalls found

Codex CLI pitfalls below are historical (CLI leads removed in Phase 2c).

- Codex on Windows: `--ignore-user-config` drops `windows.sandbox="elevated"`, so
  `workspace-write` silently cannot write; Studio passes it explicitly. The elevated sandbox
  runs as another user and cannot see an MSIX-virtualized `%LOCALAPPDATA%` (error
  `CreateProcessWithLogonW failed: 267`) — when Studio is started from the Claude desktop
  app, set `STUDIO_DATA_DIR` outside `AppData` (`.claude/launch.json` does).
- Telling Codex "you cannot run commands" blinds it: it reads files through the shell.

- Windows: uvicorn sets `SO_REUSEADDR`, so a second server can bind the same port and steal
  requests. `studio serve` probes the port and refuses to start.
- Claude desktop app (MSIX) virtualises `LOCALAPPDATA` for processes it spawns:
  `%LOCALAPPDATA%` resolves under `...\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local`. Set
  `STUDIO_DATA_DIR` explicitly if paths must be stable across launchers.
- Acceptance commands on a copy of a project with an editable install: the venv `.pth`
  points at the original source, so tests silently import the original. Use
  `-o pythonpath=<src>` (pytest) or a dedicated venv, and check `module.__file__` once.
- OmniRoute `auto/*` combos: `auto/coding:free` failed live (one target, 403). The gateway
  refuses `auto/`; the router owns fallback.
