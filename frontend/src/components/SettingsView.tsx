import { Fragment, useEffect, useState } from "react";
import { api, type AgentInfo, type AnyLead, type AgentRole, type Health, type ModelInfo, type ModelStats, type ProbeResult, type ProviderToday, type SettingsSnapshot, type VaultOverview } from "../api";
import {
  activeProviders,
  BLOCKED_LABELS,
  gatewayState,
  LEAD_LABELS,
  leadsFor,
  normalizeOrch,
  KEY_SOURCES,
  OMNIROUTE_PROVIDERS_URL,
  PRIVACY_LABELS,
  providerLabel,
} from "../options";
import { lang, LANGS, locale, setLang, t, type Lang } from "../i18n";
import { notificationsSupported } from "../notify";
import { AgentsCard, MemoryFolder, ProvidersCard } from "./SettingsCards";

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

/** The model without the provider prefix (the Provider column shows it):
 * "groq/openai/gpt-oss-120b" → "openai/gpt-oss-120b", "cf/@cf/zai-org/glm" → "zai-org/glm". */
export function modelName(id: string): string {
  return id.replace(/^[^/]+\//, "").replace(/^@cf\//, "");
}

/** "reliability 92% · 3/4 accepted" from the router's stats (ADR 0006); empty without data. */
export function reliabilityText(st: ModelStats | undefined): string {
  if (!st || st.calls === 0) return "";
  const parts = [t("reliability {pct}%", { pct: Math.round(((st.calls - st.failed) / st.calls) * 100) })];
  if (st.reviewed) parts.push(t("{a}/{b} accepted", { a: st.accepted, b: st.reviewed }));
  return parts.join(" · ");
}

/** One line for a probe result. */
export function probeText(r: ProbeResult): string {
  if (r.ok) {
    const ms = r.latency_ms ? ` · ${r.latency_ms} ms` : "";
    return (r.tools ? t("works, calls tools") : t("answers but does not call tools (unusable for Studio)")) + ms;
  }
  if (r.kind === "privacy" || r.kind === "paid") return t("not sent: blocked by the privacy / paid rule");
  return `${t("error")}${r.status ? ` ${r.status}` : ""}: ${r.detail ?? r.kind ?? t("unknown")}`;
}

/** Grant browser notifications; the request must come from a click. */
function NotificationSetting() {
  const [perm, setPerm] = useState(() => (notificationsSupported() ? Notification.permission : "unsupported"));
  if (perm === "unsupported") return <p className="hint">{t("This browser cannot show notifications. The tab title shows how many tasks are waiting.")}</p>;
  if (perm === "granted") return <p>{t("On: Studio notifies you when a task waits for you or ends while Studio is not in view.")}</p>;
  if (perm === "denied") return <p className="hint">{t("Notifications are blocked in the browser; allow them in the site settings. The tab title shows how many tasks are waiting.")}</p>;
  return (
    <>
      <p>{t("Studio notifies you when a task waits for you or ends while Studio is not in view.")}</p>
      <button type="button" className="primary small" onClick={() => void Notification.requestPermission().then(setPerm)}>
        {t("Allow notifications")}
      </button>
    </>
  );
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
        {state === "busy" ? t("Testing…") : t("Test")}
      </button>
      {typeof state === "object" && (
        <span className={`badge ${state.ok && state.tools ? "good" : "bad"}`}>{probeText(state)}</span>
      )}
      {error && <span className="badge bad">{error}</span>}
    </td>
  );
}

/** Whether OmniRoute already offers a provider's models: "connected", "setup" (offered but
 * every one paused for a missing key/setting), "missing", or null when OmniRoute is down. */
export function keyStatus(provider: string, health: Health | null): "connected" | "setup" | "missing" | null {
  if (!health?.gateway.reachable || !health.models) return null;
  const offered = health.models.filter((m) => m.provider === provider && m.blocked !== "not_connected");
  if (!offered.length) return "missing";
  return offered.every((m) => m.cooldown_kind === "setup" && m.cooldown_until) ? "setup" : "connected";
}

const KEY_BADGES = {
  connected: (
    <span className="badge good" title={t("OmniRoute offers its models; \"Test\" shows whether the key works.")}>
      {t("in OmniRoute")}
    </span>
  ),
  setup: <span className="badge warn">{t("setup missing")}</span>,
  missing: <span className="badge">{t("not connected")}</span>,
};

function KeySources({ health }: { health: Health | null }) {
  return (
    <div className="key-sources">
      <p className="small">
        <strong>{t("Where to get keys and where to put them.")}</strong> {t("Create a key with the provider and add it in")}{" "}
        <a href={OMNIROUTE_PROVIDERS_URL} target="_blank" rel="noreferrer">
          OmniRoute → Providers
        </a>
        . {t("Studio never sees these keys. Then click \"Test\" next to a model.")}
      </p>
      <ul className="small">
        {KEY_SOURCES.map((k) => (
          <li key={k.provider}>
            {(() => {
              const st = keyStatus(k.provider, health);
              return st ? <>{KEY_BADGES[st]} </> : null;
            })()}
            <strong>{k.label}</strong>:{" "}
            <a href={k.signup} target="_blank" rel="noreferrer">
              {k.signupLabel ?? t("create a key")}
            </a>{" "}
            → {t("in OmniRoute: \"{name}\".", { name: k.omniroute })} {k.note}
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
  else if (!health.gateway.reachable) state = t("no provider reachable");
  else if (m.cooldown_until && m.cooldown_kind === "setup") {
    // A missing key or provider setting does not fix itself (Cloudflare needs the Account ID).
    const note = KEY_SOURCES.find((k) => k.provider === m.provider)?.note;
    state = (
      <>
        <span className="badge warn">{t("Setup missing in OmniRoute")}</span>
        <div>
          {note}{" "}
          <a href={OMNIROUTE_PROVIDERS_URL} target="_blank" rel="noreferrer">
            {t("Open OmniRoute")}
          </a>
        </div>
      </>
    );
  } else if (m.cooldown_until) state = t("paused until {time}", { time: new Date(m.cooldown_until * 1000).toLocaleTimeString(locale()) });
  else state = t("ready");
  return (
    <>
      {state}
      {(m.demoted || (m.demotion_reasons?.length ?? 0) > 0) && (
        <div>
          <span className="badge warn" title={m.demotion_reasons?.join("; ") || t("The router tries it only after more reliable models.")}>
            {t("fails often")}
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
            <th>{t("Model")}</th>
            <th>{t("Provider")}</th>
            <th>{t("Privacy")}</th>
            <th>{t("State")}</th>
            <th>{t("Test")}</th>
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
                <div className="muted small nowrap">{[m.lead && "Autopilot", m.coding && t("code")].filter(Boolean).join(" · ")}</div>
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
        {t("Code and notes go only to:")}{" "}
        {providers.length ? <strong>{providers.join(", ")}</strong> : <span className="badge bad">{t("nobody")}</span>}
        <span className="muted"> ({t("privacy floor: {floor}", { floor: PRIVACY_LABELS[health.privacy_floor] })})</span>
      </p>
      {!providers.length && (
        <p className="alert warn">
          {t("No allowed model. Turn on a provider below (Groq, OVHcloud, local Ollama…); they do not train on your data.")}
        </p>
      )}
      <ModelTable health={health} models={used} />
      {unused.length > 0 && (
        <details className="advanced">
          <summary>{t("Unused models ({n}): they may train or are paid", { n: unused.length })}</summary>
          <ModelTable health={health} models={unused} />
        </details>
      )}
      <details className="advanced">
        <summary>{t("Advanced: order and a paid model")}</summary>
        <p className="hint">
        {t("Order and choice:")} <code>STUDIO_LEAD_MODELS</code>, <code>STUDIO_WORKER_MODELS</code>.{" "}
        {t("Allow OpenRouter :free only after turning off training in its privacy settings, with")}{" "}
        <code>STUDIO_OPENROUTER_NO_TRAIN=1</code>. {t("Paid main model:")} <code>STUDIO_PAID_MODELS</code> +{" "}
        <code>STUDIO_ALLOW_PAID=1</code>.
        </p>
      </details>
    </>
  );
}

function Budget({ health }: { health: Health }) {
  const rows = health.providers_today ?? [];
  if (!rows.length) return <li>{t("No model requests today yet.")}</li>;
  return (
    <>
      {rows.map((p) => {
        const level = budgetLevel(p);
        return (
          <li key={p.provider}>
            {providerLabel(p.provider)}: {t("{n} requests today", { n: p.calls })}
            {p.cap ? (
              <>
                {" "}
                · <span className={`badge ${level === "ok" ? "good" : level === "warn" ? "warn" : "bad"}`}>
                  {t("{pct}% of the daily limit", { pct: Math.round((p.used / p.cap) * 100) })}
                </span>
              </>
            ) : null}
            {level === "warn" && <span className="small"> {t("close to the limit; once it is used up (429) the router moves to the next model")}</span>}
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
  // null until loaded: normalizing against an empty list would drop saved agent choices.
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);

  useEffect(() => {
    api.vault().then(setVault, () => setVault({ available: false, path: "" }));
    api.agents().then(setAgents, () => setAgents(null));
  }, []);

  const orch = agents ? normalizeOrch(defaults.orchestration, agents) : defaults.orchestration;
  const setLead = (key: "primary" | "reviewer" | "takeover", lead: AnyLead) =>
    setDefaults({ ...defaults, orchestration: normalizeOrch({ ...orch, [key]: lead }, agents ?? []) });

  async function switchLang(next: Lang) {
    // The UI language is per browser; a failed save only leaves event text as it was.
    await api.setLanguage(next).catch(() => undefined);
    setLang(next);
  }

  async function save() {
    try {
      onSaved(await api.saveSettings({ defaults: { ...defaults, orchestration: orch } }));
      setStatus(t("Saved."));
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
    }
  }

  const cooling = Object.entries(health?.cooldowns ?? {});

  return (
    <div className="settings">
      <h1>{t("Settings")}</h1>

      <section className="card">
        <h2>{t("Models")}</h2>
        <p className="hint">
          {t("The default is Autopilot on free models that do not train on your data. Turn on Claude Code and Codex in the Agents card below.")}
        </p>
        {health ? <Models health={health} /> : <p className="muted">{t("Loading…")}</p>}
        <label className="check">
          <input
            type="checkbox"
            role="switch"
            checked={defaults.orchestration.plan}
            onChange={(e) =>
              setDefaults({ ...defaults, orchestration: { ...defaults.orchestration, plan: e.target.checked } })
            }
          />
          {t("Plan the approach before the work")}
        </label>
        {agents?.some((a) => a.installed && Object.values(a.roles).some(Boolean)) && (
          <div className="composer-options">
            {(
              [
                ["primary", "plan", t("Brief and plan")],
                ["reviewer", "review", t("Review")],
                ["takeover", "takeover", t("Takeover")],
              ] as const
            ).map(([key, role, label]) => (
              <DefaultLead key={key} label={label} role={role} agents={agents ?? []} value={orch[key]}
                onChange={(lead) => setLead(key, lead)} />
            ))}
          </div>
        )}
        <div className="actions">
          <button type="button" className="primary" disabled={!agents} onClick={() => void save()}>
            {t("Save")}
          </button>
          {status && <span className="small">{status}</span>}
        </div>
      </section>

      <section className="card">
        <h2>{t("Providers")}</h2>
        <ProvidersCard />
        <details className="advanced">
          <summary>{t("Through OmniRoute (optional)")}</summary>
          <KeySources health={health} />
        </details>
      </section>

      <section className="card">
        <h2>{t("Agents")}</h2>
        <AgentsCard onChanged={setAgents} />
      </section>

      <section className="card">
        <h2>{t("Today")}</h2>
        <ul className="plain">
          <li>
            {health ? (
              <span className={`badge ${gatewayState(health).ok ? "good" : "bad"}`}>{gatewayState(health).text}</span>
            ) : (
              t("Loading…")
            )}
          </li>
          {health && <Budget health={health} />}
          {cooling.map(([model, until]) => (
            <li key={model} className="small muted">
              {model}: {t("paused after an error until {time}", { time: new Date(until * 1000).toLocaleTimeString(locale()) })}
            </li>
          ))}
        </ul>
        <p className="hint">{t("Limits reset at midnight UTC.")}</p>
      </section>

      <section className="card">
        <h2>{t("Notifications")}</h2>
        <NotificationSetting />
      </section>

      <section className="card">
        <h2>{t("Language")}</h2>
        <label className="chip-field">
          <span>{t("Interface language")}</span>
          <select value={lang()} onChange={(e) => switchLang(e.target.value as Lang)}>
            {LANGS.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="card">
        <h2>{t("Memory")}</h2>
        <p className="hint">
          {t(
            "An optional folder of Markdown notes (an Obsidian vault, for example). Studio takes project context from it and writes to it after your click. Without a folder nothing is shared.",
          )}
        </p>
        <MemoryFolder vault={vault} onChanged={setVault} />
        {!vault ? (
          <p className="muted">{t("Loading…")}</p>
        ) : !vault.available ? (
          vault.path ? (
            <p className="alert warn">
              {t("The folder does not exist:")} <code>{vault.path}</code>
            </p>
          ) : null
        ) : (
          <>
            <p className="small">
              {t("{p} projects · {h} open handoffs", { p: vault.projects?.length ?? 0, h: vault.handoffs?.length ?? 0 })}
            </p>
            <ul className="plain small">
              {(vault.folders ?? []).map((f) => (
                <li key={f.name}>
                  {f.shared ? <span className="badge good">{t("shared")}</span> : <span className="badge">{t("private")}</span>}{" "}
                  {f.name} <span className="muted">({f.notes})</span>
                </li>
              ))}
            </ul>
            <details className="advanced">
              <summary>{t("What is sent to models")}</summary>
              <p className="hint">
                {t("Only content from shared folders, without notes marked")} <code>private: true</code> {t("or")}{" "}
                <code>#osobni</code>{" "}
                {t("and without notes holding secrets. E-mails and phone numbers are hidden. Memory is written only after your click. Folder:")}{" "}
                <code>{vault.path}</code>
              </p>
            </details>
          </>
        )}
      </section>
    </div>
  );
}

function DefaultLead({
  label,
  role,
  agents,
  value,
  onChange,
}: {
  label: string;
  role: AgentRole;
  agents: AgentInfo[];
  value: AnyLead;
  onChange: (lead: AnyLead) => void;
}) {
  return (
    <label className="chip-field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value as AnyLead)}>
        {leadsFor(role, agents).map((l) => (
          <option key={l} value={l}>
            {LEAD_LABELS[l]}
          </option>
        ))}
      </select>
    </label>
  );
}
