// The runner writes decision reasons in English for logs and tests. The decision bar shows a
// Czech explanation with the next step instead; the original stays available as a detail.
// Each explanation names only buttons the bar shows in that state (see DecisionBar in Thread.tsx).

type Rule = [RegExp, (m: RegExpMatchArray) => string];

const PHASES: Record<string, string> = {
  drafting: "Návrh zadání se nepovedl",
  planning: "Plán se nepovedl",
  implementation: "Práce se nepovedla",
  review: "Recenze se nepovedla",
};

/** What went wrong, for the hand-over question where the bar offers no retry. */
function handOverCause(reason: string): string {
  const verify = reason.match(/independent verification failed; iteration limit (\d+) reached/);
  if (verify) return `Nezávislé ověření neprošlo ani po ${verify[1]} pokusech.`;
  const review = reason.match(/review requested changes; iteration limit (\d+) reached/);
  if (review) return `Recenze změnu nepřijala ani po ${review[1]} kolech.`;
  return "Levný model úkol nezvládl.";
}

const RULES: Rule[] = [
  [
    /independent verification failed; iteration limit (\d+) reached/,
    (m) =>
      `Nezávislé ověření neprošlo ani po ${m[1]} pokusech. V Podrobnostech na záložce Testy uvidíš, co padá; ` +
      "vrať úkol k opravě s poznámkou, nebo ho předej silnějšímu modelu.",
  ],
  [
    /review requested changes; iteration limit (\d+) reached/,
    (m) =>
      `Recenze změnu nepřijala ani po ${m[1]} kolech. Nálezy jsou v Podrobnostech na záložce Review; ` +
      "vrať úkol k opravě s poznámkou, nebo ho předej silnějšímu modelu.",
  ],
  [
    /not reachable|gateway_down/,
    () =>
      "Brána k modelům (OmniRoute nebo Refrakt) neodpovídá. Spusť ji a klikni na Zkusit fázi znovu.",
  ],
  [
    /no \w+ model (that does not train|is usable)/,
    () =>
      "Není připojený žádný použitelný model, který netrénuje na tvých datech. Připoj poskytovatele " +
      "(Nastavení → Klíče) a klikni na Zkusit fázi znovu.",
  ],
  [
    /cooling down/,
    () =>
      "Žádný vhodný model teď není volný: všechny mají pauzu po limitu nebo chybě. Zkus fázi znovu za pár minut.",
  ],
  [
    /must assess every task criterion|criterion assessments are missing/,
    () =>
      "Recenzent neohodnotil každé kritérium přesně tak, jak je v zadání (vynechal ho nebo ho přepsal jinými slovy), " +
      "takže výsledek nejde uznat. Zkus fázi znovu; když se to opakuje, úkol opusť a zadej ho znovu s jednoduššími kritérii.",
  ],
  [
    /Task acceptance criteria are missing or duplicated/,
    () => "Zadání nemá kritéria přijetí, nebo se v něm opakují. Úkol opusť a zadej ho znovu s jasnými kritérii.",
  ],
  [/Review result is missing/, () => "Recenze nevrátila žádný výsledek. Zkus fázi znovu."],
  [
    /reviewer rejected the approach: (.*)$/s,
    (m) => `Recenzent odmítl celý přístup: ${m[1].trim().replace(/[.!?]?$/, ".")} Vrať úkol k opravě s poznámkou, jak to udělat jinak.`,
  ],
  [
    /no independent reviewer/,
    () =>
      "Změnu zkontroloval jen model, který ji napsal (nezávislý nebyl k dispozici). Přijmi ji sám, " +
      "nebo zkus nezávislou recenzi znovu.",
  ],
  [
    /worker stopped: (turn_limit|time_limit)/,
    () =>
      "Worker nestihl práci v limitu kroků nebo času a nic nezapsal. Zkus fázi znovu, nebo úkol předej silnějšímu modelu.",
  ],
  [
    /worker stopped: budget/,
    () =>
      "Úkol vyčerpal svůj rozpočet tokenů nebo peněz, takže opakování ani předání nepomůže. " +
      "Úkol opusť, nebo ho zadej znovu s vyšším rozpočtem.",
  ],
  [
    /working copy could not be created/,
    () =>
      "Pracovní kopii nešlo vytvořit. Zkontroluj, že cesta vede do git repozitáře s aspoň jedním commitem, a zkus fázi znovu.",
  ],
  [
    /shared memory \(Obsidian\) is not available/,
    () => "Sdílená paměť (Obsidian) není dostupná. Zkontroluj cestu STUDIO_VAULT, nebo zadej úkol jako Nové zadání.",
  ],
  [
    /project note cannot be shared/,
    () =>
      "Poznámku projektu nejde poslat modelům (je soukromá nebo obsahuje osobní údaje). Uprav ji, nebo zadej úkol jako Nové zadání.",
  ],
  [
    /external agent rewrote git history/,
    () => "Externí agent přepsal historii gitu v pracovní kopii, takže jeho práci nejde ověřit. Vrať úkol k opravě.",
  ],
  [
    /^internal error:/,
    () => "Ve Studiu nastala vnitřní chyba. Zkus fázi znovu; když se opakuje, technický detail níže pomůže najít příčinu.",
  ],
  [
    /^(drafting|planning|implementation|review) by \S+ failed/,
    (m) => `${PHASES[m[1]]}, protože model vrátil chybu. Zkus fázi znovu.`,
  ],
];

/** Czech explanation with the next step, or null when the bar's own text says it already or the
 * reason is not a known runner text (then the original is shown). `phase` is the run's phase. */
export function explainReason(reason: string, phase?: string | null): string | null {
  // These states have their own explanation and buttons in the bar.
  if (/Sensitive .*withheld|lead has questions/.test(reason)) return null;
  // The hand-over question (pending "takeover") offers no retry or rework, only these buttons.
  const keep = phase === "work" ? ", nebo na Ponechat levný model" : "";
  if (/plan recommends a strong agent/.test(reason)) {
    return `Plán doporučuje, aby práci udělal silnější model. Klikni na Předat silnějšímu modelu${keep}.`;
  }
  if (/; hand over to \S+\?$/.test(reason)) {
    return `${handOverCause(reason)} Klikni na Předat silnějšímu modelu${keep}.`;
  }
  for (const [pattern, explain] of RULES) {
    const m = reason.match(pattern);
    if (m) return explain(m);
  }
  return null;
}
