"""Direct providers: the id prefix decides the path, keys stay out of everything Studio keeps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from studio.config import ModelSpec
from studio.db import Database
from studio.gateway import (
    DirectGateway,
    OmniRouteGateway,
    ProviderError,
    ProviderUnreachable,
    RoutedGateway,
)
from studio.providers import PRESETS, ProviderSet, load_providers, local_specs
from studio.routing import ModelRouter

KEY = "gsk_" + "x" * 40


def env(values: dict[str, str]) -> Any:
    return lambda name: values.get(name)


class Recorder:
    def __init__(self, models: dict[str, list[str]] | None = None) -> None:
        self.posts: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.models = models or {}

    def post(self, url: str, json: dict[str, Any], headers: dict[str, str], timeout: float):
        self.posts.append((url, json, headers))
        body = {
            "model": json["model"],
            "choices": [
                {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    def get(self, url: str, headers: dict[str, str] | None = None, timeout: float = 5):
        for base, ids in self.models.items():
            if url.startswith(base):
                data = {"data": [{"id": i} for i in ids]}
                return httpx.Response(200, json=data, request=httpx.Request("GET", url))
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    r = Recorder({"https://api.groq.com": ["openai/gpt-oss-120b"]})
    monkeypatch.setattr(httpx, "post", r.post)
    monkeypatch.setattr(httpx, "get", r.get)
    return r


def providers(values: dict[str, str], enabled: set[str] | None = None) -> ProviderSet:
    return ProviderSet({p.name: p for p in PRESETS}, enabled or set(), env(values))


def test_groq_with_a_key_goes_direct(rec: Recorder) -> None:
    gw = DirectGateway(providers({"GROQ_API_KEY": KEY}))
    result = gw.chat("groq/openai/gpt-oss-120b", [{"role": "user", "content": "hi"}])
    url, body, headers = rec.posts[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert body["model"] == "openai/gpt-oss-120b"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert result.actual_model == "groq/openai/gpt-oss-120b" and result.provider == "groq"
    assert "groq/openai/gpt-oss-120b" in (gw.models() or set())


def test_provider_without_key_or_switch_is_not_used(rec: Recorder) -> None:
    gw = DirectGateway(providers({}))
    assert not gw.serves("groq/openai/gpt-oss-120b")
    assert not gw.serves("ovh/Mistral-Small-3.2-24B-Instruct-2506")  # keyless, but off
    assert DirectGateway(providers({}, {"ovh"})).serves("ovh/x")
    with pytest.raises(ProviderError):
        gw.chat("groq/openai/gpt-oss-120b", [])


def test_cloudflare_needs_the_account_id_in_the_url(rec: Recorder) -> None:
    assert not DirectGateway(providers({"CLOUDFLARE_API_TOKEN": "t"})).serves("cf/@cf/a/b")
    gw = DirectGateway(providers({"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "acc1"}))
    gw.chat("cf/@cf/moonshotai/kimi-k2.6", [])
    assert (
        rec.posts[0][0]
        == "https://api.cloudflare.com/client/v4/accounts/acc1/ai/v1/chat/completions"
    )
    assert rec.posts[0][1]["model"] == "@cf/moonshotai/kimi-k2.6"
    assert "cf/@cf/moonshotai/kimi-k2.6" in (gw.models() or set())


def test_routed_gateway_splits_by_prefix_without_fallback(
    rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    omni = OmniRouteGateway("http://127.0.0.1:1")
    monkeypatch.setattr(omni, "models", lambda fresh=False: {"groq/openai/gpt-oss-120b", "cf/x"})
    gw = RoutedGateway(omni, DirectGateway(providers({"GROQ_API_KEY": KEY})))
    gw.chat("groq/openai/gpt-oss-120b", [])
    assert rec.posts[-1][0].startswith("https://api.groq.com")
    gw.chat("cf/x", [])
    assert rec.posts[-1][0] == "http://127.0.0.1:1/v1/chat/completions"
    assert gw.models() == {"groq/openai/gpt-oss-120b", "cf/x"}


def test_local_ollama_is_on_by_default_and_joins_the_catalog(rec: Recorder, tmp_path: Path) -> None:
    rec.models["http://127.0.0.1:11434"] = ["qwen3:8b"]
    ps = providers({})
    gw = DirectGateway(ps)
    exposed = gw.models() or set()
    assert exposed == {"ollama/qwen3:8b"}
    specs = local_specs(ps, exposed)
    assert [s.id for s in specs] == ["ollama/qwen3:8b"] and specs[0].privacy == "no_train"
    base = (
        ModelSpec("groq/strong", frozenset({"coding", "tools", "lead"}), 5, privacy="no_train"),
    )
    router = ModelRouter(Database(tmp_path / "s.sqlite"), base, exposed=lambda: exposed)
    router.extend(specs)
    router.extend(specs)  # idempotent
    assert router.candidates("lead") == ["ollama/qwen3:8b"]


def test_unreachable_direct_provider_cools_like_a_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(url: str, **_: Any) -> httpx.Response:
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", refuse)
    with pytest.raises(ProviderUnreachable) as err:
        DirectGateway(providers({})).chat("ollama/qwen3:8b", [])
    assert err.value.kind == "provider_error"


def test_providers_toml_adds_providers_but_never_keys(tmp_path: Path) -> None:
    (tmp_path / "providers.toml").write_text(
        '[providers.lab]\nbase_url = "http://10.0.0.5:8000/v1"\nmodels = ["coder"]\n'
        'privacy = "no_train"\nenabled = true\n',
        encoding="utf-8",
    )
    ps = load_providers(tmp_path)
    assert ps.get("lab/coder") is not None and ps.providers["lab"].keyless
    (tmp_path / "providers.toml").write_text(
        '[providers.lab]\nbase_url = "http://x/v1"\napi_key = "sk-123"\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="api_key_env"):
        load_providers(tmp_path)


def test_key_values_never_reach_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    from fastapi.testclient import TestClient

    from studio.api import create_app
    from studio.config import Settings

    monkeypatch.setenv("GROQ_API_KEY", KEY)
    saved: dict[str, str | None] = {}
    monkeypatch.setattr("studio.api.save_key", lambda name, value: saved.update({name: value}))
    app = create_app(Settings(data_dir=tmp_path), start_runner=False)
    with TestClient(app, headers={"X-Studio": "1"}) as c:
        listed = c.get("/api/providers").json()
        groq = next(p for p in listed if p["name"] == "groq")
        assert groq["active"] and groq["has_key"] and KEY not in str(listed)
        ovh = c.put("/api/providers/ovh", json={"enabled": True}).json()
        assert next(p for p in ovh if p["name"] == "ovh")["active"]
        c.put("/api/providers/cerebras", json={"key": "csk-abc"})
        assert saved == {"CEREBRAS_API_KEY": "csk-abc"}
        assert c.put("/api/providers/ollama", json={"key": "x"}).status_code == 422
        assert KEY not in str(c.get("/api/health").json())
    assert KEY.encode() not in (tmp_path / "studio.sqlite").read_bytes()
