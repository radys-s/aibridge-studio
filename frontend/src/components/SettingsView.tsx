import { useState } from "react";
import { api, LEADS, type CatalogModel, type Health, type Lead, type Probe, type SettingsSnapshot } from "../api";
import { formatDuration, relativeTime } from "../format";
import { LEAD_LABELS } from "../options";
import { LeadPicker } from "./LeadPicker";

interface Props {
  settings: SettingsSnapshot;
  health: Health | null;
  onSaved: (s: SettingsSnapshot) => void;
}

function ProbeResult({ probe }: { probe: Probe | undefined }) {
  if (!probe) return <span className="muted small">neověřeno</span>;
  return probe.ok ? (
    <span className="badge good" title={probe.actual_model ?? ""}>
      funguje · {formatDuration(probe.latency_ms / 1000)} · {relativeTime(probe.at)}
    </span>
  ) : (
    <span className="badge bad" title={probe.error}>
      {probe.kind === "not_logged_in" ? "nepřihlášeno" : (probe.kind ?? "chyba")} · {relativeTime(probe.at)}
    </span>
  );
}

function CatalogEditor({
  lead,
  models,
  probes,
  effort,
  onChange,
  onProbed,
}: {
  lead: Lead;
  models: CatalogModel[];
  probes: Record<string, Probe>;
  effort: string | null;
  onChange: (m: CatalogModel[]) => void;
  onProbed: () => void;
}) {
  const [id, setId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function probe(model: string) {
    setBusy(model);
    setError(null);
    try {
      await api.probe(lead, model, effort);
      onProbed();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="catalog">
      <h3>{LEAD_LABELS[lead]} – modely v nabídce</h3>
      <ul className="catalog-list">
        {models.map((m) => (
          <li key={m.id}>
            <code>{m.id}</code> <span className="muted small">{m.label}</span>
            <ProbeResult probe={probes[`${lead}:${m.id}:${effort ?? ""}`]} />
            <button type="button" className="small" disabled={!!busy} onClick={() => void probe(m.id)}>
              {busy === m.id ? "Ověřuji…" : "Ověřit"}
            </button>
            <button
              type="button"
              className="small"
              aria-label={`Odebrat ${m.id}`}
              onClick={() => onChange(models.filter((x) => x.id !== m.id))}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
      <form
        className="inline-add"
        onSubmit={(e) => {
          e.preventDefault();
          const v = id.trim();
          if (v && !models.some((m) => m.id === v)) onChange([...models, { id: v, label: "" }]);
          setId("");
        }}
      >
        <input value={id} placeholder="id modelu, např. claude-sonnet-5-5" spellCheck={false} onChange={(e) => setId(e.target.value)} />
        <button type="submit" className="small">Přidat</button>
      </form>
      <p className="hint">
        Ověření pošle jeden malý dotaz přes tvoje přihlášené CLI (s výchozím effortem {effort ?? "–"}). Spotřebuje
        trochu limitu.
      </p>
      {error && <p className="error small">{error}</p>}
    </div>
  );
}

export function SettingsView({ settings, health, onSaved }: Props) {
  const [defaults, setDefaults] = useState(settings.defaults);
  const [catalog, setCatalog] = useState(settings.catalog);
  const [status, setStatus] = useState<string | null>(null);

  async function save() {
    try {
      onSaved(await api.saveSettings({ defaults, catalog }));
      setStatus("Uloženo.");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
    }
  }

  async function reload() {
    onSaved(await api.settings());
  }

  return (
    <div className="settings">
      <h1>Nastavení</h1>

      <section className="card">
        <h2>Připojení</h2>
        <ul className="plain">
          <li>
            OmniRoute: {health?.gateway.reachable ? <span className="badge good">dostupný</span> : <span className="badge bad">nedostupný</span>}
            <span className="muted small"> worker modely: {health?.worker_models.join(", ") || "–"}</span>
          </li>
          {LEADS.map((l) => (
            <li key={l}>
              {LEAD_LABELS[l]} CLI:{" "}
              {health?.leads[l]?.installed ? <span className="badge good">nainstalováno</span> : <span className="badge bad">chybí</span>}
            </li>
          ))}
        </ul>
        <p className="hint">
          Leady běží přes tvoje přihlášení v terminálu (claude / codex). Když ověření hlásí „nepřihlášeno“, spusť v
          terminálu <code>claude</code> a <code>/login</code>, nebo <code>codex login</code>.
        </p>
      </section>

      <section className="card">
        <h2>Výchozí orchestrátoři</h2>
        <div className="lead-pickers">
          {LEADS.map((lead) => (
            <LeadPicker
              key={lead}
              lead={lead}
              config={defaults.orchestration[lead]}
              catalog={catalog[lead]}
              efforts={settings.efforts[lead]}
              primary={defaults.orchestration.primary === lead}
              onChange={(c) => setDefaults({ ...defaults, orchestration: { ...defaults.orchestration, [lead]: c } })}
            />
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Katalog modelů</h2>
        {LEADS.map((lead) => (
          <CatalogEditor
            key={lead}
            lead={lead}
            models={catalog[lead]}
            probes={settings.probes}
            effort={defaults.orchestration[lead].effort}
            onChange={(m) => setCatalog({ ...catalog, [lead]: m })}
            onProbed={() => void reload()}
          />
        ))}
      </section>

      <div className="actions">
        <button type="button" className="primary" onClick={() => void save()}>
          Uložit nastavení
        </button>
        {status && <span className="small">{status}</span>}
      </div>
    </div>
  );
}
