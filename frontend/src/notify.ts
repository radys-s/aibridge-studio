// Tab title and system notifications for runs that need Tomáš or just ended (v0.5).
import { useEffect, useRef } from "react";
import type { RunSummary } from "./api";

export const NEEDS_YOU = ["needs_decision", "awaiting_approval", "awaiting_external"];
const ENDED = ["accepted", "abandoned"];
export const APP_TITLE = "AI Bridge Studio";

export type Change = { run: RunSummary; kind: "waiting" | "accepted" | "abandoned" };

/** Runs that moved into a state worth telling about since the previous poll. Nothing on the
 * first poll (prev = null): old runs must not ring when the app opens. A run that appears
 * already in such a state after the first poll is reported too. */
export function transitions(prev: ReadonlyMap<string, string> | null, runs: RunSummary[]): Change[] {
  if (!prev) return [];
  const out: Change[] = [];
  for (const run of runs) {
    const before = prev.get(run.id);
    if (before === run.state) continue;
    if (NEEDS_YOU.includes(run.state) && !(before && NEEDS_YOU.includes(before))) {
      out.push({ run, kind: "waiting" });
    } else if (ENDED.includes(run.state)) {
      out.push({ run, kind: run.state as "accepted" | "abandoned" });
    }
  }
  return out;
}

export function titleFor(runs: RunSummary[]): string {
  const waiting = runs.filter((r) => NEEDS_YOU.includes(r.state)).length;
  return waiting ? `(${waiting}) ${APP_TITLE}` : APP_TITLE;
}

export function changeText(c: Change): string {
  if (c.kind === "accepted") return "Přijato — změna prošla ověřením i recenzí.";
  if (c.kind === "abandoned") return "Úkol skončil bez přijetí.";
  return "Čeká na tvoje rozhodnutí.";
}

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

/** Keeps the tab title in sync and shows a notification per change while the tab is hidden. */
export function useRunNotifications(runs: RunSummary[], loaded: boolean, open: (id: string) => void): void {
  const prev = useRef<Map<string, string> | null>(null);
  useEffect(() => {
    document.title = titleFor(runs);
  }, [runs]);
  useEffect(() => {
    if (!loaded) return;
    const changes = transitions(prev.current, runs);
    prev.current = new Map(runs.map((r) => [r.id, r.state]));
    if (!changes.length || !notificationsSupported() || Notification.permission !== "granted") return;
    if (document.visibilityState === "visible") return;
    for (const c of changes) {
      try {
        const n = new Notification(c.run.title || APP_TITLE, { body: changeText(c), tag: `run-${c.run.id}` });
        n.onclick = () => {
          window.focus();
          open(c.run.id);
          n.close();
        };
      } catch {
        /* some browsers only allow notifications from a service worker; the title still shows it */
      }
    }
  }, [runs, loaded, open]);
}
