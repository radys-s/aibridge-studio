# Status

Last update: 2026-10-06 (Claude Code). Phase 1 complete. Phase 2 "Studio app" (ADR 0002)
implemented: orchestration modes, per-lead model/effort, draft + chat, takeover/escalation,
SSE live view, new UI, `studio app` window. Claude lead still blocked on login.

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
`STUDIO_WORKER_MODELS` (comma-separated explicit OmniRoute ids), `STUDIO_GATEWAY_TIMEOUT`,
`STUDIO_CLAUDE_BIN`, `STUDIO_CODEX_BIN`, `STUDIO_CODEX_MODEL`, `STUDIO_PORT`.

## Known blockers and gaps

- **Claude CLI headless login expired**: `claude -p` returns "Failed to authenticate: OAuth
  session expired and could not be refreshed". Until Tomáš logs in again, use Codex for both
  plan and review (reduced independence) or planner `none`.
- Codex lead runs with `--ignore-user-config -m gpt-5.6-sol`: the user's config pins a model
  the ChatGPT login cannot use in `exec`, and loads personal hooks.
- Worker resends the whole history each turn; tokens grow fast on real repos.
- Lead (CLI) token use is not recorded.
- Narrow crash window: if Studio dies between the pre-iteration commit and the DB write of
  `iteration_base`, resume resets to the previous base and the previous iteration's changes
  are lost (still reachable via the copy's reflog). Low risk; fix with a step marker.
- No apply flow, no memory/context packet yet.
- Codex loads the global `~/.codex/AGENTS.md` and skills even with `--ignore-user-config`:
  50–100k extra input tokens per call, and it tries to follow personal instructions
  (Obsidian). Prompts now tell leads to stay in the working copy; real isolation (own
  `CODEX_HOME` with only the login) is Tomáš's decision (ADR 0002).

## Pitfalls found

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
