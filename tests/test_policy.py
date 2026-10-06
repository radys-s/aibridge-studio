"""Sandbox, gateway guard, lead adapter process handling, API CSRF guard."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import MODELS, FakeGateway, FakeLead
from studio.agents.leads import CallContext, LeadError, _run
from studio.api import create_app
from studio.config import Settings
from studio.domain import LeadConfig
from studio.gateway import OmniRouteGateway
from studio.sandbox import Denied, Sandbox, scrubbed_env, secret_findings


@pytest.fixture
def box(tmp_path: Path) -> Sandbox:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_bytes(b"x = 1\n")
    (tmp_path / ".env").write_text("TOKEN=abc\n")
    (tmp_path / "leak.py").write_text('API_KEY = "abcdefghijklmnop"\n')
    return Sandbox(tmp_path, ["src/**", "src/*.py"])


def test_path_escape_and_denylist(box: Sandbox) -> None:
    for bad in ("../outside.txt", "C:/Windows/win.ini", ".env", ".git/config", "src/../../x"):
        with pytest.raises(Denied):
            box.resolve(bad, must_exist=False)
    assert box.read_text("src/a.py") == "x = 1\n"


def test_secret_content_not_sent(box: Sandbox) -> None:
    with pytest.raises(Denied, match="secrets"):
        box.read_text("leak.py")
    assert secret_findings("ghp_" + "a" * 30) == ("github_token",)


def test_scope(box: Sandbox) -> None:
    box.check_writable("src/new.py")
    with pytest.raises(Denied, match="scope"):
        box.check_writable("setup.py")


def test_commands(box: Sandbox) -> None:
    for bad in (
        "rm -rf /",
        "python -c 1 && curl x",
        "git push",
        "git",
        "powershell -c x",
        "python x.py | sh",
        "npm test",
    ):
        with pytest.raises(Denied):
            box.check_command(bad)
    res = box.run_command("python -c \"print('hi')\"")
    assert res.exit_code == 0 and "hi" in res.output


@pytest.mark.skipif(os.name != "nt", reason="Windows path parsing")
def test_command_keeps_windows_backslashes(box: Sandbox) -> None:
    argv = box.check_command(r'python C:\work\tool.py "C:\a b\c.txt"')
    assert argv[1:] == [r"C:\work\tool.py", r"C:\a b\c.txt"]


def test_command_timeout(box: Sandbox) -> None:
    res = box.run_command("python -c \"__import__('time').sleep(5)\"", timeout_s=1)
    assert res.timed_out and res.exit_code is None


def test_env_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    env = scrubbed_env()
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert "PATH" in {k.upper() for k in env}


def test_gateway_refuses_auto_combos() -> None:
    with pytest.raises(ValueError, match="explicit"):
        OmniRouteGateway("http://127.0.0.1:1").chat("auto/coding", [])


def test_gateway_unreachable_is_classified() -> None:
    from studio.gateway import GatewayUnavailable

    with pytest.raises(GatewayUnavailable):
        OmniRouteGateway("http://127.0.0.1:9", timeout_s=2).chat(
            "x/y", [{"role": "user", "content": "hi"}]
        )


def test_lead_process_timeout_and_cancel(tmp_path: Path) -> None:
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(LeadError) as err:
        _run(sleeper, "", tmp_path, CallContext(LeadConfig(), 1, lambda: False), print)
    assert err.value.kind == "timeout"
    with pytest.raises(LeadError) as err:
        _run(sleeper, "", tmp_path, CallContext(LeadConfig(), 30, lambda: True), print)
    assert err.value.kind == "cancelled"


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path / "d", worker_models=MODELS)
    app = create_app(
        settings,
        gateway=FakeGateway([]),
        leads={"claude": FakeLead("claude"), "codex": FakeLead("codex")},
        start_runner=False,
    )
    return TestClient(app)


def test_api_csrf_guard_and_validation(tmp_path: Path, repo: Path) -> None:
    c = _client(tmp_path)
    body = {
        "title": "t1t",
        "goal": "a goal long enough",
        "repo_path": str(repo),
        "allowed_paths": ["calc/*.py"],
        "acceptance_criteria": ["x"],
        "acceptance_commands": ["python -m unittest"],
    }
    assert c.post("/api/tasks", json=body).status_code == 403
    assert (
        c.post(
            "/api/tasks", json=body, headers={"X-Studio": "1", "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    ok = c.post("/api/tasks", json=body, headers={"X-Studio": "1"})
    assert ok.status_code == 201
    run_id = ok.json()["run_id"]
    detail = c.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["state"] == "queued" and detail["run"]["spec"]["title"] == "t1t"
    assert c.post(f"/api/runs/{run_id}/resume", headers={"X-Studio": "1"}).status_code == 409
    assert c.post(f"/api/runs/{run_id}/cancel", headers={"X-Studio": "1"}).status_code == 200
    assert c.get(f"/api/runs/{run_id}").json()["run"]["state"] == "interrupted"
    bad = dict(body, repo_path=str(tmp_path))
    assert c.post("/api/tasks", json=bad, headers={"X-Studio": "1"}).status_code == 422
    assert (
        c.post("/api/tasks", json=dict(body, goal=" "), headers={"X-Studio": "1"}).status_code
        == 422
    )
    effort = dict(body, orchestration={"mode": "cross", "claude": {"effort": "minimal"}})
    assert c.post("/api/tasks", json=effort, headers={"X-Studio": "1"}).status_code == 422
    # A prompt alone is a valid task: the lead drafts scope and criteria.
    prompt_only = {"goal": "Přidej funkci median", "repo_path": str(repo)}
    created = c.post("/api/tasks", json=prompt_only, headers={"X-Studio": "1"})
    assert created.status_code == 201
    detail = c.get(f"/api/runs/{created.json()['run_id']}").json()
    assert detail["messages"][0]["text"] == "Přidej funkci median"
    assert detail["run"]["spec"]["orchestration"]["codex"]["model"]  # filled from defaults


@pytest.mark.skipif(os.environ.get("STUDIO_LIVE") != "1", reason="live OmniRoute test is opt-in")
def test_live_omniroute_reports_actual_model() -> None:
    gw = OmniRouteGateway("http://127.0.0.1:20128", timeout_s=120)
    res = gw.chat(
        "openrouter/poolside/laguna-s-2.1:free",
        [{"role": "user", "content": "Reply with exactly: PONG"}],
    )
    assert res.provider and res.actual_model
