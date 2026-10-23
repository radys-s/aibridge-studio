// Orchestration choices offered by the composer and settings, with UI labels.
import { t } from "./i18n";
import { AUTOPILOT, type AgentInfo, type AgentRole, type AnyLead, type Escalation, type Health, type Implementer, type ModelInfo, type Orchestration, type Privacy } from "./api";

/** Labels for authors and actors in run history. */
const HISTORY_LABELS: Record<string, string> = { autopilot: "Autopilot", claude: "Claude", codex: "Codex", studio: "Studio" };

export function actorLabel(name: string | null): string | null {
  return name ? (HISTORY_LABELS[name] ?? name) : null;
}

export const IMPLEMENTERS: { id: Implementer; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: t("Cheap worker; a stronger model only when the plan or a failure calls for it.") },
  { id: "worker", label: "Worker", hint: t("Cheap free model.") },
  { id: "autopilot", label: "Autopilot", hint: t("A stronger free model writes the code itself (for harder tasks).") },
  { id: "claude", label: "Claude Code", hint: t("Claude Code writes the code (your subscription).") },
  { id: "codex", label: "Codex", hint: t("Codex writes the code (your subscription).") },
  { id: "freebuff", label: "Freebuff", hint: t("You run it by hand in a terminal; Studio verifies afterwards.") },
];

export const ESCALATIONS: { id: Escalation; label: string; hint: string }[] = [
  { id: "ask", label: t("Ask"), hint: t("When the worker is not enough, offer a takeover by Autopilot.") },
  { id: "auto", label: t("Automatic"), hint: t("When the worker is not enough, Autopilot takes over on its own.") },
  { id: "off", label: t("Off"), hint: t("Never hand over to a stronger model.") },
];

export const PRIVACY_LABELS: Record<Privacy, string> = {
  no_train: t("no training"),
  unknown: t("unverified"),
  may_train: t("may train"),
};

export const BLOCKED_LABELS: Record<NonNullable<ModelInfo["blocked"]>, string> = {
  privacy: t("below the privacy floor"),
  paid: t("paid (off)"),
  not_connected: t("provider not connected"),
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

/** Where the user creates a free key and which provider to pick in the OmniRoute dashboard
 * (ADR 0005). Only providers that do not train on inputs are offered. */
export const KEY_SOURCES: { provider: string; label: string; signup: string; signupLabel?: string; omniroute: string; note: string }[] = [
  { provider: "groq", label: "Groq", signup: "https://console.groq.com/keys", omniroute: "Groq", note: t("Turn on Zero Data Retention in Data Controls.") },
  {
    provider: "ovh",
    label: "OVHcloud AI Endpoints",
    signup: "https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/",
    signupLabel: t("model catalog"),
    omniroute: "OVHcloud AI",
    note: t("No key needed: add OVHcloud AI in OmniRoute with an empty key (2 requests/min per model, 400 with a key). OVHcloud says it runs in the EU and does not train on data (checked 2026-10-10, not separately for the keyless mode)."),
  },
  {
    provider: "ollamacloud",
    label: "Ollama Cloud",
    signup: "https://ollama.com/settings/keys",
    omniroute: "Ollama Cloud",
    note: t("Free plan without a card, one model at a time, limited quota. Ollama's policy says cloud prompts are not used for training (checked 2026-10-10)."),
  },
  { provider: "cerebras", label: "Cerebras", signup: "https://cloud.cerebras.ai", omniroute: "Cerebras", note: t("The free tier asks for a payment card; skip it without one.") },
  {
    provider: "cf",
    label: "Cloudflare Workers AI",
    signup: "https://dash.cloudflare.com/profile/api-tokens",
    omniroute: "Cloudflare AI",
    note: t("Token from the \"Workers AI\" template. In OmniRoute also fill in the Account ID (right panel on dash.cloudflare.com), otherwise it returns 502."),
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

export const LEAD_LABELS: Record<AnyLead, string> = { autopilot: "Autopilot", claude: "Claude Code", codex: "Codex" };

export const ROLE_LABELS: Record<AgentRole, string> = {
  plan: t("brief and plan"),
  review: t("review"),
  takeover: t("implementation takeover"),
};

/** Leads allowed in a role: Autopilot always, a CLI lead only when the user enabled that role. */
export function leadsFor(role: AgentRole, agents: AgentInfo[]): AnyLead[] {
  return [AUTOPILOT, ...agents.filter((a) => a.installed && a.roles[role]).map((a) => a.name)];
}

/** Keep only roles that are still allowed; anything else falls back to Autopilot, and the
 * reviewer never equals the takeover agent (Studio refuses self-review). */
export function normalizeOrch(orch: Partial<Orchestration> | undefined, agents: AgentInfo[]): Orchestration {
  const pick = (lead: AnyLead | undefined, role: AgentRole): AnyLead =>
    lead && leadsFor(role, agents).includes(lead) ? lead : AUTOPILOT;
  const takeover = pick(orch?.takeover, "takeover");
  let reviewer = pick(orch?.reviewer, "review");
  if (reviewer !== AUTOPILOT && reviewer === takeover) reviewer = AUTOPILOT;
  return {
    primary: pick(orch?.primary, "plan"),
    reviewer,
    takeover,
    plan: orch?.plan ?? true,
    ...(orch?.claude ? { claude: orch.claude } : {}),
    ...(orch?.codex ? { codex: orch.codex } : {}),
  };
}

/** Short summary for chips and the sidebar. */
export function describe(orch: Orchestration): string {
  const leads = [orch.primary, orch.reviewer, orch.takeover];
  if (leads.every((l) => l === AUTOPILOT)) {
    return orch.plan === false ? t("Autopilot · no plan") : t("Autopilot · free models");
  }
  const parts = [t("plan {lead}", { lead: LEAD_LABELS[orch.primary] }), t("review {lead}", { lead: LEAD_LABELS[orch.reviewer] })];
  if (orch.takeover !== AUTOPILOT) parts.push(t("takeover {lead}", { lead: LEAD_LABELS[orch.takeover] }));
  return parts.join(" · ");
}

/** How Studio reaches models now: OmniRoute, direct providers, both, or nothing. */
export function gatewayState(health: Health): { ok: boolean; omniroute: boolean; text: string } {
  const omniroute = health.gateway.omniroute?.reachable ?? health.gateway.reachable;
  const direct = health.gateway.direct ?? [];
  const names = direct.map(providerLabel).join(", ");
  let text: string;
  if (omniroute && direct.length) text = t("OmniRoute + direct: {names}", { names });
  else if (omniroute) text = t("OmniRoute is running");
  else if (direct.length) text = t("Direct: {names}", { names });
  else text = t("No provider is reachable");
  return { ok: health.gateway.reachable, omniroute, text };
}

/** Turn a textarea (one item per line) into a list, dropping blanks. */
export function lines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}
