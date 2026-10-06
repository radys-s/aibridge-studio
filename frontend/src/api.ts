// Typed client for the AI Bridge Studio backend (FastAPI).
// All requests use relative /api/... paths; every mutating request carries X-Studio: 1 (CSRF).

export type Lead = "claude" | "codex";
export const LEADS: Lead[] = ["claude", "codex"];
export type Mode = "claude" | "codex" | "cross" | "duo";
export type Implementer = "auto" | "worker" | "freebuff" | "claude" | "codex";
export type Escalation = "off" | "ask" | "auto";

export interface Budget {
  max_iterations: number;
  max_worker_turns: number;
  max_worker_seconds: number;
  max_tokens: number;
  max_cost_usd: number;
  command_timeout_s: number;
  agent_timeout_s: number;
  max_escalations: number;
}

export interface LeadConfig {
  model: string | null;
  effort: string | null;
}

export interface Orchestration {
  mode: Mode;
  primary: Lead;
  plan: boolean;
  claude: LeadConfig;
  codex: LeadConfig;
}

export interface TaskSpec {
  title: string;
  goal: string;
  repo_path: string;
  allowed_paths: string[];
  acceptance_criteria: string[];
  acceptance_commands: string[];
  orchestration: Orchestration;
  implementer: Implementer;
  escalation: Escalation;
  auto_approve: boolean;
  budget?: Partial<Budget>;
}

/** What the composer sends: everything but goal and repo is optional. */
export type NewTask = Pick<TaskSpec, "goal" | "repo_path"> & Partial<Omit<TaskSpec, "goal" | "repo_path">>;

export interface Health {
  version: string;
  gateway: { reachable: boolean; status?: number; error?: string };
  leads: Record<Lead, { installed: boolean; path?: string | null }>;
  worker_models: string[];
  cooldowns: Record<string, number>;
}

export interface CatalogModel {
  id: string;
  label: string;
}

export interface EffortOption {
  id: string;
  heavy: boolean;
}

export interface Defaults {
  orchestration: Orchestration;
  implementer: Implementer;
  escalation: Escalation;
  auto_approve: boolean;
}

export interface Probe {
  ok: boolean;
  kind?: string;
  error?: string;
  actual_model?: string | null;
  latency_ms: number;
  tokens: number;
  at: number;
}

export interface SettingsSnapshot {
  defaults: Defaults;
  catalog: Record<Lead, CatalogModel[]>;
  efforts: Record<Lead, EffortOption[]>;
  recent_repos: string[];
  probes: Record<string, Probe>;
}

export type RunState =
  | "queued"
  | "planning"
  | "working"
  | "verifying"
  | "reviewing"
  | "accepted"
  | "needs_decision"
  | "interrupted"
  | "abandoned"
  | "awaiting_external"
  | "awaiting_approval";

export const ACTIVE_STATES: RunState[] = ["queued", "planning", "working", "verifying", "reviewing"];

export function isActive(state: string): boolean {
  return (ACTIVE_STATES as string[]).includes(state);
}

export type Pending = "draft" | "plan" | "takeover" | "self_review" | "sensitive" | null;

export interface RunSummary {
  id: string;
  title: string;
  repo_path: string;
  mode: Mode;
  implementer: Implementer | string;
  state: RunState;
  phase: string | null;
  pending: Pending;
  iteration: number;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  decision_reason: string | null;
}

export interface Plan {
  summary: string;
  steps: string[];
  files_to_touch: string[];
  risks: string[];
  complexity: string;
  recommended_implementer: string;
  questions?: string[];
}

export interface Draft {
  title: string;
  goal: string;
  allowed_paths: string[];
  acceptance_criteria: string[];
  acceptance_commands: string[];
  questions: string[];
  notes: string;
}

export interface Run {
  id: string;
  state: RunState;
  phase: string | null;
  iteration: number;
  workspace: string | null;
  source_commit: string | null;
  iteration_base: string | null;
  decision_reason: string | null;
  cancel_requested: number | boolean;
  implementer: string | null;
  escalations: number;
  pending: Pending;
  sensitive_ok: number | boolean;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  spec: TaskSpec;
  plan: Plan | null;
  feedback: string[] | null;
}

export type StepKind = "setup" | "draft" | "plan" | "handoff" | "work" | "verify" | "review";
export type StepStatus = "running" | "done" | "failed" | "interrupted";

export interface Step {
  id: number;
  kind: StepKind;
  iteration: number;
  status: StepStatus;
  actor: string | null;
  output: unknown;
  error: string | null;
  started_at: number | null;
  finished_at: number | null;
}

export interface ClaimedCheck {
  command: string;
  exit_code: number | null;
  timed_out: boolean;
  seconds: number | null;
}

export interface WorkOutput {
  status: string;
  summary: string;
  turns?: number;
  files_written?: string[];
  files_changed?: string[];
  claimed_checks?: ClaimedCheck[];
  concerns?: string[];
  detail?: string | null;
  /** Set when the work was done by an external agent; an unverified claim. */
  claim?: string;
}

export interface HandoffOutput {
  agent: string;
  brief: string;
  text: string;
}

export interface CommandResult {
  command: string;
  exit_code: number | null;
  output: string;
  timed_out: boolean;
  seconds: number | null;
}

export interface VerifyOutput {
  passed: boolean;
  changed_files: string[];
  out_of_scope: string[];
  empty_diff: boolean;
  commands: CommandResult[];
  diff: string;
}

export type Verdict = "accept" | "request_changes" | "reject";

export interface Finding {
  severity: "blocker" | "major" | "minor";
  file: string;
  problem: string;
  fix: string;
}

export interface CriterionResult {
  criterion: string;
  met: boolean;
  evidence: string;
}

export interface ReviewOutput {
  verdict: Verdict;
  summary: string;
  findings: Finding[];
  criteria: CriterionResult[];
}

export interface RunEvent {
  id: number;
  at: number;
  kind: string;
  message: string;
  data: unknown;
}

export type Author = "user" | "claude" | "codex" | "studio";

export interface Message {
  id: number;
  at: number;
  author: Author;
  text: string;
  data: { draft?: Draft; invalid?: string[] } | null;
}

export interface AgentEvent {
  id: number;
  step_id: number | null;
  lead: string;
  at: number;
  kind: "status" | "text" | "tool" | "tool_result" | "error" | string;
  text: string;
  data: unknown;
}

export interface ModelCall {
  id: number;
  step_id: number | null;
  role: string;
  requested_model: string;
  actual_model: string | null;
  provider: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  outcome: string;
  error: string | null;
  billing: "api" | "subscription" | "external";
  at: number;
}

export interface Usage {
  tokens: number;
  cost: number;
  calls: number;
}

export interface RunDetail {
  run: Run;
  steps: Step[];
  events: RunEvent[];
  messages: Message[];
  agent_events: AgentEvent[];
  model_calls: ModelCall[];
  usage: { api: Usage; subscription: Usage; total: Usage };
}

export type RunAction =
  | "cancel"
  | "resume"
  | "rework"
  | "retry"
  | "abandon"
  | "reply"
  | "approve"
  | "takeover"
  | "keep-worker"
  | "accept"
  | "allow-sensitive"
  | "external-done"
  | "open-terminal";

export interface ActionBody {
  note?: string;
  text?: string;
  lead?: Lead;
  edits?: Partial<Pick<TaskSpec, "title" | "goal" | "allowed_paths" | "acceptance_criteria" | "acceptance_commands">>;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

interface ValidationItem {
  loc?: (string | number)[];
  msg?: string;
}

/** Turn a FastAPI error body ({detail: string | [{loc,msg}]}) into readable text. */
export function formatDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item: ValidationItem) => {
          const loc = (item.loc ?? []).filter((p) => p !== "body").join(".");
          return loc ? `${loc}: ${item.msg ?? "neplatná hodnota"}` : (item.msg ?? "neplatná hodnota");
        })
        .join("\n");
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Server je nedostupný.");
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, formatDetail(body, `HTTP ${res.status} ${res.statusText}`));
  }
  return body as T;
}

function send<T>(method: "POST" | "PUT", path: string, data: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Studio": "1" },
    body: JSON.stringify(data ?? {}),
  });
}

const runPath = (id: string) => `/api/runs/${encodeURIComponent(id)}`;

export const api = {
  health: () => request<Health>("/api/health"),
  settings: () => request<SettingsSnapshot>("/api/settings"),
  saveSettings: (body: { defaults?: Defaults; catalog?: Record<Lead, CatalogModel[]> }) =>
    send<SettingsSnapshot>("PUT", "/api/settings", body),
  probe: (lead: Lead, model: string | null, effort: string | null) =>
    send<Probe>("POST", "/api/probe", { lead, model, effort }),
  listRuns: () => request<RunSummary[]>("/api/runs"),
  getRun: (id: string) => request<RunDetail>(runPath(id)),
  createTask: (task: NewTask) => send<{ run_id: string }>("POST", "/api/tasks", task),
  runAction: (id: string, action: RunAction, body: ActionBody = {}) =>
    send<{ ok: boolean }>("POST", `${runPath(id)}/${action}`, body),
  streamUrl: (id: string, cursors: { events: number; agent: number; messages: number }) =>
    `${runPath(id)}/stream?events=${cursors.events}&agent=${cursors.agent}&messages=${cursors.messages}`,
};
