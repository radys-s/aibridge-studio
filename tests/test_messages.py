"""User-facing text in English and Czech (messages.py)."""

from __future__ import annotations

import ast
import re
import string
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import FakeGateway
from studio.api import create_app
from studio.config import Settings
from studio.db import AUTO_APPROVED, Database
from studio.domain import TaskSpec
from studio.messages import CS, tr

SRC = Path(__file__).resolve().parents[1] / "backend" / "src" / "studio"


def message_ids() -> list[str]:
    """Every literal passed to db.msg(...) or tr(lang, ...)."""
    ids: list[str] = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "msg" and node.args:
                arg = node.args[0]
            elif isinstance(func, ast.Name) and func.id == "tr" and len(node.args) > 1:
                arg = node.args[1]
            else:
                continue
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                ids.append(arg.value)
    return ids


def fields(text: str) -> list[str]:
    return sorted(f for _, f, _, _ in string.Formatter().parse(text) if f)


def test_every_message_has_czech_text_with_the_same_placeholders() -> None:
    ids = message_ids()
    assert len(ids) > 50
    assert [i for i in ids if i not in CS] == []
    for en, cs in CS.items():
        assert fields(cs) == fields(en), en


def test_no_czech_left_in_the_source_outside_the_dictionary() -> None:
    czech = re.compile(r"[áéíóúůýěščřžťďňÁÉÍÓÚŮÝĚŠČŘŽŤĎŇ]")
    allowed = {"messages.py", "vault.py"}  # vault.py: the #osobní privacy tag
    hits = [
        f"{p.name}:{n}"
        for p in SRC.rglob("*.py")
        if p.name not in allowed
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if czech.search(line)
    ]
    assert hits == []


def test_translates_by_language() -> None:
    assert tr(None, "Retrying phase {phase}", phase="work") == "Retrying phase work"
    assert tr("en", "Retrying phase {phase}", phase="work") == "Retrying phase work"
    assert tr("cs", "Retrying phase {phase}", phase="work") == "Opakuji fázi work"
    assert tr("cs", "not in the dictionary") == "not in the dictionary"


def test_events_follow_the_language_preference(tmp_path: Path, repo: Path) -> None:
    db = Database(tmp_path / "studio.db")
    spec = TaskSpec(goal="Add median", repo_path=str(repo))
    first = db.create_task_and_run(spec.model_dump_json())
    db.set_setting("language", "cs")
    second = db.create_task_and_run(spec.model_dump_json())
    assert db.events(first)[0]["message"] == "Task received and queued"
    assert db.events(second)[0]["message"] == "Úkol přijat a zařazen do fronty"
    assert db.events(first)[0]["message"] == "Task received and queued"  # stored text stays
    assert db.msg(AUTO_APPROVED) == CS[AUTO_APPROVED]


def test_language_endpoint(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "d"), gateway=FakeGateway([]), start_runner=False)
    client = TestClient(app, headers={"X-Studio": "1"})
    assert client.get("/api/settings").json()["language"] is None
    assert client.put("/api/language", json={"language": "cs"}).status_code == 200
    assert client.get("/api/settings").json()["language"] == "cs"
    assert client.put("/api/language", json={"language": "de"}).status_code == 422
