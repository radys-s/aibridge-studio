"""Claude Code and Codex as optional leads (ADR 0007): roles, independence, no fallback."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from conftest import (
    ACCEPT,
    MEDIAN_OK,
    MEDIAN_TEST,
    MODELS,
    FakeGateway,
    FakeLead,
    good_script,
    make_spec,
)
from studio.agents.leads import LeadError
from studio.api import create_app
from studio.config import Settings
from studio.db import Database
from studio.domain import Orchestration, TaskSpec


def writes_median(cwd: Path) -> None:
    (cwd / "calc" / "stats.py").write_text(MEDIAN_OK, encoding="utf-8")
    (cwd / "tests" / "test_median.py").write_text(MEDIAN_TEST, encoding="utf-8")


def start(db: Database, spec: TaskSpec) -> str:
    run_id = db.create_task_and_run(spec.model_dump_json())
    db.message(run_id, "user", spec.goal)
    return run_id


def test_claude_plans_worker_implements_codex_reviews(studio, repo: Path) -> None:
    claude, codex = FakeLead("claude"), FakeLead("codex", reviews=[ACCEPT])
    runner, db, autopilot = studio(FakeGateway(good_script()), [], extra=[claude, codex])
    orch = {"primary": "claude", "reviewer": "codex", "codex": {"effort": "high"}}
    run_id = start(db, make_spec(repo, implementer="worker", orchestration=orch))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "accepted", run["decision_reason"]
    assert autopilot.calls == []  # nothing silently goes back to Autopilot
    assert claude.calls == [("plan", "read")] and codex.calls == [("review", "read")]
    review = db.last_step(run_id, "review")
    assert review["actor"] == "codex" and '"independent": true' in review["output_json"]
    cli = [c for c in db.model_calls(run_id) if c["billing"] == "subscription"]
    assert {c["requested_model"] for c in cli} == {"claude:default", "codex:default@high"}
    assert {c["provider"] for c in cli} == {"claude CLI (own login)", "codex CLI (own login)"}


def test_takeover_by_claude_is_reviewed_by_codex(studio, repo: Path) -> None:
    claude = FakeLead("claude", implement=writes_median)
    codex = FakeLead("codex", reviews=[ACCEPT])
    runner, db, _ = studio(FakeGateway([]), [], extra=[claude, codex])
    orch = {"reviewer": "codex", "takeover": "claude", "plan": False}
    run_id = start(db, make_spec(repo, implementer="claude", orchestration=orch))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "accepted", run["decision_reason"]
    assert ("implement", "write") in claude.calls
    assert db.last_step(run_id, "work")["actor"] == "lead:claude"


def test_an_agent_never_reviews_its_own_work(studio, repo: Path) -> None:
    with pytest.raises(ValidationError, match="own implementation"):
        make_spec(repo, implementer="claude", orchestration={"reviewer": "claude"})
    with pytest.raises(ValidationError, match="own takeover"):
        Orchestration(reviewer="codex", takeover="codex")
    with pytest.raises(ValidationError, match="effort"):
        Orchestration(claude={"effort": "minimal"})  # type: ignore[arg-type]

    runner, db, _ = studio(
        FakeGateway([]), [], extra=[FakeLead("claude"), FakeLead("codex", reviews=[ACCEPT])]
    )
    spec = make_spec(repo, orchestration={"reviewer": "codex", "plan": False})
    run_id = start(db, spec)
    db.update_run(run_id, state="needs_decision", phase="work", workspace=str(repo))
    with pytest.raises(ValueError, match="reviews this task"):
        runner.takeover(run_id, "codex")


def test_cli_limit_stops_the_run_without_falling_back(studio, repo: Path) -> None:
    codex = FakeLead("codex", errors={"review": LeadError("usage limit reached", "limit")})
    runner, db, autopilot = studio(FakeGateway(good_script()), [ACCEPT], extra=[codex])
    spec = make_spec(repo, implementer="worker", orchestration={"reviewer": "codex", "plan": False})
    run_id = start(db, spec)
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "needs_decision"
    assert "review by codex failed" in run["decision_reason"]
    assert not any(kind == "review" for kind, _ in autopilot.calls)
    call = next(c for c in db.model_calls(run_id) if c["billing"] == "subscription")
    assert call["outcome"] == "limit"


def test_api_agents_are_off_until_enabled(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("studio.api.cli_version", lambda _exe: "0.1.0")
    settings = Settings(data_dir=tmp_path / "d", worker_models=MODELS)
    leads = {"autopilot": FakeLead("autopilot"), "codex": FakeLead("codex")}
    app = create_app(settings, gateway=FakeGateway([]), leads=leads, start_runner=False)
    c = TestClient(app, headers={"X-Studio": "1"})

    agents = {a["name"]: a for a in c.get("/api/agents").json()}
    assert agents["codex"]["installed"] and agents["codex"]["version"] == "0.1.0"
    assert not agents["claude"]["installed"] and agents["claude"]["version"] is None
    assert not any(agents["codex"]["roles"].values())

    body = make_spec(repo, orchestration={"reviewer": "codex"}).model_dump(mode="json")
    refused = c.post("/api/tasks", json=body)
    assert refused.status_code == 422 and "codex is not enabled for review" in refused.text

    listed = c.put("/api/agents/codex", json={"review": True}).json()
    assert next(a for a in listed if a["name"] == "codex")["roles"]["review"]
    assert c.post("/api/tasks", json=body).status_code == 201

    c.put("/api/agents/claude", json={"plan": True})
    claude_body = make_spec(repo, orchestration={"primary": "claude"}).model_dump(mode="json")
    missing = c.post("/api/tasks", json=claude_body)
    assert missing.status_code == 422 and "claude CLI is not installed" in missing.text
    assert c.put("/api/agents/gemini", json={"plan": True}).status_code == 404
    assert TestClient(app).put("/api/agents/codex", json={"plan": True}).status_code == 403


def test_cli_takeover_outside_the_scope_stops_for_a_decision(studio, repo: Path) -> None:
    def writes_outside(cwd: Path) -> None:
        writes_median(cwd)
        (cwd / "README.md").write_text("rewritten", encoding="utf-8")

    claude = FakeLead("claude", implement=writes_outside)
    codex = FakeLead("codex", reviews=[ACCEPT])
    runner, db, _ = studio(FakeGateway([]), [], extra=[claude, codex])
    orch = {"reviewer": "codex", "takeover": "claude", "plan": False}
    run_id = start(db, make_spec(repo, implementer="claude", orchestration=orch))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "needs_decision"
    assert "outside the task scope: README.md" in run["decision_reason"]
    assert codex.calls == []  # nothing out of scope reaches review or apply


def test_an_agent_switched_off_mid_run_is_not_called(studio, repo: Path) -> None:
    codex = FakeLead("codex", reviews=[ACCEPT])
    runner, db, autopilot = studio(FakeGateway(good_script()), [ACCEPT], extra=[codex])
    runner.agent_enabled = lambda name, role: False
    spec = make_spec(repo, implementer="worker", orchestration={"reviewer": "codex", "plan": False})
    run_id = start(db, spec)
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "needs_decision"
    assert "switched off for review" in run["decision_reason"]
    assert codex.calls == [] and not any(k == "review" for k, _ in autopilot.calls)


def test_takeover_without_a_lead_uses_the_task_agent_and_its_switch(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("studio.api.cli_version", lambda _exe: "0.1.0")
    settings = Settings(data_dir=tmp_path / "d", worker_models=MODELS)
    leads = {"autopilot": FakeLead("autopilot"), "claude": FakeLead("claude")}
    app = create_app(settings, gateway=FakeGateway([]), leads=leads, start_runner=False)
    c = TestClient(app, headers={"X-Studio": "1"})
    c.put("/api/agents/claude", json={"takeover": True})
    body = make_spec(repo, orchestration={"takeover": "claude", "plan": False})
    run_id = c.post("/api/tasks", json=body.model_dump(mode="json")).json()["run_id"]

    c.put("/api/agents/claude", json={"takeover": False})
    refused = c.post(f"/api/runs/{run_id}/takeover", json={})
    assert refused.status_code == 422 and "claude is not enabled for takeover" in refused.text
