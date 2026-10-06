import type { EffortOption, Lead, LeadConfig, CatalogModel } from "../api";
import { EFFORT_LABELS, HEAVY_WARNING, LEAD_LABELS } from "../options";

interface Props {
  lead: Lead;
  config: LeadConfig;
  catalog: CatalogModel[];
  efforts: EffortOption[];
  primary?: boolean;
  onChange: (config: LeadConfig) => void;
  /** Ask before switching to a heavy effort (default true). */
  confirmHeavy?: boolean;
}

/** Model + effort choice for one lead. Heavy efforts need a confirmation and stay marked. */
export function LeadPicker({ lead, config, catalog, efforts, primary, onChange, confirmHeavy = true }: Props) {
  const heavy = efforts.some((e) => e.id === config.effort && e.heavy);
  const listId = `models-${lead}`;

  function setEffort(value: string) {
    const option = efforts.find((e) => e.id === value);
    if (option?.heavy && confirmHeavy && !window.confirm(HEAVY_WARNING + "\n\nPoužít přesto?")) return;
    onChange({ ...config, effort: value || null });
  }

  return (
    <div className={`lead-picker lead-${lead}${heavy ? " heavy" : ""}`}>
      <div className="lead-picker-head">
        <span className={`lead-dot lead-${lead}`} aria-hidden="true" />
        <strong>{LEAD_LABELS[lead]}</strong>
        {primary && <span className="tag">hlavní</span>}
        {heavy && (
          <span className="badge warn" title={HEAVY_WARNING}>
            ⚠ rychle čerpá limit
          </span>
        )}
      </div>
      <label className="inline-field">
        <span>Model</span>
        <input
          list={listId}
          value={config.model ?? ""}
          placeholder="výchozí"
          spellCheck={false}
          onChange={(e) => onChange({ ...config, model: e.target.value.trim() || null })}
        />
        <datalist id={listId}>
          {catalog.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </datalist>
      </label>
      <label className="inline-field">
        <span>Effort</span>
        <select value={config.effort ?? ""} onChange={(e) => setEffort(e.target.value)}>
          <option value="">výchozí</option>
          {efforts.map((e) => (
            <option key={e.id} value={e.id}>
              {EFFORT_LABELS[e.id] ?? e.id}
              {e.heavy ? " ⚠" : ""}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
