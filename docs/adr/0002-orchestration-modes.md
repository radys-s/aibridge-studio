# ADR 0002 — Orchestration modes, effort, takeover and the Studio window

Date: 2026-10-06. Status: accepted.

## Context

Phase 1 had a fixed shape: one lead plans, the other reviews, a cheap worker implements.
Tomáš wants Studio to be his main coding tool: it should feel like the Claude/Codex desktop
apps and drive the terminal CLIs underneath. He decided on 2026-10-06:

- He chooses who orchestrates: Claude, Codex, or both at once.
- He chooses the model and effort for each lead. Effort above `high` warns that it uses the
  subscription limit fast.
- He writes a free-text prompt. The lead drafts the spec (scope, criteria, commands) and he
  approves or edits it.
- A strong agent can take over the implementation, either chosen by hand or by escalation.
- Studio runs in its own window, not a browser tab.

## Decision

### Modes (`Orchestration.mode`)

| Mode | Plan | Review | Independence |
|---|---|---|---|
| `cross` (default) | primary lead | the other lead | full |
| `duo` | both leads in parallel; the primary merges the two plans | both in parallel; accept only if both accept, with no blocker or major finding, and all criteria met | full, two opinions |
| `claude` / `codex` (solo) | that lead | that lead | reduced; shown in the UI |

- In `duo`, any reviewer failure gives `needs_decision`. A single review never accepts.
- If a lead implements (takeover), it never reviews its own work alone. In `cross` the other
  lead reviews. In solo mode the run stops in `needs_decision` with `pending=self_review`, and
  only Tomáš can accept it.
- Old `planner`/`reviewer` specs are migrated on read to `cross` or solo.

### Model and effort per lead

- `LeadConfig(model, effort)` is passed on every call. Claude: `--model`, `--effort`. Codex:
  `-m`, `-c model_reasoning_effort=...`.
- The catalog marks `xhigh`, `max` and `ultra` as heavy. The UI asks for confirmation and
  keeps a warning badge while a heavy effort is selected.
- Settings has a "probe" button. It makes one tiny live call, because a subscription is not
  assumed to allow every model.

### Draft, conversation and decisions

- New first phase `draft`: the primary lead reads the working copy (read-only) and returns
  title, allowed paths, criteria, commands and questions.
- If there are no questions and the user ticked auto-approve, the run continues. Otherwise
  the state is `awaiting_approval`. This state is not active, so a restart leaves it alone.
- Messages live in the `message` table. A reply answers questions, a note goes with a rework,
  and an instruction goes with a takeover.
- `run.pending` tells the UI which decision is open: `draft`, `plan`, `takeover`,
  `self_review` or `sensitive`.

### Takeover and escalation

- `Implementer`: `auto`, `worker`, `freebuff`, `claude` or `codex`.
- Lead implement mode:
  - Claude runs with `--permission-mode acceptEdits` and the tools Read, Edit, Write, Grep
    and Glob. It has no Bash; Studio runs every command itself.
  - Codex runs with `--sandbox workspace-write` in the working copy.
- `Escalation` decides what happens when the worker fails (`no_model`, `gateway_down`,
  `error`) or the last iteration fails verification:
  - `off`: `needs_decision`.
  - `ask`: `needs_decision` with `pending=takeover` and "Předat …" buttons.
  - `auto`: switch the implementer once (`Budget.max_escalations = 1`) and post a message.
- With `implementer=auto`, a plan that marks the task as `strong_agent` goes to the primary
  lead and does not call the worker.
- Before any lead or Freebuff reads the copy, Studio scans it for sensitive files. A hit
  stops the run with `pending=sensitive`, and only an explicit "allow" continues.

### Live updates

`GET /api/runs/{id}/stream` is a server-sent event stream with cursors for events, agent
output and messages. The UI reconnects with fresh cursors and falls back to polling.

### Window

- `studio app` starts uvicorn in a thread and opens Edge (or Chrome) in `--app` mode with its
  own profile in `<data dir>/ui-profile`. When the window closes, the server stops.
- If Studio already runs on the port, the command only opens a window.
- `scripts/install-shortcut.ps1` creates desktop and Start menu shortcuts.
- pywebview was rejected for now: it adds a dependency, and Edge app mode is enough.

## Consequences and open risks

- Two leads at high effort use subscription limits fast. The warning shows this, and usage is
  split into API and subscription.
- **Codex loads global user context.** Even with `--ignore-user-config`, `codex exec` loads
  `~/.codex/AGENTS.md` and the user's skills. Measured on 2026-10-06:
  - About 50–100k input tokens per call on a tiny repo.
  - The global instructions make Codex try to read the Obsidian vault.
  - The `read-only` sandbox can read the whole disk.

  Possible fix: run Codex with an isolated `CODEX_HOME` that holds only the login. This touches
  the credential file, so Tomáš decides it. Until then, the brief tells Codex not to read
  anything outside the working copy. This is an instruction, not an enforced limit.
- **Codex sandbox on Windows:**
  - `--ignore-user-config` also drops `[windows] sandbox = "elevated"`. Without that setting,
    `workspace-write` cannot write. Studio now passes `-c windows.sandbox="elevated"`.
  - The elevated sandbox runs as a separate user. If Studio was started by the Claude desktop
    app (MSIX), `%LOCALAPPDATA%` is virtualized. The sandbox user then cannot see the working
    copy and fails with `CreateProcessWithLogonW failed: 267`.
  - In that case, set `STUDIO_DATA_DIR` outside `AppData`. The dev launcher in
    `.claude/launch.json` does this.
- Claude paths (draft, plan, review, implement) are covered by fake-CLI tests only until
  Claude's headless login is renewed.
