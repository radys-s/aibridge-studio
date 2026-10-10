"""Settings "Otestovat": one tiny tool call to one catalog model, never past the policy."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from studio.api import _providers_today, create_app
from studio.config import ModelSpec, Settings
from studio.gateway import ChatResult, ProviderError

LEAD = frozenset({"coding", "tools", "lead"})
PRIVATE = ModelSpec("cerebras/gpt-oss-120b", LEAD, 6, privacy="no_train")
TRAINS = ModelSpec("nvidia/kimi", LEAD, 6, privacy="may_train")
MODELS = (PRIVATE, TRAINS)
HEADERS = {"X-Studio": "1"}


class ProbeGateway:
    def __init__(self, answer: dict[str, Any] | Exception) -> None:
        self.answer = answer
        self.sent: list[str] = []
        self.fresh: list[bool] = []

    def health(self) -> dict[str, Any]:
        return {"reachable": True, "status": 200}

    def models(self, fresh: bool = False) -> set[str] | None:
        self.fresh.append(fresh)
        return {PRIVATE.id}

    def chat(self, model, messages, tools=None, timeout_s=None):  # type: ignore[no-untyped-def]
        self.sent.append(model)
        if isinstance(self.answer, Exception):
            raise self.answer
        return ChatResult(self.answer, "tool_calls", model, model, "cerebras", latency_ms=120)


def client(tmp_path: Path, gw: ProbeGateway) -> TestClient:
    settings = Settings(data_dir=tmp_path / "d", worker_models=MODELS)
    return TestClient(create_app(settings, gateway=gw, leads={}, start_runner=False))


PING_CALL = {
    "role": "assistant",
    "tool_calls": [{"id": "1", "function": {"name": "ping", "arguments": '{"ok": true}'}}],
}


def test_probe_reports_tool_calling_and_clears_a_stale_cooldown(tmp_path: Path) -> None:
    gw = ProbeGateway(PING_CALL)
    with client(tmp_path, gw) as c:
        c.app.state.db.set_cooldown(PRIVATE.id, time.time() + 600, "401 before the key")  # type: ignore[attr-defined]
        res = c.post("/api/models/probe", json={"model": PRIVATE.id}, headers=HEADERS).json()
        assert res["ok"] is True and res["tools"] is True and res["latency_ms"] == 120
        assert PRIVATE.id not in c.app.state.db.cooldowns()  # type: ignore[attr-defined]
    assert gw.fresh == [True]  # asked OmniRoute again instead of the cached list


def test_probe_without_tool_call_is_reported(tmp_path: Path) -> None:
    gw = ProbeGateway({"role": "assistant", "content": "OK"})
    with client(tmp_path, gw) as c:
        res = c.post("/api/models/probe", json={"model": PRIVATE.id}, headers=HEADERS).json()
    assert res["ok"] is True and res["tools"] is False


def test_probe_reports_the_provider_error(tmp_path: Path) -> None:
    gw = ProbeGateway(ProviderError("401 invalid api key", status=401))
    with client(tmp_path, gw) as c:
        res = c.post("/api/models/probe", json={"model": PRIVATE.id}, headers=HEADERS).json()
    assert res["ok"] is False and res["status"] == 401 and "invalid" in res["detail"]


def test_probe_never_sends_to_a_model_below_the_privacy_floor(tmp_path: Path) -> None:
    gw = ProbeGateway(PING_CALL)
    with client(tmp_path, gw) as c:
        res = c.post("/api/models/probe", json={"model": TRAINS.id}, headers=HEADERS).json()
        assert res == {
            "model": TRAINS.id,
            "blocked": "privacy",
            "ok": False,
            "kind": "privacy",
            "detail": "not sent: blocked by policy",
        }
        unknown = c.post("/api/models/probe", json={"model": "x/y"}, headers=HEADERS)
        assert unknown.status_code == 404
    assert gw.sent == []


def test_groq_daily_cap_counts_per_model() -> None:
    rows = _providers_today({"groq/openai/gpt-oss-120b": 300, "groq/openai/gpt-oss-20b": 100})
    assert rows == [{"provider": "groq", "calls": 400, "used": 300, "cap": 1000}]


def test_injected_gateway_is_never_launched(tmp_path: Path) -> None:
    with client(tmp_path, ProbeGateway(PING_CALL)) as c:
        assert c.get("/api/health").json()["gateway"]["launchable"] is False
        assert c.post("/api/omniroute/start", headers=HEADERS).status_code == 409
        assert c.post("/api/omniroute/start").status_code == 403  # CSRF guard
