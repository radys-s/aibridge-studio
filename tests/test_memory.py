"""Continuing a project from Obsidian: what is sent, and writes only after approval."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import FakeGateway, assistant, good_script, tool_call
from studio.agents.api_lead import ApiLead
from studio.api import create_app
from studio.config import ModelSpec, Settings
from studio.db import Database
from studio.domain import MemoryProposal, TaskSpec
from studio.memory import build_proposals
from studio.routing import ModelRouter
from studio.runner import Runner
from studio.vault import Vault

A, B = "test/lead-a", "test/lead-b"
MODELS = (
    ModelSpec(A, frozenset({"coding", "tools", "lead"}), 3),
    ModelSpec(B, frozenset({"tools", "lead"}), 2),
)
DRAFT = {
    "title": "Přidat median",
    "goal": "Přidej median do calc/stats.py s testy.",
    "allowed_paths": ["calc/*.py", "tests/*.py"],
    "acceptance_criteria": ["median funguje"],
    "acceptance_commands": ["python -m unittest discover -s tests -t ."],
    "questions": [],
    "notes": "",
}
PLAN = {
    "summary": "Add median",
    "steps": ["edit"],
    "files_to_touch": [],
    "risks": [],
    "complexity": "trivial",
    "recommended_implementer": "worker",
    "questions": [],
}
ACCEPT = {"verdict": "accept", "summary": "median ok", "findings": [], "criteria": []}
MEMORY = {
    "project_update": "- median hotový, testy prošly",
    "handoff_needed": True,
    "handoff_to": "codex",
    "handoff_topic": "Calc: review median",
    "handoff_done": ["median v calc/stats.py"],
    "handoff_open": ["variance"],
    "handoff_files": ["calc/stats.py"],
    "handoff_avoid": [],
}


class Spy(FakeGateway):
    def __init__(self, script: list[Any]) -> None:
        super().__init__(script)
        self.prompts: list[str] = []

    def chat(self, model, messages, tools=None, timeout_s=None):  # type: ignore[no-untyped-def]
        self.prompts.append(str(messages[1]["content"]) if len(messages) > 1 else "")
        return super().chat(model, messages, tools, timeout_s)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "AI paměť"
    files = {
        "02 Projekty/Calc.md": "# Calc\nStav: chybí median. Kontakt jan@example.com",
        "02 Projekty/Tajne.md": "---\nprivate: true\n---\nnic",
        "08 Předávání/2026-10-06 claude → codex - Calc.md": "---\nstatus: open\n---\nCalc: median",
        "08 Předávání/2026-10-06 claude → codex - jiné.md": "---\nstatus: open\n---\nnesouvisí",
        "01 Profil/Ja.md": "Calc osobní",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return Vault(root)


def submit(payload: dict[str, Any], i: int = 0) -> dict[str, Any]:
    return assistant(tool_call("submit", payload, i))


def build(tmp_path: Path, gw: FakeGateway, vault: Vault) -> tuple[Runner, Database]:
    db = Database(tmp_path / "data" / "studio.sqlite")
    router = ModelRouter(db, MODELS)
    leads: dict[str, Any] = {"autopilot": ApiLead(db, gw, router, lambda: vault)}
    return Runner(db, gw, router, leads, tmp_path / "data" / "ws", lambda: vault), db


def start(db: Database, spec: TaskSpec) -> str:
    run_id = db.create_task_and_run(spec.model_dump_json())
    db.message(run_id, "user", spec.goal)
    return run_id


def test_continue_project_then_write_memory_only_after_approval(
    tmp_path: Path, repo: Path, vault: Vault
) -> None:
    gw = Spy([submit(DRAFT), submit(PLAN), *good_script(), submit(ACCEPT), submit(MEMORY)])
    runner, db = build(tmp_path, gw, vault)
    spec = TaskSpec(
        goal="Pokračuj v projektu Calc", repo_path=str(repo), project_note="02 Projekty/Calc.md"
    )
    run_id = start(db, spec)
    runner.execute(run_id)

    assert db.run(run_id)["state"] == "awaiting_approval"
    draft = gw.prompts[0]
    assert "CONTINUE a project" in draft and "chybí median" in draft
    assert "Calc: median" in draft and "nesouvisí" not in draft  # only related handoffs
    assert "jan@example.com" not in draft and "osobní" not in draft
    sent = next(e for e in db.events(run_id) if e["kind"] == "vault")
    assert "02 Projekty/Calc.md" in sent["message"]

    runner.approve(run_id)
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"
    before = (vault.root / "02 Projekty/Calc.md").read_text(encoding="utf-8")
    proposals = runner._proposals(run_id)[1]
    assert [p["kind"] for p in proposals] == ["append", "new"]
    assert not list((vault.root / "08 Předávání").glob("*studio*"))  # nothing written yet

    assert runner.vault_write(run_id, 0) == "02 Projekty/Calc.md"
    after = (vault.root / "02 Projekty/Calc.md").read_text(encoding="utf-8")
    assert after.startswith(before) and "median hotový" in after
    with pytest.raises(ValueError):
        runner.vault_write(run_id, 0)  # each proposal is written at most once
    runner.vault_discard(run_id, 1)
    assert not list((vault.root / "08 Předávání").glob("*studio*"))
    assert [p["status"] for p in runner._proposals(run_id)[1]] == ["written", "discarded"]


def test_private_project_note_is_never_sent(tmp_path: Path, repo: Path, vault: Vault) -> None:
    gw = Spy([])
    runner, db = build(tmp_path, gw, vault)
    spec = TaskSpec(goal="Pokračuj", repo_path=str(repo), project_note="02 Projekty/Tajne.md")
    run_id = start(db, spec)
    runner.execute(run_id)
    run = db.run(run_id)
    assert run["state"] == "needs_decision" and "cannot be shared" in run["decision_reason"]
    assert gw.calls == []


def test_handoff_proposal_follows_the_template(vault: Vault) -> None:
    items = build_proposals(
        vault, MemoryProposal(**MEMORY), "02 Projekty/Calc.md", "r1", "Median", date(2026, 10, 7)
    )
    handoff = items[1]
    assert handoff["path"] == "08 Předávání/2026-10-07 studio → codex - Calc review median.md"
    text = handoff["content"]
    assert "status: open" in text and "to: codex" in text and "## Odpověď" in text
    assert "[[AI paměť/02 Projekty/Calc|Calc]]" in text
    vault.write(handoff["path"], text, "new")
    assert handoff["path"] in vault.open_handoffs()


def test_vault_api_lists_only_shared_projects(tmp_path: Path, repo: Path, vault: Vault) -> None:
    settings = Settings(data_dir=tmp_path / "d", worker_models=MODELS, vault_path=vault.root)
    app = create_app(settings, gateway=FakeGateway([]), leads={}, start_runner=False)
    with TestClient(app) as c:
        overview = c.get("/api/vault").json()
        assert overview["projects"] == ["02 Projekty/Calc.md"]
        project = c.get("/api/vault/project", params={"note": "02 Projekty/Calc.md"}).json()
        assert project["handoffs"] == ["08 Předávání/2026-10-06 claude → codex - Calc.md"]
        assert "jan@example.com" not in project["preview"]
        denied = c.get("/api/vault/project", params={"note": "01 Profil/Ja.md"})
        assert denied.status_code == 403
        body = {"goal": "Pokračuj", "repo_path": str(repo), "project_note": "02 Projekty/Tajne.md"}
        assert c.post("/api/tasks", json=body, headers={"X-Studio": "1"}).status_code == 422


class ToolSpy(Spy):
    def __init__(self, script: list[Any]) -> None:
        super().__init__(script)
        self.tools: list[list[str]] = []

    def chat(self, model, messages, tools=None, timeout_s=None):  # type: ignore[no-untyped-def]
        self.tools.append([t["function"]["name"] for t in tools or []])
        return super().chat(model, messages, tools, timeout_s)


def accepted_run(tmp_path: Path, repo: Path, vault: Vault, memory: list[Any]) -> Any:
    gw = ToolSpy([submit(DRAFT), submit(PLAN), *good_script(), submit(ACCEPT), *memory])
    runner, db = build(tmp_path, gw, vault)
    spec = TaskSpec(
        goal="Pokračuj v projektu Calc", repo_path=str(repo), project_note="02 Projekty/Calc.md"
    )
    run_id = start(db, spec)
    runner.execute(run_id)
    runner.approve(run_id)
    runner.execute(run_id)
    assert db.run(run_id)["state"] == "accepted"
    return gw, runner, db, run_id


def test_memory_call_offers_only_submit(tmp_path: Path, repo: Path, vault: Vault) -> None:
    gw, runner, _db, run_id = accepted_run(tmp_path, repo, vault, [submit(MEMORY)])
    assert gw.tools[-1] == ["submit"]
    assert runner._proposals(run_id)[0]["actor"] == "autopilot"


def test_prose_instead_of_submit_falls_back_to_studio_facts(
    tmp_path: Path, repo: Path, vault: Vault
) -> None:
    prose = [assistant(content="Nejde mi to, tady je text bez submit.") for _ in range(3)]
    _gw, runner, db, run_id = accepted_run(tmp_path, repo, vault, prose)
    step, proposals = runner._proposals(run_id)
    assert step["actor"] == "studio"
    assert [p["kind"] for p in proposals] == ["append", "new"]
    update = proposals[0]["content"]
    assert "AI Bridge Studio (studio)" in update and "calc/stats.py" in update
    assert "python -m unittest discover" in update  # the passed acceptance command
    assert "jan@example.com" not in update
    assert any("Studio ho sestavilo" in e["message"] for e in db.events(run_id))


def test_memory_retry_only_for_accepted_project_runs(
    tmp_path: Path, repo: Path, vault: Vault
) -> None:
    gw, runner, db, run_id = accepted_run(tmp_path, repo, vault, [submit(MEMORY)])
    gw.script.append(submit({**MEMORY, "project_update": "- znovu navrženo"}))
    runner.memory_retry(run_id)
    for _ in range(100):
        step = db.last_step(run_id, "memory")
        if step and "znovu navrženo" in str(step["output_json"]):
            break
        time.sleep(0.05)
    assert "znovu navrženo" in runner._proposals(run_id)[1][0]["content"]
    other = start(db, TaskSpec(goal="Jiný úkol", repo_path=str(repo)))
    with pytest.raises(ValueError):
        runner.memory_retry(other)
