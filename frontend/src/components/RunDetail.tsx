import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  api,
  ApiError,
  isActive,
  type HandoffOutput,
  type ModelCall,
  type ReviewOutput,
  type RunAction,
  type RunDetail as RunDetailData,
  type Step,
  type VerifyOutput,
  type WorkOutput,
} from "../api";
import {
  absoluteTime,
  formatCost,
  formatDuration,
  formatTokens,
  nowSec,
  STEP_LABELS,
  STEP_STATUS_LABELS,
  VERDICT_LABELS,
} from "../format";
import { DiffView } from "./DiffView";
import { StateBadge } from "./StateBadge";

interface Props {
  runId: string;
  /** updated_at from the run list; a change triggers a reload when not already polling. */
  listUpdatedAt?: number;
  onChanged: () => void;
}

/** Latest step of a kind that has an object output (steps arrive in chronological order). */
function latestOutput<T>(steps: Step[], kind: Step["kind"]): { step: Step; out: T } | null {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s.kind === kind && s.output && typeof s.output === "object") return { step: s, out: s.output as T };
  }
  return null;
}

function Section({ title, children, id }: { title: string; children: ReactNode; id?: string }) {
  return (
    <section className="card" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {children}
    </section>
  );
}

function List({ items, mono }: { items: string[] | undefined | null; mono?: boolean }) {
  if (!items || items.length === 0) return <p className="muted">–</p>;
  return (
    <ul className={mono ? "mono-list" : undefined}>
      {items.map((it, i) => (
        <li key={i}>{mono ? <code>{it}</code> : it}</li>
      ))}
    </ul>
  );
}

function ExitBadge({ code, timedOut }: { code: number | null; timedOut: boolean }) {
  if (timedOut) return <span className="badge bad">timeout</span>;
  if (code === 0) return <span className="badge good">exit 0 · prošlo</span>;
  if (code == null) return <span className="badge neutral">bez exit kódu</span>;
  return <span className="badge bad">exit {code} · selhalo</span>;
}

const BIG_PANEL: string[] = ["needs_decision", "awaiting_external"];

function ActionPanel({ detail, onDone }: { detail: RunDetailData; onDone: () => void }) {
  const { run } = detail;
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<RunAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(action: RunAction) {
    if (action === "abandon" && !window.confirm("Opravdu opustit tento běh? Nelze vrátit.")) return;
    setBusy(action);
    setError(null);
    try {
      const withNote = action === "rework" || action === "external-done";
      await api.runAction(run.id, action, withNote ? note : undefined);
      setNote("");
      onDone();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(e instanceof ApiError && e.status === 409 ? `Akci teď nelze provést: ${msg}` : msg);
    } finally {
      setBusy(null);
    }
  }

  const err = error && (
    <p className="error" role="alert">
      {error}
    </p>
  );

  if (isActive(run.state)) {
    return (
      <div className="actions">
        <button type="button" className="danger" disabled={!!busy || run.cancel_requested} onClick={() => act("cancel")}>
          {run.cancel_requested ? "Přerušení vyžádáno…" : "Přerušit"}
        </button>
        {err}
      </div>
    );
  }

  if (run.state === "interrupted") {
    return (
      <div className="actions">
        <button type="button" className="primary" disabled={!!busy} onClick={() => act("resume")}>
          Pokračovat
        </button>
        <button type="button" className="danger" disabled={!!busy} onClick={() => act("abandon")}>
          Opustit
        </button>
        {err}
      </div>
    );
  }

  if (run.state === "awaiting_external") {
    const handoff = latestOutput<HandoffOutput>(detail.steps, "handoff");
    const command = `cd "${run.workspace ?? ""}"; freebuff`;
    return (
      <div className="decision" role="region" aria-label="Externí agent">
        <h2>Čeká na Freebuff</h2>
        <ol>
          <li>
            <button type="button" disabled={!!busy} onClick={() => act("open-terminal")}>
              Otevřít terminál s Freebuffem
            </button>{" "}
            nebo spusť ručně: <code>{command}</code> <CopyButton text={command} />
          </li>
          <li>
            Vlož mu zadání níže <CopyButton text={handoff?.out.text ?? ""} />. Necommituj, změny nech v
            pracovní kopii.
          </li>
          <li>Až skončí, klikni Hotovo. Studio pak samo ověří testy a rozsah a pustí review.</li>
        </ol>
        {handoff && (
          <details open>
            <summary>Zadání pro agenta</summary>
            <pre className="output">{handoff.out.text}</pre>
          </details>
        )}
        <div className="field">
          <label htmlFor="external-note">Co agent tvrdí, že udělal (nepovinné)</label>
          <textarea id="external-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          <p className="hint">Reviewer to dostane jen jako neověřené tvrzení.</p>
        </div>
        <div className="actions">
          <button type="button" className="primary" disabled={!!busy} onClick={() => act("external-done")}>
            Hotovo, ověř
          </button>
          <button type="button" disabled={!!busy} onClick={() => act("cancel")}>
            Přerušit
          </button>
          <button type="button" className="danger" disabled={!!busy} onClick={() => act("abandon")}>
            Opustit
          </button>
        </div>
        {err}
      </div>
    );
  }

  if (run.state === "needs_decision") {
    return (
      <div className="decision" role="region" aria-label="Rozhodnutí">
        <h2>Čeká na tvoje rozhodnutí</h2>
        <p className="decision-reason">{run.decision_reason || "Důvod nebyl uveden."}</p>
        <div className="field">
          <label htmlFor="rework-note">Poznámka pro opravu (nepovinné)</label>
          <textarea id="rework-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          <p className="hint">Předá se workeru spolu s nálezy z review.</p>
        </div>
        <div className="actions">
          <button type="button" className="primary" disabled={!!busy} onClick={() => act("rework")}>
            Vrátit k opravě
          </button>
          <button type="button" className="danger" disabled={!!busy} onClick={() => act("abandon")}>
            Opustit
          </button>
        </div>
        {err}
      </div>
    );
  }
  return null;
}

function CopyButton({ text }: { text: string }) {
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
      {copied ? "Zkopírováno" : "Kopírovat"}
    </button>
  );
}

function Timeline({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return <p className="muted">Zatím žádné kroky.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Krok</th>
            <th>Iterace</th>
            <th>Kdo</th>
            <th>Stav</th>
            <th>Trvání</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s) => (
            <tr key={s.id} className={`step-${s.status}`}>
              <td>{STEP_LABELS[s.kind] ?? s.kind}</td>
              <td>{s.iteration}</td>
              <td>{s.actor ?? "–"}</td>
              <td>
                <span className={`badge step-status-${s.status}`}>{STEP_STATUS_LABELS[s.status] ?? s.status}</span>
                {s.error && <div className="error small">{s.error}</div>}
              </td>
              <td title={absoluteTime(s.started_at)}>
                {s.started_at ? formatDuration((s.finished_at ?? nowSec()) - s.started_at) : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Evidence({ verify, work }: { verify: VerifyOutput | null; work: WorkOutput | null }) {
  return (
    <div className="evidence">
      <div className="evidence-block verified">
        <h3>Nezávislé ověření (Studio)</h3>
        {!verify ? (
          <p className="muted">Ověření zatím neproběhlo.</p>
        ) : (
          <>
            <p>
              Výsledek:{" "}
              {verify.passed ? <span className="badge good">prošlo</span> : <span className="badge bad">neprošlo</span>}
              {verify.empty_diff && <span className="badge warn">prázdný diff</span>}
            </p>
            {verify.out_of_scope?.length > 0 && (
              <div className="alert warn">
                <strong>Změny mimo povolené cesty:</strong>
                <List items={verify.out_of_scope} mono />
              </div>
            )}
            {(verify.commands ?? []).map((c, i) => (
              <details key={i} className="cmd" open={c.exit_code !== 0 || c.timed_out}>
                <summary>
                  <code>{c.command}</code> <ExitBadge code={c.exit_code} timedOut={c.timed_out} />{" "}
                  <span className="muted small">{formatDuration(c.seconds)}</span>
                </summary>
                <pre className="output">{c.output || "(bez výstupu)"}</pre>
              </details>
            ))}
            {verify.changed_files?.length > 0 && (
              <details>
                <summary>Změněné soubory ({verify.changed_files.length})</summary>
                <List items={verify.changed_files} mono />
              </details>
            )}
          </>
        )}
      </div>

      <div className="evidence-block claimed">
        <h3>Tvrzení workeru (neověřeno)</h3>
        {!work ? (
          <p className="muted">Worker zatím nic neodevzdal.</p>
        ) : (
          <>
            {work.claim ? (
              <p>
                <span className="badge warn">externí agent</span> {work.claim}
              </p>
            ) : (
              <p>
                Stav: <strong>{work.status}</strong> · tahů: {work.turns}
              </p>
            )}
            {work.summary && <p className="prewrap">{work.summary}</p>}
            {work.claimed_checks?.length > 0 ? (
              <ul className="claims">
                {work.claimed_checks.map((c, i) => (
                  <li key={i}>
                    <code>{c.command}</code> <ExitBadge code={c.exit_code} timedOut={c.timed_out} />{" "}
                    <span className="muted small">{formatDuration(c.seconds)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Worker neuvádí žádné vlastní kontroly.</p>
            )}
            {work.files_written?.length > 0 && (
              <details>
                <summary>Zapsané soubory ({work.files_written.length})</summary>
                <List items={work.files_written} mono />
              </details>
            )}
            {work.detail && (
              <details>
                <summary>Detail</summary>
                <pre className="output">{work.detail}</pre>
              </details>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Review({ review }: { review: ReviewOutput }) {
  return (
    <>
      <p>
        <span className={`badge verdict-${review.verdict}`}>{VERDICT_LABELS[review.verdict] ?? review.verdict}</span>
      </p>
      {review.summary && <p className="prewrap">{review.summary}</p>}
      <h3>Nálezy</h3>
      {review.findings?.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Závažnost</th>
                <th>Soubor</th>
                <th>Problém</th>
                <th>Oprava</th>
              </tr>
            </thead>
            <tbody>
              {review.findings.map((f, i) => (
                <tr key={i}>
                  <td>
                    <span className={`badge sev-${f.severity}`}>{f.severity}</span>
                  </td>
                  <td>
                    <code>{f.file}</code>
                  </td>
                  <td>{f.problem}</td>
                  <td>{f.fix}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">Žádné nálezy.</p>
      )}
      <h3>Kritéria</h3>
      {review.criteria?.length ? (
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
      ) : (
        <p className="muted">Bez hodnocení kritérií.</p>
      )}
    </>
  );
}

function Models({ calls }: { calls: ModelCall[] }) {
  if (calls.length === 0) return <p className="muted">Zatím žádná volání.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Role</th>
            <th>Požadovaný model</th>
            <th>Skutečný model</th>
            <th>Provider</th>
            <th className="num">Tokeny in/out</th>
            <th className="num">Cena</th>
            <th className="num">Latence</th>
            <th>Výsledek</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => {
            const mismatch = !!c.actual_model && c.actual_model !== c.requested_model;
            const failed = c.outcome !== "ok";
            return (
              <tr key={c.id} className={failed ? "row-bad" : mismatch ? "row-warn" : undefined}>
                <td>{c.role}</td>
                <td>
                  <code>{c.requested_model}</code>
                </td>
                <td>
                  <code className={mismatch ? "warn-text" : undefined}>{c.actual_model ?? "–"}</code>
                  {mismatch && <span className="badge warn">jiný model</span>}
                </td>
                <td>{c.provider ?? "–"}</td>
                <td className="num">
                  {formatTokens(c.tokens_in)} / {formatTokens(c.tokens_out)}
                </td>
                <td className="num">{formatCost(c.cost_usd)}</td>
                <td className="num">{c.latency_ms != null ? `${Math.round(c.latency_ms)} ms` : "–"}</td>
                <td>
                  <span className={`badge ${failed ? "bad" : "good"}`}>{c.outcome}</span>
                  {c.error && <div className="error small">{c.error}</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function RunDetail({ runId, listUpdatedAt, onChanged }: Props) {
  const [detail, setDetail] = useState<RunDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const alive = useRef(true);

  const load = useCallback(async () => {
    window.clearTimeout(timer.current);
    try {
      const d = await api.getRun(runId);
      if (!alive.current) return;
      setDetail(d);
      setError(null);
      if (isActive(d.run.state)) timer.current = window.setTimeout(load, 2000);
    } catch (e) {
      if (!alive.current) return;
      setError(e instanceof Error ? e.message : String(e));
      timer.current = window.setTimeout(load, 2000);
    }
  }, [runId]);

  useEffect(() => {
    alive.current = true;
    setDetail(null);
    setError(null);
    void load();
    return () => {
      alive.current = false;
      window.clearTimeout(timer.current);
    };
  }, [load]);

  // The run list polls every 3 s; if it reports a newer update for a run we are not polling, reload.
  const polling = detail ? isActive(detail.run.state) : true;
  const knownUpdatedAt = detail?.run.updated_at;
  useEffect(() => {
    if (!polling && listUpdatedAt && knownUpdatedAt && listUpdatedAt > knownUpdatedAt) void load();
  }, [listUpdatedAt, knownUpdatedAt, polling, load]);

  const afterAction = useCallback(() => {
    void load();
    onChanged();
  }, [load, onChanged]);

  if (!detail) {
    return error ? (
      <p className="alert error" role="alert">
        Běh nelze načíst: {error}
      </p>
    ) : (
      <p className="muted">Načítám běh…</p>
    );
  }

  const { run, steps, events, model_calls, usage } = detail;
  const spec = run.spec;
  const maxIter = spec.budget?.max_iterations;
  const verify = latestOutput<VerifyOutput>(steps, "verify");
  const work = latestOutput<WorkOutput>(steps, "work");
  const review = latestOutput<ReviewOutput>(steps, "review");
  const elapsed = (run.finished_at ?? nowSec()) - run.created_at;

  return (
    <article className="run-detail">
      <header className="run-header card">
        <h1>{spec.title}</h1>
        <div className="run-header-meta">
          <StateBadge state={run.state} />
          {run.phase && <span className="muted">fáze: {run.phase}</span>}
          <span>
            Iterace {run.iteration}
            {maxIter ? ` / ${maxIter}` : ""}
          </span>
          <span title={`Vytvořeno ${absoluteTime(run.created_at)}`}>Čas: {formatDuration(elapsed)}</span>
          <span>
            {formatTokens(usage.tokens)} tokenů · {formatCost(usage.cost)} · {usage.calls} volání
          </span>
        </div>
        {error && <p className="error small">Obnovení selhalo: {error}</p>}
        {!BIG_PANEL.includes(run.state) && <ActionPanel detail={detail} onDone={afterAction} />}
      </header>

      {BIG_PANEL.includes(run.state) && <ActionPanel detail={detail} onDone={afterAction} />}

      <Section title="Zadání" id="sec-spec">
        <p className="prewrap">{spec.goal}</p>
        <dl className="spec">
          <dt>Repozitář</dt>
          <dd>
            <code>{spec.repo_path}</code>
          </dd>
          <dt>Akceptační kritéria</dt>
          <dd>
            <List items={spec.acceptance_criteria} />
          </dd>
          <dt>Rozsah (povolené cesty)</dt>
          <dd>
            <List items={spec.allowed_paths} mono />
          </dd>
          <dt>Akceptační příkazy</dt>
          <dd>
            <List items={spec.acceptance_commands} mono />
          </dd>
          <dt>Planner / reviewer</dt>
          <dd>
            {spec.planner} / {spec.reviewer}
          </dd>
          {run.workspace && (
            <>
              <dt>Workspace</dt>
              <dd>
                <code>{run.workspace}</code>
                {run.source_commit && (
                  <span className="muted small"> @ {run.source_commit.slice(0, 10)}</span>
                )}
              </dd>
            </>
          )}
          {run.feedback && run.feedback.length > 0 && (
            <>
              <dt>Zpětná vazba pro další iteraci</dt>
              <dd>
                <List items={run.feedback} />
              </dd>
            </>
          )}
        </dl>
      </Section>

      {run.plan && (
        <Section title="Plán" id="sec-plan">
          <p className="prewrap">{run.plan.summary}</p>
          <p className="small muted">
            Složitost: {run.plan.complexity || "–"} · doporučený implementátor:{" "}
            {run.plan.recommended_implementer || "–"}
          </p>
          <h3>Kroky</h3>
          {run.plan.steps?.length ? (
            <ol>
              {run.plan.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          ) : (
            <p className="muted">–</p>
          )}
          <h3>Soubory</h3>
          <List items={run.plan.files_to_touch} mono />
          <h3>Rizika</h3>
          <List items={run.plan.risks} />
        </Section>
      )}

      <Section title="Průběh" id="sec-steps">
        <Timeline steps={steps} />
      </Section>

      <Section title="Diff" id="sec-diff">
        {verify ? (
          <>
            <p className="small muted">Z posledního ověření (iterace {verify.step.iteration}).</p>
            <DiffView diff={verify.out.diff ?? ""} />
          </>
        ) : (
          <p className="muted">Diff bude k dispozici po prvním ověření.</p>
        )}
      </Section>

      <Section title="Důkazy" id="sec-evidence">
        <Evidence verify={verify?.out ?? null} work={work?.out ?? null} />
      </Section>

      <Section title="Review" id="sec-review">
        {review ? <Review review={review.out} /> : <p className="muted">Review zatím neproběhlo.</p>}
      </Section>

      <Section title="Modely" id="sec-models">
        <Models calls={model_calls} />
      </Section>

      <section className="card">
        <details>
          <summary className="h2-summary">Události ({events.length})</summary>
          {events.length === 0 ? (
            <p className="muted">Žádné události.</p>
          ) : (
            <ol className="events">
              {events.map((ev) => (
                <li key={ev.id}>
                  <span className="muted small mono">{absoluteTime(ev.at)}</span>{" "}
                  <span className="tag">{ev.kind}</span> {ev.message}
                  {ev.data != null && !(typeof ev.data === "object" && Object.keys(ev.data as object).length === 0) && (
                    <details>
                      <summary className="small">data</summary>
                      <pre className="output">{JSON.stringify(ev.data, null, 2)}</pre>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          )}
        </details>
      </section>
    </article>
  );
}
