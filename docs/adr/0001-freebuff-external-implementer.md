# ADR 0001 — Freebuff as a manual external implementer, not a routed model

Date: 2026-10-06. Status: accepted.

## Context

Freebuff (https://freebuff.com, Codebuff team, Apache-2.0 repo `CodebuffAI/freebuff`) offers
free coding agents funded by text ads. Tomáš asked whether it could be a free backend for
implementation. Findings on 2026-10-06:

- No HTTP API and no OpenAI-compatible endpoint. Models are reachable only through Freebuff's
  own products (CLI, Desktop, Web, Cloud, Chat).
- The `freebuff` CLI (`npm i -g freebuff`) is an interactive TUI. Headless flags (`--print`)
  are rejected as unknown options. Request for headless mode / SDK auth:
  github.com/CodebuffAI/freebuff/issues/947 (open, no maintainer reply).
- `@codebuff/sdk` `CodebuffClient.run()` is headless but needs a paid `CODEBUFF_API_KEY`.
- Limits: a daily allowance that resets at midnight Pacific; availability depends on
  country and VPN.
- Privacy: Freebuff processes prompts, code and repository content; prompts may be analysed
  to personalise ads; some models may use data for training when flagged.

- Terms of service (freebuff.com/terms-of-service, read 2026-10-06) forbid using "a bot,
  script, macro, headless browser, autonomous agent, or similar automation to operate our
  products"; "a human must initiate each session and remain actively present". They also
  forbid wrappers or custom clients for the free inference endpoints.

## Decision

1. Freebuff is **not** placed behind `routing.ModelRouter` / `gateway.py`. Those need an
   explicit model id served over HTTP; Freebuff has none. The router remains the only owner
   of model choice for the automatic worker.
2. Studio does **not** drive the TUI through a pseudo-terminal. It would be fragile, would
   sidestep the ad model the free tier depends on, and risks the terms of service.
3. Freebuff is supported as a **manual external implementer** (`TaskSpec.implementer =
   "external"`). Studio creates the working copy, checks it for sensitive files, writes a
   brief outside the copy and enters `awaiting_external`. Tomáš runs `freebuff` in the copy
   and clicks "Hotovo". Studio then commits the copy and runs independent verification and
   review as for the worker. Freebuff's output is an unverified claim.
4. Studio may open a terminal in the working copy (`external.open_terminal`, scrubbed
   environment) on the user's click. It never types into Freebuff or reads its output; the
   user starts the session, pastes the brief and stays present, as the terms require.

## Consequences

- Free implementation without OmniRoute tokens, at the cost of Tomáš's attention per
  iteration. Worker budgets (tokens, cost, turns, wall time) do not apply; iteration limit
  does.
- Studio cannot restrict what Freebuff reads or runs. Mitigations: pre-handoff scan
  (`sandbox.sensitive_files`) blocks the handoff if the copy contains credential-like files
  or secret content; post-hoc scope check of the diff; history rewrite check
  (`workspace.contains`). Writes outside the working copy are not detectable.
- The sensitive scan has no override yet: a repo with fake test credentials blocks the
  handoff (abandon and use the worker, or remove the fixture).
- Usage (tokens, cost) of Freebuff is unknown and recorded as such.

## Revisit when

Freebuff ships `--print`/NDJSON headless mode or the SDK accepts the free login, **and** the
terms allow automated use of it. Until then no automation, also not via pseudo-terminal. Then add an
adapter in `agents/` that runs it as an automatic implementer (still not via the router),
with the same handoff checks.
