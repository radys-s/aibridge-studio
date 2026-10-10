"""Security boundaries and the evidence contract for outcome learning."""

import json
from pathlib import Path

import pytest

from studio.db import Database
from studio.domain import Review
from studio.review import review_passed, review_reason
from studio.sandbox import SENSITIVE_OUTPUT, Denied, Sandbox, safe_text
from studio.vault import MAX_NOTE_BYTES, Vault

TOKEN = "sk-" + "z" * 24


@pytest.mark.parametrize("tail", [TOKEN, "#osobni"])
def test_vault_scans_after_context_boundary(tmp_path: Path, tail: str) -> None:
    folder = tmp_path / "02 Projekty"
    folder.mkdir()
    (folder / "Safe.md").write_text("needle safe", encoding="utf-8")
    (folder / "Hidden.md").write_text("needle " + "x" * 45_000 + "\n" + tail, encoding="utf-8")
    vault = Vault(tmp_path)
    with pytest.raises(Denied):
        vault.read("02 Projekty/Hidden.md")
    assert not vault.shareable("02 Projekty/Hidden.md")
    assert [h["path"] for h in vault.search("needle")] == ["02 Projekty/Safe.md"]


def test_vault_rejects_oversized_notes_and_outside_links(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    folder = root / "02 Projekty"
    folder.mkdir(parents=True)
    (folder / "Big.md").write_bytes(b"x" * (MAX_NOTE_BYTES + 1))
    vault = Vault(root)
    with pytest.raises(Denied, match="1 MiB"):
        vault.read("02 Projekty/Big.md")
    assert vault.projects() == []
    outside = tmp_path / "outside.md"
    outside.write_text("secret fixture", encoding="utf-8")
    try:
        (folder / "Outside.md").symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links require Windows developer mode or privilege")
    assert "02 Projekty/Outside.md" not in vault.notes()


@pytest.mark.parametrize(
    "criteria",
    [
        [],
        [{"criterion": "first", "met": True}],
        [{"criterion": "first", "met": True}, {"criterion": "first", "met": True}],
        [{"criterion": "first", "met": True}, {"criterion": "other", "met": True}],
    ],
)
def test_incomplete_or_duplicate_reviews_cannot_pass(criteria: list[dict[str, object]]) -> None:
    review = {"verdict": "accept", "findings": [], "criteria": criteria}
    assert review_reason(review, ["first", "second"])
    assert not review_passed(review, ["first", "second"])


def test_acceptance_requires_complete_met_criteria_without_blockers() -> None:
    review = {
        "verdict": "accept",
        "findings": [],
        "criteria": [{"criterion": " FIRST ", "met": True}, {"criterion": "second", "met": True}],
    }
    assert review_passed(review, ["first", "second"])
    review["findings"] = [{"severity": "major"}]
    assert not review_passed(review, ["first", "second"])
    assert not review_passed(
        {"verdict": "accept", "findings": [], "criteria": [{"criterion": " ", "met": True}]}, [" "]
    )


def test_command_output_is_scanned_before_clipping(tmp_path: Path) -> None:
    script = tmp_path / "emit.py"
    script.write_text(f"print('a' * 20000 + ' ' + {TOKEN!r} + ' ' + 'b' * 20000)", encoding="utf-8")
    result = Sandbox(tmp_path, ["*"]).run_command("python emit.py")
    assert result.exit_code == 0
    assert result.output == SENSITIVE_OUTPUT
    assert TOKEN not in json.dumps(result.as_dict())
    assert safe_text("a" * 20_000 + " " + TOKEN + " " + "b" * 20_000) == SENSITIVE_OUTPUT


def test_persisted_outputs_and_events_never_echo_secrets(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    run = db.create_task_and_run('{"goal":"fixture","repo_path":"."}')
    step = db.start_step(run, "work", 1, "worker")
    db.finish_step(step, "failed", {"output": TOKEN}, TOKEN)
    db.agent_event(run, step, "autopilot", "text", TOKEN, {"args": TOKEN})
    db.event(run, "tool", TOKEN, {"result": TOKEN})
    db.message(run, "autopilot", TOKEN)
    assert TOKEN not in json.dumps(
        [db.steps(run), db.events(run), db.agent_events(run), db.messages(run)]
    )


def test_missing_criteria_pauses_without_acceptance(studio, repo: Path) -> None:
    from conftest import FakeGateway, make_spec

    runner, db, _ = studio(FakeGateway([]), [])
    spec = make_spec(repo)
    run_id = db.create_task_and_run(spec.model_dump_json())
    result = runner._apply_reviews(
        db.run(run_id),
        spec,
        {"autopilot": Review(verdict="accept", summary="claim", criteria=[])},
        "worker",
        set(),
    )
    assert result == "pause"
    assert db.run(run_id)["state"] == "needs_decision"
    assert db.run(run_id)["pending"] == "review"
    with pytest.raises(ValueError):
        runner.accept(run_id)


@pytest.mark.parametrize("phase", ["verify", "review", "work"])
def test_sensitive_model_evidence_has_no_override(studio, repo: Path, phase: str) -> None:
    from conftest import FakeGateway, make_spec

    runner, db, _ = studio(FakeGateway([]), [])
    run_id = db.create_task_and_run(make_spec(repo).model_dump_json())
    db.update_run(run_id, state="needs_decision", pending="sensitive", phase=phase)
    with pytest.raises(ValueError, match="cannot be overridden"):
        runner.allow_sensitive(run_id)
    with pytest.raises(ValueError):
        runner.accept(run_id)


def test_sensitive_diff_pauses_before_review_and_is_never_persisted(
    studio, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import studio.runner as runner_module
    from conftest import FakeGateway, make_spec

    gateway = FakeGateway([])
    runner, db, _ = studio(gateway, [])
    spec = make_spec(repo)
    run_id = db.create_task_and_run(spec.model_dump_json())
    db.update_run(run_id, workspace=str(repo), source_commit="fixture", phase="verify")
    monkeypatch.setattr(
        runner_module,
        "verify",
        lambda *_: {
            "passed": True,
            "changed_files": ["calc/stats.py"],
            "commands": [],
            "empty_diff": False,
            "out_of_scope": [],
        },
    )
    monkeypatch.setattr(runner_module.workspace, "diff", lambda *_: "safe\n+ " + TOKEN)
    assert runner._phase_verify(db.run(run_id), spec) == "pause"
    assert db.run(run_id)["pending"] == "sensitive"
    assert TOKEN not in db.last_step(run_id, "verify")["output_json"]
    assert gateway.calls == []
    with pytest.raises(ValueError):
        runner.allow_sensitive(run_id)


@pytest.mark.parametrize("actual_review", [None, "other-provider/worker-model"])
def test_unknown_or_same_actual_reviewer_requires_manual_decision(
    studio, repo: Path, monkeypatch: pytest.MonkeyPatch, actual_review: str | None
) -> None:
    from conftest import ACCEPT, FakeGateway, make_spec

    runner, db, _ = studio(FakeGateway([]), [])
    spec = make_spec(repo)
    run_id = db.create_task_and_run(spec.model_dump_json())
    db.update_run(run_id, workspace=str(repo), source_commit="fixture", phase="review")
    work = db.start_step(run_id, "work", 1, "worker")
    db.finish_step(work, "done", {"summary": "claim"})
    db.record_model_call(
        run_id=run_id,
        step_id=work,
        role="worker",
        requested_model="a/model",
        actual_model="worker-model",
        provider="a",
        outcome="ok",
    )
    verify_step = db.start_step(run_id, "verify", 1, "studio")
    db.finish_step(
        verify_step,
        "done",
        {
            "passed": True,
            "diff": "fixture",
            "commands": [],
            "changed_files": ["fixture.py"],
            "out_of_scope": [],
        },
    )
    review = db.start_step(run_id, "review", 1, "autopilot")
    db.finish_step(review, "done", ACCEPT.model_dump())
    db.record_model_call(
        run_id=run_id,
        step_id=review,
        role="review",
        requested_model="b/model",
        actual_model=actual_review,
        provider="b",
        outcome="ok",
    )
    monkeypatch.setattr(runner, "_ask_leads", lambda *_args, **_kwargs: {"autopilot": ACCEPT})
    assert runner._phase_review(db.run(run_id), spec) == "pause"
    assert db.run(run_id)["pending"] == "self_review"
    assert json.loads(db.last_step(run_id, "review")["output_json"])["independent"] is False


def test_gateway_error_is_scanned_before_clipping(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from studio.gateway import OmniRouteGateway, ProviderError

    body = "prefix " + "x" * 390 + " " + TOKEN + " padding" * 100
    response = httpx.Response(502, text=body)
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: response)
    with pytest.raises(ProviderError) as exc:
        OmniRouteGateway("http://fixture").chat("fixture/model", [])
    assert SENSITIVE_OUTPUT in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_reviewer_excludes_the_same_model_behind_another_provider(
    studio, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import ACCEPT, FakeGateway, make_spec
    from studio.config import ModelSpec

    runner, db, _ = studio(FakeGateway([]), [])
    caps = frozenset({"coding", "tools", "lead"})
    runner.router.models = (
        ModelSpec("groq/openai/gpt-oss-120b", caps, 6),
        ModelSpec("cerebras/gpt-oss-120b", caps, 6),
        ModelSpec("groq/openai/gpt-oss-20b", caps, 4),
    )
    spec = make_spec(repo)
    run_id = db.create_task_and_run(spec.model_dump_json())
    db.update_run(run_id, workspace=str(repo), source_commit="fixture", phase="review")
    work = db.start_step(run_id, "work", 1, "worker")
    db.finish_step(work, "done", {"summary": "claim"})
    db.record_model_call(
        run_id=run_id,
        step_id=work,
        role="worker",
        requested_model="groq/openai/gpt-oss-120b",
        actual_model="openai/gpt-oss-120b",
        provider="groq",
        outcome="ok",
    )
    verify_step = db.start_step(run_id, "verify", 1, "studio")
    db.finish_step(
        verify_step,
        "done",
        {"passed": True, "diff": "x", "commands": [], "changed_files": [], "out_of_scope": []},
    )
    seen: dict[str, frozenset[str]] = {}

    def ask(*_args: object, exclude_models: frozenset[str] = frozenset(), **_kw: object):  # type: ignore[no-untyped-def]
        seen["exclude"] = exclude_models
        return {"autopilot": ACCEPT}

    monkeypatch.setattr(runner, "_ask_leads", ask)
    monkeypatch.setattr(runner, "_apply_reviews", lambda *_a, **_k: "pause")
    runner._phase_review(db.run(run_id), spec)
    assert seen["exclude"] == {"groq/openai/gpt-oss-120b", "cerebras/gpt-oss-120b"}
