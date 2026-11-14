# ADR 0007 — Refrakt as a second gateway

Date: 2026-11-14. Status: accepted. Supersedes option 2 of ADR 0005 ("Prizma as a second
gateway", rejected then).

## Context

Prizma is now **Refrakt**, Tomáš's own BYOK router. It holds the logged-in keys for every
provider. ADR 0005 had him copy those keys into the OmniRoute dashboard by hand: two copies,
and every rotated key had to be changed twice. He asked for Studio to sit one level above
Refrakt and use it directly.

Refrakt now has an OpenAI-compatible `/v1` (`backend/app/api/gateway.py` there): an explicit
LiteLLM slug is called once with no fallback, `auto` uses Refrakt's own chain, and a bearer
token (`GATEWAY_TOKEN`) is required.

## Decision

- Studio talks to two gateways. Model ids starting with `refrakt/` go to Refrakt
  (`RefraktGateway`), everything else to OmniRoute (`RoutedGateway` in `gateway.py`).
- `ModelRouter` stays the only owner of choice and fallback. Studio never sends Refrakt's
  `auto` and does not list it.
- Privacy, daily caps and the "independent reviewer" check follow the **upstream** provider
  (`config.upstream`): `refrakt/groq/...` is Groq. LiteLLM's names map to OmniRoute's
  aliases (`nvidia_nim` → `nvidia`, `cohere_chat` → `cohere`, `huggingface` → `hf`), so
  NVIDIA stays `may_train`.
- The catalog lists a Refrakt twin right after each OmniRoute model that Refrakt also
  serves. With both connected OmniRoute goes first; with only Refrakt running, its twins
  are used.
- A gateway that never answered exposes nothing, so without OmniRoute the router does not
  try OmniRoute models that can only fail. Refrakt going down mid-run fails only that model
  (503, short cooldown); OmniRoute going down still stops the run, as before.
- Setup: `REFRAKT_GATEWAY_TOKEN` (the same value as Refrakt's `GATEWAY_TOKEN`) turns it on;
  `STUDIO_REFRAKT_URL` defaults to `http://127.0.0.1:8000`. Studio still never holds a
  provider key.

## Consequences

- One copy of the keys: a rotated key is changed in Refrakt only.
- Refrakt answers only to its own machine unless it runs in public mode, so Studio and
  Refrakt run side by side (on the PC or on a hub).
