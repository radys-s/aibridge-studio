# Status

Last update: 2026-10-08 (Claude Code), v0.3 below. Earlier: Phase 2c "private free Autopilot only" (ADR 0004)
implemented, tested (pytest, vitest, Playwright E2E) and run live on Groq: run
`6280086d4b75` was accepted with an independent review (gpt-oss-20b) and a model-written memory
proposal. Studio no longer calls Claude or Codex. See MEASUREMENTS.

## v0.3 — simple UI and router learning (2026-10-08, branch `v0.3-simple-ui`)

- **Apple-like UI.**
  - The stylesheet is rewritten: neutral greys, one blue accent, system font, 8-pt spacing.
    Light and dark come from one token set (`light-dark()`).
  - Composer:
    - one primary action (Spustit) and the auto-approve switch;
    - "Kdo píše kód", "Když to nezvládne", manual scope and "Uložit jako výchozí" sit
      behind "Další možnosti";
    - one quiet status line covers the gateway, the no-train providers and today's requests.
  - Run view:
    - a six-stage progress header (Zadání, Plán, Práce, Ověření, Recenze, Paměť), built by
      `thread.progress()`;
    - the Inspector opens only through "Podrobnosti", and the choice is remembered.
  - Plain wording: Autopilot instead of "lead", Recenze instead of Review, "2. pokus"
    instead of "iterace".
  - Settings cards: Modely, Klíče, Dnes, Paměť. Models that may train or are paid sit
    behind a collapsed table.
- **Router learning (ADR 0006).**
  - Reliability: failures over 3 days and reviews over 14 days. Capacity, auth and setup
    errors are not counted. Stats are cached for 60 s.
  - A model failing ≥50 % of ≥5 calls is demoted, never removed.
  - Hard tasks get a bonus of up to +1 strength for accepted work after ≥3 reviews.
  - Settings shows "spolehlivost … · x/y přijato".
- Indexes `model_call(at)` and `model_call(step_id)` back the stats queries.
- **Setup hint.** A model paused by a setup error shows "Chybí nastavení v OmniRoute" with
  the provider hint (Cloudflare Account ID) and a link to OmniRoute.
- **Live check (2026-10-09).** Run `14b0d400093e` went from the new composer to accepted
  (MEASUREMENTS). It found that OmniRoute's "reset after 3s" shortened setup cooldowns; fixed.
- Settings shows model names without the provider prefix and breaks them only after "/".

## Phase 2d — provider setup (2026-10-08)

- Settings: "Kde vzít klíče a kam je vložit" (direct links to each no-train provider's key
  page and to OmniRoute → Providers) and an "Otestovat" button per model
  (`POST /api/models/probe`): one tiny tool call, a fresh `/v1/models` check, and a stale
  cooldown cleared on success. Models below the privacy floor or paid are never sent.
- Live: Groq 120b and 20b call tools (554 / 588 ms); Cerebras answers "No active credentials
  for provider: cerebras" until its key is added.
- Keys added 2026-10-08: GitHub Models is retired (GitHub, 2026-07-30; the endpoint answers
  "OK" to everything) and was removed. Cerebras needs a card, so it stays unconnected.
  Cloudflare is connected but answers 502 until the Account ID is set in OmniRoute.
- Worker context trimming: tool results and `write_file` contents older than the last 6 tool
  results shrink before each call (`worker.loop.compact`: head and tail of outputs kept,
  latest read of each file and `replace_in_file` arguments never trimmed); the stored
  history is unchanged.
- OneDrive had turned `frontend/e2e/*` into cloud reparse points; Node reports them as
  symlinks and Playwright skipped them ("No tests found"). Recreated as regular files; if it
  recurs, mark the project folder "Always keep on this device".
- E2E waits on the run state through the API first, so a rare failure of the full-run spec
  (seen twice, not reproduced in 5 reruns) now reports where the run stopped.
- Live run `cf31341a973c` accepted with trimming on (MEASUREMENTS). Fixed on the way: the
  draft `notes` field accepts a list (Groq rejects schema mismatches), and setup errors
  (missing key or Account ID, 401/403) cool a model for 1 h instead of 30 s.
- Tests: 117 backend passed (1 live skipped), 15 vitest, 3 Playwright.

## Prizma's providers (2026-10-08, ADR 0005)

- The catalog lists the providers whose keys Prizma holds: Cerebras
  (no_train; GitHub Models removed, see ADR 0005 update), Z.ai, Cohere and Hugging Face (unknown), NVIDIA, Gemini and Mistral (may_train,
  never routed). Tomáš copies the keys into the OmniRoute dashboard; Studio never sees them.
- Ids use OmniRoute's provider alias (`cf/`, `hf/`); the old `cloudflare-ai/` ids
  could never match `/v1/models`.
- Not live-verified until the keys are in OmniRoute.

## Phase 2c — private free Autopilot only (2026-10-08, ADR 0004)

- Claude/Codex routing removed: no second opinion, no takeover by Claude/Codex, no CLI
  probe, no lead picker. Takeover goes to a stronger free model. Old runs still load.
  Claude/Codex remain only as handoff targets in vault notes.
- Model catalog with privacy classes (`no_train` / `unknown` / `may_train`) and a `paid`
  flag. The router never goes below `privacy_floor` (default `no_train`) and never picks a
  paid model unless `STUDIO_ALLOW_PAID=1`. It skips ids OmniRoute does not expose.
  Default order: Groq `gpt-oss-120b`, Cloudflare `kimi-k2.6` and `glm-4.7-flash`, Groq
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
- Tests: 104 backend passed (1 live skipped), 14 vitest, 3 Playwright. ruff and mypy clean.
- Older memory proposals show "nahrazeno novějším návrhem" instead of "Zapsat" (the server
  acts on the latest memory step only).

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
`STUDIO_PRIVACY` (`no_train` default, `unknown` relaxes), `STUDIO_NO_TRAIN_PROVIDERS`,
`STUDIO_OPENROUTER_NO_TRAIN`,
`STUDIO_PAID_MODELS`, `STUDIO_ALLOW_PAID`, `STUDIO_ROUTE_WAIT`, `STUDIO_GATEWAY_TIMEOUT`,
`STUDIO_VAULT`, `STUDIO_PORT`. `STUDIO_FAKE_GATEWAY=1` is for E2E tests only.
Provider keys live only in the OmniRoute dashboard; Studio never reads them.

## Known blockers and gaps

- Cloudflare Workers AI is in the catalog but not connected in OmniRoute; with Groq only,
  review independence is `gpt-oss-20b` reviewing `gpt-oss-120b` (same provider).
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
