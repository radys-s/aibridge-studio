// Builds the chronological thread of a run: chat messages and steps with their agent output.
import type { AgentEvent, Message, ReviewOutput, RunDetail, Step } from "./api";
import { t } from "./i18n";

/** Display acceptance is conservative, including older runs without server metadata. */
export function reviewAccepted(review: ReviewOutput | null, expected: string[]): boolean {
  if (!review || review.verdict !== "accept" || review.acceptance_valid === false) return false;
  if (!Array.isArray(review.findings) || review.findings.some((f) => f.severity === "blocker" || f.severity === "major")) return false;
  const normalize = (s: string) => s.trim().replace(/\s+/g, " ").toLowerCase();
  const wanted = expected.map(normalize);
  const got = (review.criteria ?? []).map((c) => normalize(c.criterion));
  return wanted.length > 0 && new Set(wanted).size === wanted.length
    && got.length === wanted.length && new Set(got).size === got.length
    && wanted.every((criterion) => got.includes(criterion))
    && review.criteria.every((c) => c.met === true);
}

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

/** Lead behind a step actor ("lead:autopilot", "autopilot" → autopilot). Runs from before
 * ADR 0004 may still show "claude" or "codex". */
export function actorLead(actor: string | null): string | null {
  if (!actor) return null;
  const name = actor.replace(/^lead:/, "").split(":")[0];
  return name === "claude" || name === "codex" || name === "autopilot" ? name : null;
}

export type StageState = "todo" | "running" | "done" | "failed" | "waiting" | "skipped";
export interface Stage {
  id: string;
  label: string;
  state: StageState;
}

const STAGES: { id: string; label: string; kinds: string[] }[] = [
  { id: "spec", label: t("Brief"), kinds: ["draft"] },
  { id: "plan", label: t("Plan"), kinds: ["plan"] },
  { id: "work", label: t("Work"), kinds: ["work", "handoff"] },
  { id: "verify", label: t("Verification"), kinds: ["verify"] },
  { id: "review", label: t("Review"), kinds: ["review"] },
  { id: "memory", label: t("Memory"), kinds: ["memory"] },
];
// Work, verification and review repeat on rework; only the newest iteration counts.
const REPEATED = ["work", "handoff", "verify", "review"];

/** Six plain stages for the run header: where the run is and what went wrong. */
export function progress(d: RunDetail): Stage[] {
  const { run, steps } = d;
  const iteration = Math.max(0, ...steps.filter((s) => REPEATED.includes(s.kind)).map((s) => s.iteration));
  const last = (kinds: string[]): Step | undefined =>
    steps
      .filter((s) => kinds.includes(s.kind) && (!REPEATED.includes(s.kind) || s.iteration === iteration))
      .reduce<Step | undefined>((a, s) => (!a || s.id > a.id ? s : a), undefined);
  const stages = STAGES.map((st): Stage => {
    const step = last(st.kinds);
    let state: StageState = "todo";
    if (step) {
      if (step.status === "running") state = "running";
      else if (step.status === "done") state = "done";
      // Stopped by a restart or Přerušit: it waits for the user to resume, it did not fail.
      else if (step.status === "interrupted") state = "waiting";
      else state = "failed";
      // A review that asks for changes did not pass, even though the step finished.
      if (st.id === "review" && state === "done") {
        if (!reviewAccepted(step.output as ReviewOutput | null, run.spec?.acceptance_criteria ?? [])) state = "failed";
        else if (run.pending === "self_review") state = "waiting";
      }
      const passed = (step.output as { passed?: boolean } | null)?.passed;
      if (st.id === "verify" && state === "done" && passed === false) state = "failed";
    }
    return { id: st.id, label: st.label, state };
  });
  const later = (i: number) => stages.slice(i + 1).some((s) => s.state !== "todo");
  stages.forEach((s, i) => {
    if (s.state !== "todo") return;
    // A full manual spec skips the draft; a run without planning skips the plan.
    if (s.id === "spec") s.state = run.state === "awaiting_approval" ? "waiting" : later(i) ? "done" : "todo";
    else if (s.id === "plan" && later(i)) s.state = "skipped";
  });
  if (run.state === "awaiting_approval") stages[0].state = "waiting";
  if (run.state === "awaiting_external" && stages[2].state !== "done") stages[2].state = "waiting";
  return stages;
}
