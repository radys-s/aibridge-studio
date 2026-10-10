import { useMemo, useState } from "react";
import { isActive, type Health, type RunSummary } from "../api";
import { activeProviders } from "../options";
import { relativeTime, STATE_LABELS } from "../format";

interface Props {
  health: Health | null;
  healthError: string | null;
  runs: RunSummary[];
  runsError: string | null;
  selectedId: string | null;
  view: "new" | "run" | "settings" | "overview";
  theme: string;
  onNew: () => void;
  onSelect: (id: string) => void;
  onSettings: () => void;
  onOverview: () => void;
  onTheme: () => void;
}

const NEEDS_YOU = ["needs_decision", "awaiting_approval", "awaiting_external"];

function repoName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Group runs by repository, newest group first. */
export function groupByRepo(runs: RunSummary[]): { repo: string; runs: RunSummary[] }[] {
  const groups = new Map<string, RunSummary[]>();
  for (const r of runs) {
    const key = r.repo_path || "?";
    const list = groups.get(key);
    if (list) list.push(r);
    else groups.set(key, [r]);
  }
  return [...groups.entries()]
    .map(([repo, list]) => ({ repo, runs: list }))
    .sort((a, b) => Math.max(...b.runs.map((r) => r.updated_at)) - Math.max(...a.runs.map((r) => r.updated_at)));
}

export function Sidebar(p: Props) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const runs = q ? p.runs.filter((r) => r.title.toLowerCase().includes(q) || r.repo_path.toLowerCase().includes(q)) : p.runs;
    return groupByRepo(runs);
  }, [p.runs, query]);
  const waiting = p.runs.filter((r) => NEEDS_YOU.includes(r.state)).length;
  const h = p.health;

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="logo" aria-hidden="true">◆</span> AI Bridge Studio
      </div>
      <button type="button" className={`new-task${p.view === "new" ? " on" : ""}`} onClick={p.onNew}>
        Nový úkol
      </button>
      <input
        className="search"
        type="search"
        placeholder="Hledat úkoly…"
        aria-label="Hledat úkoly"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {waiting > 0 && <p className="small waiting">{waiting} čeká na tebe</p>}

      <nav aria-label="Úkoly" className="run-list">
        {p.runsError && <p className="error small">{p.runsError}</p>}
        {p.runs.length === 0 && !p.runsError && <p className="muted small">Zatím žádné úkoly.</p>}
        {groups.map((g) => (
          <div key={g.repo} className="repo-group">
            <div className="repo-name" title={g.repo}>
              {repoName(g.repo)}
            </div>
            <ul>
              {g.runs.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className={"run-item" + (r.id === p.selectedId ? " selected" : "")}
                    aria-current={r.id === p.selectedId ? "page" : undefined}
                    onClick={() => p.onSelect(r.id)}
                    title={STATE_LABELS[r.state] ?? r.state}
                  >
                    <span
                      className={`sdot s-${r.state}${isActive(r.state) ? " busy" : ""}`}
                      aria-label={STATE_LABELS[r.state] ?? r.state}
                    />
                    <span className="run-title">{r.title || "(bez názvu)"}</span>
                    <span className="run-when muted small">{relativeTime(r.updated_at || r.created_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        <div className="health small" title={p.healthError ?? h?.gateway.error ?? ""}>
          {p.healthError ? (
            <span className="error">Backend nedostupný</span>
          ) : (
            <>
              {h?.gateway.fake ? (
                <span className="badge warn">testovací gateway</span>
              ) : (
                <>
                  <span className={`hdot ${h?.gateway.reachable ? "ok" : "bad"}`} /> OmniRoute
                </>
              )}
              <span className={`hdot ${h && activeProviders(h.models ?? []).length ? "ok" : "bad"}`} /> soukromé modely
            </>
          )}
        </div>
        <div className="foot-buttons">
          <button type="button" className={`small${p.view === "overview" ? " on" : ""}`} onClick={p.onOverview}>
            Přehled
          </button>
          <button type="button" className={`small${p.view === "settings" ? " on" : ""}`} onClick={p.onSettings}>
            Nastavení
          </button>
          <button type="button" className="small" onClick={p.onTheme} aria-label="Přepnout motiv" title="Motiv">
            {p.theme === "dark" ? "☾" : p.theme === "light" ? "☀" : "◐"}
          </button>
          {h && <span className="muted small">v{h.version}</span>}
        </div>
      </div>
    </aside>
  );
}
