import { useEffect, useState } from "react";
import { api, type AgentInfo, type AgentRole, type ProviderInfo, type VaultOverview } from "../api";
import { LEAD_LABELS, PRIVACY_LABELS, ROLE_LABELS } from "../options";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Direct OpenAI-compatible providers: work without OmniRoute. Keys go to the OS keyring. */
export function ProvidersCard({ onChanged }: { onChanged?: () => void }) {
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.providers().then(setProviders, (e) => setError(message(e)));
  }, []);

  async function update(name: string, body: { enabled?: boolean; key?: string }) {
    setError(null);
    try {
      setProviders(await api.setProvider(name, body));
      setKeys((k) => ({ ...k, [name]: "" }));
      onChanged?.();
    } catch (e) {
      setError(message(e));
    }
  }

  if (!providers) return error ? <p className="alert error">{error}</p> : <p className="muted">Načítám…</p>;
  return (
    <>
      <p className="hint">
        Studio volá tyto poskytovatele přímo, i bez OmniRoute. Klíč se uloží do trezoru systému (Windows Credential
        Manager, macOS Keychain, Secret Service), ne do Studia. Stačí i proměnná prostředí.
      </p>
      <table className="models providers">
        <tbody>
          {providers.map((p) => (
            <tr key={p.name}>
              <td>
                <strong>{p.label}</strong>
                <div className="muted small">
                  {PRIVACY_LABELS[p.privacy] ?? p.privacy}
                  {p.local ? " · na tvém počítači" : ""}
                </div>
              </td>
              <td>
                {p.local && p.active && !p.running ? (
                  <span className="badge warn">neběží</span>
                ) : p.active ? (
                  <span className="badge good">aktivní</span>
                ) : (
                  <span className="badge">vypnuto</span>
                )}
                {p.missing_env.length > 0 && (
                  <div className="small warn">chybí {p.missing_env.join(", ")}</div>
                )}
              </td>
              <td className="provider-actions">
                {p.keyless && !p.local && (
                  <label className="check small">
                    <input
                      type="checkbox"
                      role="switch"
                      checked={p.active}
                      onChange={(e) => void update(p.name, { enabled: e.target.checked })}
                    />
                    bez klíče
                  </label>
                )}
                {p.key_env && (
                  <form
                    className="key-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (keys[p.name]?.trim()) void update(p.name, { key: keys[p.name].trim() });
                    }}
                  >
                    <input
                      type="password"
                      autoComplete="off"
                      placeholder={p.has_key ? "klíč uložen" : p.key_env}
                      aria-label={`Klíč pro ${p.label}`}
                      value={keys[p.name] ?? ""}
                      onChange={(e) => setKeys((k) => ({ ...k, [p.name]: e.target.value }))}
                    />
                    <button type="submit" className="small" disabled={!keys[p.name]?.trim()}>
                      Uložit
                    </button>
                    {p.has_key && (
                      <button type="button" className="small" onClick={() => void update(p.name, { key: "" })}>
                        Smazat
                      </button>
                    )}
                  </form>
                )}
                {p.signup && (
                  <a className="small" href={p.signup} target="_blank" rel="noreferrer">
                    {p.local ? "stáhnout" : "získat klíč"}
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {error && <p className="alert error">{error}</p>}
    </>
  );
}

const ROLES: AgentRole[] = ["plan", "review", "takeover"];

/** Claude Code and Codex: optional, per role, off until the user turns a role on. */
export function AgentsCard({ onChanged }: { onChanged?: (agents: AgentInfo[]) => void }) {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.agents().then(setAgents, (e) => setError(message(e)));
  }, []);

  async function toggle(a: AgentInfo, role: AgentRole, on: boolean) {
    setError(null);
    try {
      const next = await api.setAgent(a.name, { [role]: on });
      setAgents(next);
      onChanged?.(next);
    } catch (e) {
      setError(message(e));
    }
  }

  if (!agents) return error ? <p className="alert error">{error}</p> : <p className="muted">Načítám…</p>;
  return (
    <>
      <p className="hint">
        Volitelné. Claude Code a Codex běží jako příkazy v terminálu na tvém předplatném. Čtou kód projektu, ten tedy
        odchází k Anthropic / OpenAI, a podle pravidel svého CLI mohou číst i jiné soubory na počítači. Claude nedostane
        shell; Codex spouští příkazy jen ve svém sandboxu bez sítě. Změny mimo rozsah úkolu Studio zastaví, review
        vždy dělá jiný agent než ten, kdo kód napsal, a když agentovi dojde limit, běh se zastaví a zeptá se.
      </p>
      <ul className="plain agents">
        {agents.map((a) => (
          <li key={a.name}>
            <strong>{LEAD_LABELS[a.name]}</strong>{" "}
            {a.installed ? (
              <span className="badge good">{a.version ?? "nainstalováno"}</span>
            ) : (
              <span className="badge">nenainstalováno</span>
            )}
            <div className="agent-roles">
              {ROLES.map((role) => (
                <label key={role} className="check small">
                  <input
                    type="checkbox"
                    role="switch"
                    disabled={!a.installed && !a.roles[role]}
                    checked={a.roles[role]}
                    onChange={(e) => void toggle(a, role, e.target.checked)}
                  />
                  {ROLE_LABELS[role]}
                </label>
              ))}
            </div>
          </li>
        ))}
      </ul>
      {error && <p className="alert error">{error}</p>}
    </>
  );
}

/** Choose the memory folder (Markdown notes). Nothing is shared until one is set. */
export function MemoryFolder({ vault, onChanged }: { vault: VaultOverview | null; onChanged: (v: VaultOverview) => void }) {
  const [path, setPath] = useState(vault?.path ?? "");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setPath(vault?.path ?? ""), [vault?.path]);

  async function save(next: string | null) {
    setError(null);
    try {
      onChanged(await api.setMemoryPath(next));
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <form
      className="key-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save(path.trim() || null);
      }}
    >
      <input
        value={path}
        onChange={(e) => setPath(e.target.value)}
        placeholder="C:\Users\ty\Notes nebo ~/notes"
        aria-label="Paměťová složka"
        spellCheck={false}
      />
      <button type="submit" className="small">
        Uložit
      </button>
      {vault?.path && (
        <button type="button" className="small" onClick={() => void save(null)}>
          Odpojit
        </button>
      )}
      {error && <span className="alert error small">{error}</span>}
    </form>
  );
}
