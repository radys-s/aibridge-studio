"""End-to-end control-plane tests with a scripted gateway and fake leads (no live services)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import (
    ACCEPT,
    MEDIAN_BROKEN,
    MEDIAN_OK,
    MEDIAN_TEST,
    FakeGateway,
    assistant,
    good_script,
    make_spec,
    tool_call,
)
from studio.db import Database
from studio.domain import Finding, Review
from studio.gateway import GatewayTimeout, GatewayUnavailable, ProviderError


def start(db: Database, spec) -> str:  # type: ignore[no-untyped-def]
    return db.create_task_and_run(spec.model_dump_json())


def test_happy_path_accepts_and_records_actual_model(studio, repo: Path) -> None:
    gw = FakeGateway(good_script())
    runner, db, reviewer = studio(gw, [ACCEPT])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "accepted"
    verify = json.loads(db.last_step(run_id, "verify")["output_json"])
    assert verify["passed"] and verify["commands"][0]["exit_code"] == 0
    assert set(verify["changed_files"]) == {"calc/stats.py", "tests/test_median.py"}
    calls = [c for c in db.model_calls(run_id) if c["role"] == "worker"]
    assert {c["actual_model"] for c in calls} == {"model-a-actual"}
    assert {c["provider"] for c in calls} == {"fakeprov"}
    # The review saw the diff and independent verification, not only the worker summary.
    prompt = reviewer.review_prompts[0]
    assert "def median" in prompt and "INDEPENDENT VERIFICATION" in prompt
    assert "unverified claim" in prompt
    # The user's checkout is untouched.
    assert "median" not in (repo / "calc" / "stats.py").read_text()
    assert (Path(run["workspace"]).parent / f"{run_id}.patch").exists()


def test_failing_tests_send_work_back_then_needs_decision(studio, repo: Path) -> None:
    broken = [
        assistant(
            tool_call("write_file", {"path": "calc/stats.py", "content": MEDIAN_BROKEN}),
            tool_call("write_file", {"path": "tests/test_median.py", "content": MEDIAN_TEST}, 1),
        ),
        assistant(tool_call("finish", {"summary": "done, all tests pass"})),  # a false claim
    ]
    gw = FakeGateway(broken + broken)
    runner, db, _ = studio(gw, [])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "needs_decision"
    assert "iteration limit 2" in run["decision_reason"]
    verifies = [s for s in db.steps(run_id) if s["kind"] == "verify"]
    assert [s["status"] for s in verifies] == ["failed", "failed"]
    feedback = json.loads(run["feedback_json"])
    assert any("Acceptance command failed" in f for f in feedback)


def test_rework_after_failure_can_reach_acceptance(studio, repo: Path) -> None:
    broken = [
        assistant(
            tool_call("write_file", {"path": "calc/stats.py", "content": MEDIAN_BROKEN}),
            tool_call("write_file", {"path": "tests/test_median.py", "content": MEDIAN_TEST}, 1),
        ),
        assistant(tool_call("finish", {"summary": "done"})),
    ]
    fixed = [
        assistant(tool_call("write_file", {"path": "calc/stats.py", "content": MEDIAN_OK})),
        assistant(tool_call("finish", {"summary": "fixed even case"})),
    ]
    gw = FakeGateway(broken + fixed)
    runner, db, _ = studio(gw, [ACCEPT])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "accepted" and run["iteration"] == 2


def test_review_request_changes_loops_back(studio, repo: Path) -> None:
    changes = Review(
        verdict="request_changes",
        summary="missing docstring",
        criteria=ACCEPT.criteria,
        findings=[
            Finding(severity="major", file="calc/stats.py", problem="no docstring", fix="add")
        ],
    )
    fix = [
        assistant(
            tool_call(
                "replace_in_file",
                {
                    "path": "calc/stats.py",
                    "old_text": "def median(values: list[float]) -> float:\n",
                    "new_text": 'def median(values: list[float]) -> float:\n    """Median."""\n',
                },
            )
        ),
        assistant(tool_call("finish", {"summary": "docstring"})),
    ]
    gw = FakeGateway(good_script() + fix)
    runner, db, reviewer = studio(gw, [changes, ACCEPT])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"
    assert "no docstring" in json.loads(db.run(run_id)["feedback_json"])[0]


def test_accept_verdict_with_blocker_is_not_accepted(studio, repo: Path) -> None:
    sneaky = Review(
        verdict="accept",
        summary="lgtm",
        findings=[Finding(severity="blocker", problem="deletes data")],
    )
    gw = FakeGateway(good_script())
    runner, db, _ = studio(gw, [sneaky])
    run_id = start(db, make_spec(repo, budget={"max_iterations": 1}))
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "needs_decision"


def test_provider_failure_falls_back_once_through_router(studio, repo: Path) -> None:
    gw = FakeGateway(
        good_script(), per_model={"test/model-a": ProviderError("429: rate", status=429)}
    )
    runner, db, _ = studio(gw, [ACCEPT])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"
    # model-a tried exactly once; afterwards it is cooling down and never retried.
    assert gw.calls.count("test/model-a") == 1
    assert "test/model-a" in db.cooldowns()
    outcomes = [
        (c["requested_model"], c["outcome"])
        for c in db.model_calls(run_id)
        if c["role"] == "worker"
    ]
    assert outcomes[0] == ("test/model-a", "provider_error")
    assert all(m == "test/model-b" for m, o in outcomes[1:])


def test_all_providers_failing_needs_decision(studio, repo: Path) -> None:
    err = ProviderError("503: down", status=503)
    gw = FakeGateway([], per_model={"test/model-a": err, "test/model-b": err})
    runner, db, _ = studio(gw, [])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "needs_decision" and "no_model" in run["decision_reason"]


def test_gateway_down_does_not_cool_models(studio, repo: Path) -> None:
    gw = FakeGateway([GatewayUnavailable("OmniRoute not reachable")])
    runner, db, _ = studio(gw, [])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "needs_decision" and "gateway_down" in run["decision_reason"]
    assert db.cooldowns() == {}


def test_timeout_cools_model_and_switches(studio, repo: Path) -> None:
    gw = FakeGateway([GatewayTimeout("slow"), *good_script()])
    runner, db, _ = studio(gw, [ACCEPT])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"
    assert gw.calls[:2] == ["test/model-a", "test/model-b"]


def test_cancel_during_work_interrupts_and_resume_restarts_clean(studio, repo: Path) -> None:
    holder: dict[str, object] = {}

    def cancel_on_second_call(n: int) -> None:
        if n == 2:
            holder["runner"].cancel(holder["run_id"])  # type: ignore[attr-defined]

    script = good_script()
    gw = FakeGateway(script[:2], hook=cancel_on_second_call)
    runner, db, _ = studio(gw, [ACCEPT])
    run_id = start(db, make_spec(repo))
    holder.update(runner=runner, run_id=run_id)
    runner.execute(run_id)

    run = db.run(run_id)
    assert run["state"] == "interrupted"
    assert db.last_step(run_id, "work")["status"] == "interrupted"
    ws = Path(run["workspace"])
    assert "median" in (ws / "calc" / "stats.py").read_text()  # half-done work exists

    runner.resume(run_id)
    assert "median" not in (ws / "calc" / "stats.py").read_text()  # reset to checkpoint
    gw.hook = None
    gw.script = good_script()
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"


def test_restart_recovery_marks_interrupted_without_rerunning(
    studio, repo: Path, tmp_path: Path
) -> None:
    gw = FakeGateway(good_script())
    runner, db, _ = studio(gw, [ACCEPT])
    run_id = start(db, make_spec(repo))
    # Simulate a crash in the middle of the work step.
    runner._phase_setup(db.run(run_id), make_spec(repo))
    db.update_run(run_id, state="working", phase="work")
    db.start_step(run_id, "work", 1, "worker")

    fresh = Database(db.path)  # "process restart"
    assert fresh.recover() == [run_id]
    assert fresh.run(run_id)["state"] == "interrupted"
    assert fresh.last_step(run_id, "work")["status"] == "interrupted"
    assert fresh.next_queued() is None  # nothing resumes on its own
    assert gw.calls == []
    runner.resume(run_id)
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"


def test_crash_before_the_iteration_checkpoint_is_recorded_keeps_the_previous_work(
    studio, repo: Path
) -> None:
    """Studio stops after iteration 2 was queued (or its checkpoint committed) but before the
    new base was written: no worker ran in iteration 2, so iteration 1's work must stay."""
    from studio import workspace

    runner, db, _ = studio(FakeGateway([]), [ACCEPT])
    run_id = start(db, make_spec(repo))
    runner._phase_setup(db.run(run_id), make_spec(repo))
    ws = Path(db.run(run_id)["workspace"])
    first_base = db.run(run_id)["iteration_base"]
    db.update_run(run_id, base_iteration=1)
    (ws / "calc" / "stats.py").write_text(MEDIAN_OK, encoding="utf-8")  # iteration 1's work
    checkpoint = workspace.commit_all(ws, "studio: before iteration 2")
    db.update_run(run_id, state="working", phase="work", iteration=2)
    Database(db.path).recover()

    runner.resume(run_id)
    assert "median" in (ws / "calc" / "stats.py").read_text()  # iteration 1 survived
    assert db.run(run_id)["iteration_base"] == first_base  # not adopted from git
    assert any(e["kind"] == "warning" for e in db.events(run_id))
    # The re-run work phase checkpoints the kept work as iteration 2's base.
    assert workspace.commit_all(ws, "studio: before iteration 2") == checkpoint


def test_a_commit_made_during_the_attempt_is_never_adopted_as_the_base(studio, repo: Path) -> None:
    """A worker can run git through python and forge Studio's checkpoint message; resume
    must still reset to the recorded checkpoint of the current iteration."""
    from studio import workspace

    runner, db, _ = studio(FakeGateway([]), [ACCEPT])
    run_id = start(db, make_spec(repo))
    runner._phase_setup(db.run(run_id), make_spec(repo))
    ws = Path(db.run(run_id)["workspace"])
    base = db.run(run_id)["iteration_base"]
    db.update_run(run_id, state="interrupted", phase="work", iteration=1, base_iteration=1)
    (ws / "calc" / "stats.py").write_text(MEDIAN_OK, encoding="utf-8")
    workspace.commit_all(ws, "studio: before iteration 1")  # forged by the attempt
    runner.resume(run_id)
    assert db.run(run_id)["iteration_base"] == base
    assert "median" not in (ws / "calc" / "stats.py").read_text()


def test_out_of_scope_write_is_refused_by_tool(studio, repo: Path) -> None:
    script = [
        assistant(tool_call("write_file", {"path": "README.md", "content": "pwned"})),
        *good_script()[1:],
    ]
    gw = FakeGateway(script)
    runner, db, _ = studio(gw, [ACCEPT])
    run_id = start(db, make_spec(repo))
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"
    events = [e["data_json"] or "" for e in db.events(run_id) if e["kind"] == "tool"]
    assert any("outside the task scope" in e for e in events)
    assert "pwned" not in (Path(db.run(run_id)["workspace"]) / "README.md").read_text()


def test_bad_repo_path_needs_decision(studio, tmp_path: Path) -> None:
    gw = FakeGateway([])
    runner, db, _ = studio(gw, [])
    plain = tmp_path / "plain"
    plain.mkdir()
    run_id = start(db, make_spec(plain))
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "needs_decision"
