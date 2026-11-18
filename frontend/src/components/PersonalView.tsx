import { useEffect, useRef, useState } from "react";
import { api, type PersonalHistory, type PersonalPreview, type PersonalStatus } from "../api";

export function PersonalView() {
  const [status, setStatus] = useState<PersonalStatus | null>(null);
  const [session, setSession] = useState(() => { try { return localStorage.getItem("studio-personal-session") ?? ""; } catch { return ""; } });
  const [history, setHistory] = useState<PersonalHistory>({ messages: [], claims: [] });
  const [exclusions, setExclusions] = useState("");
  const [query, setQuery] = useState("");
  const [notes, setNotes] = useState<{ path: string; snippet?: string; text?: string }[]>([]);
  const [paths, setPaths] = useState<string[]>([]);
  const [preview, setPreview] = useState<PersonalPreview | null>(null);
  const [error, setError] = useState("");
  const [claim, setClaim] = useState("");
  const [claimPreview, setClaimPreview] = useState<{ claim_id: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const refresh = async () => { const s = await api.personalStatus(); setStatus(s); setExclusions(s.policy?.exclusions.join("\n") ?? ""); };
  useEffect(() => { void refresh().catch(() => setError("Stav osobního režimu nelze načíst.")); }, []);
  useEffect(() => { let alive = true; if (session) api.personalHistory(session).then((h) => { if (alive) setHistory(h); }, () => { if (alive) { setSession(""); setError("Uložená relace není dostupná. Vytvoř novou."); } }); return () => { alive = false; }; }, [session]);
  const act = async (fn: () => Promise<unknown>) => { if (guard.current) return; guard.current = true; setBusy(true); setError(""); try { await fn(); } catch (e) { setPreview(null); setError(e instanceof Error ? e.message : "Akce selhala."); } finally { guard.current = false; setBusy(false); } };
  const changeQuery = (value: string) => { setQuery(value); setPreview(null); };
  return <section className="knowledge-view pad">
    <header><h1>Osobní režim</h1><p className="muted">Lokální hledání a plánování. Bez příkazů, úprav repozitářů či automatických úkolů.</p></header>
    <p className="alert">„Bez trénování“ neznamená lokální zpracování ani nulové uchovávání dat. Cloud dostane jen výslovně schválený obsah.</p>
    <p className="small muted">Historie zůstává v šifrovaném úložišti; nepřidává se automaticky do dalších otázek. DPAPI není OS sandbox: používej Studio jen s důvěryhodnými projekty.</p>
    {error && <p className="alert error" role="alert">{error}</p>}
    {!status ? <p role="status">Načítám…</p> : !status.available ? <p className="alert error">Chráněné úložiště není dostupné. Osobní režim je vypnutý; vývojové úkoly fungují dál.</p> : <>
      {!status.gateway_privacy_ready && <p className="alert" role="status">Cloud je zablokovaný: není ověřeno vypnutí ukládání osobních payloadů v OmniRoute. Lokální hledání lze používat.</p>}
      <details open={!status.policy?.enabled}><summary>Rozsah lokálního hledání a výjimky</summary><p>Nejprve zkontroluj výjimky. Soukromé značky, secrety, přílohy, Recorder a archiv chatů jsou vždy blokované.</p><label>Vyloučené cesty (jedna na řádku)<textarea rows={4} value={exclusions} disabled={busy} onChange={(e) => { setExclusions(e.target.value); setPreview(null); }} /></label><div className="knowledge-controls"><button disabled={busy} onClick={() => void act(async () => { await api.personalPolicy(exclusions.split("\n").map((s) => s.trim()).filter(Boolean), true); setNotes([]); setPaths([]); setPreview(null); await refresh(); })}>Schválit rozsah a zapnout hledání</button><button disabled={busy || !status.policy?.enabled} onClick={() => void act(async () => { await api.personalPolicy(exclusions.split("\n").filter(Boolean), false); setNotes([]); setPaths([]); setPreview(null); await refresh(); })}>Vypnout hledání</button></div></details>
      <button disabled={busy} onClick={() => void act(async () => { const s = await api.personalSession(); setPreview(null); setHistory({ messages: [], claims: [] }); setSession(s.session_id); try { localStorage.setItem("studio-personal-session", s.session_id); } catch { /* Only a UUID is retained by the browser. */ } })}>Nová osobní relace</button>
      <form onSubmit={(e) => { e.preventDefault(); void act(async () => { setPreview(null); setPaths([]); setNotes(await api.personalSearch(query)); }); }}><label>Otázka / lokální hledání<textarea rows={3} value={query} disabled={busy} onChange={(e) => changeQuery(e.target.value)} /></label><button disabled={busy || !query.trim() || !status.policy?.enabled}>Hledat pouze lokálně</button></form>
      {notes.map((n) => <label className="knowledge-card" key={n.path}><input type="checkbox" checked={paths.includes(n.path)} disabled={busy} onChange={(e) => { setPreview(null); setPaths((p) => e.target.checked ? [...p, n.path] : p.filter((x) => x !== n.path)); }} /> {n.path}<span className="knowledge-text">{n.snippet ?? n.text}</span></label>)}
      <button disabled={busy || !session || !query.trim() || !status.gateway_privacy_ready || !status.policy?.enabled} onClick={() => void act(async () => setPreview(await api.personalPreview(session, query, paths)))}>Připravit náhled pro cloud</button>
      {preview && <section className="knowledge-card"><h2>Schválení jednoho cloudového běhu</h2><p>Poskytovatel: {preview.provider} · Model: {preview.model}</p><h3>Přesná otázka</h3><p className="knowledge-text">{preview.question}</p>{preview.snippets.map((n) => <details open key={n.path}><summary>{n.path}</summary><pre className="knowledge-text">{n.text}</pre></details>)}<p className="small muted">Schválení vyprší: {new Date(preview.expires_at * 1000).toLocaleTimeString()}</p><button className="primary" disabled={busy} onClick={() => void act(async () => { const id = preview.approval_id; setPreview(null); await api.personalApprove(id, session); await api.personalAnswer(id, session); setHistory(await api.personalHistory(session)); })}>Schválit přesný obsah a odeslat</button><button disabled={busy} onClick={() => setPreview(null)}>Zrušit</button></section>}
      <h2>Šifrovaná historie relace</h2>{!history.messages.length && <p className="muted">Zatím žádná zpráva nebyla odeslána.</p>}{history.messages.map((m, i) => <article className="knowledge-card" key={i}><h3>{m.role === "user" ? "Ty" : "Osobní plánovač"}</h3><p className="knowledge-text">{m.content}</p></article>)}
      {session && <><h2>Osobní znalost (jen lokálně)</h2><label>Krátké tvrzení<textarea value={claim} disabled={busy} onChange={(e) => { setClaim(e.target.value); setClaimPreview(null); }} /></label><button disabled={busy || !claim.trim()} onClick={() => void act(async () => setClaimPreview(await api.personalClaim(session, claim)))}>Navrhnout znalost</button>{claimPreview && <article className="knowledge-card"><p>{claimPreview.text}</p><button disabled={busy} onClick={() => void act(async () => { await api.personalApproveClaim(claimPreview.claim_id, session); setClaimPreview(null); setClaim(""); setPreview(null); setHistory(await api.personalHistory(session)); })}>Schválit do šifrované paměti</button></article>}</>}
      {history.claims.length > 0 && <><h2>Schválené osobní znalosti</h2>{history.claims.map((c) => <p key={c.claim_id}>{c.text}</p>)}</>}
    </>}
  </section>;
}
