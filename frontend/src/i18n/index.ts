// UI language. English text in the source is the message id; cs.ts maps it to Czech.
// The language is fixed for the page's lifetime: switching it stores the choice and reloads,
// so module-level label tables may call t() at import time.
import { cs } from "./cs";

export type Lang = "en" | "cs";
export const LANGS: { id: Lang; label: string }[] = [
  { id: "en", label: "English" },
  { id: "cs", label: "Čeština" },
];

const KEY = "studio-lang";

function detect(): Lang {
  try {
    const saved = globalThis.localStorage?.getItem(KEY);
    if (saved === "en" || saved === "cs") return saved;
  } catch {
    /* storage unavailable: fall back to the browser language */
  }
  const nav = typeof navigator === "undefined" ? "" : navigator.language || "";
  return nav.toLowerCase().startsWith("cs") ? "cs" : "en";
}

let current: Lang = detect();
if (typeof document !== "undefined") document.documentElement.lang = current;

export function lang(): Lang {
  return current;
}

/** BCP 47 locale for number and date formatting. */
export function locale(): string {
  return current === "cs" ? "cs-CZ" : "en-US";
}

/** Store the choice and reload, so every label is rebuilt in the new language. */
export function setLang(next: Lang): void {
  try {
    globalThis.localStorage?.setItem(KEY, next);
  } catch {
    /* storage unavailable: the reload falls back to the browser language */
  }
  window.location.reload();
}

/** Tests only: switch without a reload. Label tables built at import keep their language. */
export function _setLangForTests(next: Lang): void {
  current = next;
}

function fill(text: string, params?: Record<string, string | number>): string {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (m, k: string) => (k in params ? String(params[k]) : m));
}

/** Translate an English message; `{name}` placeholders are filled from params. */
export function t(text: string, params?: Record<string, string | number>): string {
  return fill(current === "cs" ? (cs[text] ?? text) : text, params);
}

/** Czech plural form index: 1 / 2–4 / 0, 5+ (and fractions). */
function czechForm(n: number): number {
  if (n === 1) return 0;
  if (Number.isInteger(n) && n >= 2 && n <= 4) return 1;
  return 2;
}

/** Count-dependent message. English uses one/other; the Czech entry for `other` holds
 * three forms separated by `|` (1 | 2–4 | 5+). `{n}` is the count. */
export function tn(n: number, one: string, other: string, params?: Record<string, string | number>): string {
  const all = { n, ...params };
  if (current === "cs") {
    const forms = cs[other]?.split("|");
    if (forms?.length === 3) return fill(forms[czechForm(n)], all);
  }
  return fill(n === 1 ? one : other, all);
}
