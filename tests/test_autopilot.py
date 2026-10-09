"""Autopilot orchestration end to end: real ApiLead + Runner, scripted gateway, real git."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import (
    MEDIAN_OK,
    MEDIAN_TEST,
    FakeGateway,
    assistant,
    good_script,
    make_spec,
    tool_call,
)
from studio.agents.api_lead import ApiLead
from studio.config import ModelSpec
from studio.db import Database
from studio.domain import Orchestration, TaskSpec
from studio.gateway import GatewayError, ProviderError
from studio.routing import ModelRouter
from studio.runner import Runner

A = "test/lead-a"
B = "test/lead-b"
TWO_LEADS = (
    ModelSpec(A, frozenset({"coding", "tools", "lead"}), 3, privacy="no_train"),
    ModelSpec(B, frozenset({"tools", "lead"}), 2, privacy="no_train"),
)
PLAN = {
    "summary": "Add median",
    "steps": ["edit", "test"],
    "files_to_touch": ["calc/stats.py"],
    "risks": [],
    "complexity": "trivial",
    "recommended_implementer": "worker",
    "questions": [],
}
ACCEPT = {
    "verdict": "accept",
    "summary": "ok",
    "findings": [],
    "criteria": [
        {"criterion": "median works for odd and even lengths", "met": True, "evidence": "tests"},
        {"criterion": "tests pass", "met": True, "evidence": "unittest"},
    ],
}


def submit(payload: dict[str, Any], i: int = 0) -> dict[str, Any]:
    return assistant(tool_call("submit", payload, i))


def build(
    tmp_path: Path,
    gateway: FakeGateway,
    models: tuple[ModelSpec, ...] = TWO_LEADS,
) -> tuple[Runner, Database]:
    db = Database(tmp_path / "data" / "studio.sqlite")
    router = ModelRouter(db, models)
    lead_map: dict[str, Any] = {"autopilot": ApiLead(db, gateway, router)}
    return Runner(db, gateway, router, lead_map, tmp_path / "data" / "ws"), db


def start(db: Database, spec: TaskSpec) -> str:
    run_id = db.create_task_and_run(spec.model_dump_json())
    db.message(run_id, "user", spec.goal)
    return run_id


def autopilot_spec(repo: Path, **kw: Any) -> TaskSpec:
    orch: dict[str, Any] = {"mode": "autopilot", **kw.pop("orch", {})}
    return make_spec(repo, orchestration=orch, **kw)


def test_only_autopilot_leads_and_legacy_specs_load_as_autopilot(repo: Path) -> None:
    spec = TaskSpec(goal="přidej median", repo_path=str(repo))
    assert spec.orchestration.mode == "autopilot" and spec.orchestration.primary == "autopilot"
    assert spec.orchestration.planners() == ["autopilot"]
    assert spec.orchestration.reviewers() == ["autopilot"]
    for legacy in (
        {"mode": "cross", "primary": "codex", "second_opinion": ["codex"]},
        {"mode": "claude", "claude": {"model": "opus", "effort": "max"}},
        {"mode": "duo", "plan": False},
    ):
        orch = Orchestration.model_validate(legacy)
        assert orch.model_dump() == {
            "mode": "autopilot",
            "primary": "autopilot",
            "plan": legacy.get("plan", True),
        }
    old = TaskSpec.model_validate(
        {"goal": "xyz", "repo_path": ".", "planner": "claude", "reviewer": "codex"}
    )
    assert old.orchestration.mode == "autopilot"
    assert TaskSpec(goal="xyz", repo_path=".", implementer="codex").implementer == "autopilot"  # type: ignore[arg-type]


def test_full_autopilot_run_reviews_on_a_different_model(tmp_path: Path, repo: Path) -> None:
    gw = FakeGateway([submit(PLAN), *good_script(), submit(ACCEPT)])
    runner, db = build(tmp_path, gw)
    run_id = start(db, autopilot_spec(repo))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "accepted", run["decision_reason"]
    assert gw.calls[0] == A and gw.calls[1:5] == [A] * 4  # plan, then the worker
    assert gw.calls[-1] == B  # the reviewer is not the model that wrote the change
    review = db.last_step(run_id, "review")
    assert review["actor"] == "autopilot"
    calls = db.model_calls(run_id)
    assert {c["billing"] for c in calls} == {"api"}
    assert [c["role"] for c in calls if c["step_id"] == review["id"]] == ["review"]


def test_single_free_model_falls_back_to_self_review_gate(tmp_path: Path, repo: Path) -> None:
    gw = FakeGateway([submit(PLAN), *good_script(), submit(ACCEPT)])
    runner, db = build(tmp_path, gw, models=TWO_LEADS[:1])
    run_id = start(db, autopilot_spec(repo))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "needs_decision" and run["pending"] == "self_review"
    assert gw.calls[-1] == A
    runner.accept(run_id)
    assert db.run(run_id)["state"] == "accepted"


def test_exhausted_free_pool_needs_a_decision(tmp_path: Path, repo: Path) -> None:
    err: dict[str, GatewayError] = {
        A: ProviderError("429", status=429),
        B: ProviderError("429", status=429),
    }
    gw = FakeGateway([], per_model=err)
    runner, db = build(tmp_path, gw)
    run_id = start(db, autopilot_spec(repo))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "needs_decision"
    assert "planning by autopilot failed" in run["decision_reason"]
    assert set(db.cooldowns()) == {A, B}


def test_strong_task_is_implemented_by_autopilot_and_reviewed_elsewhere(
    tmp_path: Path, repo: Path
) -> None:
    hard = dict(PLAN, complexity="hard", recommended_implementer="strong_agent")
    report = {"summary": "median added", "files_changed": ["calc/stats.py"], "concerns": []}
    gw = FakeGateway(
        [
            submit(hard),
            assistant(
                tool_call("write_file", {"path": "calc/stats.py", "content": MEDIAN_OK}),
                tool_call(
                    "write_file", {"path": "tests/test_median.py", "content": MEDIAN_TEST}, 1
                ),
            ),
            submit(report, 2),
            submit(ACCEPT, 3),
        ]
    )
    # hard: the strongest lead model (B here) plans and implements, A reviews
    models = (
        ModelSpec(A, frozenset({"coding", "tools", "lead"}), 2, privacy="no_train"),
        ModelSpec(B, frozenset({"tools", "lead"}), 5, privacy="no_train"),
    )
    runner, db = build(tmp_path, gw, models=models)
    run_id = start(db, autopilot_spec(repo, escalation="auto"))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "accepted", run["decision_reason"]
    assert db.last_step(run_id, "work")["actor"] == "lead:autopilot"
    assert gw.calls == [A, B, B, A]


def test_worker_gateway_down_does_not_escalate_to_autopilot(tmp_path: Path, repo: Path) -> None:
    from studio.gateway import GatewayUnavailable

    gw = FakeGateway([submit(PLAN), GatewayUnavailable("refused")])
    runner, db = build(tmp_path, gw)
    run_id = start(db, autopilot_spec(repo, escalation="auto"))
    runner.execute(run_id)

    run = db.run(run_id)
    assert (
        run["state"] == "needs_decision" and "Autopilot shares this pool" in run["decision_reason"]
    )
    assert run["escalations"] == 0


def test_missing_lead_is_a_decision_not_a_crash(tmp_path: Path, repo: Path) -> None:
    db = Database(tmp_path / "data" / "studio.sqlite")
    gw = FakeGateway([])
    runner = Runner(db, gw, ModelRouter(db, TWO_LEADS), {}, tmp_path / "data" / "ws")
    run_id = start(db, autopilot_spec(repo))
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "needs_decision" and "not configured" in run["decision_reason"]
