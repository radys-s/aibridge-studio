// Orchestration choices offered by the composer and settings, with Czech labels.
import type { AnyLead, Escalation, Implementer, ModelInfo, Orchestration, Privacy } from "./api";

export const LEAD_LABELS: Record<AnyLead, string> = { autopilot: "Autopilot" };

/** Labels for authors and actors, including Claude/Codex from runs before ADR 0004. */
const HISTORY_LABELS: Record<string, string> = { autopilot: "Autopilot", claude: "Claude", codex: "Codex", studio: "Studio" };

export function actorLabel(name: string | null): string | null {
  return name ? (HISTORY_LABELS[name] ?? name) : null;
}

export const IMPLEMENTERS: { id: Implementer; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "Levný worker; silnější model jen když plán nebo selhání řekne." },
  { id: "worker", label: "Worker", hint: "Levný model přes OmniRoute." },
  { id: "autopilot", label: "Autopilot", hint: "Silnější free model píše kód sám (pro těžší úkoly)." },
  { id: "freebuff", label: "Freebuff", hint: "Spouštíš ho ručně v terminálu, Studio pak ověří." },
];

export const ESCALATIONS: { id: Escalation; label: string; hint: string }[] = [
  { id: "ask", label: "Zeptat se", hint: "Když worker nestačí, nabídne převzetí Autopilotem." },
  { id: "auto", label: "Automaticky", hint: "Když worker nestačí, převezme to Autopilot sám." },
  { id: "off", label: "Vypnuto", hint: "Nikdy nepředávat silnějšímu modelu." },
];

export const PRIVACY_LABELS: Record<Privacy, string> = {
  no_train: "bez tréninku",
  unknown: "neověřeno",
  may_train: "může trénovat",
};

export const BLOCKED_LABELS: Record<NonNullable<ModelInfo["blocked"]>, string> = {
  privacy: "pod hranicí soukromí",
  paid: "placený (vypnuto)",
  not_connected: "poskytovatel nepřipojen v OmniRoute",
};

const PROVIDER_LABELS: Record<string, string> = {
  groq: "Groq",
  "cloudflare-ai": "Cloudflare",
  cf: "Cloudflare",
  cerebras: "Cerebras",
  ollamacloud: "Ollama Cloud",
  "ollama-cloud": "Ollama Cloud",
  ovh: "OVHcloud",
  ovhcloud: "OVHcloud",
  // GitHub Models was retired 2026-07-30; labels stay for old runs.
  ghm: "GitHub Models",
  "github-models": "GitHub Models",
  nvidia: "NVIDIA",
  gemini: "Gemini",
  mistral: "Mistral",
  cohere: "Cohere",
  zai: "Z.ai",
  hf: "Hugging Face",
  openrouter: "OpenRouter",
};

/** Where Tomáš creates a free key and which provider to pick in the OmniRoute dashboard
 * (ADR 0005). Only providers that do not train on inputs are offered. */
export const KEY_SOURCES: { provider: string; label: string; signup: string; signupLabel?: string; omniroute: string; note: string }[] = [
  { provider: "groq", label: "Groq", signup: "https://console.groq.com/keys", omniroute: "Groq", note: "V Data Controls zapni Zero Data Retention." },
  {
    provider: "ovh",
    label: "OVHcloud AI Endpoints",
    signup: "https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/",
    signupLabel: "katalog modelů",
    omniroute: "OVHcloud AI",
    note: "Klíč není potřeba: v OmniRoute přidej OVHcloud AI s prázdným klíčem (2 dotazy/min na model, s klíčem 400). Podle OVHcloud běží v EU a na datech se netrénuje (ověřeno 10. 10. 2026, ne zvlášť pro režim bez klíče).",
  },
  {
    provider: "ollamacloud",
    label: "Ollama Cloud",
    signup: "https://ollama.com/settings/keys",
    omniroute: "Ollama Cloud",
    note: "Free plán bez karty, jeden model naráz, omezená kvóta. Podle zásad Ollamy se na promptech z cloudu netrénuje (ověřeno 10. 10. 2026).",
  },
  { provider: "cerebras", label: "Cerebras", signup: "https://cloud.cerebras.ai", omniroute: "Cerebras", note: "Free tier chce platební kartu – bez ní ho přeskoč." },
  {
    provider: "cf",
    label: "Cloudflare Workers AI",
    signup: "https://dash.cloudflare.com/profile/api-tokens",
    omniroute: "Cloudflare AI",
    note: "Token ze šablony „Workers AI“. V OmniRoute vyplň i Account ID (pravý panel na dash.cloudflare.com), jinak vrací 502.",
  },
];
export const OMNIROUTE_PROVIDERS_URL = "http://localhost:20128/dashboard/providers";

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

/** Providers that will actually receive code and notes, in the router's order. */
export function activeProviders(models: ModelInfo[]): string[] {
  const out: string[] = [];
  for (const m of models) {
    if (m.blocked) continue;
    const label = providerLabel(m.provider);
    if (!out.includes(label)) out.push(label);
  }
  return out;
}

/** Studio's only orchestration: Autopilot plans, reviews and writes memory proposals. */
export function asAutopilot(orch: Orchestration): Orchestration {
  return { mode: "autopilot", primary: "autopilot", plan: orch.plan ?? true };
}

/** Short summary for chips and the sidebar. */
export function describe(orch: Orchestration): string {
  return orch.plan === false ? "Autopilot · bez plánu" : "Autopilot · free modely";
}

/** Turn a textarea (one item per line) into a list, dropping blanks. */
export function lines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}
