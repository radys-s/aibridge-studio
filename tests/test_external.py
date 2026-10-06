"""External implementer (Freebuff driven by the user): handoff, wait, verify, review."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import ACCEPT, MEDIAN_BROKEN, MEDIAN_OK, MEDIAN_TEST, FakeGateway, git, make_spec
from studio.db import Database
from studio.runner import Runner


def start(db: Database, repo: Path, **kw: object) -> str:
    return db.create_task_and_run(make_spec(repo, implementer="external", **kw).model_dump_json())


def freebuff_writes(ws: Path, stats: str = MEDIAN_OK) -> None:
    (ws / "calc" / "stats.py").write_text(stats, encoding="utf-8")
    (ws / "tests" / "test_median.py").write_text(MEDIAN_TEST, encoding="utf-8")


def handed_off(runner: Runner, db: Database, run_id: str) -> Path:
    runner.execute(run_id)
    run = db.run(run_id)
    assert run is not None and run["state"] == "awaiting_external"
    return Path(run["workspace"])


def test_handoff_waits_then_verifies_and_reviews(studio, repo: Path) -> None:
    gw = FakeGateway([])
    runner, db, reviewer = studio(gw, [ACCEPT])
    run_id = start(db, repo)
    ws = handed_off(runner, db, run_id)

    assert gw.calls == []  # no OmniRoute call: the external agent does the work
    brief = ws.parent / f"{run_id}.brief.md"
    text = brief.read_text(encoding="utf-8")
    assert "Add median" in text and "`calc/*.py`" in text and "unittest" in text
    assert not list(ws.glob("*.brief.md"))  # outside the working copy, never in the diff
    runner.execute(run_id)  # nothing to do while waiting
    assert db.run(run_id)["state"] == "awaiting_external"

    freebuff_writes(ws)
    runner.external_done(run_id, "freebuff says done")
    assert db.run(run_id)["state"] == "queued"
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "accepted"
    work = db.last_step(run_id, "work")
    assert work["actor"] == "external:freebuff"
    assert json.loads(work["output_json"])["summary"] == "freebuff says done"
    calls = db.model_calls(run_id)
    assert any(c["requested_model"] == "freebuff-external" for c in calls)
    assert "freebuff says done" in reviewer.review_prompts[0]
    assert "median" not in (repo / "calc" / "stats.py").read_text()


def test_failed_verification_hands_off_again_with_feedback(studio, repo: Path) -> None:
    runner, db, _ = studio(FakeGateway([]), [ACCEPT])
    run_id = start(db, repo)
    ws = handed_off(runner, db, run_id)
    freebuff_writes(ws, MEDIAN_BROKEN)
    runner.external_done(run_id)
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "awaiting_external" and run["iteration"] == 2
    brief = (ws.parent / f"{run_id}.brief.md").read_text(encoding="utf-8")
    assert "Fix these problems" in brief and "Acceptance command failed" in brief

    freebuff_writes(ws)
    runner.external_done(run_id)
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"


def test_out_of_scope_change_by_external_agent_fails_verification(studio, repo: Path) -> None:
    runner, db, _ = studio(FakeGateway([]), [])
    run_id = start(db, repo, budget={"max_iterations": 1})
    ws = handed_off(runner, db, run_id)
    freebuff_writes(ws)
    (ws / "README.md").write_text("rewritten by the agent", encoding="utf-8")
    runner.external_done(run_id)
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "needs_decision"
    verify = json.loads(db.last_step(run_id, "verify")["output_json"])
    assert not verify["passed"] and "README.md" in json.dumps(verify)


def test_secret_in_working_copy_blocks_handoff(studio, repo: Path) -> None:
    (repo / "config.py").write_text('API_KEY = "abcdefghijklmnop1234"\n', encoding="utf-8")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "cfg")
    runner, db, _ = studio(FakeGateway([]), [])
    run_id = start(db, repo)
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "needs_decision" and "config.py" in run["decision_reason"]
    assert not (Path(run["workspace"]).parent / f"{run_id}.brief.md").exists()


def test_rewritten_history_needs_decision(studio, repo: Path) -> None:
    runner, db, _ = studio(FakeGateway([]), [])
    run_id = start(db, repo)
    ws = handed_off(runner, db, run_id)
    git(ws, "checkout", "-q", "--orphan", "elsewhere")
    git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "orphan")
    runner.external_done(run_id)
    run = db.run(run_id)
    assert run["state"] == "needs_decision" and "history" in run["decision_reason"]


def test_restart_while_awaiting_keeps_waiting(studio, repo: Path) -> None:
    runner, db, _ = studio(FakeGateway([]), [ACCEPT])
    run_id = start(db, repo)
    ws = handed_off(runner, db, run_id)

    fresh = Database(db.path)  # "process restart"
    assert fresh.recover() == []
    assert fresh.run(run_id)["state"] == "awaiting_external"
    freebuff_writes(ws)
    runner.external_done(run_id)
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"


def test_cancel_and_abandon_while_awaiting(studio, repo: Path) -> None:
    runner, db, _ = studio(FakeGateway([]), [ACCEPT])
    run_id = start(db, repo)
    ws = handed_off(runner, db, run_id)
    freebuff_writes(ws)  # half-done work
    runner.cancel(run_id)
    assert db.run(run_id)["state"] == "interrupted"
    with pytest.raises(ValueError):
        runner.external_done(run_id)

    runner.resume(run_id)  # resets the copy and hands it off again
    assert "median" not in (ws / "calc" / "stats.py").read_text()
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "awaiting_external"
    runner.abandon(run_id)
    assert db.run(run_id)["state"] == "abandoned"


def test_api_external_done_in_wrong_state_is_409(tmp_path: Path, repo: Path) -> None:
    from test_policy import _client

    c = _client(tmp_path)
    body = make_spec(repo, implementer="external").model_dump(mode="json")
    run_id = c.post("/api/tasks", json=body, headers={"X-Studio": "1"}).json()["run_id"]
    resp = c.post(
        f"/api/runs/{run_id}/external-done", json={"note": "x"}, headers={"X-Studio": "1"}
    )
    assert resp.status_code == 409


def test_open_terminal_only_while_awaiting_and_with_scrubbed_env(
    studio, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from studio import external

    launched: list[tuple[list[str], dict[str, str]]] = []

    def fake_popen(argv, cwd, env, stdin):  # type: ignore[no-untyped-def]
        launched.append((argv, env))

    runner, db, _ = studio(FakeGateway([]), [])
    run_id = start(db, repo)
    with pytest.raises(ValueError):
        runner.open_external_terminal(run_id)  # queued, not handed off yet
    ws = handed_off(runner, db, run_id)
    # Patched only now: `external.subprocess` is the global module that git also uses.
    monkeypatch.setattr(external.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "should-not-leak")

    runner.open_external_terminal(run_id)
    argv, env = launched[0]
    assert str(ws) in argv and argv[-1] == "freebuff"
    assert "OMNIROUTE_API_KEY" not in env
    assert db.run(run_id)["state"] == "awaiting_external"
