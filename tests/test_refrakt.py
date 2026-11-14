"""Refrakt as a second gateway (ADR 0007): ids, privacy, routing between gateways."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from studio.config import DEFAULT_MODELS, ModelSpec, Settings, upstream
from studio.db import Database
from studio.gateway import (
    ChatResult,
    GatewayUnavailable,
    OmniRouteGateway,
    ProviderError,
    RefraktGateway,
    RoutedGateway,
)
from studio.routing import ModelRouter

LEAD = frozenset({"coding", "tools", "lead"})


def test_refrakt_models_keep_the_upstream_provider_and_its_privacy() -> None:
    privacy = {m.id: m.privacy for m in DEFAULT_MODELS}
    assert privacy["refrakt/groq/openai/gpt-oss-120b"] == "no_train"
    assert privacy["refrakt/cerebras/gpt-oss-120b"] == "no_train"
    # LiteLLM's names map to OmniRoute's aliases, so NVIDIA stays may_train.
    assert privacy["refrakt/nvidia_nim/moonshotai/kimi-k2.6"] == "may_train"
    assert privacy["refrakt/gemini/gemini-3.5-flash"] == "may_train"
    assert privacy["refrakt/cohere_chat/command-a-03-2025"] == "unknown"
    assert upstream("refrakt/groq/openai/gpt-oss-120b") == "groq"
    assert upstream("refrakt/huggingface/deepseek-ai/DeepSeek-V3.2") == "hf"
    assert upstream("groq/openai/gpt-oss-120b") == "groq"


def test_refrakt_is_off_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFRAKT_GATEWAY_TOKEN", raising=False)
    assert Settings.from_env().refrakt_token is None
    monkeypatch.setenv("REFRAKT_GATEWAY_TOKEN", "t")
    monkeypatch.setenv("STUDIO_REFRAKT_URL", "http://127.0.0.1:8123")
    s = Settings.from_env()
    assert (s.refrakt_token, s.refrakt_url) == ("t", "http://127.0.0.1:8123")


class _Resp:
    def __init__(self, body: Any) -> None:
        self.body = body
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.text = ""

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self.body


def test_refrakt_gateway_prefixes_ids_and_hides_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_get(url: str, **kw: Any) -> _Resp:
        seen["url"], seen["headers"] = url, kw["headers"]
        return _Resp({"data": [{"id": "auto"}, {"id": "groq/openai/gpt-oss-120b"}]})

    monkeypatch.setattr(httpx, "get", fake_get)
    gw = RefraktGateway("http://refrakt", "secret")
    assert gw.models() == {"refrakt/groq/openai/gpt-oss-120b"}
    assert seen["url"] == "http://refrakt/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer secret"


def test_refrakt_gateway_sends_the_bare_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}
    answer = {
        "model": "groq/openai/gpt-oss-120b",
        "provider": "groq",
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    }

    def fake_post(url: str, **kw: Any) -> _Resp:
        sent["url"], sent["json"] = url, kw["json"]
        return _Resp(answer)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = RefraktGateway("http://refrakt", "secret").chat(
        "refrakt/groq/openai/gpt-oss-120b", [{"role": "user", "content": "hi"}]
    )
    assert sent["json"]["model"] == "groq/openai/gpt-oss-120b"
    assert result.requested_model == "refrakt/groq/openai/gpt-oss-120b"
    assert (result.provider, result.tokens_in, result.tokens_out) == ("groq", 5, 1)


class _Stub:
    def __init__(self, ids: set[str] | None, fail: Exception | None = None) -> None:
        self.ids, self.fail, self.calls = ids, fail, []  # type: ignore[var-annotated]

    def chat(
        self, model: str, messages: Any, tools: Any = None, timeout_s: Any = None
    ) -> ChatResult:
        self.calls.append(model)
        if self.fail:
            raise self.fail
        return ChatResult({"role": "assistant", "content": "ok"}, "stop", model, model, None)

    def health(self) -> dict[str, Any]:
        return {"reachable": self.ids is not None}

    def models(self, fresh: bool = False) -> set[str] | None:
        return self.ids


def test_routed_gateway_sends_each_id_to_its_gateway() -> None:
    omni, refrakt = _Stub({"groq/x"}), _Stub({"refrakt/groq/x"})
    gw = RoutedGateway(omni, refrakt)
    gw.chat("groq/x", [])
    gw.chat("refrakt/groq/x", [])
    assert (omni.calls, refrakt.calls) == (["groq/x"], ["refrakt/groq/x"])
    assert gw.models() == {"groq/x", "refrakt/groq/x"}
    assert gw.health() == {"reachable": True, "refrakt": {"reachable": True}}


def test_a_down_refrakt_fails_the_model_not_the_run() -> None:
    gw = RoutedGateway(_Stub({"groq/x"}), _Stub(set(), GatewayUnavailable("down")))
    with pytest.raises(ProviderError) as exc:
        gw.chat("refrakt/groq/x", [])
    assert exc.value.status == 503
    down_omniroute = RoutedGateway(_Stub(None, GatewayUnavailable("down")), _Stub(set()))
    with pytest.raises(GatewayUnavailable):
        down_omniroute.chat("groq/x", [])


def test_without_omniroute_the_router_picks_refrakt_models(tmp_path: Path) -> None:
    models = (
        ModelSpec("groq/openai/gpt-oss-120b", LEAD, 6, privacy="no_train"),
        ModelSpec("refrakt/groq/openai/gpt-oss-120b", LEAD, 6, privacy="no_train"),
    )
    gw = RoutedGateway(_Stub(None), _Stub({"refrakt/groq/openai/gpt-oss-120b"}))
    router = ModelRouter(Database(tmp_path / "s.sqlite"), models, exposed=gw.models)
    assert router.candidates() == ["refrakt/groq/openai/gpt-oss-120b"]


def test_omniroute_gateway_keeps_its_name_in_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", refuse)
    with pytest.raises(GatewayUnavailable, match="OmniRoute not reachable"):
        OmniRouteGateway("http://omniroute").chat("groq/x", [])
    with pytest.raises(GatewayUnavailable, match="Refrakt not reachable"):
        RefraktGateway("http://refrakt", "t").chat("refrakt/groq/x", [])
