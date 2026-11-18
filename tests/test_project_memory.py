"""Curated memory persistence/security acceptance without cloud or personal data."""

import json
import sqlite3
from pathlib import Path

import pytest

from studio.db import Database
from studio.project_memory import ProjectMemory


@pytest.fixture
def memory(tmp_path: Path) -> ProjectMemory:
    return ProjectMemory(Database(tmp_path / "studio.db"))


def candidate(title: str = "Rotation rule", **updates: object) -> dict:
    return {"title": title, "content": "Rotate sessions every seven days", **updates}


def test_persistent_context_after_source_removed_and_restart(
    memory: ProjectMemory, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "rule.md"
    source.write_text("Rotate sessions every seven days", encoding="utf-8")
    project = memory.register_project(root)
    proposed = memory.propose(
        project["id"],
        candidate(
            kind="learning",
            evidence=[{"path": "rule.md", "quote": "Rotate sessions every seven days"}],
        ),
        workspace=root,
    )
    assert not memory.search(project["id"])
    memory.approve(proposed["id"])
    source.unlink()
    restarted = ProjectMemory(Database(memory.db.path))
    packet = restarted.context(project["id"], "rotation sessions", run_id="fresh", phase="plan")
    assert "seven days" in packet["text"]
    assert len(packet["text"]) <= 8000
    assert packet["items"]
    assert "UNTRUSTED DATA" in packet["text"]
    second = restarted.context(project["id"], "rotation sessions", run_id="fresh", phase="review")
    assert packet["fingerprint"] == second["fingerprint"]
    assert packet["id"] != second["id"]


def test_replay_immutable_atomic_and_duplicate_delivery(
    memory: ProjectMemory, tmp_path: Path
) -> None:
    project = memory.register_project(tmp_path)
    pending = memory.propose(project["id"], candidate())
    memory.approve(pending["id"])
    replayed = ProjectMemory(Database(tmp_path / "replay.db"))
    replayed.replay(memory.export_events())
    replayed.replay(memory.export_events())
    assert (
        replayed.search(project["id"])[0]["payload"] == memory.search(project["id"])[0]["payload"]
    )
    assert len(replayed.export_events()) == 3
    for table in ("memory_change_event", "memory_revision", "memory_provenance"):
        with pytest.raises(sqlite3.IntegrityError), memory.db.connect() as c:
            c.execute(f"DELETE FROM {table}")
    before = len(memory.export_events())
    with pytest.raises(ValueError), memory.db.connect() as c:
        memory._event(c, "bad", {})
    assert len(memory.export_events()) == before


def test_dedup_and_conflict_beyond_first_hundred(memory: ProjectMemory, tmp_path: Path) -> None:
    project = memory.register_project(tmp_path)
    for i in range(105):
        memory.approve(
            memory.propose(project["id"], candidate(f"Claim {i}", subject_key=f"claim:{i}"))["id"]
        )
    duplicate = memory.propose(project["id"], candidate("Claim 0", subject_key="claim:0"))
    assert duplicate["state"] == "duplicate"
    conflict = memory.propose(project["id"], candidate("Different rule", subject_key="claim:0"))
    assert conflict["state"] == "conflict"
    with pytest.raises(ValueError, match="Conflict"):
        memory.approve(conflict["id"])
    memory.approve(conflict["id"], resolve_conflicts=True)
    assert memory.search(project["id"], "Claim 0")[0]["id"] != duplicate["applied_item_id"]


@pytest.mark.parametrize(
    "field", ["title", "content", "subject_key", "source_agent", "evidence", "extra"]
)
def test_every_candidate_field_scanned(memory: ProjectMemory, tmp_path: Path, field: str) -> None:
    project = memory.register_project(tmp_path)
    value = "sk-proj-" + "X" * 32
    data = candidate(**{field: [{"quote": value}] if field == "evidence" else value})
    with pytest.raises(ValueError, match="Sensitive"):
        memory.propose(project["id"], data)
    assert value not in json.dumps(memory.export_events())
    assert not memory.candidates()


def test_evidence_ranges_denied_files_and_traversal(memory: ProjectMemory, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "rule.md").write_text("first\nsecond", encoding="utf-8")
    (root / ".env").write_text("ordinary content", encoding="utf-8")
    project = memory.register_project(root)
    for evidence in (
        {"path": "../outside.md"},
        {"path": ".env"},
        {"path": "rule.md", "line_start": 2, "line_end": 1},
        {"path": "rule.md", "line_start": 1, "line_end": 1, "quote": "second"},
    ):
        with pytest.raises(ValueError):
            memory.propose(project["id"], candidate(evidence=[evidence]), workspace=root)


def test_scope_fts_and_packet_budget(memory: ProjectMemory, tmp_path: Path) -> None:
    a = memory.register_project(tmp_path / "a")
    b = memory.register_project(tmp_path / "b")
    p = memory.propose(
        a["id"], candidate("České pravidlo", content="Řešení příliš žluťoučkého koně")
    )
    memory.approve(p["id"])
    assert memory.search(a["id"], "reseni prilis")
    assert not memory.search(b["id"], "reseni")
    assert len(memory.context(a["id"], "reseni", max_chars=128)["text"]) <= 128
    with pytest.raises(ValueError):
        memory.propose(a["id"], candidate(result_state="merged"))


def test_recheck_race_and_rejection(memory: ProjectMemory, tmp_path: Path) -> None:
    project = memory.register_project(tmp_path)
    a = memory.propose(project["id"], candidate())
    b = memory.propose(project["id"], candidate())
    memory.approve(a["id"])
    with pytest.raises(ValueError, match="Duplicate"):
        memory.approve(b["id"])
    memory.reject(b["id"])
    with pytest.raises(ValueError):
        memory.approve(b["id"])


def test_staleness_uses_relevant_file_not_entire_head(
    memory: ProjectMemory, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "rule.md"
    source.write_text("Rotate sessions every seven days", encoding="utf-8")
    project = memory.register_project(root)
    pending = memory.propose(
        project["id"], candidate(evidence=[{"path": "rule.md"}]), workspace=root
    )
    memory.approve(pending["id"])
    first = memory.context(project["id"], "rotate", head="old")
    assert not first["items"][0]["potentially_stale"]
    unrelated_head = memory.context(project["id"], "rotate", head="unrelated-new-head")
    assert not unrelated_head["items"][0]["potentially_stale"]
    source.write_text("Rotate sessions daily", encoding="utf-8")
    changed = memory.context(project["id"], "rotate", head="unrelated-new-head")
    assert changed["items"][0]["potentially_stale"]
    assert changed["fingerprint"] != unrelated_head["fingerprint"]


def test_application_is_explicit_event_and_never_implies_merge(
    memory: ProjectMemory, tmp_path: Path
) -> None:
    project = memory.register_project(tmp_path)
    pending = memory.propose(project["id"], candidate())
    memory.approve(pending["id"])
    assert memory.search(project["id"])[0]["result_state"] == "claim"
    memory.mark_applied("run-id", "studio/run-id", "abc")
    # A different run's apply never changes this unrelated claim.
    assert memory.search(project["id"])[0]["result_state"] == "claim"
    replayed = ProjectMemory(Database(tmp_path / "replayed.db"))
    replayed.replay(memory.export_events())
    with replayed.db.connect() as c:
        assert c.execute("SELECT branch FROM memory_application").fetchone()[0] == "studio/run-id"


def test_candidate_pagination_and_nested_whole_file_scan(
    memory: ProjectMemory, tmp_path: Path
) -> None:
    project = memory.register_project(tmp_path)
    for i in range(3):
        memory.propose(project["id"], candidate(f"Rule {i}"))
    assert len(memory.candidates(project["id"], limit=1)) == 1
    assert len(memory.candidates(project["id"], limit=1, offset=2)) == 1
    source = tmp_path / "late.md"
    source.write_text("x" * 40_100 + "\nsk-proj-" + "X" * 32, encoding="utf-8")
    with pytest.raises(ValueError, match="Sensitive"):
        memory.propose(project["id"], candidate(evidence=[{"path": "late.md"}]), workspace=tmp_path)


@pytest.mark.parametrize(
    "secret", ['password = "synthetic-secret-1234"', "password = 'synthetic secret words'"]
)
@pytest.mark.parametrize(
    "field",
    [
        "title",
        "content",
        "subject_key",
        "source_agent",
        "source_commit",
        "evidence",
        "unknown_nested_key",
    ],
)
def test_raw_quoted_credentials_are_blocked_before_json_escaping(
    memory: ProjectMemory, tmp_path: Path, secret: str, field: str
) -> None:
    project = memory.register_project(tmp_path)
    if field == "evidence":
        value: object = [{"type": "user", "quote": secret}]
    elif field == "unknown_nested_key":
        value = [{secret: "ordinary"}]
    else:
        value = secret
    before = memory.export_events()
    with pytest.raises(ValueError, match="Sensitive"):
        memory.propose(project["id"], candidate(**{field: value}))
    assert memory.export_events() == before
    assert not memory.candidates()


@pytest.mark.parametrize("field", ["objective", "head", "policy", "phase", "run_id"])
def test_context_metadata_is_scanned_as_raw_text(
    memory: ProjectMemory, tmp_path: Path, field: str
) -> None:
    project = memory.register_project(tmp_path)
    options = {field: 'password = "synthetic-secret-1234"'}
    objective = options.pop("objective", "rotation")
    with pytest.raises(ValueError, match="Sensitive"):
        memory.context(project["id"], objective, **options)
    with memory.db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM memory_context_packet").fetchone()[0] == 0


def test_project_metadata_and_decoded_replay_block_quoted_secrets(
    memory: ProjectMemory, tmp_path: Path
) -> None:
    secret = 'password = "synthetic-secret-1234"'
    with pytest.raises(ValueError, match="Sensitive"):
        memory.register_project(tmp_path, name=secret)
    assert not memory.projects()
    project = memory.register_project(tmp_path)
    memory.propose(project["id"], candidate())
    events = memory.export_events()
    payload = json.loads(events[-1]["payload_json"])
    payload["payload"]["content"] = secret
    events[-1]["payload_json"] = json.dumps(payload)
    replayed = ProjectMemory(Database(tmp_path / "secret-replay.db"))
    with pytest.raises(ValueError, match="Sensitive"):
        replayed.replay(events)
    # The project preceding the rejected event is rolled back with the entire batch.
    assert not replayed.export_events()
    assert not replayed.projects()
