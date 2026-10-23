import { useEffect, useRef, useState } from "react";
import type { ModelCall, ReviewOutput, RunDetail, Step, Usage, VerifyOutput } from "../api";
import { absoluteTime, formatCost, formatDuration, formatTokens } from "../format";
import { t } from "../i18n";
import { actorLabel } from "../options";
import { actorLead } from "../thread";
import { ExitBadge, latestOutput, List } from "./bits";
import { DiffView } from "./DiffView";
import { ReviewBody } from "./Thread";

type Tab = "diff" | "tests" | "review" | "models" | "events" | "spec";
const TABS: { id: Tab; label: string }[] = [
  { id: "diff", label: "Diff" },
  { id: "tests", label: t("Tests") },
  { id: "review", label: "Review" },
  { id: "spec", label: t("Brief") },
  { id: "models", label: t("Models") },
  { id: "events", label: t("Events") },
];

function Tests({ verify }: { verify: VerifyOutput | null }) {
  if (!verify) return <p className="muted">{t("No verification yet.")}</p>;
  return (
    <>
      <p>
        {t("Studio's independent verification:")}{" "}
        {verify.passed ? <span className="badge good">{t("passed")}</span> : <span className="badge bad">{t("did not pass")}</span>}
        {verify.empty_diff && <span className="badge warn">{t("empty diff")}</span>}
      </p>
      {verify.out_of_scope?.length > 0 && (
        <div className="alert warn">
          {t("Outside the allowed paths:")} <List items={verify.out_of_scope} mono />
        </div>
      )}
      {(verify.commands ?? []).map((c, i) => (
        <details key={i} className="cmd" open={c.exit_code !== 0 || c.timed_out}>
          <summary>
            <code>{c.command}</code> <ExitBadge code={c.exit_code} timedOut={c.timed_out} />{" "}
            <span className="muted small">{formatDuration(c.seconds)}</span>
          </summary>
          <pre className="output">{c.output || t("(no output)")}</pre>
        </details>
      ))}
    </>
  );
}

function Reviews({ steps }: { steps: Step[] }) {
  const reviews = steps.filter((s) => s.kind === "review");
  if (reviews.length === 0) return <p className="muted">{t("No review yet.")}</p>;
  const last = Math.max(...reviews.map((s) => s.iteration));
  const current = reviews.filter((s) => s.iteration === last);
  const older = reviews.filter((s) => s.iteration !== last);
  return (
    <>
      <p className="small muted">{t("Iteration {n}", { n: last })}</p>
      {current.map((s) => (
        <div key={s.id} className={`review-card by-${actorLead(s.actor) ?? "x"}`}>
          <h3>{actorLabel(actorLead(s.actor)) ?? s.actor}</h3>
          {s.error ? <p className="error">{s.error}</p> : s.output ? <ReviewBody review={s.output as ReviewOutput} /> : <p className="muted">{t("Running…")}</p>}
        </div>
      ))}
      {older.length > 0 && (
        <details>
          <summary className="small">{t("Older reviews ({n})", { n: older.length })}</summary>
          {older.map((s) => (
            <div key={s.id} className="review-card">
              <h3>
                {s.actor} · {t("iteration {n}", { n: s.iteration })}
              </h3>
              {s.output ? <ReviewBody review={s.output as ReviewOutput} /> : <p className="error">{s.error}</p>}
            </div>
          ))}
        </details>
      )}
    </>
  );
}

const BILLING: Record<string, string> = { api: "API", subscription: t("subscription"), external: t("external") };

function UsageLine({ label, u }: { label: string; u: Usage }) {
  return (
    <div className="usage-line">
      <span>{label}</span>
      <span>
        {t("{calls} calls · {tokens} tokens · {cost}", { calls: u.calls, tokens: formatTokens(u.tokens), cost: formatCost(u.cost) })}
      </span>
    </div>
  );
}

function Models({ calls, usage }: { calls: ModelCall[]; usage: RunDetail["usage"] }) {
  return (
    <>
      <div className="usage">
        <UsageLine label={t("Model API calls (Autopilot + worker, counted against the budget)")} u={usage.api} />
        {usage.subscription.calls > 0 && <UsageLine label={t("Claude Code / Codex (your subscription)")} u={usage.subscription} />}
      </div>
      {calls.length === 0 ? (
        <p className="muted">{t("No calls yet.")}</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("Role")}</th>
                <th>{t("Requested")}</th>
                <th>{t("Actual")}</th>
                <th className="num">In/out</th>
                <th className="num">{t("Cost")}</th>
                <th className="num">{t("Time")}</th>
                <th>{t("Outcome")}</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => {
                const failed = c.outcome !== "ok";
                return (
                  <tr key={c.id} className={failed ? "row-bad" : undefined}>
                    <td>
                      {c.role}
                      <div className="muted small">{BILLING[c.billing] ?? c.billing}</div>
                    </td>
                    <td><code>{c.requested_model}</code></td>
                    <td><code>{c.actual_model ?? "–"}</code></td>
                    <td className="num">
                      {formatTokens(c.tokens_in)} / {formatTokens(c.tokens_out)}
                    </td>
                    <td className="num">{formatCost(c.cost_usd)}</td>
                    <td className="num">{c.latency_ms != null ? formatDuration(c.latency_ms / 1000) : "–"}</td>
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
      )}
    </>
  );
}

function Spec({ detail }: { detail: RunDetail }) {
  const { run } = detail;
  const spec = run.spec;
  return (
    <dl className="spec stacked">
      <dt>{t("Goal")}</dt>
      <dd className="prewrap">{spec.goal}</dd>
      <dt>{t("Repository")}</dt>
      <dd><code>{spec.repo_path}</code></dd>
      <dt>{t("Allowed paths")}</dt>
      <dd><List items={spec.allowed_paths} mono /></dd>
      <dt>{t("Criteria")}</dt>
      <dd><List items={spec.acceptance_criteria} /></dd>
      <dt>{t("Verification")}</dt>
      <dd><List items={spec.acceptance_commands} mono /></dd>
      {run.workspace && (
        <>
          <dt>{t("Working copy")}</dt>
          <dd>
            <code>{run.workspace}</code>
            {run.source_commit && <span className="muted small"> @ {run.source_commit.slice(0, 10)}</span>}
          </dd>
        </>
      )}
      {run.feedback && run.feedback.length > 0 && (
        <>
          <dt>{t("Feedback for the next iteration")}</dt>
          <dd><List items={run.feedback} /></dd>
        </>
      )}
    </dl>
  );
}

export function Inspector({ detail, diffRequest = 0 }: { detail: RunDetail; diffRequest?: number }) {
  const [tab, setTab] = useState<Tab>("diff");
  const diffTab = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (diffRequest) { setTab("diff"); diffTab.current?.focus(); } }, [diffRequest]);
  const verify = latestOutput<VerifyOutput>(detail.steps, "verify");
  return (
    <aside className="inspector" aria-label={t("Inspector")}>
      <div className="tabs" role="tablist">
        {TABS.map((tb, index) => (
          <button
            key={tb.id}
            ref={tb.id === "diff" ? diffTab : undefined}
            type="button"
            role="tab"
            id={`inspector-tab-${tb.id}`}
            aria-controls="inspector-panel"
            tabIndex={tab === tb.id ? 0 : -1}
            aria-selected={tab === tb.id}
            className={tab === tb.id ? "on" : undefined}
            onClick={() => setTab(tb.id)}
            onKeyDown={(e) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
              e.preventDefault();
              const next = e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1
                : (index + (e.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
              setTab(TABS[next].id);
              document.getElementById(`inspector-tab-${TABS[next].id}`)?.focus();
            }}
          >
            {tb.label}
          </button>
        ))}
      </div>
      <div className="tab-body" role="tabpanel" id="inspector-panel" aria-labelledby={`inspector-tab-${tab}`} tabIndex={0}>
        {tab === "diff" &&
          (verify ? (
            <>
              <p className="small muted">{t("From the latest verification (iteration {n}).", { n: verify.step.iteration })}</p>
              <DiffView diff={verify.out.diff ?? ""} />
            </>
          ) : (
            <p className="muted">{t("The diff appears after the first verification.")}</p>
          ))}
        {tab === "tests" && <Tests verify={verify?.out ?? null} />}
        {tab === "review" && <Reviews steps={detail.steps} />}
        {tab === "spec" && <Spec detail={detail} />}
        {tab === "models" && <Models calls={detail.model_calls} usage={detail.usage} />}
        {tab === "events" && (
          <ol className="events">
            {[...detail.events].reverse().map((ev) => (
              <li key={ev.id}>
                <span className="muted small mono">{absoluteTime(ev.at)}</span> <span className="tag">{ev.kind}</span>{" "}
                {ev.message}
              </li>
            ))}
          </ol>
        )}
      </div>
    </aside>
  );
}
