import { useMemo, useState } from "react";
import { isActive, type Health, type RunSummary } from "../api";
import { activeProviders, gatewayState } from "../options";
import { relativeTime, STATE_LABELS } from "../format";
import { t } from "../i18n";
import { NEEDS_YOU } from "../notify";
import { Icon } from "./Icon";

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
        <span className="app-icon" aria-hidden="true">◆</span>
        <span className="brand-name">AI Bridge Studio</span>
        {h && <span className="brand-version">v{h.version}</span>}
      </div>
      <button type="button" className={`new-task${p.view === "new" ? " on" : ""}`} onClick={p.onNew}>
        <Icon name="compose" />
        {t("New task")}
      </button>
      <div className="search-wrap">
        <Icon name="search" />
        <input
          className="search"
          type="search"
          placeholder={t("Search tasks…")}
          aria-label={t("Search tasks")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className="nav-rows">
        <button type="button" className={`nav-row${p.view === "overview" ? " on" : ""}`} onClick={p.onOverview}>
          <Icon name="chart" />
          {t("Overview")}
        </button>
        <button type="button" className={`nav-row${p.view === "settings" ? " on" : ""}`} onClick={p.onSettings}>
          <Icon name="gear" />
          {t("Settings")}
        </button>
      </div>
      {waiting > 0 && <p className="small waiting">{t("{n} waiting for you", { n: waiting })}</p>}

      <nav aria-label={t("Tasks")} className="run-list">
        {p.runsError && <p className="error small">{p.runsError}</p>}
        {p.runs.length === 0 && !p.runsError && <p className="muted small">{t("No tasks yet.")}</p>}
        {groups.map((g) => (
          <div key={g.repo} className="repo-group">
            <div className="repo-name" title={g.repo}>
              <Icon name="folder" />
              <span>{repoName(g.repo)}</span>
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
                      role="img"
                      className={`sdot s-${r.state}${isActive(r.state) ? " busy" : ""}`}
                      aria-label={STATE_LABELS[r.state] ?? r.state}
                    />
                    <span className="run-title">{r.title || t("(untitled)")}</span>
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
            <span className="error">{t("Backend unreachable")}</span>
          ) : (
            <>
              {h?.gateway.fake ? (
                <span className="badge warn">{t("test gateway")}</span>
              ) : (
                <>
                  <span className={`hdot ${h?.gateway.reachable ? "ok" : "bad"}`} /> {h ? gatewayState(h).text : t("Models")}
                </>
              )}
              <span className={`hdot ${h && activeProviders(h.models ?? []).length ? "ok" : "bad"}`} /> {t("private models")}
            </>
          )}
        </div>
        <button
          type="button"
          className="theme-toggle"
          onClick={p.onTheme}
          aria-label={t("Switch theme")}
          title={t("Theme")}
        >
          <Icon name={p.theme === "dark" ? "moon" : p.theme === "light" ? "sun" : "auto"} />
        </button>
      </div>
    </aside>
  );
}
