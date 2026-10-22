"""Model gateways. One request, one explicit model, no retries, no substitution.

- `OmniRouteGateway`: OmniRoute (MIT, github.com/diegosouzapw/OmniRoute, `npm i -g
  omniroute`), an external process with many providers behind one OpenAI-compatible API.
- `DirectGateway`: OpenAI-compatible providers called directly (`providers.py`).
- `RoutedGateway`: both. A model whose provider is configured directly goes direct, every
  other model goes to OmniRoute. The split is fixed by the id prefix, never a fallback.

Model choice and fallback belong to `routing.ModelRouter`, never to this module.
"""

from __future__ import annotations

import contextlib
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from studio.providers import Provider, ProviderSet
from studio.sandbox import safe_text


class GatewayError(Exception):
    """Base class. `kind` is stable and stored in model_call.outcome."""

    kind = "error"

    def __init__(
        self, message: str, *, status: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after  # seconds, from the provider's Retry-After header


class GatewayUnavailable(GatewayError):
    """OmniRoute itself is not reachable (not running, connection refused)."""

    kind = "gateway_unavailable"


class GatewayTimeout(GatewayError):
    kind = "timeout"


class ProviderError(GatewayError):
    """OmniRoute answered, but the upstream provider failed (429, 403, 5xx, bad output)."""

    kind = "provider_error"


@dataclass
class ChatResult:
    message: dict[str, Any]
    finish_reason: str | None
    requested_model: str
    actual_model: str | None
    provider: str | None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw_headers: dict[str, str] = field(default_factory=dict)


class ChatGateway(Protocol):
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult: ...

    def health(self) -> dict[str, Any]: ...

    def models(self, fresh: bool = False) -> set[str] | None:
        """Model ids OmniRoute exposes now, or None when unknown. fresh skips the cache."""
        ...


def _float(value: str | None) -> float:
    try:
        number = float(value) if value else 0.0
    except ValueError:
        return 0.0
    return number if math.isfinite(number) else 0.0


class OmniRouteGateway:
    def __init__(self, base_url: str, api_key: str | None = None, timeout_s: float = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._models: tuple[float, set[str] | None] | None = None
        self._known: set[str] | None = None  # last successful probe

    def health(self) -> dict[str, Any]:
        try:
            r = httpx.get(f"{self.base_url}/api/monitoring/health", timeout=5)
            return {"reachable": True, "status": r.status_code}
        except httpx.HTTPError as exc:
            return {"reachable": False, "error": type(exc).__name__}

    def models(self, fresh: bool = False) -> set[str] | None:
        """Exposed model ids, cached for a minute (connected providers rarely change).

        fresh=True re-asks OmniRoute, e.g. right after the user added a key in its dashboard.
        """
        now = time.monotonic()
        if fresh:
            self._models = None
        # A failed probe is cached too (shorter), so a down OmniRoute does not add a 5 s
        # timeout to every routing decision or health poll.
        if self._models is not None and now - self._models[0] < (60 if self._models[1] else 15):
            return self._models[1]
        ids: set[str] | None
        try:
            r = httpx.get(f"{self.base_url}/v1/models", headers=self._headers, timeout=5)
            r.raise_for_status()
            ids = {str(m["id"]) for m in r.json()["data"]}
            self._known = ids
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # A busy OmniRoute can miss one probe mid-run (seen live 2026-10-08): keep the
            # last good list so the router does not try providers that were never connected.
            ids = self._known
            # Either way the next probe comes in about 15 s.
            self._models = (now - 45 if ids else now, ids)
            return ids
        self._models = (now, ids)
        return ids

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult:
        if model.startswith("auto/"):
            # auto/* combos make OmniRoute pick and fall back on its own: a second owner.
            raise ValueError("Studio sends explicit model ids only, not auto/* combos")
        return _post_chat(
            f"{self.base_url}/v1/chat/completions",
            self._headers,
            model,
            model,
            messages,
            tools,
            timeout_s or self.timeout_s,
            "OmniRoute",
        )


def _post_chat(
    url: str,
    headers: dict[str, str],
    model: str,
    sent_model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    timeout_s: float,
    via: str,
    unreachable: type[GatewayError] = GatewayUnavailable,
) -> ChatResult:
    body: dict[str, Any] = {"model": sent_model, "messages": messages, "stream": False}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    started = time.monotonic()
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=timeout_s)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise unreachable(f"{via} not reachable") from exc
    except httpx.TimeoutException as exc:
        raise GatewayTimeout(f"no answer from {model} within the timeout") from exc
    except httpx.HTTPError as exc:
        raise unreachable(f"HTTP error talking to {via}: {type(exc).__name__}") from exc
    latency = int((time.monotonic() - started) * 1000)
    if r.status_code >= 400:
        detail = safe_text(r.text)[:400]
        with contextlib.suppress(ValueError, KeyError, TypeError):
            detail = safe_text(str(r.json()["error"]["message"]))[:400]
        retry_after = _float(r.headers.get("retry-after")) or None
        raise ProviderError(
            f"{r.status_code}: {detail}", status=r.status_code, retry_after=retry_after
        )
    try:
        data = r.json()
        choice = data["choices"][0]
        message = choice["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            f"unparseable response from {model}: {safe_text(r.text)[:200]}"
        ) from exc
    usage = data.get("usage") or {}
    h = r.headers
    actual = h.get("x-omniroute-model") or data.get("model")
    provider = h.get("x-omniroute-provider") or data.get("provider")
    if via != "OmniRoute":
        prefix = model.split("/", 1)[0]
        provider = prefix
        if actual and not str(actual).startswith(prefix + "/"):
            actual = f"{prefix}/{actual}"
    return ChatResult(
        message=message,
        finish_reason=choice.get("finish_reason"),
        requested_model=model,
        actual_model=actual,
        provider=provider,
        tokens_in=int(usage.get("prompt_tokens") or h.get("x-omniroute-tokens-in") or 0),
        tokens_out=int(usage.get("completion_tokens") or h.get("x-omniroute-tokens-out") or 0),
        cost_usd=_float(h.get("x-omniroute-response-cost")),
        latency_ms=latency,
        raw_headers={k: v for k, v in h.items() if k.startswith("x-omniroute-")},
    )


class ProviderUnreachable(ProviderError):
    """A direct provider (e.g. local Ollama) did not answer. The router cools the model
    briefly ("not reachable" counts as transient), unlike a down OmniRoute."""


class DirectGateway:
    """OpenAI-compatible providers called directly; the id prefix names the provider."""

    def __init__(self, providers: ProviderSet, timeout_s: float = 180) -> None:
        self.providers = providers
        self.timeout_s = timeout_s
        self._models: dict[str, tuple[float, set[str]]] = {}

    def _headers(self, provider: Provider) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.providers.key(provider)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def serves(self, model: str) -> bool:
        return self.providers.get(model) is not None

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult:
        provider = self.providers.get(model)
        url = provider.url(self.providers.env) if provider else None
        if provider is None or url is None or "/" not in model:
            raise ProviderError(f"no direct provider configured for {model}")
        return _post_chat(
            f"{url}/chat/completions",
            self._headers(provider),
            model,
            model.split("/", 1)[1],
            messages,
            tools,
            timeout_s or self.timeout_s,
            provider.label or provider.name,
            ProviderUnreachable,
        )

    def _provider_models(self, provider: Provider, fresh: bool) -> set[str]:
        """Model ids from the provider's /models: cached a minute, a failure 15 s.
        A fixed model list skips the call, except for a local provider, which counts only
        while it answers."""
        if provider.models and not provider.local:
            return {f"{provider.name}/{m}" for m in provider.models}
        now = time.monotonic()
        cached = self._models.get(provider.name)
        if cached and not fresh and now - cached[0] < (60 if cached[1] else 15):
            return cached[1]
        url = provider.url(self.providers.env)
        if not url:
            return set()
        try:
            wait = 1 if provider.local else 5
            r = httpx.get(f"{url}/models", headers=self._headers(provider), timeout=wait)
            r.raise_for_status()
            ids = {f"{provider.name}/{m['id']}" for m in r.json()["data"]}
            if provider.models:
                ids = {f"{provider.name}/{m}" for m in provider.models}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Keep the last good list for a short outage, but ask again in about 15 s
            # instead of renewing the stale list for another minute.
            ids = cached[1] if cached else set()
            self._models[provider.name] = (now - 45 if ids else now, ids)
            return ids
        self._models[provider.name] = (now, ids)
        return ids

    def models(self, fresh: bool = False) -> set[str] | None:
        active = [self.providers.providers[n] for n in self.providers.active_names()]
        if len(active) <= 1:
            return set().union(*(self._provider_models(p, fresh) for p in active))
        # In parallel: several slow providers must not add up their timeouts.
        with ThreadPoolExecutor(max_workers=min(8, len(active))) as pool:
            found = pool.map(lambda p: self._provider_models(p, fresh), active)
            return set().union(*found)

    def reachable_names(self) -> list[str]:
        """Active providers that can take a call: a local one (Ollama) only while it answers."""
        out = []
        for name in self.providers.active_names():
            provider = self.providers.providers[name]
            if not provider.local or self._provider_models(provider, fresh=False):
                out.append(name)
        return out

    def health(self) -> dict[str, Any]:
        return {"reachable": bool(self.reachable_names())}


class RoutedGateway:
    """OmniRoute for everything except providers configured directly. No fallback between."""

    def __init__(self, omniroute: OmniRouteGateway, direct: DirectGateway) -> None:
        self.omniroute = omniroute
        self.direct = direct

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult:
        if self.direct.serves(model):
            return self.direct.chat(model, messages, tools, timeout_s)
        return self.omniroute.chat(model, messages, tools, timeout_s)

    def models(self, fresh: bool = False) -> set[str] | None:
        direct = self.direct.models(fresh) or set()
        via_omniroute = self.omniroute.models(fresh)
        if via_omniroute is None:
            return direct or None
        # A directly configured provider is never reached through OmniRoute.
        return direct | {m for m in via_omniroute if not self.direct.serves(m)}

    def health(self) -> dict[str, Any]:
        omni = self.omniroute.health()
        direct = self.direct.reachable_names()
        return {
            "reachable": bool(omni.get("reachable")) or bool(direct),
            "omniroute": omni,
            "direct": direct,
        }
