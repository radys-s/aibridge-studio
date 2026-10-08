import { describe as group, expect, it } from "vitest";
import type { ModelInfo, Orchestration, RunDetail } from "./api";
import { actorLabel, activeProviders, asAutopilot, describe, IMPLEMENTERS, lines } from "./options";
import { applyStream, cursorsOf, mergeRows } from "./stream";
import { actorLead, buildThread, latestDraftId } from "./thread";
import { groupByRepo } from "./components/Sidebar";
import { readable } from "./components/Thread";
import { continueGoal, noteTitle } from "./components/Composer";
import { budgetLevel } from "./components/SettingsView";

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
