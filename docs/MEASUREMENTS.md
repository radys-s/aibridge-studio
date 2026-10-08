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

## Phase 2b — Autopilot on free models (2026-10-07)

Setup: OmniRoute 3.8.49, OpenRouter `:free` models only. Test vault
`~/.aibridge-studio-dev/test-vault` has the project note `Calc` and one open handoff. The run
used the demo repo `aibridge-studio-demo/calc`. It was started from "Pokračovat projekt" in
the UI.

| Run | Start | Implementer | Outcome | Free calls | Tokens |
|---|---|---|---|---|---|
| c3daa9ee51e6 | Pokračovat projekt: Calc | worker (auto) | needs_decision: self_review | 19 | 40.8k |

Steps:

- **Draft (laguna, 4 calls).** The draft got the project note and the related handoff; the
  vault event lists both. The spec was correct: `median()` with ValueError on an empty list
  and the even/odd cases, scope `calc/stats.py` + `tests/test_stats.py`, and the unittest
  command. Approved in the UI.
- **Plan (laguna, 5 calls), then worker (laguna, 5 calls).** The worker added `median()` and
  4 tests.
- **Studio verification.** Exit 0, 7 tests, in scope.
- **Review, first attempt.** Excluding the worker's model, nemotron answered once and then
  returned 502 (an empty upstream response). gemma returned 429. The step failed with
  `no_model`.
- **Review, retry.** It ran without the exclusion on laguna, the same model as the worker.
  The verdict was accept, and the run went to `self_review`, as designed. Accepting is
  Tomáš's decision.

Findings:

- Free providers are flaky within a single run, so the different-model review often falls
  back to `self_review`. The gate worked. Expect to confirm more often until another free
  provider or a small credit is added in OmniRoute.
- About 19 requests per small task. With the OpenRouter free cap of about 50/day without
  credit, that is about 2–3 tasks a day.

Earlier in the day, apply was live-checked on run `4b9fa0d87ecb` (Codex era). It created
`studio/4b9fa0d87ecb` in the demo repo with one squashed commit on `d73a565`, and the current
branch `main` and the working tree stayed clean.

Not live-verified yet: the memory proposals and "Zapsat". They start only after acceptance,
which is waiting for Tomáš on `c3daa9ee51e6`.

## 2026-10-08 — Phase 2c: memory fallback and E2E (Claude Code)

- **Memory retry on `c3daa9ee51e6`.** Tomáš accepted the run. The first memory step had
  failed (laguna wrote prose and never called `submit`). "Navrhnout znovu" ran with OmniRoute
  down, so the model call returned `gateway_down` and Studio built the fact-only fallback
  proposal (`author: studio`): title, changed files, verification result, review summary.
  Checked by hand: facts only, nothing personal. "Zapsat" wrote the project-note append and
  the handoff to the test vault (`~/.aibridge-studio-dev/test-vault`). Nothing was written
  before the click.
- **Playwright E2E** (`npm run e2e`, scripted gateway): 3/3 passed. The full run reached
  accepted, "Zapsat" appended to the fixture note only after the click, and apply created
  `studio/<run_id>` while the fixture repo stayed on `main`.
- **Not done:** a live run on Groq. OmniRoute was not running on 127.0.0.1:20128. Earlier on
  2026-10-08 a single non-streaming tool call to `groq/openai/gpt-oss-120b` through OmniRoute
  worked; a full Studio run on it is still unmeasured.
