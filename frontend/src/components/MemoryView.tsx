import { useEffect, useRef, useState } from "react";
import { api, type MemoryCandidate, type MemoryItem, type MemoryProject } from "../api";

export function MemoryView() {
  const [projects, setProjects] = useState<MemoryProject[]>([]);
  const [project, setProject] = useState("");
  const [path, setPath] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);
  const [packet, setPacket] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const generation = useRef(0);
  useEffect(() => { api.memoryProjects().then(setProjects, () => setError("Projekty nelze načíst.")); }, []);
  const load = async (id: string) => {
    const g = ++generation.current;
    setItems([]); setCandidates([]); setPacket("");
    if (!id) return;
    try {
      const [facts, proposals] = await Promise.all([api.memoryItems(id, query), api.memoryCandidates(id)]);
      if (generation.current === g) { setItems(facts); setCandidates(proposals); }
    } catch { if (generation.current === g) setError("Paměť nelze načíst."); }
  };
  useEffect(() => { void load(project); }, [project]); // query is submitted explicitly
  const act = async (fn: () => Promise<unknown>) => {
    if (guard.current) return;
    guard.current = true; setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "Akce selhala."); }
    finally { guard.current = false; setBusy(false); }
  };
  return <section className="knowledge-view pad">
    <header><h1>Projektová paměť</h1><p className="muted">Ověřitelné znalosti, ne celá historie. Nic se nezapisuje do Obsidianu automaticky.</p></header>
    {error && <p className="alert error" role="alert">{error}</p>}
    <form className="knowledge-controls" onSubmit={(e) => { e.preventDefault(); void act(async () => { const p = await api.registerMemoryProject(path); setProjects(await api.memoryProjects()); setProject(p.id); }); }}>
      <label>Cesta Git projektu<input value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\\…\\projekt" required /></label>
      <button disabled={busy}>Přidat projekt</button>
    </form>
    <label>Projekt<select value={project} onChange={(e) => { setError(""); setProject(e.target.value); }}><option value="">Vyber projekt</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    {project && <>
      <form className="knowledge-controls" onSubmit={(e) => { e.preventDefault(); void act(() => load(project)); }}><label>Hledání / cíl kontextu<input value={query} onChange={(e) => setQuery(e.target.value)} /></label><button disabled={busy}>Hledat</button><button type="button" disabled={busy || !query.trim()} onClick={() => void act(async () => { const p = await api.memoryContext(project, query); setPacket(p.text); })}>Náhled kontextu</button></form>
      {packet && <details open><summary>Kontext pro model · nejvýše 8 000 znaků</summary><pre className="knowledge-text">{packet}</pre></details>}
      <h2>Aktivní znalosti</h2>{!items.length && <p className="muted">Žádné odpovídající znalosti.</p>}
      {items.map((item) => { const p = item.payload ?? item; return <article className="knowledge-card" key={item.id}><h3>{p.title}</h3><p className="knowledge-text">{p.content}</p><p className="small muted">Důkaz: {item.evidence_status ?? "viz původ"} · {item.id}</p><details><summary>Původ a důkazy</summary><pre className="knowledge-text">{JSON.stringify(p.evidence ?? [], null, 2)}</pre></details></article>; })}
      <h2>Návrhy ke kontrole</h2>{!candidates.length && <p className="muted">Žádné návrhy.</p>}
      {candidates.map((c) => { const p = c.payload ?? (c.payload_json ? JSON.parse(c.payload_json) as MemoryCandidate["payload"] : { title: "Návrh", content: "" }); const pending = !["approved", "rejected", "duplicate"].includes(c.state.toLowerCase()); return <article className="knowledge-card" key={c.id}><h3>{p.title}</h3><p className="knowledge-text">{p.content}</p><p>{c.state} · {c.reason}</p><details><summary>Důkazy</summary><pre className="knowledge-text">{JSON.stringify(p.evidence ?? [], null, 2)}</pre></details>{pending && <div className="knowledge-controls"><button disabled={busy} onClick={() => void act(async () => { await api.approveMemory(c.id); await load(project); })}>Schválit</button><button disabled={busy} onClick={() => void act(async () => { await api.rejectMemory(c.id); await load(project); })}>Zamítnout</button>{c.state.toLowerCase().includes("conflict") && <button disabled={busy} onClick={() => { if (window.confirm("Nahradit konfliktní tvrzení? Původní důkazy zůstanou zachované.")) void act(async () => { await api.approveMemory(c.id, true); await load(project); }); }}>Vyřešit konflikt a schválit</button>}</div>}</article>; })}
    </>}
  </section>;
}
