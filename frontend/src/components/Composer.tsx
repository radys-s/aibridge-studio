import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  api,
  type Escalation,
  type Health,
  type Implementer,
  type NewTask,
  type SettingsSnapshot,
  type VaultOverview,
  type VaultProject,
} from "../api";
import { activeProviders, asAutopilot, describe, ESCALATIONS, IMPLEMENTERS, lines } from "../options";
import { List } from "./bits";

interface Props {
  settings: SettingsSnapshot;
  health: Health | null;
  onCreated: (runId: string) => void;
  onSettingsSaved: (s: SettingsSnapshot) => void;
  onHealthChanged?: () => void;
  /** Prefill from this run's spec ("Podobný úkol"); nothing starts until Spustit. */
  fromRun?: string;
}

type Start = "project" | "free";

/** Note path without folder and extension: "02 Projekty/Calc.md" → "Calc". */
export function noteTitle(path: string): string {
  return (path.split("/").pop() ?? path).replace(/\.md$/i, "");
}

/** Goal sent to Autopilot when continuing a project; the focus is optional. */
export function continueGoal(title: string, focus: string): string {
  const f = focus.trim();
  return f ? `Pokračuj v projektu ${title}. Zaměř se na: ${f}` : `Pokračuj v projektu ${title}.`;
}

/** One quiet line: gateway, who receives the code (only providers that do not train on it)
 * and today's free requests. */
export function StatusLine({ health, onHealthChanged }: { health: Health | null; onHealthChanged?: () => void }) {
  if (!health) return null;
  const providers = activeProviders(health.models ?? []);
  const calls = health.api_calls_today ?? 0;
  return (
    <p
      className="status-line privacy-line"
      title="Posílá se kód z repozitáře a sdílené poznámky bez osobního obsahu. Limity free modelů se nulují o půlnoci UTC."
    >
      <span className={`hdot ${health.gateway.reachable ? "ok" : "bad"}`} aria-hidden="true" />
      <span>{health.gateway.reachable ? "OmniRoute běží" : "OmniRoute neběží"}</span>
      {!health.gateway.reachable && health.gateway.launchable && <StartGateway onDone={onHealthChanged} />}
      <span aria-hidden="true">·</span>
      {providers.length ? (
        <span>
          {health.privacy_floor === "no_train"
            ? "Data jdou jen k poskytovatelům bez tréninku: "
            : "Data jdou i k poskytovatelům s neověřeným soukromím: "}
          <strong>{providers.join(", ")}</strong>
        </span>
      ) : (
        <span className="error">Žádný poskytovatel bez tréninku není připojený, běh se zastaví.</span>
      )}
      <span aria-hidden="true">·</span>
      <span>dnes {calls} {calls === 1 ? "dotaz" : calls >= 2 && calls <= 4 ? "dotazy" : "dotazů"}</span>
    </p>
  );
}

/** Starts the local OmniRoute (`omniroute serve`); Studio waits until it answers. */
function StartGateway({ onDone }: { onDone?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function start() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.startOmniRoute();
      if (!r.reachable) setError("OmniRoute se nespustil do minuty.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      onDone?.();
    }
  }
  return (
    <>
      <button type="button" className="small primary" disabled={busy} onClick={() => void start()}>
        {busy ? "Spouštím OmniRoute…" : "Spustit OmniRoute"}
      </button>
      {error && <span className="error" role="alert">{error}</span>}
    </>
  );
}

/** New task. Main path: continue a project from the shared Obsidian memory; or a free prompt. */
export function Composer({ settings, health, onCreated, onSettingsSaved, onHealthChanged, fromRun }: Props) {
  const d = settings.defaults;
  const [vault, setVault] = useState<VaultOverview | null>(null);
  const [start, setStart] = useState<Start>("project");
  const [note, setNote] = useState("");
  const [project, setProject] = useState<VaultProject | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [loadingProject, setLoadingProject] = useState(false);
  const selectedNote = useRef("");
  const submitting = useRef(false);
  const [focus, setFocus] = useState("");
  const [goal, setGoal] = useState("");
  const [repo, setRepo] = useState(settings.recent_repos[0] ?? "");
  const [implementer, setImplementer] = useState<Implementer>(d.implementer);
  const [escalation, setEscalation] = useState<Escalation>(d.escalation);
  const [autoApprove, setAutoApprove] = useState(d.auto_approve === true);
  const [advanced, setAdvanced] = useState(false);
  const [paths, setPaths] = useState("");
  const [criteria, setCriteria] = useState("");
  const [commands, setCommands] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [template, setTemplate] = useState<{ title: string } | null>(null);
  const templateRequested = useRef(!!fromRun);

  // Settings may load after the first render or change in the settings view.
  useEffect(() => {
    // An old default (Claude/Codex, removed in ADR 0004) falls back to auto.
    const impl = settings.defaults.implementer;
    setImplementer(IMPLEMENTERS.some((i) => i.id === impl) ? impl : "auto");
    setEscalation(settings.defaults.escalation);
    setAutoApprove(settings.defaults.auto_approve === true);
  }, [settings.defaults]);

  useEffect(() => {
    if (!fromRun) return;
    let alive = true;
    api.getRun(fromRun).then(
      (r) => {
        if (!alive) return;
        const spec = r.run.spec;
        setStart("free");
        setGoal(spec.goal);
        setRepo(spec.repo_path);
        if (IMPLEMENTERS.some((i) => i.id === spec.implementer)) setImplementer(spec.implementer);
        if (ESCALATIONS.some((x) => x.id === spec.escalation)) setEscalation(spec.escalation);
        const hasManual = spec.allowed_paths.length + spec.acceptance_criteria.length + spec.acceptance_commands.length > 0;
        setAdvanced(hasManual);
        setPaths(spec.allowed_paths.join("\n"));
        setCriteria(spec.acceptance_criteria.join("\n"));
        setCommands(spec.acceptance_commands.join("\n"));
        setTemplate({ title: spec.title || spec.goal.slice(0, 60) });
      },
      (e) => {
        if (!alive) return;
        setStart("free"); // still a fresh free task, just without the prefill
        setError(`Původní úkol nelze načíst: ${e instanceof Error ? e.message : String(e)}`);
      },
    );
    return () => {
      alive = false;
    };
  }, [fromRun]);

  useEffect(() => {
    api.vault().then(
      (v) => {
        setVault(v);
        if (templateRequested.current) return; // "Podobný úkol" is a free task; keep its fields
        if (!v.available || !v.projects?.length) setStart("free");
        // One-click resume: preselect the project of the last task.
        else if (settings.last_project && v.projects.includes(settings.last_project)) selectProject(settings.last_project);
      },
      () => {
        setVault({ available: false, path: "" });
        if (!templateRequested.current) setStart("free");
      },
    );
    // Only on mount: later settings changes must not override the user's pick.
  }, []);

  useEffect(() => {
    if (!note) {
      setProject(null);
      setLoadingProject(false);
      return;
    }
    let alive = true;
    setProjectError(null);
    setLoadingProject(true);
    api.vaultProject(note).then(
      (p) => {
        if (!alive) return;
        setProject(p);
        setRepo(p.repo_hint ?? "");
        setLoadingProject(false);
      },
      (e) => {
        if (!alive) return;
        setProject(null);
        setProjectError(e instanceof Error ? e.message : String(e));
        setLoadingProject(false);
      },
    );
    return () => {
      alive = false;
    };
  }, [note]);

  const orchestration = asAutopilot(d.orchestration);
  const projects = vault?.projects ?? [];
  const projectReady = !!note && !loadingProject && !projectError && project?.note === note;

  function selectProject(next: string) {
    if (next === selectedNote.current) return;
    selectedNote.current = next;
    setNote(next);
    setProject(null);
    setRepo("");
    setProjectError(null);
    setLoadingProject(!!next);
  }

  async function submit() {
    if (submitting.current) return;
    if (start === "project" && (!projectReady || selectedNote.current !== note)) {
      setError(projectError ? "Oprav načtení projektu nebo přepni na Nové zadání." : "Vyber projekt a počkej na načtení jeho poznámky.");
      return;
    }
    const text = start === "project" ? continueGoal(project?.title ?? noteTitle(note), focus) : goal.trim();
    if (text.length < 3 || !repo.trim()) {
      setError(start === "project" ? "Vyber repozitář projektu." : "Napiš, co se má udělat, a vyber repozitář.");
      return;
    }
    const task: NewTask = {
      goal: text,
      repo_path: repo.trim(),
      orchestration,
      implementer,
      escalation,
      auto_approve: autoApprove,
    };
    if (start === "project") task.project_note = note;
    if (advanced) {
      task.allowed_paths = lines(paths);
      task.acceptance_criteria = lines(criteria);
      task.acceptance_commands = lines(commands);
    }
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await api.createTask(task);
      setGoal("");
      setFocus("");
      onCreated(run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  async function saveDefaults() {
    try {
      const s = await api.saveSettings({
        defaults: { orchestration, implementer, escalation, auto_approve: autoApprove },
      });
      onSettingsSaved(s);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <div className="composer-page">
      <h1 className="composer-title">{start === "project" ? "Kde pokračujeme?" : "Co postavíme?"}</h1>
      <p className="composer-sub">Autopilot navrhne zadání, udělá práci a nechá ji nezávisle zkontrolovat.</p>
      {template && (
        <p className="alert info small">
          Podle úkolu „{template.title}“. Uprav, co je potřeba, a spusť.
        </p>
      )}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="segmented" role="radiogroup" aria-label="Začátek">
          <button
            type="button"
            role="radio"
            aria-checked={start === "project"}
            className={start === "project" ? "on" : undefined}
            disabled={!vault?.available || projects.length === 0}
            title={vault?.available ? "Autopilot naváže na poznámku projektu a otevřené předávky." : "Obsidian vault není dostupný."}
            onClick={() => setStart("project")}
          >
            Pokračovat projekt
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={start === "free"}
            className={start === "free" ? "on" : undefined}
            onClick={() => setStart("free")}
          >
            Nové zadání
          </button>
        </div>

        {start === "project" ? (
          <div className="project-pick">
            <label className="field">
              <span>Projekt ze sdílené paměti</span>
              <select aria-label="Projekt" value={note} disabled={busy} onChange={(e) => selectProject(e.target.value)}>
                <option value="">Vyber projekt…</option>
                {projects.map((p) => (
                  <option key={p} value={p}>
                    {noteTitle(p)}
                  </option>
                ))}
              </select>
            </label>
            {loadingProject && <p className="muted small" role="status">Načítám projekt…</p>}
            {projectError && <p className="alert error small" role="alert">{projectError} Oprav výběr nebo přepni na Nové zadání.</p>}
            {projectReady && project && (
              <div className="project-info small">
                <div>
                  Autopilot dostane poznámku <strong>{noteTitle(project.note)}</strong>
                  {project.handoffs.length > 0 ? " a otevřené předávky:" : " (žádné otevřené předávky)."}
                </div>
                {project.handoffs.length > 0 && <List items={project.handoffs.map(noteTitle)} />}
                {project.refused.length > 0 && (
                  <div className="muted">Nepošle se (osobní nebo tajné): {project.refused.map(noteTitle).join(", ")}</div>
                )}
                <details>
                  <summary className="muted">Náhled poznámky</summary>
                  <p className="hint">E-maily a telefony jsou skryté.</p>
                  <pre className="output">{project.preview}</pre>
                </details>
              </div>
            )}
            <textarea
              className="composer-input"
              aria-label="Na co se zaměřit"
              rows={2}
              placeholder={
                project?.handoffs.length
                  ? `Na co se zaměřit? (nepovinné – jinak naváže na ${noteTitle(project.handoffs[0])})`
                  : "Na co se zaměřit? (nepovinné – jinak Autopilot navrhne další krok sám)"
              }
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              onKeyDown={onKey}
            />
          </div>
        ) : (
          <textarea
            className="composer-input"
            aria-label="Zadání"
            rows={5}
            autoFocus
            placeholder="Popiš, co se má udělat. Autopilot z toho navrhne rozsah a kontroly, ty to jen schválíš."
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={onKey}
          />
        )}

        <div className="composer-bar">
          <label className="chip-field repo-field">
            <span>Repozitář</span>
            <input
              list="recent-repos"
              value={repo}
              spellCheck={false}
              placeholder="C:\cesta\k\repu"
              onChange={(e) => setRepo(e.target.value)}
            />
            <datalist id="recent-repos">
              {settings.recent_repos.map((r) => (
                <option key={r} value={r} />
              ))}
            </datalist>
          </label>
          <button type="submit" className="primary send" disabled={busy || (start === "project" && !projectReady)} title="Ctrl+Enter">
            {busy ? "Spouštím…" : "Spustit"}
          </button>
        </div>

        <label className="check" title="Když Autopilot nemá otázky, návrh zadání se schválí sám.">
          <input
            type="checkbox"
            role="switch"
            checked={autoApprove}
            onChange={(e) => setAutoApprove(e.target.checked)}
          />
          Schválit návrh zadání automaticky
        </label>

        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}

        <details className="more">
          <summary>Další možnosti</summary>
          <div className="composer-options">
            <label className="chip-field">
              <span>Kdo píše kód</span>
              <select value={implementer} onChange={(e) => setImplementer(e.target.value as Implementer)}>
                {IMPLEMENTERS.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="chip-field">
              <span>Když to nezvládne</span>
              <select value={escalation} onChange={(e) => setEscalation(e.target.value as Escalation)}>
                {ESCALATIONS.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="hint">
            {IMPLEMENTERS.find((i) => i.id === implementer)?.hint} {ESCALATIONS.find((x) => x.id === escalation)?.hint}
          </p>

          <details
            className="advanced"
            open={advanced}
            onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}
          >
            <summary>Rozsah a kontroly zadat ručně</summary>
            <div className="field-row">
              <label className="field">
                <span>Povolené cesty (glob, po řádcích)</span>
                <textarea rows={3} value={paths} onChange={(e) => setPaths(e.target.value)} placeholder="src/**/*.py" />
              </label>
              <label className="field">
                <span>Akceptační kritéria</span>
                <textarea rows={3} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
              </label>
              <label className="field">
                <span>Příkazy k ověření</span>
                <textarea
                  rows={3}
                  value={commands}
                  onChange={(e) => setCommands(e.target.value)}
                  placeholder="python -m pytest -q"
                  spellCheck={false}
                />
              </label>
            </div>
            <p className="hint">Když vyplníš všechno, návrh se přeskočí a jde se rovnou na plán.</p>
          </details>

          <div className="actions">
            <button type="button" className="small" onClick={() => void saveDefaults()}>
              {saved ? "Uloženo" : "Uložit jako výchozí"}
            </button>
            <span className="muted small">{describe(orchestration)}</span>
          </div>
        </details>
      </form>
      <p className="hint execution-hint">Příkazy běží pod tvým uživatelským účtem. Používej pouze důvěryhodné projekty.</p>
      <StatusLine health={health} onHealthChanged={onHealthChanged} />
    </div>
  );
}
