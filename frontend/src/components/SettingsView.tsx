import { useEffect, useState } from "react";
import { api, type Health, type ProviderToday, type SettingsSnapshot, type VaultOverview } from "../api";
import { activeProviders, asAutopilot, BLOCKED_LABELS, PRIVACY_LABELS, providerLabel } from "../options";

interface Props {
  settings: SettingsSnapshot;
  health: Health | null;
  onSaved: (s: SettingsSnapshot) => void;
}

/** Share of the free daily cap used; warn from 80 %. */
export function budgetLevel(p: ProviderToday): "ok" | "warn" | "out" | null {
  if (!p.cap) return null;
  const share = p.used / p.cap;
  return share >= 1 ? "out" : share >= 0.8 ? "warn" : "ok";
}

function Models({ health }: { health: Health }) {
  const providers = activeProviders(health.models);
  return (
    <>
      <p className="small">
        Kód a poznámky dostanou jen:{" "}
        {providers.length ? <strong>{providers.join(", ")}</strong> : <span className="badge bad">nikdo</span>}
        <span className="muted"> (hranice soukromí: {PRIVACY_LABELS[health.privacy_floor]})</span>
      </p>
      {!providers.length && (
        <p className="alert warn">
          Žádný povolený model. Připoj v OmniRoute Groq nebo Cloudflare Workers AI (netrénují na tvých datech).
        </p>
      )}
      <div className="table-wrap">
      <table className="models">
        <thead>
          <tr>
            <th>Model</th>
            <th>Poskytovatel</th>
            <th>Soukromí</th>
            <th>Role</th>
            <th>Stav</th>
          </tr>
        </thead>
        <tbody>
          {health.models.map((m) => (
            <tr key={m.id} className={m.blocked ? "muted" : undefined}>
              <td>
                <code>{m.id}</code>
              </td>
              <td>{providerLabel(m.provider)}</td>
              <td>
                <span className={`badge ${m.privacy === "no_train" ? "good" : "warn"}`}>{PRIVACY_LABELS[m.privacy]}</span>
              </td>
              <td className="small">{[m.lead && "Autopilot", m.coding && "worker"].filter(Boolean).join(", ")}</td>
              <td className="small">
                {m.blocked
                  ? BLOCKED_LABELS[m.blocked]
                  : !health.gateway.reachable
                    ? "OmniRoute nedostupný"
                    : m.cooldown_until
                    ? `pauza do ${new Date(m.cooldown_until * 1000).toLocaleTimeString("cs-CZ")}`
                    : "připraven"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <p className="hint">
        Pořadí a výběr: <code>STUDIO_LEAD_MODELS</code>, <code>STUDIO_WORKER_MODELS</code>. OpenRouter :free povolíš
        až po vypnutí tréninku v jeho nastavení soukromí a <code>STUDIO_OPENROUTER_NO_TRAIN=1</code>. Placený hlavní
        model: <code>STUDIO_PAID_MODELS</code> + <code>STUDIO_ALLOW_PAID=1</code>.
      </p>
    </>
  );
}

function Budget({ health }: { health: Health }) {
  const rows = health.providers_today ?? [];
  if (!rows.length) return <li>Dnes zatím žádné requesty na modely.</li>;
  return (
    <>
      {rows.map((p) => {
        const level = budgetLevel(p);
        return (
          <li key={p.provider}>
            {providerLabel(p.provider)}: {p.calls} requestů dnes
            {p.cap ? (
              <>
                {" "}
                · <span className={`badge ${level === "ok" ? "good" : level === "warn" ? "warn" : "bad"}`}>
                  {Math.round((p.used / p.cap) * 100)} % denního limitu
                </span>
              </>
            ) : null}
            {level === "warn" && <span className="small"> blíží se limit; po vyčerpání (429) router přejde na další model</span>}
          </li>
        );
      })}
    </>
  );
}

export function SettingsView({ settings, health, onSaved }: Props) {
  const [defaults, setDefaults] = useState(settings.defaults);
  const [status, setStatus] = useState<string | null>(null);
  const [vault, setVault] = useState<VaultOverview | null>(null);

  useEffect(() => {
    api.vault().then(setVault, () => setVault({ available: false, path: "" }));
  }, []);

  async function save() {
    try {
      onSaved(await api.saveSettings({ defaults: { ...defaults, orchestration: asAutopilot(defaults.orchestration) } }));
      setStatus("Uloženo.");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
    }
  }

  const cooling = Object.entries(health?.cooldowns ?? {});

  return (
    <div className="settings">
      <h1>Nastavení</h1>

      <section className="card">
        <h2>Připojení</h2>
        <ul className="plain">
          <li>
            OmniRoute:{" "}
            {health?.gateway.reachable ? <span className="badge good">dostupný</span> : <span className="badge bad">nedostupný</span>}
          </li>
          {health && <Budget health={health} />}
          {cooling.map(([model, until]) => (
            <li key={model} className="small muted">
              {model}: pauza po chybě do {new Date(until * 1000).toLocaleTimeString("cs-CZ")}
            </li>
          ))}
        </ul>
        <p className="hint">Limity se nulují o půlnoci UTC. API klíče žijí jen v OmniRoute, Studio je nevidí.</p>
      </section>

      <section className="card">
        <h2>Autopilot a modely</h2>
        <p className="hint">
          Studio řídí jen Autopilot na free modelech, které netrénují na tvých datech. Claude ani Codex přes Studio
          nejdou.
        </p>
        {health ? <Models health={health} /> : <p className="muted">Načítám…</p>}
        <label className="check">
          <input
            type="checkbox"
            checked={defaults.orchestration.plan}
            onChange={(e) =>
              setDefaults({ ...defaults, orchestration: { ...defaults.orchestration, plan: e.target.checked } })
            }
          />
          Před prací nechat Autopilota naplánovat postup
        </label>
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

      <div className="actions">
        <button type="button" className="primary" onClick={() => void save()}>
          Uložit nastavení
        </button>
        {status && <span className="small">{status}</span>}
      </div>
    </div>
  );
}
