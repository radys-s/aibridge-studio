import { useEffect, useState } from "react";
import { api, type Overview } from "../api";
import { formatCost, formatDuration, formatTokens, STATE_LABELS } from "../format";
import { StateBadge } from "./StateBadge";
import { modelName } from "./SettingsView";

const WINDOWS = [7, 30, 90] as const;

/** "67 %" or "–" when there is nothing to divide. */
export function percent(share: number | null | undefined): string {
  return share == null ? "–" : `${Math.round(share * 100)} %`;
}

/** "1,5" with one decimal in Czech, "–" without data. */
export function oneDecimal(n: number | null | undefined): string {
  return n == null ? "–" : n.toLocaleString("cs-CZ", { maximumFractionDigits: 1 });
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
      <h1>Přehled</h1>
      <div className="segmented" role="group" aria-label="Období">
        {WINDOWS.map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={days === d}
            className={days === d ? "on" : undefined}
            onClick={() => setDays(d)}
          >
            {d} dní
          </button>
        ))}
      </div>
      {error && <p className="alert error">Přehled nelze načíst: {error}</p>}
      {!o && !error && <p className="muted">Načítám…</p>}
      {o && o.runs === 0 && <p className="muted">Za posledních {o.days} dní žádné úkoly.</p>}
      {o && o.runs > 0 && (
        <>
          <section className="card">
            <h2>Výsledky</h2>
            <div className="tiles">
              <Tile label="úkolů" value={String(o.runs)} />
              <Tile
                label="přijato"
                value={percent(o.accept_rate)}
                note={[
                  o.finished ? `${o.accepted} z ${o.finished} dokončených` : "zatím nic nedokončeno",
                  o.runs > o.finished ? `${o.runs - o.finished} rozpracovaných` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              />
              <Tile label="medián do přijetí" value={formatDuration(o.median_seconds_to_accepted)} note="od zadání po přijetí" />
              <Tile
                label="opravných kol"
                value={oneDecimal(o.fix_rounds_avg)}
                note={o.accepted ? `${o.first_try}× napoprvé` : undefined}
              />
              <Tile label="tvých rozhodnutí" value={String(o.decisions)} note={`${oneDecimal(o.decisions / o.runs)} na úkol`} />
              <Tile label="použito do repozitáře" value={String(o.applied)} />
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
            <h2>Spotřeba</h2>
            <div className="tiles">
              <Tile
                label="volání modelů"
                value={formatTokens(o.api.calls)}
                note={o.api.failed ? `${o.api.failed} neúspěšných` : undefined}
              />
              <Tile label="tokenů" value={formatTokens(o.api.tokens)} />
              <Tile
                label="tokenů na přijatý úkol"
                value={formatTokens(o.api_tokens_per_accepted == null ? null : Math.round(o.api_tokens_per_accepted))}
                note="jen volání přes OmniRoute"
              />
              <Tile label="cena podle OmniRoute" value={formatCost(o.api.cost)} note="free modely hlásí 0" />
            </div>
            {o.subscription.calls > 0 && (
              <p className="muted small">
                Předplatné (Claude / Codex přes vlastní přihlášení): {o.subscription.calls} volání,{" "}
                {formatTokens(o.subscription.tokens)} tokenů, API ekvivalent {formatCost(o.subscription.cost)}.
              </p>
            )}
            {o.external_runs > 0 && (
              <p className="muted small">
                {o.external_runs}× pracoval externí agent (Freebuff) — jeho spotřebu Studio nezná a v číslech není.
              </p>
            )}
            <p className="muted small">Úspory se neuvádějí, dokud nejsou změřené proti přímé práci v Claude Code nebo Codexu.</p>
          </section>
          {o.top_models.length > 0 && (
            <section className="card">
              <h2>Nejpoužívanější modely</h2>
              <table className="models">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Volání</th>
                    <th>Úspěšných</th>
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
