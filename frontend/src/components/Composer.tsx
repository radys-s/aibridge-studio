import { useEffect, useState, type KeyboardEvent } from "react";
import {
  api,
  type Escalation,
  type Implementer,
  type Lead,
  type NewTask,
  type Orchestration,
  type SettingsSnapshot,
} from "../api";
import {
  ESCALATIONS,
  HEAVY_WARNING,
  heavyLeads,
  IMPLEMENTERS,
  isSolo,
  LEAD_LABELS,
  leadsIn,
  lines,
  MODES,
  withMode,
} from "../options";
import { LeadPicker } from "./LeadPicker";

interface Props {
  settings: SettingsSnapshot;
  onCreated: (runId: string) => void;
  onSettingsSaved: (s: SettingsSnapshot) => void;
}

/** New task: one prompt box plus orchestration chips. The lead drafts scope and criteria. */
export function Composer({ settings, onCreated, onSettingsSaved }: Props) {
  const d = settings.defaults;
  const [goal, setGoal] = useState("");
  const [repo, setRepo] = useState(settings.recent_repos[0] ?? "");
  const [orch, setOrch] = useState<Orchestration>(d.orchestration);
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
    setOrch(settings.defaults.orchestration);
    setImplementer(settings.defaults.implementer);
    setEscalation(settings.defaults.escalation);
    setAutoApprove(settings.defaults.auto_approve);
  }, [settings.defaults]);

  const heavy = heavyLeads(orch, settings.efforts);
  const active = leadsIn(orch.mode);

  async function submit() {
    if (goal.trim().length < 3 || !repo.trim()) {
      setError("Napiš, co se má udělat, a vyber repozitář.");
      return;
    }
    if (heavy.length && !window.confirm(`${HEAVY_WARNING}\n\nSpustit s effortem nad high (${heavy.map((l) => LEAD_LABELS[l]).join(", ")})?`)) {
      return;
    }
    const task: NewTask = {
      goal: goal.trim(),
      repo_path: repo.trim(),
      orchestration: orch,
      implementer,
      escalation,
      auto_approve: autoApprove,
    };
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
        defaults: { orchestration: orch, implementer, escalation, auto_approve: autoApprove },
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
      <h1 className="composer-title">Co postavíme?</h1>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <textarea
          className="composer-input"
          aria-label="Zadání"
          rows={5}
          autoFocus
          placeholder="Popiš úkol. Lead z něj navrhne rozsah, kritéria a příkazy k ověření, ty to jen schválíš.  (Ctrl+Enter odešle)"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={onKey}
        />
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
          <button type="submit" className="primary send" disabled={busy}>
            {busy ? "Odesílám…" : "Spustit"}
          </button>
        </div>

        <div className="composer-options">
          <div className="segmented" role="radiogroup" aria-label="Orchestrátor">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={orch.mode === m.id}
                className={orch.mode === m.id ? "on" : undefined}
                title={m.hint}
                onClick={() => setOrch(withMode(orch, m.id))}
              >
                {m.label}
              </button>
            ))}
          </div>
          {!isSolo(orch.mode) && (
            <label className="chip-field">
              <span>{orch.mode === "duo" ? "Slučuje plán" : "Plánuje"}</span>
              <select value={orch.primary} onChange={(e) => setOrch({ ...orch, primary: e.target.value as Lead })}>
                <option value="claude">Claude</option>
                <option value="codex">Codex</option>
              </select>
            </label>
          )}
          <label className="chip-field" title={IMPLEMENTERS.find((i) => i.id === implementer)?.hint}>
            <span>Implementuje</span>
            <select value={implementer} onChange={(e) => setImplementer(e.target.value as Implementer)}>
              {IMPLEMENTERS.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.label}
                </option>
              ))}
            </select>
          </label>
          <label className="chip-field" title={ESCALATIONS.find((x) => x.id === escalation)?.hint}>
            <span>Převzetí</span>
            <select value={escalation} onChange={(e) => setEscalation(e.target.value as Escalation)}>
              {ESCALATIONS.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.label}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} />
            Schválit návrh zadání automaticky
          </label>
        </div>
        <p className="hint">{MODES.find((m) => m.id === orch.mode)?.hint}</p>

        <div className="lead-pickers">
          {active.map((lead) => (
            <LeadPicker
              key={lead}
              lead={lead}
              primary={!isSolo(orch.mode) && orch.primary === lead}
              config={orch[lead]}
              catalog={settings.catalog[lead] ?? []}
              efforts={settings.efforts[lead] ?? []}
              onChange={(c) => setOrch({ ...orch, [lead]: c })}
            />
          ))}
        </div>
        {heavy.length > 0 && (
          <p className="alert warn" role="status">
            ⚠ {HEAVY_WARNING}
          </p>
        )}
        {isSolo(orch.mode) && (implementer === orch.mode) && (
          <p className="alert warn">Stejný model by psal i hodnotil vlastní kód. Přijetí pak potvrdíš ručně.</p>
        )}

        <details className="advanced" open={advanced} onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}>
          <summary>Rozsah a kritéria ručně (jinak je navrhne lead)</summary>
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
        </div>
        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}
