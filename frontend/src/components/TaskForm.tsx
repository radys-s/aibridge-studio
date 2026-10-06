import { useState, type FormEvent } from "react";
import {
  api,
  DEFAULT_BUDGET,
  type Budget,
  type Implementer,
  type Lead,
  type Planner,
  type TaskSpec,
} from "../api";

interface Props {
  onCreated: (runId: string) => void;
}

const BUDGET_FIELDS: { key: keyof Budget; label: string; hint: string; step?: string }[] = [
  { key: "max_iterations", label: "Max. iterací", hint: "Kolikrát se smí opakovat práce → ověření → review." },
  { key: "max_worker_turns", label: "Max. tahů workeru", hint: "Počet kol agenta v jedné iteraci." },
  { key: "max_worker_seconds", label: "Max. čas workeru (s)", hint: "Časový limit práce workeru v jedné iteraci." },
  { key: "max_tokens", label: "Max. tokenů", hint: "Celkový limit tokenů pro celý běh." },
  { key: "max_cost_usd", label: "Max. cena (USD)", hint: "Celkový cenový strop běhu.", step: "0.01" },
  { key: "command_timeout_s", label: "Timeout příkazu (s)", hint: "Limit pro každý akceptační příkaz." },
  { key: "agent_timeout_s", label: "Timeout agenta (s)", hint: "Limit pro planner a reviewer." },
];

function lines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

export function TaskForm({ onCreated }: Props) {
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [allowed, setAllowed] = useState("");
  const [criteria, setCriteria] = useState("");
  const [commands, setCommands] = useState("");
  const [planner, setPlanner] = useState<Planner>("claude");
  const [reviewer, setReviewer] = useState<Lead>("codex");
  const [implementer, setImplementer] = useState<Implementer>("worker");
  const [budget, setBudget] = useState<Record<keyof Budget, string>>(
    () =>
      Object.fromEntries(Object.entries(DEFAULT_BUDGET).map(([k, v]) => [k, String(v)])) as Record<
        keyof Budget,
        string
      >,
  );
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  function validate(spec: TaskSpec): string[] {
    const e: string[] = [];
    if (spec.title.length < 3 || spec.title.length > 200) e.push("Název musí mít 3–200 znaků.");
    if (spec.goal.length < 10) e.push("Cíl musí mít aspoň 10 znaků.");
    if (!spec.repo_path) e.push("Vyplň cestu k repozitáři.");
    if (spec.allowed_paths.length === 0) e.push("Zadej aspoň jednu povolenou cestu.");
    if (spec.acceptance_criteria.length === 0) e.push("Zadej aspoň jedno akceptační kritérium.");
    if (spec.acceptance_commands.length === 0) e.push("Zadej aspoň jeden akceptační příkaz.");
    for (const f of BUDGET_FIELDS) {
      const v = spec.budget?.[f.key];
      if (v === undefined || !isFinite(v) || v <= 0) e.push(`Limit „${f.label}“ musí být kladné číslo.`);
    }
    return e;
  }

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    const b = {} as Budget;
    for (const f of BUDGET_FIELDS) {
      const raw = budget[f.key].trim().replace(",", ".");
      b[f.key] = f.key === "max_cost_usd" ? parseFloat(raw) : parseInt(raw, 10);
    }
    const spec: TaskSpec = {
      title: title.trim(),
      goal: goal.trim(),
      repo_path: repoPath.trim(),
      allowed_paths: lines(allowed),
      acceptance_criteria: lines(criteria),
      acceptance_commands: lines(commands),
      planner,
      reviewer,
      implementer,
      budget: b,
    };
    const v = validate(spec);
    setErrors(v);
    if (v.length) return;
    setSubmitting(true);
    try {
      const res = await api.createTask(spec);
      onCreated(res.run_id);
    } catch (err) {
      setErrors((err instanceof Error ? err.message : String(err)).split("\n"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="task-form" onSubmit={onSubmit} noValidate>
      <h1>Nový úkol</h1>

      <div className="field">
        <label htmlFor="f-title">Název</label>
        <input id="f-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        <p className="hint">Krátký popis, 3–200 znaků.</p>
      </div>

      <div className="field">
        <label htmlFor="f-goal">Cíl</label>
        <textarea id="f-goal" rows={5} value={goal} onChange={(e) => setGoal(e.target.value)} />
        <p className="hint">Co přesně má být hotové a proč. Aspoň 10 znaků.</p>
      </div>

      <div className="field">
        <label htmlFor="f-repo">Cesta k repozitáři</label>
        <input
          id="f-repo"
          value={repoPath}
          onChange={(e) => setRepoPath(e.target.value)}
          placeholder="C:\Projekty\muj-repo"
          spellCheck={false}
        />
        <p className="hint">Absolutní cesta ke git repozitáři. Studio z něj vytvoří pracovní kopii.</p>
      </div>

      <div className="field">
        <label htmlFor="f-allowed">Povolené cesty</label>
        <textarea
          id="f-allowed"
          rows={3}
          className="mono"
          value={allowed}
          onChange={(e) => setAllowed(e.target.value)}
          spellCheck={false}
        />
        <p className="hint">Jedna na řádek. Glob, např. <code>src/**</code> nebo <code>tests/test_*.py</code>. Změny mimo ně se hlásí jako mimo rozsah.</p>
      </div>

      <div className="field">
        <label htmlFor="f-criteria">Akceptační kritéria</label>
        <textarea id="f-criteria" rows={4} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
        <p className="hint">Jedno na řádek. Reviewer u každého posoudí, zda je splněné, a doloží proč.</p>
      </div>

      <div className="field">
        <label htmlFor="f-commands">Akceptační příkazy</label>
        <textarea
          id="f-commands"
          rows={3}
          className="mono"
          value={commands}
          onChange={(e) => setCommands(e.target.value)}
          spellCheck={false}
        />
        <p className="hint">Jeden na řádek, např. <code>pytest -q</code>. Spouští je Studio samo, ne worker — to je nezávislý důkaz.</p>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="f-planner">Planner</label>
          <select id="f-planner" value={planner} onChange={(e) => setPlanner(e.target.value as Planner)}>
            <option value="claude">Claude</option>
            <option value="codex">Codex</option>
            <option value="none">Bez plánu</option>
          </select>
          <p className="hint">Kdo připraví plán před prací.</p>
        </div>
        <div className="field">
          <label htmlFor="f-reviewer">Reviewer</label>
          <select id="f-reviewer" value={reviewer} onChange={(e) => setReviewer(e.target.value as Lead)}>
            <option value="claude">Claude</option>
            <option value="codex">Codex</option>
          </select>
          <p className="hint">Kdo zhodnotí výsledek proti kritériím.</p>
        </div>
      </div>

      <div className="field">
        <label htmlFor="f-implementer">Kdo implementuje</label>
        <select
          id="f-implementer"
          value={implementer}
          onChange={(e) => setImplementer(e.target.value as Implementer)}
        >
          <option value="worker">Worker přes OmniRoute (automaticky)</option>
          <option value="external">Freebuff (ručně, externí agent)</option>
        </select>
        <p className="hint">
          U Freebuffu Studio připraví pracovní kopii a zadání, ty v ní spustíš <code>freebuff</code> a pak
          klikneš Hotovo. Ověření a review proběhnou stejně jako u workeru.
        </p>
        {implementer === "external" && (
          <div className="alert warn">
            Freebuff pošle kód z pracovní kopie na své servery, zobrazuje reklamy (texty promptů mohou
            sloužit k cílení) a má denní limit. Studio před předáním zkontroluje, že kopie neobsahuje
            tajné údaje, ale co agent udělá mimo kopii, uhlídat nedokáže.
          </div>
        )}
      </div>

      <details className="budget">
        <summary>Limity</summary>
        <div className="budget-grid">
          {BUDGET_FIELDS.map((f) => (
            <div className="field" key={f.key}>
              <label htmlFor={`f-b-${f.key}`}>{f.label}</label>
              <input
                id={`f-b-${f.key}`}
                type="number"
                inputMode="decimal"
                min="0"
                step={f.step ?? "1"}
                value={budget[f.key]}
                onChange={(e) => setBudget((b) => ({ ...b, [f.key]: e.target.value }))}
              />
              <p className="hint">{f.hint}</p>
            </div>
          ))}
        </div>
      </details>

      {errors.length > 0 && (
        <div className="alert error" role="alert">
          <strong>Úkol nelze odeslat:</strong>
          <ul>
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="actions">
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? "Odesílám…" : "Spustit úkol"}
        </button>
      </div>
    </form>
  );
}
