"""Autopilot (ApiLead): tool loop, submit + repair, router fallback, budgets, vault, scope."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conftest import FakeGateway, assistant, make_spec, tool_call
from studio.agents.api_lead import ApiLead, schema_errors
from studio.agents.leads import AgentEvent, CallContext, LeadError
from studio.agents.prompts import IMPLEMENT_SCHEMA, PLAN_SCHEMA, REVIEW_SCHEMA
from studio.config import ModelSpec
from studio.db import Database
from studio.gateway import GatewayUnavailable, ProviderError
from studio.routing import ModelRouter
from studio.vault import Vault

LEADS = (
    ModelSpec("test/lead-strong", frozenset({"tools", "lead"}), 4, privacy="no_train"),
    ModelSpec("test/lead-other", frozenset({"tools", "lead"}), 2, privacy="no_train"),
    ModelSpec("test/coder", frozenset({"coding", "tools"}), 3, privacy="no_train"),
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
REVIEW = {"verdict": "accept", "summary": "ok", "findings": [], "criteria": []}


@pytest.fixture
def env(tmp_path: Path, repo: Path) -> dict[str, Any]:
    db = Database(tmp_path / "data" / "studio.sqlite")
    run_id = db.create_task_and_run(make_spec(repo).model_dump_json())
    step = db.start_step(run_id, "plan", 1, "autopilot")
    return {"db": db, "run_id": run_id, "step": step, "repo": repo}


def make_lead(env: dict[str, Any], gateway: FakeGateway, vault: Vault | None = None) -> ApiLead:
    db = env["db"]
    return ApiLead(db, gateway, ModelRouter(db, LEADS), lambda: vault)


def ctx(env: dict[str, Any], **kw: Any) -> tuple[CallContext, list[AgentEvent]]:
    events: list[AgentEvent] = []
    base: dict[str, Any] = {
        "timeout_s": 60,
        "should_stop": lambda: False,
        "on_event": events.append,
        "run_id": env["run_id"],
        "step_id": env["step"],
        "role": "plan",
        "scope": ("calc/*.py", "tests/*.py"),
    }
    base.update(kw)
    return CallContext(**base), events


def test_plan_reads_files_then_submits_and_records_api_calls(env: dict[str, Any]) -> None:
    gw = FakeGateway(
        [
            assistant(tool_call("grep", {"pattern": "def mean"})),
            assistant(tool_call("read_file", {"path": "calc/stats.py"}, 1)),
            assistant(tool_call("submit", PLAN, 2)),
        ]
    )
    c, events = ctx(env, complexity="hard")
    assert make_lead(env, gw).ask("plan it", PLAN_SCHEMA, env["repo"], c) == PLAN
    assert gw.calls == ["test/lead-strong"] * 3  # hard: strongest lead model first
    calls = env["db"].model_calls(env["run_id"])
    assert len(calls) == 3
    assert {(r["billing"], r["role"], r["outcome"]) for r in calls} == {("api", "plan", "ok")}
    assert c.usage.tokens_in == 300 and c.usage.actual_model == "lead-strong-actual"
    assert any(e.kind == "tool" and e.text.startswith("read_file calc/stats.py") for e in events)


def test_bad_submit_is_repaired_and_json_text_is_accepted(env: dict[str, Any]) -> None:
    broken = dict(PLAN, complexity="enormous")
    del broken["risks"]
    gw = FakeGateway([assistant(tool_call("submit", broken)), assistant(tool_call("submit", PLAN))])
    c, _ = ctx(env)
    assert make_lead(env, gw).ask("plan", PLAN_SCHEMA, env["repo"], c) == PLAN

    gw = FakeGateway([assistant(content="Here it is:\n```json\n" + _json(REVIEW) + "\n```")])
    c, _ = ctx(env)
    assert make_lead(env, gw).ask("review", REVIEW_SCHEMA, env["repo"], c) == REVIEW


def test_repairs_are_bounded(env: dict[str, Any]) -> None:
    gw = FakeGateway([assistant(tool_call("submit", {"verdict": "maybe"}, i)) for i in range(3)])
    c, _ = ctx(env)
    with pytest.raises(LeadError) as err:
        make_lead(env, gw).ask("review", REVIEW_SCHEMA, env["repo"], c)
    assert err.value.kind == "bad_output"


def test_provider_error_moves_to_the_next_lead_model(env: dict[str, Any]) -> None:
    gw = FakeGateway(
        [assistant(tool_call("submit", PLAN))],
        per_model={"test/lead-strong": ProviderError("upstream 503", status=503)},
    )
    c, events = ctx(env, complexity="hard")
    assert make_lead(env, gw).ask("plan", PLAN_SCHEMA, env["repo"], c) == PLAN
    assert gw.calls == ["test/lead-strong", "test/lead-other"]
    assert "test/lead-strong" in env["db"].cooldowns()
    outcomes = [r["outcome"] for r in env["db"].model_calls(env["run_id"])]
    assert outcomes == ["provider_error", "ok"]
    assert any(e.kind == "error" for e in events)


def test_review_excludes_implementer_and_never_falls_back_to_non_lead(
    env: dict[str, Any],
) -> None:
    gw = FakeGateway([assistant(tool_call("submit", REVIEW))])
    c, _ = ctx(env, role="review", exclude_models=frozenset({"test/lead-strong"}))
    make_lead(env, gw).ask("review", REVIEW_SCHEMA, env["repo"], c)
    assert gw.calls == ["test/lead-other"]

    gw = FakeGateway([])
    c, _ = ctx(env, exclude_models=frozenset({"test/lead-strong", "test/lead-other"}))
    with pytest.raises(LeadError) as err:
        make_lead(env, gw).ask("review", REVIEW_SCHEMA, env["repo"], c)
    assert err.value.kind == "no_model" and gw.calls == []  # test/coder is not a lead


def test_gateway_down_budget_and_cancel(env: dict[str, Any]) -> None:
    gw = FakeGateway([GatewayUnavailable("connection refused")])
    c, _ = ctx(env)
    with pytest.raises(LeadError) as err:
        make_lead(env, gw).ask("plan", PLAN_SCHEMA, env["repo"], c)
    assert err.value.kind == "gateway_down"
    assert env["db"].cooldowns() == {}  # OmniRoute down is not the model's fault

    c, _ = ctx(env, max_tokens=1_000)
    env["db"].record_model_call(
        run_id=env["run_id"],
        role="worker",
        requested_model="x",
        tokens_in=5_000,
        outcome="ok",
        billing="api",
    )
    with pytest.raises(LeadError) as err:
        make_lead(env, FakeGateway([])).ask("plan", PLAN_SCHEMA, env["repo"], c)
    assert err.value.kind == "budget"

    c, _ = ctx(env, should_stop=lambda: True)
    with pytest.raises(LeadError) as err:
        make_lead(env, FakeGateway([])).ask("plan", PLAN_SCHEMA, env["repo"], c)
    assert err.value.kind == "cancelled"


def test_turn_cap_and_nudges(env: dict[str, Any]) -> None:
    gw = FakeGateway([assistant(content="thinking...") for _ in range(3)])
    c, _ = ctx(env)
    with pytest.raises(LeadError) as err:
        make_lead(env, gw).ask("plan", PLAN_SCHEMA, env["repo"], c)
    assert err.value.kind == "bad_output"

    gw = FakeGateway([assistant(tool_call("list_files", {"path": "."}, i)) for i in range(20)])
    lead = make_lead(env, gw)
    lead.max_turns = 4
    with pytest.raises(LeadError, match="4 turns"):
        lead.ask("plan", PLAN_SCHEMA, env["repo"], ctx(env)[0])


def test_write_mode_edits_only_inside_scope(env: dict[str, Any]) -> None:
    report = {"summary": "done", "files_changed": ["calc/stats.py"], "concerns": []}
    gw = FakeGateway(
        [
            assistant(
                tool_call("write_file", {"path": "calc/new.py", "content": "X = 1\n"}),
                tool_call("write_file", {"path": "README.md", "content": "hacked"}, 1),
            ),
            assistant(tool_call("submit", report, 2)),
        ]
    )
    c, events = ctx(env, role="implement")
    assert make_lead(env, gw).ask("do", IMPLEMENT_SCHEMA, env["repo"], c, "write") == report
    assert (env["repo"] / "calc" / "new.py").read_text() == "X = 1\n"
    assert "hacked" not in (env["repo"] / "README.md").read_text()
    assert any(e.kind == "tool_result" and e.text.startswith("denied") for e in events)


def test_read_mode_has_no_write_tools(env: dict[str, Any]) -> None:
    seen: list[list[str]] = []

    class Spy(FakeGateway):
        def chat(self, model, messages, tools=None, timeout_s=None):  # type: ignore[no-untyped-def]
            seen.append([t["function"]["name"] for t in tools or []])
            return super().chat(model, messages, tools, timeout_s)

    gw = Spy(
        [
            assistant(tool_call("write_file", {"path": "calc/x.py", "content": "1"})),
            assistant(tool_call("submit", PLAN, 1)),
        ]
    )
    make_lead(env, gw).ask("plan", PLAN_SCHEMA, env["repo"], ctx(env)[0])
    assert "write_file" not in seen[0] and "vault_read" not in seen[0]
    assert not (env["repo"] / "calc" / "x.py").exists()


def test_vault_tools_respect_policy_and_log_reads(env: dict[str, Any], tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "02 Projekty").mkdir(parents=True)
    (root / "01 Profil").mkdir()
    (root / "02 Projekty/Calc.md").write_text("# Calc\nNext: median", encoding="utf-8")
    (root / "01 Profil/Ja.md").write_text("osobni", encoding="utf-8")
    vault = Vault(root)
    gw = FakeGateway(
        [
            assistant(tool_call("vault_search", {"query": "median"})),
            assistant(
                tool_call("vault_read", {"path": "02 Projekty/Calc.md"}, 1),
                tool_call("vault_read", {"path": "01 Profil/Ja.md"}, 2),
            ),
            assistant(tool_call("submit", PLAN, 3)),
        ]
    )
    c, events = ctx(env)
    make_lead(env, gw, vault).ask("plan", PLAN_SCHEMA, env["repo"], c)
    assert vault.reads == ["02 Projekty/Calc.md"]
    logged = [e.data for e in events if e.data]
    assert logged == [{"vault": "02 Projekty/Calc.md"}]
    results = [e.text for e in events if e.kind == "tool_result"]
    assert any(r.startswith("denied") for r in results)


def test_schema_errors() -> None:
    assert schema_errors(PLAN, PLAN_SCHEMA) == []
    bad = dict(REVIEW, findings=[{"severity": "huge", "file": 1}])
    errors = schema_errors(bad, REVIEW_SCHEMA)
    assert any("severity" in e for e in errors) and any("file" in e for e in errors)
    assert any("problem is missing" in e for e in errors)


def _json(value: Any) -> str:
    import json

    return json.dumps(value)
