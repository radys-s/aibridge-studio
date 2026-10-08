import { useEffect, useState, type KeyboardEvent } from "react";
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
export function StatusLine({ health }: { health: Health | null }) {
  if (!health) return null;
  const providers = activeProviders(health.models ?? []);
  const calls = health.api_calls_today ?? 0;
  return (
    <p
      className="status-line privacy-line"
      title="Posílá se kód z repozitáře a sdílené poznámky bez osobního obsahu. Limity free modelů se nulují o půlnoci UTC."
    >
      <span className={`hdot ${health.gateway.reachable ? "ok" : "bad"}`} aria-hidden="true" />
      <span>{health.gateway.reachable ? "OmniRoute běží" : "OmniRoute nedostupný"}</span>
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

/** New task. Main path: continue a project from the shared Obsidian memory; or a free prompt. */
export function Composer({ settings, health, onCreated, onSettingsSaved }: Props) {
  const d = settings.defaults;
  const [vault, setVault] = useState<VaultOverview | null>(null);
  const [start, setStart] = useState<Start>("project");
  const [note, setNote] = useState("");
  const [project, setProject] = useState<VaultProject | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [focus, setFocus] = useState("");
  const [goal, setGoal] = useState("");
  const [repo, setRepo] = useState(settings.recent_repos[0] ?? "");
  const [implementer, setImplementer] = useState<Implementer>(d.implementer);
  const [escalation, setEscalation] = useState<Escalation>(d.escalation);
  const [autoApprove, setAutoApprove] = useState(d.auto_approve);
  const [advanced, setAdvanced] = useState(false);
  const [paths, setPaths] = useState("");
  const [criteria, setCriteria] = useState("");
  const [commands, setCommands] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Settings may load after the first render or change in the settings view.
  useEffect(() => {
    // An old default (Claude/Codex, removed in ADR 0004) falls back to auto.
    const impl = settings.defaults.implementer;
    setImplementer(IMPLEMENTERS.some((i) => i.id === impl) ? impl : "auto");
    setEscalation(settings.defaults.escalation);
    setAutoApprove(settings.defaults.auto_approve);
  }, [settings.defaults]);

  useEffect(() => {
    api.vault().then(
      (v) => {
        setVault(v);
        if (!v.available || !v.projects?.length) setStart("free");
        // One-click resume: preselect the project of the last task.
        else if (settings.last_project && v.projects.includes(settings.last_project)) setNote(settings.last_project);
      },
      () => {
        setVault({ available: false, path: "" });
        setStart("free");
      },
    );
    // Only on mount: later settings changes must not override the user's pick.
  }, []);

  useEffect(() => {
    if (!note) {
      setProject(null);
      return;
    }
    let alive = true;
    setProjectError(null);
    api.vaultProject(note).then(
      (p) => {
        if (!alive) return;
        setProject(p);
        if (p.repo_hint) setRepo(p.repo_hint);
      },
      (e) => {
        if (!alive) return;
        setProject(null);
        setProjectError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => {
      alive = false;
    };
  }, [note]);

  const orchestration = asAutopilot(d.orchestration);
  const projects = vault?.projects ?? [];

  async function submit() {
    if (start === "project" && !note) {
      setError("Vyber projekt, ve kterém se má pokračovat.");
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
              <select aria-label="Projekt" value={note} onChange={(e) => setNote(e.target.value)}>
                <option value="">Vyber projekt…</option>
                {projects.map((p) => (
                  <option key={p} value={p}>
                    {noteTitle(p)}
                  </option>
                ))}
              </select>
            </label>
            {projectError && <p className="alert error small">{projectError}</p>}
            {project && (
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
          <button type="submit" className="primary send" disabled={busy} title="Ctrl+Enter">
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
      <StatusLine health={health} />
    </div>
  );
}
