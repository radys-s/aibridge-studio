import { useState } from "react";
import type { Step } from "../api";
import { t } from "../i18n";

export function List({ items, mono }: { items: string[] | undefined | null; mono?: boolean }) {
  if (!items || items.length === 0) return <p className="muted">–</p>;
  return (
    <ul className={mono ? "mono-list" : undefined}>
      {items.map((it, i) => (
        <li key={i}>{mono ? <code>{it}</code> : it}</li>
      ))}
    </ul>
  );
}

export function ExitBadge({ code, timedOut }: { code: number | null; timedOut: boolean }) {
  if (timedOut) return <span className="badge bad">timeout</span>;
  if (code === 0) return <span className="badge good">exit 0</span>;
  if (code == null) return <span className="badge neutral">{t("no exit code")}</span>;
  return <span className="badge bad">exit {code}</span>;
}

export function CopyButton({ text, label = t("Copy") }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }
  return (
    <button type="button" className="small" onClick={() => void copy()} disabled={!text}>
      {copied ? t("Copied") : label}
    </button>
  );
}

/** Latest step of a kind that has an object output (steps arrive in chronological order). */
export function latestOutput<T>(steps: Step[], kind: Step["kind"]): { step: Step; out: T } | null {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s.kind === kind && s.output && typeof s.output === "object") return { step: s, out: s.output as T };
  }
  return null;
}
