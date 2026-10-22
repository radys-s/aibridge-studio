# Status

## 1.0 open-source track (2026-10-22, branch `oss/v1`, Claude Code)

Plan: M1 de-personalize, M2 direct providers, M3 optional Claude Code / Codex, M4 i18n,
M5 CLI + TUI, M6 packaging, M7 public repo files + CI, M8 publish (repo goes public only on
the owner's explicit word).

- **M1 + M2 (`01ff9cd`).** Memory folder instead of a fixed vault path (Settings or
  `config.toml`), cross-platform external terminal and desktop window, `DirectGateway` for
  OpenAI-compatible providers (Groq, Cloudflare, Cerebras, Ollama Cloud, OVH, OpenRouter,
  local Ollama). OmniRoute is optional.
- **M3 (ADR 0007).** Claude Code and Codex are back as optional leads, per role (plan,
  review, takeover), all off by default (Settings → Agenti). Self-review is refused in four
  places; a CLI failure stops in `needs_decision`, never a silent switch.
- **Review fixes for `01ff9cd`.** `providers.toml` cannot redirect a built-in provider;
  custom providers need https unless on loopback; key saved before a provider is enabled;
  stale model lists retried after ~15 s; providers fetched in parallel; the memory folder
  cannot be the home folder or a drive root; Recorder exports refused.
- **Found in the browser check.** A stopped local Ollama counted as a working provider
  ("Přímo: Ollama"). Now a local provider counts only while its `/models` answers, and
  Settings shows "neběží".
- **M3 review (independent subagent): 5 critical, 12 risks.** Fixed: CLI edits outside
  the task scope now stop the run; takeover without a named lead was not gated; a custom
  provider could borrow a built-in key (`GROQ_API_KEY`) for its own host; Codex docs
  claimed "no shell" (it runs commands in its own sandbox; now stated honestly in UI and
  ADR). Also: roles re-checked before every CLI call, wider secret-name filter, stdin fed in
  a thread, `.cmd` shim argument guard, Claude deny rules for its config and credential
  files, subscription cost not counted as API spend, loopback no longer implies no-train,
  local providers with a fixed model list still probed, Composer / Settings no longer drop
  agent choices while agents load. Accepted risk: both CLIs may read outside the working
  copy within their own permissions (documented).
- **E2E fixture** still used the old personal vault layout (`02 Projekty/`); now
  `projects/` and `handoffs/`.
- **Gates (M3).** Backend 214 passed / 2 skipped, ruff, format, mypy clean; Vitest 26/26;
  Playwright 11/11 (27 s) from a checkout outside OneDrive.
- **Windows + OneDrive caveat (dev only).** When the venv lives in a OneDrive-synced folder,
  a server started through the venv's `studio.exe` launcher gets its whole process tree
  suspended by the OS (all threads `Suspended`, even the faulthandler watchdog) a few
  seconds into the first runs. The same code passes from `python -m studio.cli serve`, and
  passes through `studio.exe` from a folder outside OneDrive; v0.6 outside OneDrive also
  passes. Not a Studio bug; keep dev checkouts and e2e runs outside OneDrive.
- Open: M7 must scrub `CLAUDE.md` and `docs/*` of personal names and paths before publish.

## v0.6 — more free no-train providers (2026-10-10, branch `v0.6`, Claude Code)

v0.5 merged into `main` (fast-forward to `6850bb9`) on Tomáš's word.

- **New providers (ADR 0005, update 2026-10-10).** Searched public free-LLM lists and kept
  only providers with a published no-training statement: Ollama Cloud (`ollamacloud/*`,
  free plan with a key) and OVHcloud AI Endpoints (`ovh/*`, works without a key, 2 req/min
  per model). LLM7 excluded (no privacy policy). Ollama models rank below Groq and
  Cloudflare; OVH `Mistral-Small-3.2-24B-Instruct-2506` is the lowest lead (tool call
  verified live against OVH; OmniRoute only exposes its fixed OVH list).
- **Settings → Klíče** shows per provider whether OmniRoute offers its models
  ("v OmniRoute" / "chybí nastavení" / "nepřipojeno"), plus the signup link and a note.
  Verified against the running OmniRoute: Groq and Cloudflare "v OmniRoute", OVH, Ollama and
  Cerebras "nepřipojeno".
- Session and weekly quota errors (Ollama free plan) get the long cooldown.
- Not done by Claude: creating accounts, signing in, entering keys. Tomáš connects OVHcloud
  AI (empty key) and Ollama Cloud in OmniRoute → Providers.
- Review (independent subagent): 2 critical (OVH ids OmniRoute does not expose, so OVH
  always looked "missing"), 8 risks (ranking, wording overclaims, privacy sources, quota
  cooldown, test fixture). All fixed except: a keyless OVH 403 would still get the 1 h setup
  cooldown (not seen live; untested which status OVH sends at its keyless limit).
- Gates: 182 passed / 2 skipped, ruff, format, mypy; 25 Vitest, build, Playwright 11/11.

## v0.5 — daily comfort and reliability (2026-10-10, branch `v0.5`, Claude Code)

v0.4 merged into `main` (fast-forward to `c4d77e8`) on Tomáš's word.

- **Notifications.** The tab title shows how many runs wait for Tomáš ("(2) AI Bridge
  Studio"). When a run starts waiting for him or ends (accepted / abandoned), a system
  notification appears, only if he granted permission (Settings → Upozornění), only while
  the tab is hidden, never for runs already in that state when the app opens. Clicking it
  opens the run. Verified in the browser with a stub `Notification` (the embedded pane denies
  real ones).
- **Podobný úkol.** A finished run offers "Podobný úkol": `#/new/from/<id>` opens the
  composer with that run's goal, repo, implementer, escalation, scope, criteria and commands.
  Nothing starts until Spustit. E2E checks the prefill and that no run was created.
- **Crash window closed.** `run.base_iteration` records which iteration `iteration_base`
  belongs to. If Studio stopped after the iteration counter moved but before the new
  checkpoint was written, no worker ran in that iteration, so resume / takeover keep the
  working copy (the previous iteration's work) instead of resetting to the older base.
  Git commit messages are never trusted for this: a worker can run git through python.
- Review (independent subagent) of the first version found 0 critical, 5 risks: a forgeable
  checkpoint message, a second crash window at the iteration bump, takeover skipping the fix,
  an unguarded `decodeURIComponent` in routing and a stuck composer when the source run fails
  to load. All fixed; the design above replaced message matching.
- Gates: 182 passed / 2 skipped, ruff, format, mypy; 24 Vitest, build, Playwright 11/11.

## v0.4 — everyday MVP (in progress 2026-10-10, branch `v0.4-mvp`, Claude Code)

Built on Codex's `codex/v0.3.1-hardening` (387f7a8) after an independent review by Claude.

- **Review findings on v0.3.1, fixed.**
  - The `credential_assignment` secret pattern matched ordinary code (`password =
    hash_password(raw)`, `api_key=settings.api_key`). Since v0.3.1 such a match stops a run with
    no override, so Studio could not do any auth-related task. Now only literals count: quoted
    values (without spaces or templates) and unquoted values that are not calls, indexes,
    attribute paths or digit-free identifiers. The key may be part of a longer name
    (`DB_PASSWORD`, `client_secret`), quoted (JSON) or annotated. Known gap: letter-only
    unquoted literals (`password=mysecretpass`) are not detected.
  - The reviewer excluded only the implementer's exact model ids. The same model behind
    another provider (`cerebras/gpt-oss-120b` after `groq/openai/gpt-oss-120b`) was offered,
    and the identity check then sent the run to `self_review`. This broke the E2E project run
    every time. The exclusion now covers every catalog model with the same identity.
  - Kept as designed: picking a project without a repo hint leaves the repo field empty, so a
    run never starts in another project's repo.
- **Spustit OmniRoute.** When the gateway URL is local and OmniRoute does not answer, the
  status line offers "Spustit OmniRoute". `POST /api/omniroute/start` runs the fixed command
  `omniroute serve --port <port> --no-open` (no shell; binary found only in absolute PATH
  entries; Studio's `PORT` removed from its environment; no second start while the first one
  boots) and waits up to 60 s. Log: `<data dir>/omniroute.log`. Verified live: up in ~15 s.
- **Přehled** (sidebar, `#/overview`, `GET /api/overview?days=7|30|90`): read-only totals
  over runs created in the window, for the measurement protocol. It shows runs by state,
  accepted share of finished runs (open runs listed apart), median time from task to
  accepted (includes waiting for Tomáš), average fix rounds and first-try count, Tomáš's own
  decisions (the automatic draft approval excluded), branches applied, gateway calls,
  failures, tokens, OmniRoute-reported cost, and gateway tokens per accepted run. Subscription
  use and external-agent runs are shown apart and not averaged in. Top 5 models with their
  success share. No savings claimed. An independent review found 0 critical and 5 risks
  (misleading denominators, external work hidden, radio roles without radio keys); all fixed.
  Known limit: a call without a cost from OmniRoute is stored as 0, so "$0" means
  "reported 0 or unknown".

## v0.3.1 hardening (2026-10-09, `codex/v0.3.1-hardening`)

- Vault content is loaded through one bounded 1 MiB loader and checked for secrets/private
  markers before any 40,000-character context truncation or search snippet. Unavailable
  notes and links resolving outside the vault are skipped during listing/search.
- Command output is scanned before clipping; model context, step output, messages and
  agent/tool events share the same secret-withholding policy. Sensitive diff/verification
  evidence pauses the run in `needs_decision` and cannot be approved through the old
  false-positive preflight override.
- Review acceptance and router learning share exact normalized criterion coverage:
  missing, duplicate or unrelated assessments cannot establish acceptance. Self-review
  still requires a manual decision and the same complete acceptance contract.
- Review outputs add `work_step_id`, `independent`, `acceptance_valid` and `review_reason`.
  Learning counts the latest proven independent review per work step, excludes ambiguous
  historical/external/self-review evidence, and checks actual model overlap across aliases.
  Reliability is tracked by role; catalog aggregate fields remain compatible, with added
  `stats_roles`, `qualified_reviews` and `demotion_reasons`.
- Regression tests cover late-note secrets/private markers, oversized notes, output/history
  leakage, incomplete review, override prevention, repeated review and role-specific failures.
- UI clears project-derived context immediately on selection changes, waits for the selected
  project to load and prevents duplicate button/keyboard submission. Manual draft approval
  remains the default; an explicitly stored auto-approval preference is retained.
- Final verification: 148 backend tests passed, two skipped (Windows symlink privilege
  and the standard opt-in OmniRoute test); one Starlette deprecation warning. Ruff check
  and format check passed across 63 files, and mypy passed across 28 source files.
  Frontend build and TypeScript passed, 22 Vitest tests passed, and
  11 Playwright E2E tests passed. After the last mobile styling adjustment, five visual and
  self-review scenarios passed again. Screenshots cover 1440/390 px in light/dark themes.
  Independent review approved the changes after 54 targeted tests passed (one Windows
  symlink test skipped). Earlier test-import failures were corrected before the full
  backend gate passed. Implementation, verification and independent review are complete.
- Real Groq fixture run `8b4837626d17` accepted in 200.3 s: 22 calls, 34,150 tokens and
  gateway-reported 0 USD. Work ran on actual gpt-oss-120b and independent review on actual
  gpt-oss-20b. See MEASUREMENTS for the limits of this evidence.

Commands execute as the local user, under policy restrictions rather than an OS sandbox.
This release is intended for trusted projects. Cloudflare remains unverified until its
Account ID is configured in OmniRoute.

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
