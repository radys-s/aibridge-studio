// Orchestration choices offered by the composer and settings, with Czech labels.
import type { AnyLead, EffortOption, Escalation, Implementer, Lead, Mode, Orchestration } from "./api";

export const LEAD_LABELS: Record<AnyLead, string> = { claude: "Claude", codex: "Codex", autopilot: "Autopilot" };

export const MODES: { id: Mode; label: string; hint: string }[] = [
  {
    id: "autopilot",
    label: "Autopilot",
    hint: "Free model navrhne, naplánuje a zreviduje; review dělá jiný model než implementaci.",
  },
  { id: "cross", label: "Křížově", hint: "Jeden plánuje, druhý nezávisle reviduje." },
  { id: "duo", label: "Oba", hint: "Oba plánují i reviewují; přijme se jen se souhlasem obou." },
  { id: "claude", label: "Jen Claude", hint: "Claude plánuje i reviduje. Menší nezávislost." },
  { id: "codex", label: "Jen Codex", hint: "Codex plánuje i reviduje. Menší nezávislost." },
];

export const IMPLEMENTERS: { id: Implementer; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "Levný worker; silný agent jen když plán nebo selhání řekne." },
  { id: "worker", label: "Worker", hint: "Levný model přes OmniRoute." },
  { id: "autopilot", label: "Autopilot", hint: "Silnější free model píše kód sám (pro těžší úkoly)." },
  { id: "freebuff", label: "Freebuff", hint: "Spouštíš ho ručně v terminálu, Studio pak ověří." },
  { id: "claude", label: "Claude", hint: "Claude píše kód v pracovní kopii (bez shellu)." },
  { id: "codex", label: "Codex", hint: "Codex píše kód v pracovní kopii (sandbox)." },
];

/** What the composer offers: Claude/Codex stay out, Studio is for after their limits. */
export const COMPOSER_IMPLEMENTERS = IMPLEMENTERS.filter((i) => i.id !== "claude" && i.id !== "codex");

export const ESCALATIONS: { id: Escalation; label: string; hint: string }[] = [
  { id: "ask", label: "Zeptat se", hint: "Když worker nestačí, nabídne převzetí silným agentem." },
  { id: "auto", label: "Automaticky", hint: "Když worker nestačí, převezme to Autopilot sám." },
  { id: "off", label: "Vypnuto", hint: "Nikdy nepředávat silnému agentovi." },
];

export const EFFORT_LABELS: Record<string, string> = {
  none: "žádné",
  minimal: "minimální",
  low: "nízké",
  medium: "střední",
  high: "vysoké",
  xhigh: "velmi vysoké",
  max: "maximum",
};

export const HEAVY_WARNING =
  "Effort nad „high“ rychle spotřebuje limit předplatného. Hodí se jen na opravdu těžké úkoly.";

/** Leads that take part in a run with this mode. */
export function leadsIn(mode: Mode): Lead[] {
  if (mode === "autopilot") return [];
  if (mode === "claude") return ["claude"];
  if (mode === "codex") return ["codex"];
  return ["claude", "codex"];
}

/** Leads in this orchestration whose effort is marked heavy. */
export function heavyLeads(orch: Orchestration, efforts: Record<Lead, EffortOption[]>): Lead[] {
  return leadsIn(orch.mode).filter((lead) => {
    const effort = orch[lead].effort;
    return !!effort && efforts[lead]?.some((e) => e.id === effort && e.heavy);
  });
}

/** A solo mode puts plan and review in one model's hands. */
export function isSolo(mode: Mode): boolean {
  return mode === "claude" || mode === "codex";
}

/** Normalise primary for a mode change: solo modes force their own lead. */
export function withMode(orch: Orchestration, mode: Mode): Orchestration {
  return { ...orch, mode, primary: mode === "claude" || mode === "codex" ? mode : orch.primary };
}

/** Studio's own orchestration: Autopilot leads, Claude/Codex settings are kept for second opinions. */
export function asAutopilot(orch: Orchestration): Orchestration {
  return { ...orch, mode: "autopilot", primary: "autopilot", second_opinion: orch.second_opinion ?? [] };
}

/** Short summary for chips and the sidebar, e.g. "Oba · Claude opus/high + Codex gpt-5.6-sol/medium". */
export function describe(orch: Orchestration): string {
  if (orch.mode === "autopilot") {
    const second = (orch.second_opinion ?? []).map((l) => LEAD_LABELS[l]);
    return second.length ? `Autopilot · druhý názor ${second.join(" + ")}` : "Autopilot · free modely";
  }
  const mode = MODES.find((m) => m.id === orch.mode)?.label ?? orch.mode;
  const leads = leadsIn(orch.mode).map((l) => {
    const c = orch[l];
    return `${LEAD_LABELS[l]} ${c.model ?? "výchozí"}${c.effort ? `/${c.effort}` : ""}`;
  });
  return `${mode} · ${leads.join(" + ")}`;
}

/** Turn a textarea (one item per line) into a list, dropping blanks. */
export function lines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}
