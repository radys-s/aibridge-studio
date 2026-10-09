import { useCallback, useEffect, useState } from "react";
import { api, type Health, type RunSummary, type SettingsSnapshot } from "./api";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { SettingsView } from "./components/SettingsView";
import { Sidebar } from "./components/Sidebar";
import { Thread } from "./components/Thread";
import { useRunDetail } from "./stream";

// Routing via location.hash: "#/new" (default), "#/run/<id>", "#/settings".
type Route = { view: "new" } | { view: "settings" } | { view: "run"; id: string };

function parseHash(hash: string): Route {
  const m = /^#\/run\/(.+)$/.exec(hash);
  if (m) return { view: "run", id: decodeURIComponent(m[1]) };
  return hash === "#/settings" ? { view: "settings" } : { view: "new" };
}

function navigate(route: Route) {
  window.location.hash =
    route.view === "run" ? `#/run/${encodeURIComponent(route.id)}` : route.view === "settings" ? "#/settings" : "#/new";
}

function usePolling(fn: () => Promise<void>, ms: number) {
  useEffect(() => {
    let alive = true;
    let t: number | undefined;
    const tick = async () => {
      await fn();
      if (alive) t = window.setTimeout(tick, ms);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [fn, ms]);
}

// Theme: "system" follows the OS; the choice is a per-viewer convenience.
const THEMES = ["system", "light", "dark"] as const;
type Theme = (typeof THEMES)[number];

function loadTheme(): Theme {
  try {
    const t = window.localStorage.getItem("studio-theme");
    return (THEMES as readonly string[]).includes(t ?? "") ? (t as Theme) : "system";
  } catch {
    return "system";
  }
}

/** A remembered on/off switch (per viewer); works without storage too. */
function usePersistedFlag(key: string): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState(() => {
    try {
      return window.localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  const set = (v: boolean) => {
    setValue(v);
    try {
      window.localStorage.setItem(key, v ? "1" : "0");
    } catch {
      /* storage unavailable: the choice holds for this session */
    }
  };
  return [value, set];
}

function RunView({ runId, onChanged }: { runId: string; onChanged: () => void }) {
  const { detail, error, live, reload } = useRunDetail(runId);
  const [detailsOpen, setDetailsOpen] = usePersistedFlag("studio-details");
  const [diffRequest, setDiffRequest] = useState(0);
  const changed = useCallback(() => {
    void reload();
    onChanged();
  }, [reload, onChanged]);
  if (!detail) {
    return error ? (
      <p className="alert error" role="alert">
        Úkol nelze načíst: {error}
      </p>
    ) : (
      <p className="muted pad">Načítám…</p>
    );
  }
  return (
    <div className={`run-view${detailsOpen ? " split" : ""}`}>
      <Thread
        detail={detail}
        live={live}
        onChanged={changed}
        detailsOpen={detailsOpen}
        onToggleDetails={() => setDetailsOpen(!detailsOpen)}
        onShowDiff={() => { setDetailsOpen(true); setDiffRequest((n) => n + 1); }}
      />
      {detailsOpen && <Inspector detail={detail} diffRequest={diffRequest} />}
    </div>
  );
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [settings, setSettings] = useState<SettingsSnapshot | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(loadTheme);

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      window.localStorage.setItem("studio-theme", theme);
    } catch {
      /* storage unavailable: the theme still applies for this session */
    }
  }, [theme]);

  const loadRuns = useCallback(async () => {
    try {
      const list = await api.listRuns();
      setRuns([...list].sort((a, b) => b.updated_at - a.updated_at));
      setRunsError(null);
    } catch (e) {
      setRunsError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadHealth = useCallback(async () => {
    try {
      setHealth(await api.health());
      setHealthError(null);
    } catch (e) {
      setHealthError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    api.settings().then(setSettings, (e) => setSettingsError(e instanceof Error ? e.message : String(e)));
  }, []);

  usePolling(loadRuns, 3000);
  usePolling(loadHealth, 20000);

  const selectedId = route.view === "run" ? route.id : null;
  const onChanged = useCallback(() => void loadRuns(), [loadRuns]);

  let main;
  if (route.view === "run") {
    main = <RunView key={route.id} runId={route.id} onChanged={onChanged} />;
  } else if (!settings) {
    main = settingsError ? <p className="alert error pad">Nastavení nelze načíst: {settingsError}</p> : <p className="muted pad">Načítám…</p>;
  } else if (route.view === "settings") {
    main = <SettingsView settings={settings} health={health} onSaved={setSettings} />;
  } else {
    main = (
      <Composer
        settings={settings}
        health={health}
        onSettingsSaved={setSettings}
        onCreated={(id) => {
          void loadRuns();
          api.settings().then(setSettings, () => undefined); // recent repos
          navigate({ view: "run", id });
        }}
      />
    );
  }

  return (
    <div className="layout">
      <Sidebar
        health={health}
        healthError={healthError}
        runs={runs}
        runsError={runsError}
        selectedId={selectedId}
        view={route.view}
        theme={theme}
        onNew={() => navigate({ view: "new" })}
        onSelect={(id) => navigate({ view: "run", id })}
        onSettings={() => navigate({ view: "settings" })}
        onTheme={() => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length])}
      />
      <main className="main">{main}</main>
    </div>
  );
}
