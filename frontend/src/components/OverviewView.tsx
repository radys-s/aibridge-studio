import { useEffect, useState } from "react";
import { api, type Overview } from "../api";
import { formatCost, formatDuration, formatTokens, STATE_LABELS } from "../format";
import { lang, locale, t } from "../i18n";
import { StateBadge } from "./StateBadge";
import { modelName } from "./SettingsView";

const WINDOWS = [7, 30, 90] as const;

/** "67 %" (Czech) or "67%", "–" when there is nothing to divide. */
export function percent(share: number | null | undefined): string {
  return share == null ? "–" : `${Math.round(share * 100)}${lang() === "cs" ? " %" : "%"}`;
}

/** One decimal in the UI locale ("1,5" / "1.5"), "–" without data. */
export function oneDecimal(n: number | null | undefined): string {
  return n == null ? "–" : n.toLocaleString(locale(), { maximumFractionDigits: 1 });
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile">
      <div className="tile-value">{value}</div>
      <div className="tile-label">{label}</div>
      {note && <div className="tile-note muted small">{note}</div>}
    </div>
  );
}

export function OverviewView() {
  const [days, setDays] = useState<number>(30);
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    api.overview(days).then(
      (o) => live && setData(o),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [days]);

  const o = data && data.days === days ? data : null;
  return (
    <div className="settings overview">
      <h1>{t("Overview")}</h1>
      <div className="segmented" role="group" aria-label={t("Period")}>
        {WINDOWS.map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={days === d}
            className={days === d ? "on" : undefined}
            onClick={() => setDays(d)}
          >
            {t("{n} days", { n: d })}
          </button>
        ))}
      </div>
      {error && <p className="alert error">{t("Cannot load the overview: {error}", { error })}</p>}
      {!o && !error && <p className="muted">{t("Loading…")}</p>}
      {o && o.runs === 0 && <p className="muted">{t("No tasks in the last {n} days.", { n: o.days })}</p>}
      {o && o.runs > 0 && (
        <>
          <section className="card">
            <h2>{t("Results")}</h2>
            <div className="tiles">
              <Tile label={t("tasks")} value={String(o.runs)} />
              <Tile
                label={t("accepted")}
                value={percent(o.accept_rate)}
                note={[
                  o.finished ? t("{a} of {b} finished", { a: o.accepted, b: o.finished }) : t("nothing finished yet"),
                  o.runs > o.finished ? t("{n} in progress", { n: o.runs - o.finished }) : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              />
              <Tile label={t("median to accepted")} value={formatDuration(o.median_seconds_to_accepted)} note={t("from task to acceptance")} />
              <Tile
                label={t("fix rounds")}
                value={oneDecimal(o.fix_rounds_avg)}
                note={o.accepted ? t("{n}× first try", { n: o.first_try }) : undefined}
              />
              <Tile label={t("your decisions")} value={String(o.decisions)} note={t("{n} per task", { n: oneDecimal(o.decisions / o.runs) })} />
              <Tile label={t("applied to the repository")} value={String(o.applied)} />
            </div>
            <div className="state-counts">
              {Object.entries(o.by_state)
                .sort((a, b) => b[1] - a[1])
                .map(([state, n]) => (
                  <span key={state} title={STATE_LABELS[state] ?? state}>
                    <StateBadge state={state} /> {n}
                  </span>
                ))}
            </div>
          </section>
          <section className="card">
            <h2>{t("Usage")}</h2>
            <div className="tiles">
              <Tile
                label={t("model calls")}
                value={formatTokens(o.api.calls)}
                note={o.api.failed ? t("{n} failed", { n: o.api.failed }) : undefined}
              />
              <Tile label={t("tokens")} value={formatTokens(o.api.tokens)} />
              <Tile
                label={t("tokens per accepted task")}
                value={formatTokens(o.api_tokens_per_accepted == null ? null : Math.round(o.api_tokens_per_accepted))}
                note={t("model API calls only")}
              />
              <Tile label={t("reported cost")} value={formatCost(o.api.cost)} note={t("free models report 0")} />
            </div>
            {o.subscription.calls > 0 && (
              <p className="muted small">
                {t("Subscription (Claude Code / Codex with your own login): {calls} calls, {tokens} tokens.", {
                  calls: o.subscription.calls,
                  tokens: formatTokens(o.subscription.tokens),
                })}
              </p>
            )}
            {o.external_runs > 0 && (
              <p className="muted small">
                {t("An external agent (Freebuff) worked {n}×; Studio does not know its usage and it is not counted.", { n: o.external_runs })}
              </p>
            )}
            <p className="muted small">{t("Savings are not shown until they are measured against working directly in Claude Code or Codex.")}</p>
          </section>
          {o.top_models.length > 0 && (
            <section className="card">
              <h2>{t("Most used models")}</h2>
              <table className="models">
                <thead>
                  <tr>
                    <th>{t("Model")}</th>
                    <th>{t("Calls")}</th>
                    <th>{t("Succeeded")}</th>
                  </tr>
                </thead>
                <tbody>
                  {o.top_models.map((m) => (
                    <tr key={m.model}>
                      <td className="model-id" title={m.model}>
                        {modelName(m.model)}
                      </td>
                      <td>{m.calls}</td>
                      <td>{percent(m.calls ? m.ok / m.calls : null)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </div>
  );
}
