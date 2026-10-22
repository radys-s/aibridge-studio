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


def test_stopped_local_ollama_does_not_count_as_a_working_provider(rec: Recorder) -> None:
    gw = DirectGateway(providers({}))
    assert gw.reachable_names() == [] and gw.health() == {"reachable": False}
    rec.models["http://127.0.0.1:11434"] = ["qwen3:8b"]
    assert gw.models(fresh=True) == {"ollama/qwen3:8b"}
    assert gw.reachable_names() == ["ollama"] and gw.health() == {"reachable": True}


def test_unreachable_direct_provider_cools_like_a_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(url: str, **_: Any) -> httpx.Response:
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", refuse)
    with pytest.raises(ProviderUnreachable) as err:
        DirectGateway(providers({})).chat("ollama/qwen3:8b", [])
    assert err.value.kind == "provider_error"


def toml(tmp_path: Path, text: str) -> Path:
    (tmp_path / "providers.toml").write_text(text, encoding="utf-8")
    return tmp_path


def test_providers_toml_adds_providers_but_never_keys(tmp_path: Path) -> None:
    ps = load_providers(
        toml(
            tmp_path,
            '[providers.lab]\nbase_url = "https://lab.example/v1"\nmodels = ["coder"]\n'
            'privacy = "no_train"\nenabled = true\n'
            '[providers.box]\nbase_url = "http://127.0.0.1:8080/v1"\nenabled = true\n',
        )
    )
    assert ps.get("lab/coder") is not None and ps.providers["lab"].keyless
    assert not ps.providers["lab"].local and ps.providers["box"].local
    for bad, match in (
        ('[providers.lab]\nbase_url = "https://x/v1"\napi_key = "sk-123"\n', "api_key_env"),
        ('[providers.lab]\nbase_url = "http://10.0.0.5/v1"\n', "https"),
        ('[providers.lab]\nbase_url = ""\n', "https"),
        ('[providers.lab]\nbase_url = "https://x/v1"\nprivacy = "great"\n', "privacy"),
        # A shared config must not send a preset's key (or "local" trust) elsewhere.
        ('[providers.groq]\nbase_url = "https://evil.example/v1"\n', "built-in"),
        ('[providers.ollama]\nbase_url = "https://evil.example/v1"\n', "built-in"),
    ):
        with pytest.raises(SystemExit, match=match):
            load_providers(toml(tmp_path, bad))


def test_preset_overrides_keep_url_key_and_keyless(tmp_path: Path) -> None:
    ps = load_providers(toml(tmp_path, '[providers.groq]\nenabled = true\nmodels = ["m1"]\n'))
    groq = ps.providers["groq"]
    assert groq.base_url == "https://api.groq.com/openai/v1" and not groq.keyless
    ps.env = env({})
    assert not ps.active(groq)  # switched on, but still needs its key


def test_failed_model_listing_retries_soon_and_keeps_last_list(
    rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr("studio.gateway.time.monotonic", lambda: clock[0])
    gw = DirectGateway(providers({"GROQ_API_KEY": KEY}))
    assert gw.models() == {"groq/openai/gpt-oss-120b"}
    rec.models.clear()  # provider down from now on
    clock[0] += 61
    assert gw.models() == {"groq/openai/gpt-oss-120b"}  # short outage: last good list
    stamp = gw._models["groq"][0]
    assert clock[0] - stamp >= 45  # asks again in ~15 s, not after another minute
    clock[0] += 16
    gw.models()
    assert gw._models["groq"][0] < clock[0] - 44  # still failing: stays due for retry


def test_enabling_swaps_the_set_instead_of_mutating_it() -> None:
    ps = providers({})
    before = ps.enabled
    assert ps.set_enabled("ovh", True) == ["ovh"]
    assert before == set() and ps.enabled is not before
    assert ps.set_enabled("ovh", False) == []


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


def test_a_custom_provider_cannot_borrow_a_built_in_key(tmp_path: Path) -> None:
    (tmp_path / "providers.toml").write_text(
        '[providers.evil]\nbase_url = "https://evil.example/v1"\napi_key_env = "GROQ_API_KEY"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="belongs to a built-in provider"):
        load_providers(tmp_path)


def test_local_provider_with_a_fixed_list_still_has_to_answer(
    rec: Recorder, tmp_path: Path
) -> None:
    (tmp_path / "providers.toml").write_text(
        '[providers.box]\nbase_url = "http://127.0.0.1:8080/v1"\nmodels = ["m"]\n',
        encoding="utf-8",
    )
    ps = load_providers(tmp_path)
    assert ps.providers["box"].privacy == "unknown"  # loopback is not proof of no training
    ps.set_enabled("box", True)
    gw = DirectGateway(ps)
    assert "box" not in gw.reachable_names()
    rec.models["http://127.0.0.1:8080"] = ["whatever"]
    assert gw.models(fresh=True) >= {"box/m"} and "box" in gw.reachable_names()
