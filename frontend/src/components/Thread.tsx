import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  api,
  ApiError,
  AUTOPILOT,
  isActive,
  type ActionBody,
  type AgentEvent,
  type Applied,
  type Draft,
  type HandoffOutput,
  type MemoryOutput,
  type Message,
  type Plan,
  type ReviewOutput,
  type RunAction,
  type RunDetail,
  type Step,
  type VerifyOutput,
  type WorkOutput,
} from "../api";
import { absoluteTime, formatDuration, nowSec, STEP_LABELS, STEP_STATUS_LABELS, VERDICT_LABELS } from "../format";
import { locale, t, tn } from "../i18n";
import { actorLabel, describe, LEAD_LABELS, lines } from "../options";
import { actorLead, buildThread, latestDraftId, progress, type StageState } from "../thread";
import { CopyButton, ExitBadge, latestOutput, List } from "./bits";
import { StateBadge } from "./StateBadge";

type Act = (action: RunAction, body?: ActionBody) => Promise<boolean>;

const AUTHOR_LABELS: Record<string, string> = {
  user: t("You"),
  claude: "Claude",
  codex: "Codex",
  autopilot: "Autopilot",
  studio: "Studio",
};
const AVATAR_LETTERS: Record<string, string> = { user: t("You").charAt(0), studio: "S", claude: "C", codex: "X", autopilot: "A" };

function Avatar({ who }: { who: string }) {
  const letter = AVATAR_LETTERS[who] ?? "?";
  return (
    <span className={`avatar av-${who}`} aria-hidden="true">
      {letter}
    </span>
  );
}

/** Codex answers in the output schema even mid-turn; show its summary, not raw JSON. */
export function readable(text: string): string {
  const s = text.trim();
  if (!s.startsWith("{")) return text;
  try {
    const obj = JSON.parse(s) as Record<string, unknown>;
    for (const key of ["summary", "notes", "goal", "title"]) {
      if (typeof obj[key] === "string" && obj[key]) return obj[key] as string;
    }
  } catch {
    /* not JSON after all */
  }
  return t("structured answer ({n} characters)", { n: s.length });
}

function AgentStream({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const end = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(running);
  useEffect(() => {
    if (running) setOpen(true);
  }, [running]);
  useEffect(() => {
    if (open && running) end.current?.scrollIntoView({ block: "nearest" });
  }, [events.length, open, running]);
  if (events.length === 0) return running ? <p className="muted small thinking">{t("The agent is thinking…")}</p> : null;
  const shown = events.slice(-300);
  const tools = events.filter((e) => e.kind === "tool").length;
  return (
    <details className="agent-stream" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="small muted">
        {t("Agent activity · {n} entries", { n: events.length })}
        {tools ? ` · ${t("{n} tools", { n: tools })}` : ""}
        {running && <span className="pulse" aria-label={t("running")} />}
      </summary>
      <div className="agent-log">
        {events.length > shown.length && <div className="muted small">{t("… older entries hidden")}</div>}
        {shown.map((ev) => (
          <div key={ev.id} className={`ae ae-${ev.kind}`}>
            {ev.kind === "tool" ? (
              <>
                <span className="ae-icon">⚙</span> <code>{ev.text}</code>
              </>
            ) : ev.kind === "tool_result" ? (
              <span className="muted">↳ {ev.text.length > 240 ? ev.text.slice(0, 240) + "…" : ev.text}</span>
            ) : ev.kind === "error" ? (
              <span className="error">{ev.text}</span>
            ) : ev.kind === "status" ? (
              <span className="muted">{ev.text}</span>
            ) : (
              <span className="prewrap">{readable(ev.text)}</span>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>
    </details>
  );
}

function PlanBody({ plan }: { plan: Plan }) {
  return (
    <>
      <p className="prewrap">{plan.summary}</p>
      {plan.steps?.length > 0 && (
        <ol className="plan-steps">
          {plan.steps.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
      )}
      <p className="small muted">
        {t("Complexity: {c} · recommended implementer: {i}", { c: plan.complexity || "–", i: plan.recommended_implementer || "–" })}
      </p>
      {plan.files_to_touch?.length > 0 && (
        <details>
          <summary className="small">{t("Files ({n})", { n: plan.files_to_touch.length })}</summary>
          <List items={plan.files_to_touch} mono />
        </details>
      )}
      {plan.risks?.length > 0 && (
        <details>
          <summary className="small">{t("Risks ({n})", { n: plan.risks.length })}</summary>
          <List items={plan.risks} />
        </details>
      )}
    </>
  );
}

export function ReviewBody({ review }: { review: ReviewOutput }) {
  return (
    <>
      <p>
        <span className={`badge verdict-${review.verdict}`}>{VERDICT_LABELS[review.verdict] ?? review.verdict}</span>{" "}
        <span className="prewrap">{review.summary}</span>
      </p>
      {review.findings?.length > 0 && (
        <ul className="findings">
          {review.findings.map((f, i) => (
            <li key={i}>
              <span className={`badge sev-${f.severity}`}>{f.severity}</span> <code>{f.file}</code> {f.problem}
              {f.fix && <div className="muted small">{t("Fix: {fix}", { fix: f.fix })}</div>}
            </li>
          ))}
        </ul>
      )}
      {review.criteria?.length > 0 && (
        <ul className="criteria">
          {review.criteria.map((c, i) => (
            <li key={i} className={c.met ? "met" : "unmet"}>
              <span className="mark" aria-label={c.met ? t("met") : t("not met")}>
                {c.met ? "✓" : "✗"}
              </span>
              <div>
                <div>{c.criterion}</div>
                {c.evidence && <div className="muted small prewrap">{c.evidence}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function MemoryBody({ out, act, latest }: { out: MemoryOutput; act: Act; latest: boolean }) {
  const [busy, setBusy] = useState<number | null>(null);
  const proposals = out.proposals ?? [];

  async function run(action: "vault-write" | "vault-discard" | "memory-retry", index: number) {
    setBusy(index);
    await act(action, action === "memory-retry" ? {} : { index });
    setBusy(null);
  }

  const again = (
    <button type="button" className="small" disabled={busy !== null} onClick={() => void run("memory-retry", -1)}>
      {t("Propose again")}
    </button>
  );
  if (proposals.length === 0)
    return (
      <div className="actions">
        <span className="muted small">{t("Autopilot proposed nothing to write.")}</span>
        {latest && again}
      </div>
    );

  return (
    <>
      <p className="small muted">
        {out.author === "studio"
          ? t("The model gave no proposal; Studio built it from the run's verified facts.")
          : t("Proposal from Autopilot.")}{" "}
        {t("It is written to the memory folder only after your click, appended to the end of a note or as a new handoff. Nothing is overwritten.")}
      </p>
      {proposals.map((p, i) => (
        <div key={i} className={`proposal status-${p.status}`}>
          <div className="small">
            <strong>{p.kind === "append" ? t("Add to the project note") : t("New handoff")}</strong>{" "}
            <code>{p.path}</code>
          </div>
          <details open={p.status === "proposed" && latest}>
            <summary className="small">{t("Text")}</summary>
            <pre className="output">{p.content}</pre>
          </details>
          {p.status === "proposed" && !latest ? (
            // The server acts on the latest memory step only; an older proposal is superseded.
            <span className="badge">{t("replaced by a newer proposal")}</span>
          ) : p.status === "proposed" ? (
            <div className="actions">
              <button type="button" className="primary small" disabled={busy !== null} onClick={() => void run("vault-write", i)}>
                {t("Write")}
              </button>
              <button type="button" className="small" disabled={busy !== null} onClick={() => void run("vault-discard", i)}>
                {t("Discard")}
              </button>
            </div>
          ) : (
            <span className={`badge ${p.status === "written" ? "good" : "warn"}`}>
              {p.status === "written" ? t("written") : t("discarded")}
            </span>
          )}
        </div>
      ))}
      {latest && <div className="actions">{again}</div>}
    </>
  );
}

function StepBody({ step, act, latest }: { step: Step; act: Act; latest: boolean }) {
  const out = step.output as Record<string, unknown> | null;
  if (!out || typeof out !== "object") return null;
  switch (step.kind) {
    case "memory":
      return <MemoryBody out={out as unknown as MemoryOutput} act={act} latest={latest} />;
    case "plan":
      return <PlanBody plan={out as unknown as Plan} />;
    case "review":
      return <ReviewBody review={out as unknown as ReviewOutput} />;
    case "verify": {
      const v = out as unknown as VerifyOutput;
      return (
        <>
          <p>
            {v.passed ? <span className="badge good">{t("passed")}</span> : <span className="badge bad">{t("did not pass")}</span>}
            {v.empty_diff && <span className="badge warn">{t("empty diff")}</span>}
            <span className="muted small">{tn(v.changed_files?.length ?? 0, "{n} changed file", "{n} changed files")}</span>
          </p>
          {v.out_of_scope?.length > 0 && (
            <div className="alert warn">
              {t("Outside the allowed scope:")} <List items={v.out_of_scope} mono />
            </div>
          )}
          <ul className="claims">
            {(v.commands ?? []).map((c, i) => (
              <li key={i}>
                <code>{c.command}</code> <ExitBadge code={c.exit_code} timedOut={c.timed_out} />{" "}
                <span className="muted small">{formatDuration(c.seconds)}</span>
              </li>
            ))}
          </ul>
        </>
      );
    }
    case "work": {
      const w = out as unknown as WorkOutput;
      const files = w.files_changed ?? w.files_written ?? [];
      return (
        <>
          {w.claim && <p className="small"><span className="badge warn">{t("unverified claim")}</span> {w.claim}</p>}
          {w.summary && <p className="prewrap">{w.summary}</p>}
          {files.length > 0 && (
            <details>
              <summary className="small">{t("Files according to the implementer ({n})", { n: files.length })}</summary>
              <List items={files} mono />
            </details>
          )}
          {w.concerns && w.concerns.length > 0 && (
            <div className="alert warn small">
              {t("Implementer's concerns:")} <List items={w.concerns} />
            </div>
          )}
          {w.detail && <p className="muted small prewrap">{w.detail}</p>}
        </>
      );
    }
    case "setup":
      return <p className="small muted">{t("Working copy:")} <code>{String(out.workspace ?? "")}</code></p>;
    case "handoff": {
      const h = out as unknown as HandoffOutput;
      return h.text ? (
        <details>
          <summary className="small">{t("Brief for {agent}", { agent: h.agent })}</summary>
          <pre className="output">{h.text}</pre>
        </details>
      ) : null;
    }
    default:
      return null;
  }
}

function stepTitle(step: Step): string {
  const label = STEP_LABELS[step.kind] ?? step.kind;
  const actor = step.actor ?? "";
  if (actor.endsWith(":merge")) return t("Merged plan");
  if (actor.startsWith("lead:")) return `${label} · ${t("implemented by {who}", { who: actorLabel(actor.slice(5)) ?? "" })}`;
  if (actor.startsWith("external:")) return `${label} · ${actor.slice(9)} (${t("by hand")})`;
  const lead = actorLead(actor);
  if (lead) return `${label} · ${actorLabel(lead)}`;
  return actor && actor !== "studio" ? `${label} · ${actor}` : label;
}

/** `latest`: the newest step of its kind; only that one offers "Propose again". */
function StepCard({ step, agent, act, latest }: { step: Step; agent: AgentEvent[]; act: Act; latest: boolean }) {
  const lead = actorLead(step.actor);
  const running = step.status === "running";
  const dur = step.started_at ? formatDuration((step.finished_at ?? nowSec()) - step.started_at) : "";
  return (
    <div className={`step-card kind-${step.kind} status-${step.status}${lead ? ` by-${lead}` : ""}`}>
      <div className="step-head">
        <span className={`step-dot ${step.status}`} aria-hidden="true" />
        <strong>{stepTitle(step)}</strong>
        {step.iteration > 1 && <span className="muted small">{t("attempt {n}", { n: step.iteration })}</span>}
        <span className={`badge step-status-${step.status}`}>{STEP_STATUS_LABELS[step.status] ?? step.status}</span>
        <span className="muted small" title={absoluteTime(step.started_at)}>
          {dur}
        </span>
      </div>
      {step.error && <p className="error small">{step.error}</p>}
      {latest && step.kind === "memory" && step.status === "failed" && (
        <div className="actions">
          <button type="button" className="small" onClick={() => void act("memory-retry")}>
            {t("Propose again")}
          </button>
        </div>
      )}
      <StepBody step={step} act={act} latest={latest} />
      {(lead || agent.length > 0) && <AgentStream events={agent} running={running} />}
    </div>
  );
}

function DraftCard({ message, editable, act }: { message: Message; editable: boolean; act: Act }) {
  const draft = message.data?.draft as Draft;
  const invalid = message.data?.invalid ?? [];
  const [title, setTitle] = useState(draft.title);
  const [paths, setPaths] = useState(draft.allowed_paths.join("\n"));
  const [criteria, setCriteria] = useState(draft.acceptance_criteria.join("\n"));
  const [commands, setCommands] = useState(draft.acceptance_commands.join("\n"));
  const [busy, setBusy] = useState(false);
  const hasQuestions = draft.questions.length > 0;

  async function approve() {
    setBusy(true);
    await act("approve", {
      edits: {
        title: title.trim() || draft.title,
        allowed_paths: lines(paths),
        acceptance_criteria: lines(criteria),
        acceptance_commands: lines(commands),
      },
    });
    setBusy(false);
  }

  if (!editable) {
    return (
      <div className="draft-card readonly">
        <strong>{draft.title}</strong>
        <dl className="spec">
          <dt>{t("Scope")}</dt>
          <dd><List items={draft.allowed_paths} mono /></dd>
          <dt>{t("Criteria")}</dt>
          <dd><List items={draft.acceptance_criteria} /></dd>
          <dt>{t("Verification")}</dt>
          <dd><List items={draft.acceptance_commands} mono /></dd>
        </dl>
      </div>
    );
  }
  return (
    <div className="draft-card">
      <div className="draft-head">
        <strong>{t("Brief")}</strong>
        <span className="muted small">{t("Fix what does not fit and approve. Answer questions with a message below.")}</span>
      </div>
      {hasQuestions && (
        <div className="alert warn">
          <strong>{t("Autopilot asks:")}</strong>
          <List items={draft.questions} />
        </div>
      )}
      <label className="field">
        <span>{t("Title")}</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="field-row">
        <label className="field">
          <span>{t("Allowed paths")}</span>
          <textarea rows={4} value={paths} spellCheck={false} onChange={(e) => setPaths(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("Acceptance criteria")}</span>
          <textarea rows={4} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("Verification commands")}</span>
          <textarea rows={4} value={commands} spellCheck={false} onChange={(e) => setCommands(e.target.value)} />
        </label>
      </div>
      {invalid.length > 0 && (
        <div className="alert error">
          {t("Studio will not run these commands; change them:")} <List items={invalid} mono />
        </div>
      )}
      <div className="actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void approve()}>
          {hasQuestions ? t("Approve without answers") : t("Approve and plan")}
        </button>
      </div>
    </div>
  );
}

function MessageBubble({ message, draftEditable, act }: { message: Message; draftEditable: boolean; act: Act }) {
  const who = message.author;
  return (
    <div className={`msg msg-${who}`}>
      <Avatar who={who} />
      <div className="msg-body">
        <div className="msg-meta">
          <strong>{AUTHOR_LABELS[who] ?? who}</strong>{" "}
          <span className="muted small" title={absoluteTime(message.at)}>
            {new Date(message.at * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
        {message.data?.draft ? (
          <DraftCard message={message} editable={draftEditable} act={act} />
        ) : (
          <div className="prewrap">{message.text}</div>
        )}
      </div>
    </div>
  );
}

function files(n: number): string {
  return tn(n, "{n} file", "{n} files");
}

/** Takeover: the run's takeover agent writes the code itself (Autopilot by default; Claude Code
 * or Codex when the task chose one, ADR 0007). */
function TakeoverButton({ run, act, note }: { run: RunDetail["run"]; act: Act; note: string }) {
  const lead = run.spec.orchestration.takeover ?? AUTOPILOT;
  return (
    <button type="button" onClick={() => void act("takeover", { lead, note })}>
      {lead === AUTOPILOT ? t("Hand over to a stronger model") : t("Hand over: {lead}", { lead: LEAD_LABELS[lead] })}
    </button>
  );
}

function DecisionBar({ detail, act, note }: { detail: RunDetail; act: Act; note: string }) {
  const { run } = detail;
  const implementable = !!run.workspace && !["setup", "draft", "approve"].includes(run.phase ?? "");
  const abandon = (
    <button
      type="button"
      className="danger"
      onClick={() => {
        if (window.confirm(t("Really abandon this task? This cannot be undone."))) void act("abandon");
      }}
    >
      {t("Abandon")}
    </button>
  );

  if (isActive(run.state)) {
    return (
      <div className="decision-bar working">
        <span className="pulse" aria-hidden="true" />
        <span>
          {run.cancel_requested ? t("Stopping…") : t("Working · {phase}", { phase: STEP_LABELS[run.phase ?? ""] ?? run.phase ?? "" })}
        </span>
        <button type="button" className="danger" disabled={!!run.cancel_requested} onClick={() => void act("cancel")}>
          {t("Interrupt")}
        </button>
      </div>
    );
  }

  if (run.state === "accepted") {
    const ev = [...detail.events].reverse().find((e) => e.kind === "accepted");
    const patch = (ev?.data as { patch?: string } | null)?.patch ?? "";
    const applied = [...detail.events].reverse().find((e) => e.kind === "applied")?.data as Applied | undefined;
    return (
      <div className="decision-bar done">
        <span className="badge good">{t("Accepted")}</span>
        {applied ? (
          <>
            <span className="small">
              {t("In branch")} <code>{applied.branch}</code> ({files(applied.files.length)})
            </span>
            <CopyButton text={`git merge ${applied.branch}`} label={t("Copy merge")} />
            <span className="muted small">{t("You do the merge and push yourself.")}</span>
          </>
        ) : (
          <>
            <button
              type="button"
              className="primary"
              onClick={() => {
                const msg =
                  t("Create a new branch studio/{id} with this change in {repo}?", { id: run.id, repo: run.spec.repo_path }) +
                  "\n\n" +
                  t("The current branch, uncommitted files and the remote do not change. Merge and push stay with you.");
                if (window.confirm(msg)) void act("apply");
              }}
            >
              {t("Apply to the repository")}
            </button>
            <span className="small">
              Patch: <code>{patch || "–"}</code>
            </span>
            {patch && <CopyButton text={patch} label={t("Copy path")} />}
          </>
        )}
      </div>
    );
  }

  if (run.state === "abandoned") return <div className="decision-bar"><span className="muted">{t("Task abandoned.")}</span></div>;

  if (run.state === "awaiting_approval") {
    return (
      <div className="decision-bar ask">
        <span>{run.pending === "draft" ? t("Autopilot asks: answer with a message below, or approve the brief right away.") : t("The brief waits for approval.")}</span>
        <button type="button" onClick={() => void act("cancel")}>{t("Interrupt")}</button>
        {abandon}
      </div>
    );
  }

  if (run.state === "awaiting_external") {
    const handoff = latestOutput<HandoffOutput>(detail.steps, "handoff");
    const command = `cd "${run.workspace ?? ""}"; freebuff`;
    return (
      <div className="decision-bar ask column">
        <strong>{t("Waiting for Freebuff")}</strong>
        <ol className="small">
          <li>
            <button type="button" className="small" onClick={() => void act("open-terminal")}>
              {t("Open a terminal with Freebuff")}
            </button>{" "}
            {t("or by hand")} <code>{command}</code> <CopyButton text={command} />
          </li>
          <li>
            {t("Give it the brief")} <CopyButton text={handoff?.out.text ?? ""} label={t("Copy brief")} />. {t("Do not commit.")}
          </li>
          <li>{t("When it finishes, write below what it claims and click Done. Studio verifies everything itself.")}</li>
        </ol>
        <div className="actions">
          <button type="button" className="primary" onClick={() => void act("external-done", { note })}>
            {t("Done, verify")}
          </button>
          <button type="button" onClick={() => void act("cancel")}>{t("Interrupt")}</button>
          {abandon}
        </div>
      </div>
    );
  }

  if (run.state === "interrupted") {
    return (
      <div className="decision-bar ask">
        <span>{t("Interrupted in phase {phase}.", { phase: STEP_LABELS[run.phase ?? ""] ?? run.phase ?? "" })}</span>
        <button type="button" className="primary" onClick={() => void act("resume")}>
          {t("Continue")}
        </button>
        {implementable && <TakeoverButton run={run} act={act} note={note} />}
        {abandon}
      </div>
    );
  }

  // needs_decision
  const reason = run.decision_reason || t("No reason was given.");
  const sensitiveOverrideAllowed = ["draft", "setup"].includes(run.phase ?? "")
    || (run.phase === "work" && (run.implementer || run.spec.implementer) === "freebuff");
  const sensitiveWork = detail.steps.some((s) => s.kind === "work" && s.error === "sensitive");
  let buttons;
  switch (run.pending) {
    case "sensitive":
      buttons = !sensitiveOverrideAllowed || sensitiveWork ? (
        <>
        <span className="muted small">
          {t("Remove the sensitive data in the working copy and run the check again. Verification and review protection cannot be bypassed.")}
        </span>
        <button type="button" className="primary" onClick={() => void act("retry")}>{t("Check again")}</button>
        </>
      ) : (
        <>
          <button
            type="button"
            className="primary"
            onClick={() => {
              if (window.confirm(t("Do you confirm these files hold no secrets? Models will see them."))) {
                void act("allow-sensitive");
              }
            }}
          >
            {t("Not secret, continue")}
          </button>
          <button type="button" onClick={() => void act("retry")}>{t("Check again")}</button>
        </>
      );
      break;
    case "takeover":
      buttons = (
        <>
          <TakeoverButton run={run} act={act} note={note} />
          {run.phase === "work" && <button type="button" onClick={() => void act("keep-worker")}>{t("Keep the cheap model")}</button>}
        </>
      );
      break;
    case "self_review":
      buttons = (
        <>
          <button
            type="button"
            className="primary"
            onClick={() => {
              if (window.confirm(t("Only the model that wrote the change approved it. Accept?"))) void act("accept");
            }}
          >
            {t("Accept the change")}
          </button>
          <button
            type="button"
            title={t("The other model was paused after a limit. The review is retried with a model that did not write the change.")}
            onClick={() => void act("retry")}
          >
            {t("Retry the independent review")}
          </button>
          <button type="button" onClick={() => void act("rework", { note })}>{t("Send back for a fix")}</button>
        </>
      );
      break;
    case "draft":
    case "plan":
      buttons = <span className="muted small">{t("Answer with a message below; Autopilot then continues.")}</span>;
      break;
    default:
      buttons = (
        <>
          {/* Re-running verification on unchanged code cannot help; rework can. */}
          {run.phase !== "verify" && (
            <button type="button" className="primary" onClick={() => void act("retry")}>
              {t("Retry the phase")}
            </button>
          )}
          {implementable && (
            <button
              type="button"
              className={run.phase === "verify" ? "primary" : undefined}
              onClick={() => void act("rework", { note })}
            >
              {t("Send back for a fix")}
            </button>
          )}
          {implementable && <TakeoverButton run={run} act={act} note={note} />}
        </>
      );
  }
  return (
    <div className="decision-bar ask column">
      <strong>{t("Waiting for your decision")}</strong>
      <p className="decision-reason">{reason}</p>
      <div className="actions">
        {buttons}
        {abandon}
      </div>
    </div>
  );
}

const STAGE_HINTS: Record<StageState, string> = {
  todo: t("pending"),
  running: t("running now"),
  done: t("done"),
  failed: t("did not pass"),
  waiting: t("waiting for you"),
  skipped: t("skipped"),
};
const STAGE_MARKS: Record<StageState, string> = { todo: "○", running: "▶", done: "✓", failed: "!", waiting: "…", skipped: "–" };

interface Props {
  detail: RunDetail;
  live: boolean;
  onChanged: () => void;
  detailsOpen: boolean;
  onToggleDetails: () => void;
  onShowDiff: () => void;
}

export function Thread({ detail, live, onChanged, detailsOpen, onToggleDetails, onShowDiff }: Props) {
  const { run } = detail;
  const items = useMemo(() => buildThread(detail), [detail]);
  const draftId = latestDraftId(detail.messages);
  const lastMemoryId = detail.steps.filter((s) => s.kind === "memory").reduce((m, s) => Math.max(m, s.id), -1);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const actionPending = useRef(new Set<RunAction>());
  const replyPending = useRef(false);
  const bottom = useRef<HTMLDivElement>(null);
  const count = items.length;

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [count]);

  const act: Act = async (action, body = {}) => {
    if (actionPending.current.has(action)) return false;
    actionPending.current.add(action);
    setError(null);
    try {
      await api.runAction(run.id, action, body);
      if (["reply", "rework", "takeover", "external-done"].includes(action)) setText("");
      onChanged();
      return true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(e instanceof ApiError && e.status === 409 ? t("Not possible now: {msg}", { msg }) : msg);
      return false;
    } finally {
      actionPending.current.delete(action);
    }
  };

  async function send() {
    if (!text.trim() || replyPending.current) return;
    replyPending.current = true;
    setSending(true);
    await act("reply", { text });
    setSending(false);
    replyPending.current = false;
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void send();
    }
  }

  const closed = run.state === "accepted" || run.state === "abandoned";
  const stages = progress(detail);
  const spec = run.spec;

  return (
    <section className="thread" aria-label={t("Task progress")}>
      <header className="thread-head">
        <div className="thread-title">
          <div>
            <h1>{spec.title || t("New task")}</h1>
            <div className="thread-meta small">
              <StateBadge state={run.state} />
              <span className="muted">{describe(spec.orchestration)}</span>
              {run.escalations > 0 && <span className="badge warn">{t("taken over by a stronger model")}</span>}
              <span className={`live-dot${live ? " on" : ""}`} title={live ? t("Live connection") : t("No live connection")} />
            </div>
          </div>
          <div className="actions">
          {closed && (
            <button
              type="button"
              className="small"
              title={t("Opens a new task with the same brief, repository and criteria; nothing starts by itself.")}
              onClick={() => (window.location.hash = `#/new/from/${encodeURIComponent(run.id)}`)}
            >
              {t("Similar task")}
            </button>
          )}
          <button type="button" className="small" onClick={onShowDiff}>Diff</button>
          <button type="button" className="small" aria-expanded={detailsOpen} onClick={onToggleDetails}>
            {detailsOpen ? t("Hide details") : t("Details")}
          </button>
          </div>
        </div>
        <ol className="progress" aria-label={t("Progress")}>
          {stages.map((s) => (
            <li key={s.id} className={`stage ${s.state}`} title={STAGE_HINTS[s.state]} aria-current={s.state === "running" || s.state === "waiting" ? "step" : undefined}>
              <span>
                <span aria-hidden="true">{STAGE_MARKS[s.state]} </span>
                {s.label}
                <span className="sr-only">: {STAGE_HINTS[s.state]}</span>
              </span>
            </li>
          ))}
        </ol>
      </header>

      <div className="thread-body">
        {items.map((it) =>
          it.type === "message" ? (
            <MessageBubble
              key={`m${it.message.id}`}
              message={it.message}
              draftEditable={run.state === "awaiting_approval" && it.message.id === draftId}
              act={act}
            />
          ) : (
            <StepCard
              key={`s${it.step.id}`}
              step={it.step}
              agent={it.agent}
              act={act}
              latest={it.step.id === lastMemoryId}
            />
          ),
        )}
        <div ref={bottom} />
      </div>

      <footer className="thread-foot">
        <DecisionBar detail={detail} act={act} note={text} />
        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}
        {!closed && (
          <div className="reply">
            <textarea
              rows={2}
              aria-label={t("Message for Autopilot")}
              placeholder={t("Write to Autopilot: an answer, a note for the fix or an instruction. (Ctrl+Enter sends)")}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
            />
            <button type="button" className="primary" disabled={sending || !text.trim()} onClick={() => void send()}>
              {t("Send")}
            </button>
          </div>
        )}
      </footer>
    </section>
  );
}
