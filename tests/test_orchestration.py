"""Spec drafting, takeover and escalation with a scripted Autopilot (real git)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

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
from studio.api import create_app
from studio.config import Settings
from studio.db import Database
from studio.domain import Finding, Plan, Review, TaskSpec
from studio.gateway import ProviderError

CHANGES = Review(
    verdict="request_changes",
    summary="needs work",
    findings=[Finding(severity="major", file="calc/stats.py", problem="no docstring", fix="add")],
)


def writes_median(cwd: Path) -> None:
    (cwd / "calc" / "stats.py").write_text(MEDIAN_OK, encoding="utf-8")
    (cwd / "tests" / "test_median.py").write_text(MEDIAN_TEST, encoding="utf-8")


def start(db: Database, spec: TaskSpec) -> str:
    run_id = db.create_task_and_run(spec.model_dump_json())
    db.message(run_id, "user", spec.goal)
    return run_id


DRAFT = {
    "title": "Přidat median",
    "goal": "Přidej median do calc/stats.py s testy.",
    "allowed_paths": ["calc/*.py", "tests/*.py"],
    "acceptance_criteria": ["median funguje pro sudé i liché délky"],
    "acceptance_commands": ["python -m unittest discover -s tests -t ."],
    "questions": [],
    "notes": "",
}


def test_prompt_only_task_is_drafted_then_approved(studio, repo: Path) -> None:
    lead = FakeLead("autopilot", drafts=[dict(DRAFT, questions=["Jaký typ návratu?"]), DRAFT])
    runner, db, _ = studio(FakeGateway(good_script()), [ACCEPT], lead)
    spec = TaskSpec(goal="přidej median", repo_path=str(repo))
    assert not spec.complete
    run_id = start(db, spec)
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "awaiting_approval" and run["pending"] == "draft"
    assert db.messages(run_id)[-1]["author"] == "autopilot"
    assert "Jaký typ návratu?" in db.messages(run_id)[-1]["text"]

    runner.reply(run_id, "float")  # answer → the lead redrafts with it
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "awaiting_approval" and run["pending"] is None

    db.recover()  # restart while waiting for approval: nothing moves on its own
    assert db.run(run_id)["state"] == "awaiting_approval"

    runner.approve(run_id, {"acceptance_criteria": ["median ok", "tests pass"]})
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "accepted"
    assert TaskSpec.model_validate_json(run["spec_json"]).acceptance_criteria == [
        "median ok",
        "tests pass",
    ]


def test_approval_refuses_commands_outside_policy(studio, repo: Path) -> None:
    lead = FakeLead("autopilot", drafts=[dict(DRAFT, acceptance_commands=["bash -c 'rm -rf /'"])])
    runner, db, _ = studio(FakeGateway([]), [], lead)
    run_id = start(db, TaskSpec(goal="přidej median", repo_path=str(repo)))
    runner.execute(run_id)
    assert "nespustí" in db.messages(run_id)[-1]["text"]
    try:
        runner.approve(run_id)
        raise AssertionError("approve must refuse")
    except ValueError as exc:
        assert "not allowed" in str(exc)


def test_sensitive_files_block_leads_until_the_user_confirms(studio, repo: Path) -> None:
    (repo / "config.py").write_text('API_KEY = "abcdefghijklmnop1234"\n', encoding="utf-8")
    from conftest import git

    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "x", "--allow-empty")
    git(repo, "add", "config.py")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "cfg")
    lead = FakeLead("autopilot")
    runner, db, _ = studio(FakeGateway(good_script()), [ACCEPT], lead)
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "needs_decision" and run["pending"] == "sensitive"
    assert lead.calls == []  # no lead saw the copy
    runner.retry(run_id)  # retry does not bypass the gate
    runner.execute(run_id)
    assert db.run(run_id)["pending"] == "sensitive"
    runner.allow_sensitive(run_id)
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"


def no_model_gateway() -> FakeGateway:
    err = ProviderError("down", status=503)
    return FakeGateway([], per_model={"test/model-a": err, "test/model-b": err})


def test_auto_escalation_hands_failed_worker_task_to_primary(studio, repo: Path) -> None:
    lead = FakeLead("autopilot", implement=writes_median)
    runner, db, _ = studio(no_model_gateway(), [ACCEPT], lead)
    run_id = start(db, make_spec(repo, escalation="auto"))
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "accepted", run["decision_reason"]
    assert run["implementer"] == "autopilot" and run["escalations"] == 1
    actors = [s["actor"] for s in db.steps(run_id) if s["kind"] == "work"]
    assert actors == ["worker", "lead:autopilot"]
    assert db.last_step(run_id, "review")["actor"] == "autopilot"


def test_ask_escalation_waits_for_takeover(studio, repo: Path) -> None:
    lead = FakeLead("autopilot", implement=writes_median)
    runner, db, _ = studio(no_model_gateway(), [ACCEPT], lead)
    run_id = start(db, make_spec(repo, escalation="ask"))
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "needs_decision" and run["pending"] == "takeover"
    runner.takeover(run_id)
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"


def test_escalation_off_and_limit(studio, repo: Path) -> None:
    lead = FakeLead("autopilot", implement=writes_median)
    runner, db, _ = studio(no_model_gateway(), [], lead)
    for spec in (
        make_spec(repo, escalation="off"),
        make_spec(repo, escalation="auto", budget={"max_escalations": 0}),
    ):
        run_id = start(db, spec)
        runner.execute(run_id)
        run = db.run(run_id)
        assert run["state"] == "needs_decision" and run["pending"] is None
        assert run["escalations"] == 0
    assert lead.calls == [("plan", "read")] * 2  # never asked to implement


def test_strong_agent_recommendation_routes_auto_task_to_lead(studio, repo: Path) -> None:
    hard = Plan(
        summary="hard", steps=["x"], complexity="hard", recommended_implementer="strong_agent"
    )
    lead = FakeLead("autopilot", plans=[hard], implement=writes_median)
    gw = FakeGateway([])
    runner, db, _ = studio(gw, [ACCEPT], lead)
    run_id = start(db, make_spec(repo, escalation="auto"))
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"
    assert gw.calls == []  # the cheap worker was never tried


def test_plan_questions_pause_until_answered(studio, repo: Path) -> None:
    asking = Plan(summary="?", steps=["x"], questions=["Má median vracet int?"])
    lead = FakeLead("autopilot", plans=[asking])
    runner, db, _ = studio(FakeGateway(good_script()), [ACCEPT], lead)
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "needs_decision" and run["pending"] == "plan"
    runner.reply(run_id, "float")
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"


def test_legacy_phase1_spec_still_loads(repo: Path) -> None:
    old = {
        "title": "t",
        "goal": "goal text",
        "repo_path": str(repo),
        "allowed_paths": ["a"],
        "acceptance_criteria": ["b"],
        "acceptance_commands": ["python -V"],
        "planner": "none",
        "reviewer": "claude",
        "implementer": "external",
        "external_agent": "freebuff",
    }
    spec = TaskSpec.model_validate(old)
    assert spec.implementer == "freebuff"
    assert spec.orchestration.planners() == []
    assert spec.orchestration.reviewers() == ["autopilot"]


def test_stream_catch_up_sends_messages_events_and_state(tmp_path: Path, repo: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d", worker_models=MODELS)
    leads: dict[str, Any] = {"autopilot": FakeLead("autopilot")}
    app = create_app(settings, gateway=FakeGateway([]), leads=leads, start_runner=False)
    with TestClient(app) as c:
        body = {"goal": "Přidej median", "repo_path": str(repo)}
        run_id = c.post("/api/tasks", json=body, headers={"X-Studio": "1"}).json()["run_id"]
        res = c.get(f"/api/runs/{run_id}/stream?follow=false")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        assert "event: message" in res.text and "Přidej median" in res.text
        assert "event: run" in res.text and '"state": "queued"' in res.text
        again = c.get(f"/api/runs/{run_id}/stream?follow=false&messages=999999&events=999999")
        assert "event: message" not in again.text  # cursors skip what the client has
        assert c.get("/api/runs/nope/stream?follow=false").status_code == 404
        # Event payloads reach the UI as `data` (e.g. the accepted patch path), in both views.
        app.state.db.event(run_id, "accepted", "ok", {"patch": "x.patch"})
        events = c.get(f"/api/runs/{run_id}").json()["events"]
        assert events[-1]["data"] == {"patch": "x.patch"} and "data_json" not in events[-1]
        streamed = c.get(f"/api/runs/{run_id}/stream?follow=false").text
        assert '"data": {"patch": "x.patch"}' in streamed
