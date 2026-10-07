"""Apply an accepted change as a new branch without touching the user's checkout."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import MEDIAN_OK, git
from studio import workspace
from studio.apply import ApplyError, apply_run


@pytest.fixture
def accepted(tmp_path: Path, repo: Path) -> tuple[Path, Path, str]:
    git(repo, "config", "user.name", "Tomas")
    git(repo, "config", "user.email", "t@example.com")
    work = tmp_path / "ws" / "r1"
    base = workspace.create(repo, work)
    (work / "calc" / "stats.py").write_text(MEDIAN_OK, encoding="utf-8")
    workspace.commit_all(work, "studio: worker iteration 1")
    return repo, work, base


def test_branch_is_created_and_user_checkout_is_untouched(
    accepted: tuple[Path, Path, str],
) -> None:
    repo, work, base = accepted
    (repo / "README.md").write_text("my local edit\n", encoding="utf-8")  # unrelated dirt
    before_head = git(repo, "rev-parse", "HEAD")
    before_status = git(repo, "status", "--porcelain")

    applied = apply_run(repo, work, base, "r1", "Přidat median")

    assert applied.branch == "studio/r1" and applied.files == ["calc/stats.py"]
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert git(repo, "status", "--porcelain") == before_status
    assert "median" not in (repo / "calc" / "stats.py").read_text(encoding="utf-8")
    log = subprocess.run(
        ["git", "log", "--format=%an|%s|%P", "-1", "studio/r1"],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    assert log == f"Tomas|Přidat median|{base}"  # one squashed commit on the source commit
    assert "def median" in git(repo, "show", "studio/r1:calc/stats.py")

    with pytest.raises(ApplyError, match="already exists"):
        apply_run(repo, work, base, "r1", "again")


def test_refuses_when_user_changed_a_touched_file(accepted: tuple[Path, Path, str]) -> None:
    repo, work, base = accepted
    (repo / "calc" / "stats.py").write_text("# my work in progress\n", encoding="utf-8")
    with pytest.raises(ApplyError, match="calc/stats.py"):
        apply_run(repo, work, base, "r1", "t")
    assert not git(repo, "branch", "--list", "studio/r1").strip()


def test_dirty_clash_matches_unusual_file_names(accepted: tuple[Path, Path, str]) -> None:
    repo, work, base = accepted
    name = "calc/poznámka k'nová.txt"  # Windows forbids quotes; spaces and diacritics remain
    (work / name).write_text("z worku\n", encoding="utf-8")
    (repo / name).write_text("moje rozpracovaná\n", encoding="utf-8")
    with pytest.raises(ApplyError, match="poznámka"):
        apply_run(repo, work, base, "r1", "t")
    assert "studio-apply" not in git(work, "branch", "--list")


def test_refuses_an_empty_change(tmp_path: Path, repo: Path) -> None:
    work = tmp_path / "ws" / "r2"
    base = workspace.create(repo, work)
    with pytest.raises(ApplyError, match="empty"):
        apply_run(repo, work, base, "r2", "t")


def test_runner_applies_only_accepted_runs(studio, repo: Path) -> None:  # type: ignore[no-untyped-def]
    from conftest import ACCEPT, FakeGateway, good_script, make_spec

    git(repo, "config", "user.name", "Tomas")
    git(repo, "config", "user.email", "t@example.com")
    runner, db, _ = studio(FakeGateway(good_script()), [ACCEPT])
    run_id = db.create_task_and_run(make_spec(repo).model_dump_json())
    with pytest.raises(ValueError, match="accepted"):
        runner.apply_to_repo(run_id)
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"
    data = runner.apply_to_repo(run_id)
    assert data["branch"] == f"studio/{run_id}"
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert any(e["kind"] == "applied" for e in db.events(run_id))
    with pytest.raises(ValueError, match="already exists"):
        runner.apply_to_repo(run_id)
