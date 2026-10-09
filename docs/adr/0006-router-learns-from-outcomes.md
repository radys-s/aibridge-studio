# ADR 0006 — Router learns from outcomes

Date: 2026-10-08. Status: accepted.

## v0.3.1 amendment — evidence qualification (2026-10-09)

This amendment supersedes pooled reliability and inferred review ownership below.

- Reliability is tracked per role, so successful planning cannot hide failing work calls.
  Existing aggregate catalog fields remain readable; `stats_roles`, `qualified_reviews`
  and `demotion_reasons` explain the role-specific evidence and ordering.
- A review records `work_step_id` and `independent`, plus `acceptance_valid` and
  `review_reason`. Acceptance uses the shared complete-criterion policy: an `accept`
  verdict alone, missing/duplicate assessments or blocking findings cannot establish success.
- Only the latest proven independent review of a specific work step contributes to its
  reviewed/accepted sample. Self-review, ambiguous historical records and external work
  without a proven model do not contribute. Actual model identities are checked across
  provider aliases; repeated reviews cannot multiply evidence from the same work.
- Historical runs stay readable. Missing provenance is treated conservatively as unknown,
  rather than invented. Call-failure statistics remain available. Existing time windows,
  thresholds, privacy filters and paid-model restrictions remain in force.
- When independent review is unavailable, self-review remains a user decision; when
  sensitive evidence is withheld, review cannot be accepted through a scanner override.

## Context

`ModelRouter` ordered models by catalog order, or by `strength` for hard tasks. It ignored
what had happened before. A model that kept returning broken tool calls stayed first, and
`model_call` already recorded the outcome of every gateway call.

## Decision

- **Stats.** `ModelRouter.stats()` reads gateway calls (`billing = 'api'`) from the last
  3 days, so a demoted model returns once its provider is fixed. It reads reviews from the
  last 14 days. The result is cached for 60 s, because `choose()` runs on every worker turn.
  - A failed call counts against a model unless it says nothing about the model:
    - OmniRoute down (`gateway_unavailable`);
    - rate limits (`429`);
    - auth (`401`, `403`);
    - missing setup in OmniRoute (the same patterns as the setup cooldown).
  - A finished review counts for the model that wrote the reviewed attempt. That is the
    model with the most successful calls in the last work step of the same run and
    iteration; an earlier, interrupted attempt does not count. "Accepted" uses the runner's
    rule: the verdict is `accept`, there are no blocker or major findings, and every
    criterion is met.
- **Demotion.** A model with at least 5 counted calls and a failure rate of 50 % or more goes
  below the healthy models of its capability group. It is never removed. Cooling models still
  go last.
- **Bonus.** For `hard` tasks the sort key is `strength + bonus`. The bonus is the accept
  rate (0 to 1) once a model has at least 3 reviewed runs. Other tasks keep the catalog
  order.
- **Unchanged.** The privacy floor, the paid switch, the exposed check and the cooldowns are
  applied before learning and are never overridden by it.
- **Explainable.** `catalog()` (Settings) returns:
  - `stats` (calls, failed, reviewed, accepted);
  - `demoted`;
  - `cooldown_kind` (`setup`, `rate_limit`, …).

  Settings shows "spolehlivost 92 % · 3/4 přijato" and "často selhává". When the
  cooldown kind is `setup`, Settings shows "Chybí nastavení v OmniRoute" with the provider's
  hint and a link to OmniRoute.

## Consequences

- The ordering is deterministic and easy to recompute from the database. No hidden state.
- The stats queries run at most once a minute per router. Indexes on `model_call(at)` and
  `model_call(step_id)` keep them cheap.
- Failures are pooled across roles (worker, review, lead). A model that fails as a reviewer
  also moves down as a worker. This is acceptable while every role uses the same models.
- There is little evidence at first. The thresholds keep a few bad calls from reordering
  anything.
