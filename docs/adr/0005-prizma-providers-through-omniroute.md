# ADR 0005 — Prizma's providers through OmniRoute

Date: 2026-10-08. Status: accepted. Extends ADR 0004.

## Context

Tomáš runs Prizma (`../multiagent`), a BYOK chat router, next to OmniRoute and Studio. Prizma
already holds his free-tier keys for Groq, Cerebras, GitHub Models, NVIDIA, Gemini, Mistral,
Z.ai, Cohere, Hugging Face and OpenRouter. Prizma calls providers itself through LiteLLM with
its own fallback. It has no OmniRoute integration and no OpenAI-compatible endpoint.

He asked for Studio to use the same providers so that both work at once.

Options considered:

1. **Copy the keys into the OmniRoute dashboard** (chosen). Studio's catalog lists the same
   providers. OmniRoute stays the only gateway, and Prizma stays unchanged and independent.
2. Prizma as a second gateway. It would need a new endpoint in Prizma (a read-only reference
   project for Studio) and a second fallback owner next to `ModelRouter`. Rejected.
3. Studio reads Prizma's encrypted key store. Rejected: Studio must never hold provider keys.

## Decision

- Tomáš copies the keys from Prizma's settings into the OmniRoute dashboard himself. Neither
  Studio nor a coding agent reads them.
- Model ids use the provider **alias** that OmniRoute's `/v1/models` lists (`cf/...`,
  `ghm/...`, `hf/...`). The earlier `cloudflare-ai/...` ids never matched the exposed list.
- Privacy is a per-provider table (`config.PROVIDER_PRIVACY`), checked 2026-10-08:

  | Alias | Class | Basis |
  | --- | --- | --- |
  | groq, cf, cerebras | no_train | ADR 0004 |
  | ghm (GitHub Models) | removed | retired by GitHub on 2026-07-30 (see update below) |
  | nvidia (API trial) | may_train | trial terms let NVIDIA use content to improve its products and models |
  | gemini, mistral (free tiers) | may_train | ADR 0004 |
  | zai, cohere, hf, openrouter | unknown | no clear public commitment for free or trial keys |

- `may_train` models are listed so Settings can show them, but they are never routed. The
  floor can only be relaxed to `unknown`. After Tomáš opts out of training with a provider
  (for example in the Mistral admin console), `STUDIO_NO_TRAIN_PROVIDERS=mistral` promotes it.
- New lead order: Groq `gpt-oss-120b`, Cerebras `gpt-oss-120b`, Cloudflare `kimi-k2.6`,
  Cerebras `zai-glm-4.7`, Cloudflare `glm-4.7-flash`, Groq `gpt-oss-20b`.

## Consequences

- With Groq and Cerebras connected, the review runs on a different provider than the work,
  not only on a different model.
- Prizma keeps its own copy of the keys. A rotated key must be updated in both places.

## Update 2026-10-08 (after the keys were added)

- GitHub Models was retired by GitHub on 2026-07-30. `models.github.ai` now answers every
  request, with or without a token, with `200 text/plain "OK"`, which OmniRoute reports as
  "Invalid JSON response from provider" (502). Removed from the catalog and key links.
- Cerebras' free tier asks for a payment method. Tomáš will not add one, so Cerebras stays in
  the catalog (skipped while not connected) but is not the planned second provider.
- OmniRoute's Cloudflare list has no `gpt-oss-120b`; `glm-4.7-flash` replaces it. Cloudflare
  also needs the Account ID in the OmniRoute provider settings, otherwise every call is a 502
  "requires an Account ID".
- So the second no-train provider for independent review is Cloudflare Workers AI.

## Update 2026-10-10 (v0.6: more free no-train providers)

Tomáš asked for as many legitimate free providers as possible. Candidates came from public
lists (freellm.net/providers, awesome-free-llm-apis) and were kept only with a published
no-training statement. Claude cannot create accounts or enter keys; Tomáš connects them in
OmniRoute.

| Provider (OmniRoute alias) | Class | Source, checked 2026-10-10 |
| --- | --- | --- |
| Ollama Cloud (`ollamacloud`) | no_train | registry.ollama.ai/privacy: cloud prompts processed transiently, never used for training. OmniRoute's free catalog marks its terms "ambiguous". |
| OVHcloud AI Endpoints (`ovh`) | no_train | ovhcloud.com Free AI API page: data never stored or used for training. Not specific to the keyless tier. |
| LLM7 | excluded | no privacy policy found |

- Ollama Cloud: free plan, key from ollama.com/settings/keys, small session and weekly quota.
  Ranked below Groq and Cloudflare. Session/weekly quota errors get the long cooldown.
- OVHcloud works without a key (2 requests/min per model and IP; 400/min with a key).
  OmniRoute exposes only its fixed OVH list, so the catalog uses
  `Mistral-Small-3.2-24B-Instruct-2506`, which returned a correct tool call live. It is the
  lowest-ranked lead: a different-family fallback, not a primary worker.
- Settings shows "v OmniRoute" (not "připojeno") because a listed model does not prove the
  key works; "Otestovat" does.
