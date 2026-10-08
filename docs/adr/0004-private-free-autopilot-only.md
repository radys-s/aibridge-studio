# ADR 0004 — Autopilot only, on free models that do not train on Tomáš's data

Date: 2026-10-08. Status: accepted. Supersedes the Claude/Codex parts of ADR 0002 (all
modes except `autopilot`) and ADR 0003 (second opinion, takeover by Claude/Codex).

## Context

On 2026-10-07 Tomáš asked for three things:

- Studio must never route work through Claude or Codex. He uses them directly, and Studio is
  for after their limits.
- Until he buys a paid "main orchestrator" model, Studio routes through the strongest free
  model whose provider does not train on his inputs and does not expose them.
- Buying the paid model later must be a configuration change, not a code change.

Provider data policies, checked 2026-10-07/08:

| Provider | Training on inputs | Retention | Free tier |
| --- | --- | --- | --- |
| Groq | no | up to 30 days for abuse/debugging; Zero Data Retention toggle | ~30 RPM, ~1K RPD per model |
| Cloudflare Workers AI | no | not stored, not shared with model vendors | 10k neurons/day |
| Cerebras | no | not retained | trial credits, needs a card |
| OpenRouter `:free` | depends on the upstream provider; free endpoints often may train | OpenRouter itself does not log by default | 50 RPD without credits |
| Mistral free, Google AI Studio free | may train (opt-in or outside EEA) | — | excluded |

## Decision

### Only Autopilot

- `domain.AnyLead = Literal["autopilot"]`. `Orchestration` keeps only `mode`, `primary` and
  `plan`. Old specs with `cross`, `duo`, `claude`, `codex`, `second_opinion` or Claude/Codex
  implementers still load, as Autopilot.
- Removed: `ClaudeLead`, `CodexLead`, the CLI process runner, plan merging, the second-opinion
  review, `/api/probe`, the Claude/Codex model catalog and effort settings.
- Takeover goes only to Autopilot (a stronger free model). A takeover request naming
  `claude` or `codex` returns 422.
- Claude and Codex remain only as **memory handoff targets** (a handoff note in the vault
  for them to pick up in their own apps) and in the history of old runs.

### Privacy-aware model catalog (`config.py`, `routing.py`)

- `ModelSpec.privacy` is `no_train`, `unknown` or `may_train`; `ModelSpec.paid` marks paid
  models.
- `Settings.privacy_floor` defaults to `no_train`. `ModelRouter` never returns a model below
  the floor. There is no silent fallback to a weaker privacy class. When nothing is left, the
  error names the providers to connect in OmniRoute.
  - `STUDIO_PRIVACY=unknown` relaxes the floor (explicit choice by Tomáš).
  - `STUDIO_OPENROUTER_NO_TRAIN=1` marks OpenRouter `:free` models as `no_train` after
    Tomáš turns off "allow providers that may train" in his OpenRouter privacy settings.
- Default lead order: `groq/openai/gpt-oss-120b`, Cloudflare `kimi-k2.6` and `gpt-oss-120b`,
  `groq/openai/gpt-oss-20b`, then OpenRouter `:free` (blocked by the default floor).
- The router asks the gateway which ids OmniRoute exposes (`GET /v1/models`, cached for 60
  s) and skips the rest, so the catalog can list providers that are not connected yet.
- Paid orchestrator later: `STUDIO_LEAD_MODELS=<id>,…`, `STUDIO_PAID_MODELS=<id>` and
  `STUDIO_ALLOW_PAID=1`. The per-run token and cost budget still applies.

### Rate limits

- A per-minute 429 cools the model for 60 s or the provider's `Retry-After`; a daily cap
  for 1 hour; a 5xx or empty upstream for 30 s.
- When every candidate is cooling and the soonest is free within `STUDIO_ROUTE_WAIT`
  (default 45 s), the router waits instead of stopping the run. Models the step must not use
  (the implementer's models during an independent review) are never waited for.
- The gateway still never retries or substitutes. Waiting is the router's decision.

## Consequences

- Without a connected no-train provider, runs stop with a clear message instead of sending
  code to a provider that may train on it.
- Groq's daily per-model cap limits Studio to a few runs a day; Cloudflare adds capacity.
- If no other lead model is free for the review, it
  runs on the implementer's model as before (ADR 0003), but the run stops with
  `pending=self_review`: Tomáš either accepts it or clicks "Zkusit nezávislou recenzi znovu".
  A self-review never accepts a run on its own.
- Review independence needs two `lead` models. With only Groq connected, `gpt-oss-20b`
  reviews `gpt-oss-120b`'s work (different model, same provider).
