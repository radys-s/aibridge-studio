// Small formatting helpers (Czech locale).

export const STATE_LABELS: Record<string, string> = {
  queued: "Ve frontě",
  planning: "Plánuje",
  working: "Pracuje",
  verifying: "Ověřuje",
  reviewing: "Review",
  accepted: "Přijato",
  needs_decision: "Čeká na rozhodnutí",
  interrupted: "Přerušeno",
  abandoned: "Opuštěno",
  awaiting_external: "Čeká na externího agenta",
  awaiting_approval: "Čeká na schválení",
};

export const STEP_LABELS: Record<string, string> = {
  setup: "Příprava",
  draft: "Návrh zadání",
  approve: "Schválení",
  plan: "Plán",
  handoff: "Předání",
  work: "Práce",
  verify: "Ověření",
  review: "Review",
  memory: "Návrh do paměti",
};

export const STEP_STATUS_LABELS: Record<string, string> = {
  running: "běží",
  done: "hotovo",
  failed: "selhalo",
  interrupted: "přerušeno",
};

export const VERDICT_LABELS: Record<string, string> = {
  accept: "Přijmout",
  request_changes: "Požaduje změny",
  reject: "Zamítnout",
};

export function nowSec(): number {
  return Date.now() / 1000;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "–";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m} min ${s} s`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min`;
}

export function relativeTime(ts: number | null | undefined, now = nowSec()): string {
  if (!ts) return "–";
  const diff = Math.max(0, now - ts);
  if (diff < 45) return "právě teď";
  if (diff < 3600) return `před ${Math.round(diff / 60)} min`;
  if (diff < 86400) return `před ${Math.round(diff / 3600)} h`;
  const days = Math.round(diff / 86400);
  return days === 1 ? "včera" : `před ${days} dny`;
}

export function absoluteTime(ts: number | null | undefined): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString("cs-CZ");
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "–";
  return n.toLocaleString("cs-CZ");
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null) return "–";
  return `$${usd.toFixed(usd < 0.01 && usd > 0 ? 4 : 3)}`;
}
