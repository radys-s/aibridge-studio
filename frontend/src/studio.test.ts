import { describe as group, expect, it } from "vitest";
import type { EffortOption, Orchestration, RunDetail } from "./api";
import { asAutopilot, COMPOSER_IMPLEMENTERS, describe, heavyLeads, leadsIn, lines, withMode } from "./options";
import { applyStream, cursorsOf, mergeRows } from "./stream";
import { actorLead, buildThread, latestDraftId } from "./thread";
import { groupByRepo } from "./components/Sidebar";
import { readable } from "./components/Thread";
import { continueGoal, noteTitle } from "./components/Composer";

const EFFORTS: Record<"claude" | "codex", EffortOption[]> = {
  claude: ["low", "medium", "high", "xhigh", "max"].map((id) => ({ id, heavy: id === "xhigh" || id === "max" })),
  codex: ["none", "low", "high", "xhigh"].map((id) => ({ id, heavy: id === "xhigh" })),
};

const ORCH: Orchestration = {
  mode: "cross",
  primary: "claude",
  plan: true,
  second_opinion: [],
  claude: { model: "opus", effort: "high" },
  codex: { model: "gpt-5.6-sol", effort: "medium" },
};

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

group("effort warning", () => {
  it("flags only leads that take part and use a heavy effort", () => {
    expect(heavyLeads(ORCH, EFFORTS)).toEqual([]);
    const heavy = { ...ORCH, claude: { model: "opus", effort: "max" }, codex: { model: null, effort: "xhigh" } };
    expect(heavyLeads(heavy, EFFORTS)).toEqual(["claude", "codex"]);
    expect(heavyLeads({ ...heavy, mode: "codex", primary: "codex" }, EFFORTS)).toEqual(["codex"]);
  });

  it("solo modes force their own lead as primary", () => {
    expect(withMode(ORCH, "codex").primary).toBe("codex");
    expect(withMode({ ...ORCH, primary: "codex" }, "duo").primary).toBe("codex");
    expect(leadsIn("claude")).toEqual(["claude"]);
    expect(describe(withMode(ORCH, "duo"))).toBe("Oba · Claude opus/high + Codex gpt-5.6-sol/medium");
  });

  it("autopilot is Studio's orchestration; Claude/Codex only as a second opinion", () => {
    const auto = asAutopilot(ORCH);
    expect(auto.mode).toBe("autopilot");
    expect(auto.primary).toBe("autopilot");
    expect(auto.claude).toEqual(ORCH.claude);
    expect(leadsIn("autopilot")).toEqual([]);
    expect(heavyLeads({ ...auto, claude: { model: "opus", effort: "max" } }, EFFORTS)).toEqual([]);
    expect(describe(auto)).toBe("Autopilot · free modely");
    expect(describe({ ...auto, second_opinion: ["codex"] })).toBe("Autopilot · druhý názor Codex");
    expect(COMPOSER_IMPLEMENTERS.map((i) => i.id)).toEqual(["auto", "worker", "autopilot", "freebuff"]);
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
