// Builds the chronological thread of a run: chat messages and steps with their agent output.
import type { AgentEvent, Message, RunDetail, Step } from "./api";

export type ThreadItem =
  | { type: "message"; at: number; message: Message }
  | { type: "step"; at: number; step: Step; agent: AgentEvent[] };

export function buildThread(d: RunDetail): ThreadItem[] {
  const byStep = new Map<number, AgentEvent[]>();
  for (const ev of d.agent_events) {
    if (ev.step_id == null) continue;
    const list = byStep.get(ev.step_id);
    if (list) list.push(ev);
    else byStep.set(ev.step_id, [ev]);
  }
  const items: ThreadItem[] = [
    ...d.messages.map((m): ThreadItem => ({ type: "message", at: m.at, message: m })),
    ...d.steps.map(
      (s): ThreadItem => ({ type: "step", at: s.started_at ?? 0, step: s, agent: byStep.get(s.id) ?? [] }),
    ),
  ];
  // Stable by time; a message and the step that produced it at the same instant keep step first.
  return items.sort((a, b) => a.at - b.at || (a.type === "step" ? -1 : 1));
}

/** The newest message carrying a drafted spec (the one awaiting approval). */
export function latestDraftId(messages: Message[]): number | null {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].data?.draft) return messages[i].id;
  return null;
}

/** Name of the lead behind a step actor ("lead:claude", "claude:merge", "claude" → claude). */
export function actorLead(actor: string | null): "claude" | "codex" | null {
  if (!actor) return null;
  const name = actor.replace(/^lead:/, "").split(":")[0];
  return name === "claude" || name === "codex" ? name : null;
}
