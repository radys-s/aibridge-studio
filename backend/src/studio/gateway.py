"""OmniRoute adapter. One request, one explicit model, no retries, no substitution.

OmniRoute is an external dependency (MIT, upstream github.com/diegosouzapw/OmniRoute; local
install `npm i -g omniroute`). Studio talks to its OpenAI-compatible endpoint only.
Model choice and fallback belong to `routing.ModelRouter`, never to this module.
"""

from __future__ import annotations

import contextlib
import math
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from studio.config import REFRAKT_PREFIX
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
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_s: float = 180,
        *,
        name: str = "OmniRoute",
        health_path: str = "/api/monitoring/health",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.health_path = health_path
        self.timeout_s = timeout_s
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._models: tuple[float, set[str] | None] | None = None
        self._known: set[str] | None = None  # last successful probe

    def health(self) -> dict[str, Any]:
        try:
            r = httpx.get(f"{self.base_url}{self.health_path}", timeout=5)
            return {"reachable": True, "status": r.status_code}
        except httpx.HTTPError as exc:
            return {"reachable": False, "error": type(exc).__name__}

    def models(self, fresh: bool = False) -> set[str] | None:
        """Exposed model ids, cached for a minute (connected providers rarely change).

        fresh=True re-asks OmniRoute, e.g. right after Tomáš added a key in its dashboard.
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
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        started = time.monotonic()
        try:
            r = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json=body,
                headers=self._headers,
                timeout=timeout_s or self.timeout_s,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise GatewayUnavailable(f"{self.name} not reachable at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise GatewayTimeout(f"no answer from {model} within the timeout") from exc
        except httpx.HTTPError as exc:
            raise GatewayUnavailable(f"HTTP error talking to {self.name}: {exc}") from exc
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
        return ChatResult(
            message=message,
            finish_reason=choice.get("finish_reason"),
            requested_model=model,
            actual_model=h.get("x-omniroute-model") or data.get("model"),
            provider=h.get("x-omniroute-provider") or data.get("provider"),
            tokens_in=int(usage.get("prompt_tokens") or h.get("x-omniroute-tokens-in") or 0),
            tokens_out=int(usage.get("completion_tokens") or h.get("x-omniroute-tokens-out") or 0),
            cost_usd=_float(h.get("x-omniroute-response-cost")),
            latency_ms=latency,
            raw_headers={k: v for k, v in h.items() if k.startswith("x-omniroute-")},
        )


class RefraktGateway(OmniRouteGateway):
    """Refrakt's OpenAI-compatible /v1 (ADR 0007). Refrakt holds the provider keys.

    Studio's ids carry `refrakt/`; Refrakt itself knows the bare LiteLLM slug. Its `auto`
    model is never offered: Refrakt would pick and fall back on its own, a second owner.
    """

    def __init__(self, base_url: str, token: str, timeout_s: float = 180) -> None:
        super().__init__(base_url, token, timeout_s, name="Refrakt", health_path="/api/health")

    def models(self, fresh: bool = False) -> set[str] | None:
        ids = super().models(fresh)
        return None if ids is None else {REFRAKT_PREFIX + i for i in ids if i != "auto"}

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult:
        result = super().chat(model.removeprefix(REFRAKT_PREFIX), messages, tools, timeout_s)
        result.requested_model = model
        return result


class RoutedGateway:
    """OmniRoute plus Refrakt: each model id goes to the gateway that serves it."""

    def __init__(self, default: ChatGateway, refrakt: ChatGateway) -> None:
        self.default = default
        self.refrakt = refrakt

    def _for(self, model: str) -> ChatGateway:
        return self.refrakt if model.startswith(REFRAKT_PREFIX) else self.default

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult:
        gateway = self._for(model)
        try:
            return gateway.chat(model, messages, tools, timeout_s)
        except GatewayUnavailable as exc:
            if gateway is self.default:
                raise
            # Refrakt is down: that model fails and the router moves on, the run goes on.
            raise ProviderError(str(exc), status=503) from exc

    def health(self) -> dict[str, Any]:
        return {**self.default.health(), "refrakt": self.refrakt.health()}

    def models(self, fresh: bool = False) -> set[str] | None:
        """Union of what each gateway exposes. A gateway that never answered adds nothing,
        so with OmniRoute not installed the router uses Refrakt's models instead of trying
        OmniRoute ones that can only fail."""
        found = [self.default.models(fresh), self.refrakt.models(fresh)]
        if all(ids is None for ids in found):
            return None
        return set().union(*(ids for ids in found if ids is not None))
