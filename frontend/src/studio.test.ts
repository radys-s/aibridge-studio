import { describe as group, expect, it } from "vitest";
import type { ModelInfo, Orchestration, RunDetail, RunSummary } from "./api";
import { actorLabel, activeProviders, asAutopilot, describe, IMPLEMENTERS, lines } from "./options";
import { applyStream, cursorsOf, mergeRows } from "./stream";
import { actorLead, buildThread, latestDraftId, progress, reviewAccepted } from "./thread";
import { groupByRepo } from "./components/Sidebar";
import { readable } from "./components/Thread";
import { continueGoal, noteTitle } from "./components/Composer";
import { transitions, titleFor } from "./notify";
import { oneDecimal, percent } from "./components/OverviewView";
import { keyStatus } from "./components/SettingsView";
import { budgetLevel, modelName, probeText, reliabilityText } from "./components/SettingsView";

const ORCH: Orchestration = { mode: "autopilot", primary: "autopilot", plan: true };

function model(id: string, over: Partial<ModelInfo> = {}): ModelInfo {
  return {
    id,
    provider: id.split("/")[0],
    privacy: "no_train",
    paid: false,
    lead: true,
    coding: true,
    strength: 1,
    blocked: null,
    cooldown_until: null,
    ...over,
  };
}

function detail(over: Partial<RunDetail> = {}): RunDetail {
  return {
    run: { id: "r", state: "planning", phase: "plan", iteration: 1, pending: null } as RunDetail["run"],
    steps: [],
    events: [],
    messages: [],
    agent_events: [],
    model_calls: [],
    usage: {
      api: { tokens: 0, cost: 0, calls: 0 },
      subscription: { tokens: 0, cost: 0, calls: 0 },
      total: { tokens: 0, cost: 0, calls: 0 },
    },
    ...over,
  };
}

group("orchestration", () => {
  it("is only Autopilot; old saved Claude/Codex orchestration is upgraded", () => {
    const old = { mode: "cross", primary: "claude", plan: false, second_opinion: ["codex"] } as unknown as Orchestration;
    expect(asAutopilot(old)).toEqual({ mode: "autopilot", primary: "autopilot", plan: false });
    expect(describe(ORCH)).toBe("Autopilot · free modely");
    expect(IMPLEMENTERS.map((i) => i.id)).toEqual(["auto", "worker", "autopilot", "freebuff"]);
    expect(actorLabel("codex")).toBe("Codex"); // history from before ADR 0004
  });

  it("lists only providers that will receive data, in router order", () => {
    const models = [
      model("groq/openai/gpt-oss-120b"),
      model("cloudflare-ai/@cf/x", { blocked: "not_connected" }),
      model("openrouter/x:free", { privacy: "unknown", blocked: "privacy" }),
      model("groq/openai/gpt-oss-20b"),
    ];
    expect(activeProviders(models)).toEqual(["Groq"]);
    expect(activeProviders([])).toEqual([]);
  });

  it("warns at 80 % of a free daily cap", () => {
    expect(budgetLevel({ provider: "groq", calls: 10, used: 10, cap: 1000 })).toBe("ok");
    expect(budgetLevel({ provider: "groq", calls: 900, used: 800, cap: 1000 })).toBe("warn");
    expect(budgetLevel({ provider: "openrouter", calls: 50, used: 50, cap: 50 })).toBe("out");
    expect(budgetLevel({ provider: "cloudflare-ai", calls: 5, used: 5, cap: null })).toBeNull();
  });

  it("continues a project by its note title", () => {
    expect(noteTitle("02 Projekty/AI Bridge Studio.md")).toBe("AI Bridge Studio");
    expect(continueGoal("Calc", "  ")).toBe("Pokračuj v projektu Calc.");
    expect(continueGoal("Calc", "variance")).toBe("Pokračuj v projektu Calc. Zaměř se na: variance");
  });

  it("splits textarea lines", () => {
    expect(lines(" a \r\n\n b\n")).toEqual(["a", "b"]);
  });
});

group("live stream", () => {
  it("dedupes rows resent after a reconnect", () => {
    const have = [{ id: 1 }, { id: 2 }];
    expect(mergeRows(have, [{ id: 2 }, { id: 3 }]).map((r) => r.id)).toEqual([1, 2, 3]);
    expect(mergeRows(have, [{ id: 2 }])).toBe(have);
  });

  it("appends agent output without refetch, refetches on events and state changes", () => {
    const d = detail();
    const a = applyStream(d, "agent", [{ id: 5, step_id: 1, lead: "claude", at: 1, kind: "text", text: "hi", data: null }]);
    expect(a.refetch).toBe(false);
    expect(cursorsOf(a.detail).agent).toBe(5);
    expect(applyStream(d, "event", [{ id: 1, at: 1, kind: "plan", message: "x", data: null }]).refetch).toBe(true);
    expect(applyStream(d, "run", { state: "planning", phase: "plan", iteration: 1, pending: null }).refetch).toBe(false);
    const moved = applyStream(d, "run", { state: "needs_decision", phase: "plan", iteration: 1, pending: "plan" });
    expect(moved.refetch).toBe(true);
    expect(moved.detail.run.pending).toBe("plan");
  });
});

group("thread", () => {
  it("orders messages and steps by time and attaches agent output to its step", () => {
    const d = detail({
      messages: [
        { id: 1, at: 10, author: "user", text: "do it", data: null },
        { id: 2, at: 30, author: "claude", text: "draft", data: { draft: { title: "t" } as never } },
      ],
      steps: [
        { id: 7, kind: "draft", iteration: 1, status: "done", actor: "claude", output: null, error: null, started_at: 20, finished_at: 30 },
      ],
      agent_events: [{ id: 1, step_id: 7, lead: "claude", at: 21, kind: "tool", text: "Read a", data: null }],
    });
    const items = buildThread(d);
    expect(items.map((i) => i.type)).toEqual(["message", "step", "message"]);
    expect(items[1].type === "step" && items[1].agent.length).toBe(1);
    expect(latestDraftId(d.messages)).toBe(2);
  });

  it("maps step actors to leads", () => {
    expect(actorLead("lead:codex")).toBe("codex");
    expect(actorLead("claude:merge")).toBe("claude");
    expect(actorLead("autopilot")).toBe("autopilot");
    expect(actorLead("lead:autopilot")).toBe("autopilot");
    expect(actorLead("worker")).toBeNull();
  });

  it("groups runs by repo, most recently active first", () => {
    const runs = [
      { id: "a", repo_path: "C:\\x\\one", updated_at: 1 },
      { id: "b", repo_path: "C:\\x\\two", updated_at: 5 },
      { id: "c", repo_path: "C:\\x\\one", updated_at: 3 },
    ] as never;
    expect(groupByRepo(runs).map((g) => [g.repo, g.runs.length])).toEqual([
      ["C:\\x\\two", 1],
      ["C:\\x\\one", 2],
    ]);
  });
});

group("agent text", () => {
  it("shows the summary of a structured answer instead of raw JSON", () => {
    expect(readable('{"summary":"Upravuji stats.py","files_changed":[]}')).toBe("Upravuji stats.py");
    expect(readable("plain words")).toBe("plain words");
    expect(readable('{"x":1}')).toMatch(/strukturovaná/);
  });
});

group("model probe", () => {
  it("says whether the model can drive Studio", () => {
    const base = { model: "cerebras/gpt-oss-120b", blocked: null };
    expect(probeText({ ...base, ok: true, tools: true, latency_ms: 300 })).toBe("funguje, volá nástroje · 300 ms");
    expect(probeText({ ...base, ok: true, tools: false })).toContain("nevolá nástroje");
    expect(probeText({ ...base, ok: false, kind: "provider_error", status: 401, detail: "invalid key" })).toBe(
      "chyba 401: invalid key",
    );
    expect(probeText({ ...base, blocked: "privacy", ok: false, kind: "privacy" })).toContain("neodesláno");
  });
});

group("run progress", () => {
  it("does not count missing, duplicate or blocked criteria as a successful review", () => {
    const review = { verdict: "accept" as const, summary: "ok", findings: [], criteria: [{ criterion: "works", met: true, evidence: "test" }] };
    expect(reviewAccepted(review, ["works"])).toBe(true);
    expect(reviewAccepted(review, ["works", "safe"])).toBe(false);
    expect(reviewAccepted({ ...review, criteria: [...review.criteria, ...review.criteria] }, ["works", "safe"])).toBe(false);
    expect(reviewAccepted({ ...review, findings: [{ severity: "major", file: "a", problem: "broken", fix: "fix" }] }, ["works"])).toBe(false);
    expect(reviewAccepted({ ...review, acceptance_valid: false }, ["works"])).toBe(false);
    expect(reviewAccepted({ ...review, criteria: [] }, [])).toBe(false);
  });
  const step = (id: number, kind: string, status: string, iteration = 1, output: unknown = null) =>
    ({ id, kind, status, iteration, output, actor: null, error: null, started_at: id, finished_at: null }) as RunDetail["steps"][number];
  const states = (d: RunDetail) => progress(d).map((s) => `${s.label}:${s.state}`);

  it("keeps self review waiting and incomplete acceptance failed", () => {
    const review = { verdict: "accept", summary: "ok", findings: [], criteria: [{ criterion: "works", met: true, evidence: "test" }] };
    const d = detail({ run: { ...detail().run, state: "needs_decision", pending: "self_review", spec: { acceptance_criteria: ["works"] } as never }, steps: [step(1, "review", "done", 1, review)] });
    expect(progress(d).find((s) => s.id === "review")?.state).toBe("waiting");
    const missing = { ...d, run: { ...d.run, pending: "review" as const }, steps: [step(1, "review", "done", 1, { ...review, criteria: [] })] };
    expect(progress(missing).find((s) => s.id === "review")?.state).toBe("failed");
  });

  it("waits on the draft before anything else runs", () => {
    const d = detail({ run: { ...detail().run, state: "awaiting_approval" }, steps: [step(1, "draft", "done")] });
    expect(states(d)).toEqual(["Zadání:waiting", "Plán:todo", "Práce:todo", "Ověření:todo", "Recenze:todo", "Paměť:todo"]);
  });

  it("marks a manual spec done and a skipped plan, and a review asking for changes as failed", () => {
    const d = detail({
      run: { ...detail().run, state: "needs_decision" },
      steps: [step(1, "work", "done"), step(2, "verify", "done", 1, { passed: true }), step(3, "review", "done", 1, { verdict: "request_changes" })],
    });
    expect(states(d)).toEqual(["Zadání:done", "Plán:skipped", "Práce:done", "Ověření:done", "Recenze:failed", "Paměť:todo"]);
  });

  it("counts only the newest iteration after a rework", () => {
    const d = detail({
      run: { ...detail().run, state: "working" },
      steps: [
        step(1, "draft", "done"),
        step(2, "plan", "done"),
        step(3, "work", "done"),
        step(4, "verify", "done", 1, { passed: false }),
        step(5, "work", "running", 2),
      ],
    });
    expect(states(d)).toEqual(["Zadání:done", "Plán:done", "Práce:running", "Ověření:todo", "Recenze:todo", "Paměť:todo"]);
  });
});

group("model reliability", () => {
  it("shows the share of good calls and accepted work", () => {
    expect(reliabilityText(undefined)).toBe("");
    expect(reliabilityText({ calls: 0, failed: 0, reviewed: 0, accepted: 0 })).toBe("");
    expect(reliabilityText({ calls: 25, failed: 2, reviewed: 0, accepted: 0 })).toBe("spolehlivost 92 %");
    expect(reliabilityText({ calls: 10, failed: 0, reviewed: 4, accepted: 3 })).toBe("spolehlivost 100 % · 3/4 přijato");
  });

  it("drops the provider prefix from model names", () => {
    expect(modelName("groq/openai/gpt-oss-120b")).toBe("openai/gpt-oss-120b");
    expect(modelName("cf/@cf/zai-org/glm-4.7-flash")).toBe("zai-org/glm-4.7-flash");
    expect(modelName("plain")).toBe("plain");
  });

  it("shows overview rates without inventing them", () => {
    expect(percent(2 / 3)).toBe("67 %");
    expect(percent(null)).toBe("–");
    expect(oneDecimal(1.25)).toMatch(/^1,[23]$/);
    expect(oneDecimal(null)).toBe("–");
  });

  it("tells only about new waits and ends, never on the first load", () => {
    const run = (id: string, state: string) => ({ id, state, title: id }) as unknown as RunSummary;
    const first = [run("a", "needs_decision"), run("b", "working")];
    expect(transitions(null, first)).toEqual([]);
    const prev = new Map([["a", "needs_decision"], ["b", "working"], ["c", "reviewing"]]);
    const now = [run("a", "awaiting_approval"), run("b", "needs_decision"), run("c", "accepted"), run("d", "awaiting_external"), run("e", "queued")];
    expect(transitions(prev, now).map((c) => [c.run.id, c.kind])).toEqual([
      ["b", "waiting"],
      ["c", "accepted"],
      ["d", "waiting"],
    ]);
    expect(titleFor(now)).toBe("(3) AI Bridge Studio");
    expect(titleFor([])).toBe("AI Bridge Studio");
  });

  it("shows which key sources OmniRoute already offers", () => {
    const m = (id: string, extra: Partial<ModelInfo> = {}) =>
      ({ id, provider: id.split("/")[0], blocked: null, cooldown_until: null, ...extra }) as ModelInfo;
    const health = (models: ModelInfo[], reachable = true) =>
      ({ gateway: { reachable }, models }) as unknown as Parameters<typeof keyStatus>[1];
    const h = health([
      m("groq/a"),
      m("ovh/Mistral-Small-3.2-24B-Instruct-2506", { blocked: "not_connected" }),
      m("ollamacloud/glm-5.1"),
      m("cf/c", { cooldown_kind: "setup", cooldown_until: 9e9 }),
    ]);
    expect(keyStatus("groq", h)).toBe("connected");
    expect(keyStatus("ovh", h)).toBe("missing");
    expect(keyStatus("ollamacloud", h)).toBe("connected");
    expect(keyStatus("cerebras", h)).toBe("missing");
    expect(keyStatus("cf", h)).toBe("setup");
    expect(keyStatus("groq", health([m("groq/a")], false))).toBeNull();
  });
});
