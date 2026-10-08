# ADR 0006 — Router learns from outcomes

Date: 2026-10-08. Status: accepted.

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
