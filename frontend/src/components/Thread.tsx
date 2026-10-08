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
import { actorLabel, describe, lines } from "../options";
import { actorLead, buildThread, latestDraftId, progress, type StageState } from "../thread";
import { CopyButton, ExitBadge, latestOutput, List } from "./bits";
import { StateBadge } from "./StateBadge";

type Act = (action: RunAction, body?: ActionBody) => Promise<boolean>;

const AUTHOR_LABELS: Record<string, string> = {
  user: "Ty",
  claude: "Claude",
  codex: "Codex",
  autopilot: "Autopilot",
  studio: "Studio",
};
const AVATAR_LETTERS: Record<string, string> = { user: "T", studio: "S", claude: "C", codex: "X", autopilot: "A" };

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
  const t = text.trim();
  if (!t.startsWith("{")) return text;
  try {
    const obj = JSON.parse(t) as Record<string, unknown>;
    for (const key of ["summary", "notes", "goal", "title"]) {
      if (typeof obj[key] === "string" && obj[key]) return obj[key] as string;
    }
  } catch {
    /* not JSON after all */
  }
  return `strukturovaná odpověď (${t.length} znaků)`;
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
  if (events.length === 0) return running ? <p className="muted small thinking">Agent přemýšlí…</p> : null;
  const shown = events.slice(-300);
  const tools = events.filter((e) => e.kind === "tool").length;
  return (
    <details className="agent-stream" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="small muted">
        Průběh agenta · {events.length} záznamů{tools ? ` · ${tools} nástrojů` : ""}
        {running && <span className="pulse" aria-label="běží" />}
      </summary>
      <div className="agent-log">
        {events.length > shown.length && <div className="muted small">… starší záznamy skryty</div>}
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
        Složitost: {plan.complexity || "–"} · doporučený implementátor: {plan.recommended_implementer || "–"}
      </p>
      {plan.files_to_touch?.length > 0 && (
        <details>
          <summary className="small">Soubory ({plan.files_to_touch.length})</summary>
          <List items={plan.files_to_touch} mono />
        </details>
      )}
      {plan.risks?.length > 0 && (
        <details>
          <summary className="small">Rizika ({plan.risks.length})</summary>
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
              {f.fix && <div className="muted small">Oprava: {f.fix}</div>}
            </li>
          ))}
        </ul>
      )}
      {review.criteria?.length > 0 && (
        <ul className="criteria">
          {review.criteria.map((c, i) => (
            <li key={i} className={c.met ? "met" : "unmet"}>
              <span className="mark" aria-label={c.met ? "splněno" : "nesplněno"}>
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
      Navrhnout znovu
    </button>
  );
  if (proposals.length === 0)
    return (
      <div className="actions">
        <span className="muted small">Autopilot nenavrhl nic k zápisu.</span>
        {latest && again}
      </div>
    );

  return (
    <>
      <p className="small muted">
        {out.author === "studio"
          ? "Model návrh nedodal, Studio ho sestavilo z ověřených faktů běhu. "
          : "Návrh od Autopilota. "}
        Zapíše se do Obsidianu až po kliknutí, jen připojením na konec poznámky nebo jako nová předávka. Nic se
        nepřepisuje.
      </p>
      {proposals.map((p, i) => (
        <div key={i} className={`proposal status-${p.status}`}>
          <div className="small">
            <strong>{p.kind === "append" ? "Doplnit poznámku projektu" : "Nová předávka"}</strong>{" "}
            <code>{p.path}</code>
          </div>
          <details open={p.status === "proposed" && latest}>
            <summary className="small">Text</summary>
            <pre className="output">{p.content}</pre>
          </details>
          {p.status === "proposed" && !latest ? (
            // The server acts on the latest memory step only; an older proposal is superseded.
            <span className="badge">nahrazeno novějším návrhem</span>
          ) : p.status === "proposed" ? (
            <div className="actions">
              <button type="button" className="primary small" disabled={busy !== null} onClick={() => void run("vault-write", i)}>
                Zapsat
              </button>
              <button type="button" className="small" disabled={busy !== null} onClick={() => void run("vault-discard", i)}>
                Zahodit
              </button>
            </div>
          ) : (
            <span className={`badge ${p.status === "written" ? "good" : "warn"}`}>
              {p.status === "written" ? "zapsáno" : "zahozeno"}
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
            {v.passed ? <span className="badge good">prošlo</span> : <span className="badge bad">neprošlo</span>}
            {v.empty_diff && <span className="badge warn">prázdný diff</span>}
            <span className="muted small">{v.changed_files?.length ?? 0} změněných souborů</span>
          </p>
          {v.out_of_scope?.length > 0 && (
            <div className="alert warn">
              Mimo povolený rozsah: <List items={v.out_of_scope} mono />
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
          {w.claim && <p className="small"><span className="badge warn">neověřené tvrzení</span> {w.claim}</p>}
          {w.summary && <p className="prewrap">{w.summary}</p>}
          {files.length > 0 && (
            <details>
              <summary className="small">Soubory podle implementátora ({files.length})</summary>
              <List items={files} mono />
            </details>
          )}
          {w.concerns && w.concerns.length > 0 && (
            <div className="alert warn small">
              Obavy implementátora: <List items={w.concerns} />
            </div>
          )}
          {w.detail && <p className="muted small prewrap">{w.detail}</p>}
        </>
      );
    }
    case "setup":
      return <p className="small muted">Pracovní kopie: <code>{String(out.workspace ?? "")}</code></p>;
    case "handoff": {
      const h = out as unknown as HandoffOutput;
      return h.text ? (
        <details>
          <summary className="small">Zadání pro {h.agent}</summary>
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
  if (actor.endsWith(":merge")) return "Sloučený plán";
  if (actor.startsWith("lead:")) return `${label} · implementuje ${actorLabel(actor.slice(5))}`;
  if (actor.startsWith("external:")) return `${label} · ${actor.slice(9)} (ručně)`;
  const lead = actorLead(actor);
  if (lead) return `${label} · ${actorLabel(lead)}`;
  return actor && actor !== "studio" ? `${label} · ${actor}` : label;
}

/** `latest`: the newest step of its kind; only that one offers "Navrhnout znovu". */
function StepCard({ step, agent, act, latest }: { step: Step; agent: AgentEvent[]; act: Act; latest: boolean }) {
  const lead = actorLead(step.actor);
  const running = step.status === "running";
  const dur = step.started_at ? formatDuration((step.finished_at ?? nowSec()) - step.started_at) : "";
  return (
    <div className={`step-card kind-${step.kind} status-${step.status}${lead ? ` by-${lead}` : ""}`}>
      <div className="step-head">
        <span className={`step-dot ${step.status}`} aria-hidden="true" />
        <strong>{stepTitle(step)}</strong>
        {step.iteration > 1 && <span className="muted small">{step.iteration}. pokus</span>}
        <span className={`badge step-status-${step.status}`}>{STEP_STATUS_LABELS[step.status] ?? step.status}</span>
        <span className="muted small" title={absoluteTime(step.started_at)}>
          {dur}
        </span>
      </div>
      {step.error && <p className="error small">{step.error}</p>}
      {latest && step.kind === "memory" && step.status === "failed" && (
        <div className="actions">
          <button type="button" className="small" onClick={() => void act("memory-retry")}>
            Navrhnout znovu
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
          <dt>Rozsah</dt>
          <dd><List items={draft.allowed_paths} mono /></dd>
          <dt>Kritéria</dt>
          <dd><List items={draft.acceptance_criteria} /></dd>
          <dt>Ověření</dt>
          <dd><List items={draft.acceptance_commands} mono /></dd>
        </dl>
      </div>
    );
  }
  return (
    <div className="draft-card">
      <div className="draft-head">
        <strong>Návrh zadání</strong>
        <span className="muted small">Uprav, co nesedí, a schval. Na otázky odpověz zprávou dole.</span>
      </div>
      {hasQuestions && (
        <div className="alert warn">
          <strong>Autopilot se ptá:</strong>
          <List items={draft.questions} />
        </div>
      )}
      <label className="field">
        <span>Název</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="field-row">
        <label className="field">
          <span>Povolené cesty</span>
          <textarea rows={4} value={paths} spellCheck={false} onChange={(e) => setPaths(e.target.value)} />
        </label>
        <label className="field">
          <span>Akceptační kritéria</span>
          <textarea rows={4} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
        </label>
        <label className="field">
          <span>Příkazy k ověření</span>
          <textarea rows={4} value={commands} spellCheck={false} onChange={(e) => setCommands(e.target.value)} />
        </label>
      </div>
      {invalid.length > 0 && (
        <div className="alert error">
          Tyto příkazy Studio nespustí, uprav je: <List items={invalid} mono />
        </div>
      )}
      <div className="actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void approve()}>
          {hasQuestions ? "Schválit i bez odpovědí" : "Schválit a plánovat"}
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
            {new Date(message.at * 1000).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })}
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
  return `${n} ${n === 1 ? "soubor" : n >= 2 && n <= 4 ? "soubory" : "souborů"}`;
}

/** Takeover: Autopilot (a stronger free model) writes the code itself. Claude/Codex are not
 * offered (ADR 0004). */
function TakeoverButton({ act, note }: { act: Act; note: string }) {
  return (
    <button type="button" onClick={() => void act("takeover", { lead: AUTOPILOT, note })}>
      Předat silnějšímu modelu
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
        if (window.confirm("Opravdu opustit tento úkol? Nelze vrátit.")) void act("abandon");
      }}
    >
      Opustit
    </button>
  );

  if (isActive(run.state)) {
    return (
      <div className="decision-bar working">
        <span className="pulse" aria-hidden="true" />
        <span>
          {run.cancel_requested ? "Zastavuji…" : `Pracuje · ${STEP_LABELS[run.phase ?? ""] ?? run.phase ?? ""}`}
        </span>
        <button type="button" className="danger" disabled={!!run.cancel_requested} onClick={() => void act("cancel")}>
          Přerušit
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
        <span className="badge good">Přijato</span>
        {applied ? (
          <>
            <span className="small">
              Ve větvi <code>{applied.branch}</code> ({files(applied.files.length)})
            </span>
            <CopyButton text={`git merge ${applied.branch}`} label="Kopírovat merge" />
            <span className="muted small">Merge a push děláš sám.</span>
          </>
        ) : (
          <>
            <button
              type="button"
              className="primary"
              onClick={() => {
                const msg =
                  `Vytvořit v ${run.spec.repo_path} novou větev studio/${run.id} s touto změnou?\n\n` +
                  "Aktuální větev, rozpracované soubory ani remote se nezmění. Merge a push zůstávají na tobě.";
                if (window.confirm(msg)) void act("apply");
              }}
            >
              Použít do repozitáře
            </button>
            <span className="small">
              Patch: <code>{patch || "–"}</code>
            </span>
            {patch && <CopyButton text={patch} label="Kopírovat cestu" />}
          </>
        )}
      </div>
    );
  }

  if (run.state === "abandoned") return <div className="decision-bar"><span className="muted">Úkol opuštěn.</span></div>;

  if (run.state === "awaiting_approval") {
    return (
      <div className="decision-bar ask">
        <span>{run.pending === "draft" ? "Autopilot se ptá – odpověz zprávou dole, nebo návrh rovnou schval." : "Návrh zadání čeká na schválení."}</span>
        <button type="button" onClick={() => void act("cancel")}>Přerušit</button>
        {abandon}
      </div>
    );
  }

  if (run.state === "awaiting_external") {
    const handoff = latestOutput<HandoffOutput>(detail.steps, "handoff");
    const command = `cd "${run.workspace ?? ""}"; freebuff`;
    return (
      <div className="decision-bar ask column">
        <strong>Čeká na Freebuff</strong>
        <ol className="small">
          <li>
            <button type="button" className="small" onClick={() => void act("open-terminal")}>
              Otevřít terminál s Freebuffem
            </button>{" "}
            nebo ručně <code>{command}</code> <CopyButton text={command} />
          </li>
          <li>
            Vlož mu zadání <CopyButton text={handoff?.out.text ?? ""} label="Kopírovat zadání" />. Necommituj.
          </li>
          <li>Až skončí, napiš dole, co tvrdí, a klikni Hotovo. Studio vše ověří samo.</li>
        </ol>
        <div className="actions">
          <button type="button" className="primary" onClick={() => void act("external-done", { note })}>
            Hotovo, ověř
          </button>
          <button type="button" onClick={() => void act("cancel")}>Přerušit</button>
          {abandon}
        </div>
      </div>
    );
  }

  if (run.state === "interrupted") {
    return (
      <div className="decision-bar ask">
        <span>Přerušeno ve fázi {STEP_LABELS[run.phase ?? ""] ?? run.phase}.</span>
        <button type="button" className="primary" onClick={() => void act("resume")}>
          Pokračovat
        </button>
        {implementable && <TakeoverButton act={act} note={note} />}
        {abandon}
      </div>
    );
  }

  // needs_decision
  const reason = run.decision_reason || "Důvod nebyl uveden.";
  let buttons;
  switch (run.pending) {
    case "sensitive":
      buttons = (
        <>
          <button
            type="button"
            className="primary"
            onClick={() => {
              if (window.confirm("Potvrzuješ, že tyto soubory neobsahují tajné údaje? Uvidí je modely.")) {
                void act("allow-sensitive");
              }
            }}
          >
            Nejsou tajné, pokračovat
          </button>
          <button type="button" onClick={() => void act("retry")}>Zkontrolovat znovu</button>
        </>
      );
      break;
    case "takeover":
      buttons = (
        <>
          <TakeoverButton act={act} note={note} />
          {run.phase === "work" && <button type="button" onClick={() => void act("keep-worker")}>Ponechat levný model</button>}
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
              if (window.confirm("Změnu schválil jen model, který ji napsal. Přijmout?")) void act("accept");
            }}
          >
            Přijmout změnu
          </button>
          <button
            type="button"
            title="Jiný model byl v pauze po limitu. Recenze se zkusí znovu s modelem, který změnu nepsal."
            onClick={() => void act("retry")}
          >
            Zkusit nezávislou recenzi znovu
          </button>
          <button type="button" onClick={() => void act("rework", { note })}>Vrátit k opravě</button>
        </>
      );
      break;
    case "draft":
    case "plan":
      buttons = <span className="muted small">Odpověz zprávou dole, Autopilot pak pokračuje.</span>;
      break;
    default:
      buttons = (
        <>
          {/* Re-running verification on unchanged code cannot help; rework can. */}
          {run.phase !== "verify" && (
            <button type="button" className="primary" onClick={() => void act("retry")}>
              Zkusit fázi znovu
            </button>
          )}
          {implementable && (
            <button
              type="button"
              className={run.phase === "verify" ? "primary" : undefined}
              onClick={() => void act("rework", { note })}
            >
              Vrátit k opravě
            </button>
          )}
          {implementable && <TakeoverButton act={act} note={note} />}
        </>
      );
  }
  return (
    <div className="decision-bar ask column">
      <strong>Čeká na tvoje rozhodnutí</strong>
      <p className="decision-reason">{reason}</p>
      <div className="actions">
        {buttons}
        {abandon}
      </div>
    </div>
  );
}

const STAGE_HINTS: Record<StageState, string> = {
  todo: "čeká",
  running: "právě běží",
  done: "hotovo",
  failed: "neprošlo",
  waiting: "čeká na tebe",
  skipped: "přeskočeno",
};

interface Props {
  detail: RunDetail;
  live: boolean;
  onChanged: () => void;
  detailsOpen: boolean;
  onToggleDetails: () => void;
}

export function Thread({ detail, live, onChanged, detailsOpen, onToggleDetails }: Props) {
  const { run } = detail;
  const items = useMemo(() => buildThread(detail), [detail]);
  const draftId = latestDraftId(detail.messages);
  const lastMemoryId = detail.steps.filter((s) => s.kind === "memory").reduce((m, s) => Math.max(m, s.id), -1);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const count = items.length;

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [count]);

  const act: Act = async (action, body = {}) => {
    setError(null);
    try {
      await api.runAction(run.id, action, body);
      if (["reply", "rework", "takeover", "external-done"].includes(action)) setText("");
      onChanged();
      return true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(e instanceof ApiError && e.status === 409 ? `Teď nelze: ${msg}` : msg);
      return false;
    }
  };

  async function send() {
    if (!text.trim()) return;
    setSending(true);
    await act("reply", { text });
    setSending(false);
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
    <section className="thread" aria-label="Průběh úkolu">
      <header className="thread-head">
        <div className="thread-title">
          <div>
            <h1>{spec.title || "Nový úkol"}</h1>
            <div className="thread-meta small">
              <StateBadge state={run.state} />
              <span className="muted">{describe(spec.orchestration)}</span>
              {run.escalations > 0 && <span className="badge warn">převzal silnější model</span>}
              <span className={`live-dot${live ? " on" : ""}`} title={live ? "Živé spojení" : "Bez živého spojení"} />
            </div>
          </div>
          <button type="button" className="small" aria-expanded={detailsOpen} onClick={onToggleDetails}>
            {detailsOpen ? "Skrýt podrobnosti" : "Podrobnosti"}
          </button>
        </div>
        <ol className="progress" aria-label="Průběh">
          {stages.map((s) => (
            <li key={s.id} className={`stage ${s.state}`} title={STAGE_HINTS[s.state]}>
              <span>
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
              aria-label="Zpráva pro Autopilota"
              placeholder="Napiš Autopilotovi – odpověď, poznámku k opravě nebo instrukci. (Ctrl+Enter odešle)"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
            />
            <button type="button" className="primary" disabled={sending || !text.trim()} onClick={() => void send()}>
              Odeslat
            </button>
          </div>
        )}
      </footer>
    </section>
  );
}
