# ADR 0003 — Autopilot: Studio is the tool for after the subscription limits

Date: 2026-10-07. Status: accepted. Supersedes the default orchestration of ADR 0002
(`cross` with Claude/Codex as orchestrators); the other ADR 0002 modes stay for old runs.

## Context

On 2026-10-07 Tomáš clarified how he works:

- Normally he uses Claude and Codex directly, in their own apps or CLIs, until their
  subscription limits run out.
- After the limit he opens Studio and continues the same projects with free models.
- He does not want Claude or Codex as Studio's orchestrators.

Continuity comes from the shared Obsidian memory (`AI paměť`). Claude and Codex already keep
project notes and handoffs there.

## Decision

### Autopilot orchestrator (`agents/api_lead.py`)

- Autopilot implements the existing `Lead` protocol on `ChatGateway`. The runner phases stay
  unchanged: draft → plan → implement → verify → review.
- It works as a tool loop with the shared tools from `agents/tools.py`:
  - read mode: `list_files`, `read_file`, `grep`, plus `vault_search` and `vault_read` when
    the vault is available;
  - write mode: the read-mode tools plus the write tools, used only in the working copy;
  - no shell commands. Studio runs the acceptance commands itself.
- Structured output comes from a final `submit` tool whose parameters are the phase schema.
  A bad answer gets a bounded number of repairs.
- Model choice stays with `ModelRouter`:
  - capability `lead`, with no fallback to models that lack it;
  - `complexity=hard` orders candidates by strength.
  - Calls are recorded per gateway call as `billing="api"`, against the run budget.
- Default free models are configured in `config.DEFAULT_MODELS`; `STUDIO_LEAD_MODELS`
  overrides the lead list.

### Independence without Claude/Codex

- The review runs on a different model from the one that did the work: the review excludes
  the work step's models.
- If no other lead model is free, the review runs anyway. If it then used an excluded model,
  the run stops with `pending=self_review`, and only Tomáš can accept it.
- Verification (tests, scope, empty diff) is always Studio's own.

### Optional second opinion

- `Orchestration.second_opinion: [claude|codex]` is valid only in `autopilot` mode and is off
  by default.
- Any error from an optional reviewer only skips it, with a message in the thread. A
  `limit` error (`cli_error_kind`) is the expected case.
- Worker `gateway_down`/`budget` failures do not escalate to Autopilot: they share one pool.

### Obsidian (`vault.py`, `memory.py`)

- **Read policy:**
  - Only include-listed folders, never exclude-listed ones (`01 Profil`, `03 Počítač`,
    `04 Hardware`, `07 Claude`, `10 Chaty`, `13 Časová osa`).
  - Never notes whose path names Recorder.
  - Never notes that are marked private, tagged `osobni`, or contain secrets.
  - Emails and phone numbers are redacted.
  - Every read is an agent event in the run.
- **"Pokračovat projekt":**
  - The draft gets the project note plus up to 3 open handoffs that mention it.
  - A note that cannot be shared stops the run in `needs_decision`.
  - The API refuses such a note with 422.
- **Write-back:**
  - After acceptance, Autopilot proposes a dated append to the project note and an optional
    handoff to Claude or Codex, built from the vault handoff template.
  - Nothing is written until Tomáš clicks "Zapsat".
  - Writes only append or create, only in `02 Projekty` and `08 Předávání`, and are
    secret-scanned.

### Apply (`apply.py`)

- "Použít do repozitáře" squashes the accepted working copy into one commit on the run's
  source commit. The commit is authored with the repository's git user.
- The commit is fetched into the source repository as `studio/<run_id>`.
- It never checks out, merges or pushes, and never touches the user's branch, index or
  working tree.
- It refuses when:
  - the branch exists;
  - the change is empty;
  - the user has uncommitted edits in a file the change touches.
- The UI asks for confirmation first.

## Consequences

- Studio keeps working when the Claude/Codex limits are used up. Quality depends on free
  models; the safeguards are independent verification, a different-model review, the
  `self_review` gate and handoffs that Claude/Codex can re-check later.
- All free calls share the OpenRouter `:free` daily cap. `GET /api/health` reports
  `api_calls_today`; when the pool is exhausted the run goes to `needs_decision` honestly.
- Code and shared notes go to free providers, which may train on them. This is mitigated by
  the vault policy, the repo's sensitive-file scan and a notice in the composer.
