// Live run updates: one full fetch, then server-sent events append messages, agent output and
// events. Steps, model calls and the run row are refetched when the stream says they changed.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AgentEvent, type Message, type RunDetail, type RunEvent } from "./api";

export interface Cursors {
  events: number;
  agent: number;
  messages: number;
}

const maxId = (rows: { id: number }[]) => rows.reduce((m, r) => (r.id > m ? r.id : m), 0);

export function cursorsOf(d: RunDetail): Cursors {
  return { events: maxId(d.events), agent: maxId(d.agent_events), messages: maxId(d.messages) };
}

/** Append rows not seen yet (by id), keeping id order. Reconnects may resend rows. */
export function mergeRows<T extends { id: number }>(have: T[], incoming: T[]): T[] {
  if (incoming.length === 0) return have;
  const seen = new Set(have.map((r) => r.id));
  const fresh = incoming.filter((r) => !seen.has(r.id));
  if (fresh.length === 0) return have;
  return [...have, ...fresh].sort((a, b) => a.id - b.id);
}

export type StreamKind = "event" | "agent" | "message" | "run";

/** Apply one SSE message; returns the new detail and whether a full refetch is needed. */
export function applyStream(d: RunDetail, kind: StreamKind, payload: unknown): { detail: RunDetail; refetch: boolean } {
  switch (kind) {
    case "agent":
      return { detail: { ...d, agent_events: mergeRows(d.agent_events, payload as AgentEvent[]) }, refetch: false };
    case "message":
      return { detail: { ...d, messages: mergeRows(d.messages, payload as Message[]) }, refetch: false };
    case "event":
      // A new event usually means a step started/finished or a model call was recorded.
      return { detail: { ...d, events: mergeRows(d.events, payload as RunEvent[]) }, refetch: true };
    case "run": {
      const p = payload as Partial<RunDetail["run"]>;
      const changed =
        p.state !== d.run.state || p.phase !== d.run.phase || p.iteration !== d.run.iteration || p.pending !== d.run.pending;
      return { detail: { ...d, run: { ...d.run, ...p } }, refetch: changed };
    }
  }
}

/** Run detail kept live via SSE, with a polling fallback when EventSource is missing. */
export function useRunDetail(runId: string) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const ref = useRef<RunDetail | null>(null);
  const refetchTimer = useRef<number | undefined>(undefined);
  const alive = useRef(true);

  const set = useCallback((d: RunDetail) => {
    ref.current = d;
    setDetail(d);
  }, []);

  const load = useCallback(async () => {
    try {
      const d = await api.getRun(runId);
      if (!alive.current) return;
      // Keep rows that arrived over SSE meanwhile.
      const prev = ref.current;
      set(
        prev && prev.run.id === d.run.id
          ? {
              ...d,
              messages: mergeRows(d.messages, prev.messages),
              agent_events: mergeRows(d.agent_events, prev.agent_events),
              events: mergeRows(d.events, prev.events),
            }
          : d,
      );
      setError(null);
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [runId, set]);

  const scheduleRefetch = useCallback(() => {
    window.clearTimeout(refetchTimer.current);
    refetchTimer.current = window.setTimeout(() => void load(), 250);
  }, [load]);

  useEffect(() => {
    alive.current = true;
    ref.current = null;
    setDetail(null);
    setError(null);
    let source: EventSource | null = null;
    let retry: number | undefined;
    let poll: number | undefined;

    const connect = () => {
      const d = ref.current;
      if (!d || !alive.current) return;
      if (typeof EventSource === "undefined") {
        poll = window.setInterval(() => void load(), 2000);
        return;
      }
      source = new EventSource(api.streamUrl(runId, cursorsOf(d)));
      source.onopen = () => setLive(true);
      for (const kind of ["event", "agent", "message", "run"] as StreamKind[]) {
        source.addEventListener(kind, (ev) => {
          const cur = ref.current;
          if (!cur) return;
          const out = applyStream(cur, kind, JSON.parse((ev as MessageEvent<string>).data));
          set(out.detail);
          if (out.refetch) scheduleRefetch();
        });
      }
      source.onerror = () => {
        // Reconnect with fresh cursors instead of letting the browser replay the old URL.
        setLive(false);
        source?.close();
        source = null;
        window.clearTimeout(retry);
        retry = window.setTimeout(() => {
          void load().then(connect);
        }, 2000);
      };
    };

    void load().then(connect);
    return () => {
      alive.current = false;
      source?.close();
      window.clearTimeout(retry);
      window.clearInterval(poll);
      window.clearTimeout(refetchTimer.current);
    };
  }, [runId, load, set, scheduleRefetch]);

  return { detail, error, live, reload: load };
}
