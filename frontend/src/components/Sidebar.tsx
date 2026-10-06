import type { Health, RunSummary } from "../api";
import { relativeTime } from "../format";
import { StateBadge } from "./StateBadge";

interface Props {
  health: Health | null;
  healthError: string | null;
  runs: RunSummary[];
  runsError: string | null;
  selectedId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
}

function Dot({ ok, label, title }: { ok: boolean; label: string; title?: string }) {
  return (
    <li className={ok ? "health-ok" : "health-bad"} title={title}>
      <span className="dot" aria-hidden="true" />
      {label}: <strong>{ok ? "ok" : "ne"}</strong>
    </li>
  );
}

export function Sidebar({ health, healthError, runs, runsError, selectedId, onNew, onSelect }: Props) {
  const gw = health?.gateway;
  const gwTitle = gw ? (gw.reachable ? `HTTP ${gw.status ?? ""}` : (gw.error ?? "nedostupné")) : undefined;
  return (
    <aside className="sidebar">
      <div className="brand">
        AI Bridge Studio
        {health && <span className="muted small"> v{health.version}</span>}
      </div>

      <section aria-label="Stav služeb" className="health">
        {healthError ? (
          <p className="error small">Backend: {healthError}</p>
        ) : !health ? (
          <p className="muted small">Načítám stav…</p>
        ) : (
          <ul>
            <Dot ok={health.gateway.reachable} label="OmniRoute" title={gwTitle} />
            <Dot ok={health.leads.claude.installed} label="claude" />
            <Dot ok={health.leads.codex.installed} label="codex" />
          </ul>
        )}
      </section>

      <button type="button" className="primary block" onClick={onNew}>
        + Nový úkol
      </button>

      <nav aria-label="Běhy" className="run-list">
        {runsError && <p className="error small">{runsError}</p>}
        {runs.length === 0 && !runsError && <p className="muted small">Zatím žádné běhy.</p>}
        <ul>
          {runs.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className={"run-item" + (r.id === selectedId ? " selected" : "")}
                aria-current={r.id === selectedId ? "page" : undefined}
                onClick={() => onSelect(r.id)}
              >
                <span className="run-title">{r.title}</span>
                <span className="run-meta">
                  <StateBadge state={r.state} />
                  <span className="muted small">{relativeTime(r.updated_at || r.created_at)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
