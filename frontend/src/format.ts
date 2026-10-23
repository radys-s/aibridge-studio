// Small formatting helpers in the UI language.
import { locale, t } from "./i18n";

export const STATE_LABELS: Record<string, string> = {
  queued: t("Queued"),
  planning: t("Planning"),
  working: t("Working"),
  verifying: t("Verifying"),
  reviewing: t("Review"),
  accepted: t("Accepted"),
  needs_decision: t("Needs a decision"),
  interrupted: t("Interrupted"),
  abandoned: t("Abandoned"),
  awaiting_external: t("Waiting for the external agent"),
  awaiting_approval: t("Waiting for approval"),
};

export const STEP_LABELS: Record<string, string> = {
  setup: t("Setup"),
  draft: t("Draft brief"),
  approve: t("Approval"),
  plan: t("Plan"),
  handoff: t("Handoff"),
  work: t("Work"),
  verify: t("Verification"),
  review: t("Review"),
  memory: t("Memory"),
};

export const STEP_STATUS_LABELS: Record<string, string> = {
  running: t("running"),
  done: t("done"),
  failed: t("failed"),
  interrupted: t("interrupted"),
};

export const VERDICT_LABELS: Record<string, string> = {
  accept: t("Accept"),
  request_changes: t("Requests changes"),
  reject: t("Reject"),
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
  if (diff < 45) return t("just now");
  if (diff < 3600) return t("{n} min ago", { n: Math.round(diff / 60) });
  if (diff < 86400) return t("{n} h ago", { n: Math.round(diff / 3600) });
  const days = Math.round(diff / 86400);
  return days === 1 ? t("yesterday") : t("{n} days ago", { n: days });
}

export function absoluteTime(ts: number | null | undefined): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString(locale());
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "–";
  return n.toLocaleString(locale());
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null) return "–";
  return `$${usd.toFixed(usd < 0.01 && usd > 0 ? 4 : 3)}`;
}
