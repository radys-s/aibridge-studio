import { Fragment, useEffect, useState } from "react";
import { api, type Health, type ModelInfo, type ModelStats, type ProbeResult, type ProviderToday, type SettingsSnapshot, type VaultOverview } from "../api";
import {
  activeProviders,
  asAutopilot,
  BLOCKED_LABELS,
  KEY_SOURCES,
  OMNIROUTE_PROVIDERS_URL,
  PRIVACY_LABELS,
  providerLabel,
} from "../options";

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

/** "spolehlivost 92 % · 3/4 přijato" from the router's stats (ADR 0006); empty without data. */
/** The model without the OmniRoute provider prefix (the Poskytovatel column shows it):
 * "groq/openai/gpt-oss-120b" → "openai/gpt-oss-120b", "cf/@cf/zai-org/glm" → "zai-org/glm". */
export function modelName(id: string): string {
  return id.replace(/^[^/]+\//, "").replace(/^@cf\//, "");
}

export function reliabilityText(st: ModelStats | undefined): string {
  if (!st || st.calls === 0) return "";
  const parts = [`spolehlivost ${Math.round(((st.calls - st.failed) / st.calls) * 100)} %`];
  if (st.reviewed) parts.push(`${st.accepted}/${st.reviewed} přijato`);
  return parts.join(" · ");
}

/** One line for a probe result. */
export function probeText(r: ProbeResult): string {
  if (r.ok) {
    const ms = r.latency_ms ? ` · ${r.latency_ms} ms` : "";
    return r.tools ? `funguje, volá nástroje${ms}` : `odpovídá, ale nevolá nástroje (pro Studio nepoužitelný)${ms}`;
  }
  if (r.kind === "privacy" || r.kind === "paid") return "neodesláno: blokuje to pravidlo soukromí / placení";
  return `chyba${r.status ? ` ${r.status}` : ""}: ${r.detail ?? r.kind ?? "neznámá"}`;
}

function ProbeCell({ model, disabled }: { model: string; disabled: boolean }) {
  const [state, setState] = useState<"idle" | "busy" | ProbeResult>("idle");
  const [error, setError] = useState("");
  async function run() {
    setState("busy");
    setError("");
    try {
      setState(await api.probeModel(model));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("idle");
    }
  }
  return (
    <td className="small probe">
      <button type="button" className="small" disabled={disabled || state === "busy"} onClick={() => void run()}>
        {state === "busy" ? "Testuji…" : "Otestovat"}
      </button>
      {typeof state === "object" && (
        <span className={`badge ${state.ok && state.tools ? "good" : "bad"}`}>{probeText(state)}</span>
      )}
      {error && <span className="badge bad">{error}</span>}
    </td>
  );
}

function KeySources() {
  return (
    <div className="key-sources">
      <p className="small">
        <strong>Kde vzít klíče a kam je vložit.</strong> Klíč si založ u poskytovatele a vlož ho v{" "}
        <a href={OMNIROUTE_PROVIDERS_URL} target="_blank" rel="noreferrer">
          OmniRoute → Providers
        </a>
        . Studio klíče nikdy nevidí. Potom u modelu klikni na „Otestovat“.
      </p>
      <ul className="small">
        {KEY_SOURCES.map((k) => (
          <li key={k.provider}>
            <strong>{k.label}</strong>:{" "}
            <a href={k.signup} target="_blank" rel="noreferrer">
              založit klíč
            </a>{" "}
            → v OmniRoute „{k.omniroute}“. {k.note}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ModelState({ health, m }: { health: Health; m: ModelInfo }) {
  const reliability = reliabilityText(m.stats);
  let state;
  if (m.blocked) state = BLOCKED_LABELS[m.blocked];
  else if (!health.gateway.reachable) state = "OmniRoute nedostupný";
  else if (m.cooldown_until && m.cooldown_kind === "setup") {
    // A missing key or provider setting does not fix itself (Cloudflare needs the Account ID).
    const note = KEY_SOURCES.find((k) => k.provider === m.provider)?.note;
    state = (
      <>
        <span className="badge warn">Chybí nastavení v OmniRoute</span>
        <div>
          {note}{" "}
          <a href={OMNIROUTE_PROVIDERS_URL} target="_blank" rel="noreferrer">
            Otevřít OmniRoute
          </a>
        </div>
      </>
    );
  } else if (m.cooldown_until) state = `pauza do ${new Date(m.cooldown_until * 1000).toLocaleTimeString("cs-CZ")}`;
  else state = "připraven";
  return (
    <>
      {state}
      {(m.demoted || (m.demotion_reasons?.length ?? 0) > 0) && (
        <div>
          <span className="badge warn" title={m.demotion_reasons?.join("; ") || "Router ho zkouší až po spolehlivějších modelech."}>
            často selhává
          </span>
        </div>
      )}
      {reliability && <div className="muted">{reliability}</div>}
    </>
  );
}

function ModelTable({ health, models }: { health: Health; models: ModelInfo[] }) {
  return (
    <div className="table-wrap">
      <table className="models">
        <thead>
          <tr>
            <th>Model</th>
            <th>Poskytovatel</th>
            <th>Soukromí</th>
            <th>Stav</th>
            <th>Test</th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.id} className={m.blocked ? "muted" : undefined}>
              <td className="model-id" title={m.id}>
                {modelName(m.id)
                  .split("/")
                  .map((part, i) => (
                    <Fragment key={i}>
                      {i > 0 && "/"}
                      {i > 0 && <wbr />}
                      <span className="nowrap">{part}</span>
                    </Fragment>
                  ))}
              </td>
              <td>
                {providerLabel(m.provider)}
                <div className="muted small nowrap">{[m.lead && "Autopilot", m.coding && "kód"].filter(Boolean).join(" · ")}</div>
              </td>
              <td>
                <span className={`badge ${m.privacy === "no_train" ? "good" : "warn"}`}>{PRIVACY_LABELS[m.privacy]}</span>
              </td>
              <td className="small">
                <ModelState health={health} m={m} />
              </td>
              <ProbeCell model={m.id} disabled={m.blocked === "privacy" || m.blocked === "paid"} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Models({ health }: { health: Health }) {
  const providers = activeProviders(health.models);
  // Models the privacy floor or the paid switch rule out are clutter; they sit one click away.
  const unused = health.models.filter((m) => m.blocked === "privacy" || m.blocked === "paid");
  const used = health.models.filter((m) => !unused.includes(m));
  return (
    <>
      <p className="small">
        Kód a poznámky dostanou jen:{" "}
        {providers.length ? <strong>{providers.join(", ")}</strong> : <span className="badge bad">nikdo</span>}
        <span className="muted"> (hranice soukromí: {PRIVACY_LABELS[health.privacy_floor]})</span>
      </p>
      {!providers.length && (
        <p className="alert warn">
          Žádný povolený model. Připoj v OmniRoute Groq, Cloudflare Workers AI nebo Cerebras
          (netrénují na tvých datech).
        </p>
      )}
      <ModelTable health={health} models={used} />
      {unused.length > 0 && (
        <details className="advanced">
          <summary>Nepoužívané modely ({unused.length}) – mohou trénovat nebo jsou placené</summary>
          <ModelTable health={health} models={unused} />
        </details>
      )}
      <details className="advanced">
        <summary>Pro pokročilé: pořadí a placený model</summary>
        <p className="hint">
        Pořadí a výběr: <code>STUDIO_LEAD_MODELS</code>, <code>STUDIO_WORKER_MODELS</code>. OpenRouter :free povolíš
        až po vypnutí tréninku v jeho nastavení soukromí a <code>STUDIO_OPENROUTER_NO_TRAIN=1</code>. Placený hlavní
        model: <code>STUDIO_PAID_MODELS</code> + <code>STUDIO_ALLOW_PAID=1</code>.
        </p>
      </details>
    </>
  );
}

function Budget({ health }: { health: Health }) {
  const rows = health.providers_today ?? [];
  if (!rows.length) return <li>Dnes zatím žádné dotazy na modely.</li>;
  return (
    <>
      {rows.map((p) => {
        const level = budgetLevel(p);
        return (
          <li key={p.provider}>
            {providerLabel(p.provider)}: {p.calls} dotazů dnes
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
        <h2>Modely</h2>
        <p className="hint">
          Studio řídí jen Autopilot na free modelech, které netrénují na tvých datech. Claude ani Codex přes Studio
          nejdou.
        </p>
        {health ? <Models health={health} /> : <p className="muted">Načítám…</p>}
        <label className="check">
          <input
            type="checkbox"
            role="switch"
            checked={defaults.orchestration.plan}
            onChange={(e) =>
              setDefaults({ ...defaults, orchestration: { ...defaults.orchestration, plan: e.target.checked } })
            }
          />
          Před prací nechat Autopilota naplánovat postup
        </label>
        <div className="actions">
          <button type="button" className="primary" onClick={() => void save()}>
            Uložit
          </button>
          {status && <span className="small">{status}</span>}
        </div>
      </section>

      <section className="card">
        <h2>Klíče</h2>
        <KeySources />
      </section>

      <section className="card">
        <h2>Dnes</h2>
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
        <p className="hint">Limity se nulují o půlnoci UTC.</p>
      </section>

      <section className="card">
        <h2>Paměť</h2>
        {!vault ? (
          <p className="muted">Načítám…</p>
        ) : !vault.available ? (
          <p className="alert warn">
            Obsidian vault nenalezen{vault.path ? <> v <code>{vault.path}</code></> : ""}. Cestu nastavíš proměnnou{" "}
            <code>STUDIO_VAULT</code>.
          </p>
        ) : (
          <>
            <p className="small">
              Obsidian · {vault.projects?.length ?? 0} projektů · {vault.handoffs?.length ?? 0} otevřených předávek
            </p>
            <ul className="plain small">
              {(vault.folders ?? []).map((f) => (
                <li key={f.name}>
                  {f.shared ? <span className="badge good">sdílí se</span> : <span className="badge">soukromé</span>}{" "}
                  {f.name} <span className="muted">({f.notes})</span>
                </li>
              ))}
            </ul>
            <details className="advanced">
              <summary>Co se posílá modelům</summary>
              <p className="hint">
                Jen obsah ze sdílených složek, bez poznámek označených <code>private: true</code> nebo{" "}
                <code>#osobni</code> a bez poznámek s tajnými údaji. E-maily a telefony se skrývají. Do paměti se
                zapisuje jen po tvém kliknutí. Vault: <code>{vault.path}</code>
              </p>
            </details>
          </>
        )}
      </section>
    </div>
  );
}
