// Typed client for the AI Bridge Studio backend (FastAPI).
// All requests use relative /api/... paths; every POST carries the CSRF header X-Studio: 1.

export type Lead = "claude" | "codex";
export type Planner = Lead | "none";

export interface Budget {
  max_iterations: number;
  max_worker_turns: number;
  max_worker_seconds: number;
  max_tokens: number;
  max_cost_usd: number;
  command_timeout_s: number;
  agent_timeout_s: number;
}

export const DEFAULT_BUDGET: Budget = {
  max_iterations: 2,
  max_worker_turns: 30,
  max_worker_seconds: 900,
  max_tokens: 400000,
  max_cost_usd: 0.5,
  command_timeout_s: 120,
  agent_timeout_s: 600,
};

export interface TaskSpec {
  title: string;
  goal: string;
  repo_path: string;
  allowed_paths: string[];
  acceptance_criteria: string[];
  acceptance_commands: string[];
  planner: Planner;
  reviewer: Lead;
  budget?: Partial<Budget>;
}

export interface Health {
  version: string;
  gateway: { reachable: boolean; status?: number; error?: string };
  leads: { claude: { installed: boolean }; codex: { installed: boolean } };
  worker_models: string[];
  cooldowns: Record<string, number>;
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
  | "abandoned";

export const ACTIVE_STATES: RunState[] = ["queued", "planning", "working", "verifying", "reviewing"];

export function isActive(state: string): boolean {
  return (ACTIVE_STATES as string[]).includes(state);
}

export interface RunSummary {
  id: string;
  title: string;
  state: RunState;
  phase: string | null;
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
  cancel_requested: boolean;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  spec: TaskSpec;
  plan: Plan | null;
  feedback: string[] | null;
}

export type StepKind = "setup" | "plan" | "work" | "verify" | "review";
export type StepStatus = "running" | "done" | "failed" | "interrupted";

export interface Step {
  id: number | string;
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
  turns: number;
  files_written: string[];
  claimed_checks: ClaimedCheck[];
  detail: string | null;
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
  id: number | string;
  at: number;
  kind: string;
  message: string;
  data: unknown;
}

export interface ModelCall {
  id: number | string;
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
  at: number;
}

export interface RunDetail {
  run: Run;
  steps: Step[];
  events: RunEvent[];
  model_calls: ModelCall[];
  usage: { tokens: number; cost: number; calls: number };
}

export type RunAction = "cancel" | "resume" | "rework" | "abandon";

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

function post<T>(path: string, data: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Studio": "1" },
    body: JSON.stringify(data ?? {}),
  });
}

export const api = {
  health: () => request<Health>("/api/health"),
  listRuns: () => request<RunSummary[]>("/api/runs"),
  getRun: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  createTask: (spec: TaskSpec) => post<{ run_id: string }>("/api/tasks", spec),
  runAction: (id: string, action: RunAction, note?: string) =>
    post<{ ok: boolean }>(
      `/api/runs/${encodeURIComponent(id)}/${action}`,
      note && note.trim() ? { note: note.trim() } : {},
    ),
};
