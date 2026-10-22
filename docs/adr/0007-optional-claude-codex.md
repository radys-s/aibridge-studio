# ADR 0007 — Optional Claude Code and Codex, per role

Date: 2026-10-22. Status: accepted. Supersedes the Claude/Codex parts of ADR 0004.

## Context

Studio becomes an open-source project that other people install on their own machines. Many
of them already pay for Claude Code or Codex and want Studio to orchestrate those agents next
to the free models. ADR 0004 removed the CLI leads because the original author wanted Studio
only for work after those limits ran out. That is a personal preference, not a product rule,
so it becomes a setting.

## Decision

- `domain.AnyLead = Literal["autopilot", "claude", "codex"]`. Autopilot stays the default for
  every role.
- `Orchestration` has three roles:
  - `primary`: drafts the brief and plans;
  - `reviewer`: reviews the change;
  - `takeover`: implements when the cheap worker is not enough (escalation or manual takeover).
  Optional `claude` / `codex` `LeadConfig` set model and effort per agent.
- Each agent role is off until the user enables it in Settings → Agents (stored in
  preferences). The API rejects a task that uses a role that is not enabled, or a CLI that is
  not installed (422).
- `ClaudeLead` / `CodexLead` are restored from AI Bridge (`leads.py`). Claude runs with
  `--permission-mode plan` or `acceptEdits`, no shell tool, and extra deny rules (its own
  `.claude/` config, `CLAUDE.md`, `.env` files, common credential folders). Codex runs with
  `--sandbox read-only` or `workspace-write` (commands only inside its OS sandbox, no
  network) and `--ignore-user-config`. Both get an environment without secret-looking
  variables. On Windows, arguments cmd.exe would interpret are refused for `.cmd` shims.
  Studio runs verification commands itself.
- Both CLIs can read files their own permissions allow, which may reach beyond the working
  copy. Settings says so next to the toggles; this is the price of using them.
- After a CLI implements, Studio checks the changed files against the task scope and stops
  in `needs_decision` on anything outside it.
- Roles are checked at task creation, at manual takeover (also when no lead is named) and
  before every CLI call, so switching a role off in Settings stops it mid-run.
- **No self-review.** The reviewer is never the agent that wrote the code. This is enforced in
  the `Orchestration` validator, the `TaskSpec` validator, `Runner.takeover` and the review
  independence check.
- **No silent fallback.** When an agent fails or hits its limit, the run stops in
  `needs_decision` with the reason. Studio never swaps to another agent on its own.
- Usage is recorded per call with billing `subscription` and the CLI as provider, so Overview
  shows it next to API usage. Cost is recorded as 0: the user pays a subscription, so the
  CLI's API-equivalent figure must not count against the run's API budget.

## Consequences

- Code and context reach Anthropic or OpenAI when the user turns an agent on. Settings says
  so next to the toggles.
- Legacy saved orchestrations (`mode` other than `autopilot`) are reduced to Autopilot plus
  the `plan` flag; old runs keep their history labels.
- Tests use fake CLIs (`tests/fake_cli/`); a live smoke test with real CLIs is opt-in.
