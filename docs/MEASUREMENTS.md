# Measurements

Raw numbers from live runs. There is **no baseline yet** for the same tasks done directly in
Claude Code or Codex, so these numbers say nothing about savings. See the measurement protocol
in `docs/PLAN.md`.

## 2026-10-06 — Phase 1 live runs

Setup: OmniRoute 3.8.49 on 127.0.0.1:20128, single provider connection (OpenRouter free tier).
Worker model `openrouter/poolside/laguna-s-2.1:free` (it served every worker call; no fallback
was needed). Lead: Codex CLI `exec` with `gpt-5.6-sol` (Claude CLI was not logged in, so Codex
both planned and reviewed — reviewer independence was reduced).

| Run | Task | Repo | Planner | Wall time | Plan | Work | Verify | Review | Worker calls | Tokens (worker) | Cost | Rounds | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5f8e266866c7 | add `median()` + tests | synthetic | codex | 163 s | 64 s | 49 s | 1 s | 49 s | 6 | 10,046 | $0 | 1 | accepted |
| 8b80b7aee2d5 | add `mode()` + tests, server killed mid-work, resumed | synthetic | none | 127 s* | – | 63 s | 1 s | 10 s | 6 | 7,697 | $0 | 1 | accepted |
| 9305edf86944 | add `mode()` + tests | synthetic | none | 67 s | – | 52 s | 0 s | 13 s | 6 | 9,087 | $0 | 1 | accepted |
| a1e58dde735e | `build_fts_query` rejects `max_terms<1`, tests incl. Czech diacritics | copy of AI Bridge | codex | 340 s | 59 s | 256 s | 3 s | 20 s | 21 | 205,128 | $0 | 1 | accepted |

\* includes the interrupted attempt and the time until the manual resume.

Tokens and cost are what OmniRoute reported per call (`x-omniroute-response-cost`, usage).
Lead (Codex) token use is not captured by Studio yet.

### Failure paths verified live

- Gateway unreachable (`STUDIO_OMNIROUTE_URL` pointed at a closed port): run stops with
  `needs_decision: gateway_down`, no model is cooled down.
- Gateway timeout 1 s: each of the 4 configured models tried exactly once, all cooled down,
  run stops with `needs_decision: no_model`.
- Server killed during work: run marked `interrupted`, nothing resumed automatically; resume
  reset the working copy to the iteration base and the run was accepted.

### Observations

- Worker context grows every turn (whole history resent). The real-repo task used 20x the
  tokens of the synthetic ones. Context trimming is the obvious next optimisation.
- Small free model succeeded on all four bounded tasks in one round.

### Attempted live failing-test run (b68dafabf57e)

Task with an intentionally unsatisfiable criterion (`geomean([1, 4]) == 3`). The worker made
7 calls, then laguna timed out and the other three models returned OpenRouter 429
`free-models-per-day`. The run stopped with `needs_decision: no_model` (correct behaviour, but
the failing-test path was not reached). The OpenRouter free tier has a daily request cap: the
whole fallback list shares one provider, so one exhausted quota takes out every worker model.

## Phase 2a live runs (2026-10-06, Codex only: Claude login expired, OmniRoute not running)

| Run | Mode | Implementer | Effort | Result | Wall time | Lead calls |
|---|---|---|---|---|---|---|
| c20d95f8a610 | codex solo | codex | low | draft approved in UI, plan, then 3 iterations with an empty diff; abandoned | ~25 min | draft, plan, 3× implement |
| 91c1b481d12c | codex solo | codex | low | iteration 1 claimed a change but wrote nothing; cancelled | ~6 min | plan, 2× implement |
| 4b9fa0d87ecb | codex solo | codex | low | plan 14 s, implement 69 s, verify passed, Codex review accept, 
eeds_decision: self_review, accepted by the user in the UI | 96 s to the decision | plan, implement, review |

What the failed runs found (all fixed or documented in ADR 0002):

- With --ignore-user-config, Codex also loses [windows] sandbox = "elevated", so
  workspace-write cannot write. Studio now passes -c windows.sandbox="elevated".
- The elevated sandbox user cannot see an MSIX-virtualized %LOCALAPPDATA%. This happens when
  the Claude desktop app starts Studio, and it fails with CreateProcessWithLogonW failed: 267.
  The dev launcher now sets STUDIO_DATA_DIR outside AppData.
- Codex loads the global ~/.codex/AGENTS.md and the user's skills.
  - The draft call used about 103k input tokens and the plan call about 81k, on a 3-file repo.
  - Codex once refused to edit because it "could not read the mandatory Obsidian context".
  - Lead prompts now carry an explicit stay-in-the-repository line. Real isolation is still open.
- The prompt "You cannot run commands" made Codex refuse to read files, because it reads
  through the shell. The prompt now forbids only tests, builds and installers.
- UI bug: event payloads were sent as data_json, so the accepted patch path did not show.
  Fixed, with a test.
