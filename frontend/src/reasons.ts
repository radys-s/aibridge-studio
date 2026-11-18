// The runner writes decision reasons in English for logs and tests. The decision bar shows a
// Czech explanation with the next step instead; the original stays available as a detail.

type Rule = [RegExp, (m: RegExpMatchArray) => string];

const PHASES: Record<string, string> = {
  drafting: "Návrh zadání se nepovedl",
  planning: "Plán se nepovedl",
  implementation: "Práce se nepovedla",
  review: "Recenze se nepovedla",
};

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
    /cooling down|no_model/,
    () =>
      "Žádný vhodný model teď není volný: všechny mají pauzu po limitu nebo chybě. Zkus fázi znovu za pár minut.",
  ],
  [
    /must assess every task criterion|criterion assessments are missing/,
    () =>
      "Recenzent neohodnotil každé kritérium přesně tak, jak je v zadání (vynechal ho nebo ho přepsal jinými slovy), " +
      "takže výsledek nejde uznat. Zkus fázi znovu; když se to opakuje, zkrať a zpřesni kritéria.",
  ],
  [/Review result is missing/, () => "Recenze nevrátila žádný výsledek. Zkus fázi znovu."],
  [
    /reviewer rejected the approach: (.*)$/s,
    (m) => `Recenzent odmítl celý přístup: ${m[1].trim().replace(/[.!?]?$/, ".")} Vrať úkol k opravě s poznámkou, jak to udělat jinak.`,
  ],
  [
    /no independent reviewer/,
    () =>
      "Změnu zkontroloval jen model, který ji napsal, protože jiný nebyl volný. Přijmi ji sám, " +
      "nebo zkus nezávislou recenzi znovu.",
  ],
  [
    /plan recommends a strong agent/,
    () => "Plán doporučuje, aby práci udělal silnější model. Předej mu úkol, nebo nech pokračovat levný model.",
  ],
  [
    /hand over to/,
    () => "Levný model úkol nezvládl. Předej ho silnějšímu modelu, nebo ho vrať k opravě s poznámkou.",
  ],
  [
    /worker stopped: (turn_limit|time_limit)/,
    () =>
      "Worker nestihl práci v limitu kroků nebo času a nic nezapsal. Zkus fázi znovu, nebo úkol předej silnějšímu modelu.",
  ],
  [
    /worker stopped: budget/,
    () => "Worker narazil na limit volání free modelů. Zkus fázi znovu později, nebo úkol předej silnějšímu modelu.",
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
  [/lead has questions/, () => "Autopilot má k plánu otázky. Odpověz zprávou dole."],
  [
    /external agent rewrote git history/,
    () => "Externí agent přepsal historii gitu v pracovní kopii, takže jeho práci nejde ověřit. Vrať úkol k opravě.",
  ],
  [
    /internal error/,
    () => "Ve Studiu nastala vnitřní chyba. Zkus fázi znovu; když se opakuje, technický detail níže pomůže najít příčinu.",
  ],
  [
    /^(drafting|planning|implementation|review) by \S+ failed/,
    (m) => `${PHASES[m[1]]}, protože model vrátil chybu. Zkus fázi znovu.`,
  ],
];

/** Czech explanation with the next step, or null when the reason is not a known runner text. */
export function explainReason(reason: string): string | null {
  for (const [pattern, explain] of RULES) {
    const m = reason.match(pattern);
    if (m) return explain(m);
  }
  return null;
}
