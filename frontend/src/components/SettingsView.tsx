import { useEffect, useState } from "react";
import {
  api,
  LEADS,
  type CatalogModel,
  type Health,
  type Lead,
  type Probe,
  type SettingsSnapshot,
  type VaultOverview,
} from "../api";
import { formatDuration, relativeTime } from "../format";
import { asAutopilot, LEAD_LABELS } from "../options";
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
  const [vault, setVault] = useState<VaultOverview | null>(null);

  useEffect(() => {
    api.vault().then(setVault, () => setVault({ available: false, path: "" }));
  }, []);

  const second = defaults.orchestration.second_opinion ?? [];
  function toggleSecond(lead: Lead, on: boolean) {
    const next = on ? [...second.filter((l) => l !== lead), lead] : second.filter((l) => l !== lead);
    setDefaults({ ...defaults, orchestration: { ...defaults.orchestration, second_opinion: next } });
  }

  async function save() {
    try {
      // Studio always orchestrates with Autopilot; older saved modes are upgraded here.
      onSaved(await api.saveSettings({ defaults: { ...defaults, orchestration: asAutopilot(defaults.orchestration) }, catalog }));
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
          <li>
            Autopilot modely: <span className="muted small">{health?.lead_models?.join(", ") || "–"}</span>
          </li>
          <li>
            Dnes {health?.api_calls_today ?? 0} requestů na free modely{" "}
            <span className="muted small">(sdílený denní limit OpenRouter :free, nuluje se o půlnoci UTC)</span>
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
        <h2>Autopilot</h2>
        <p className="hint">
          Studio řídí Autopilot na free modelech. Pořadí modelů nastavíš proměnnou <code>STUDIO_LEAD_MODELS</code>{" "}
          (OpenRouter <code>:free</code> id, nejsilnější první).
        </p>
        <h3>Druhý názor</h3>
        {LEADS.map((lead) => (
          <label key={lead} className="check">
            <input type="checkbox" checked={second.includes(lead)} onChange={(e) => toggleSecond(lead, e.target.checked)} />
            Přidat review od {LEAD_LABELS[lead]}, když má volný limit
          </label>
        ))}
        <p className="hint">Když došel limit nebo není přihlášený, review se přeskočí a běh pokračuje.</p>
      </section>

      <section className="card">
        <h2>Sdílená paměť (Obsidian)</h2>
        {!vault ? (
          <p className="muted">Načítám…</p>
        ) : !vault.available ? (
          <p className="alert warn">
            Vault nenalezen{vault.path ? <> v <code>{vault.path}</code></> : ""}. Cestu nastavíš proměnnou{" "}
            <code>STUDIO_VAULT</code>.
          </p>
        ) : (
          <>
            <p className="small">
              <code>{vault.path}</code> · {vault.projects?.length ?? 0} projektů · {vault.handoffs?.length ?? 0} otevřených
              předávek
            </p>
            <ul className="plain small">
              {(vault.folders ?? []).map((f) => (
                <li key={f.name}>
                  {f.shared ? <span className="badge good">sdílí se</span> : <span className="badge">soukromé</span>}{" "}
                  {f.name} <span className="muted">({f.notes})</span>
                </li>
              ))}
            </ul>
            <p className="hint">
              Modelům jde jen obsah ze sdílených složek, bez poznámek označených <code>private: true</code> nebo{" "}
              <code>#osobni</code> a bez poznámek s tajnými údaji. E-maily a telefony se skrývají. Zapisuje se jen po
              tvém kliknutí.
            </p>
          </>
        )}
      </section>

      <section className="card">
        <h2>Claude a Codex (druhý názor a převzetí)</h2>
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
