import { useCallback, useEffect, useState } from "react";
import { api, type Health, type RunSummary } from "./api";
import { RunDetail } from "./components/RunDetail";
import { Sidebar } from "./components/Sidebar";
import { TaskForm } from "./components/TaskForm";

// Routing via location.hash: "#/new" (default) or "#/run/<id>".
type Route = { view: "new" } | { view: "run"; id: string };

function parseHash(hash: string): Route {
  const m = /^#\/run\/(.+)$/.exec(hash);
  return m ? { view: "run", id: decodeURIComponent(m[1]) } : { view: "new" };
}

function navigate(route: Route) {
  window.location.hash = route.view === "run" ? `#/run/${encodeURIComponent(route.id)}` : "#/new";
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

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      const list = await api.listRuns();
      setRuns([...list].sort((a, b) => b.created_at - a.created_at));
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

  usePolling(loadRuns, 3000);
  usePolling(loadHealth, 15000);

  const selectedId = route.view === "run" ? route.id : null;
  const selectedSummary = runs.find((r) => r.id === selectedId);

  return (
    <div className="layout">
      <Sidebar
        health={health}
        healthError={healthError}
        runs={runs}
        runsError={runsError}
        selectedId={selectedId}
        onNew={() => navigate({ view: "new" })}
        onSelect={(id) => navigate({ view: "run", id })}
      />
      <main className="main">
        {route.view === "run" ? (
          <RunDetail
            key={route.id}
            runId={route.id}
            listUpdatedAt={selectedSummary?.updated_at}
            onChanged={() => void loadRuns()}
          />
        ) : (
          <TaskForm
            onCreated={(id) => {
              void loadRuns();
              navigate({ view: "run", id });
            }}
          />
        )}
      </main>
    </div>
  );
}
