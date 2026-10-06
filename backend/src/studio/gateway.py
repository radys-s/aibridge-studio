"""OmniRoute adapter. One request, one explicit model, no retries, no substitution.

OmniRoute is an external dependency (MIT, upstream github.com/diegosouzapw/OmniRoute; local
install `npm i -g omniroute`). Studio talks to its OpenAI-compatible endpoint only.
Model choice and fallback belong to `routing.ModelRouter`, never to this module.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


class GatewayError(Exception):
    """Base class. `kind` is stable and stored in model_call.outcome."""

    kind = "error"

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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


def _float(value: str | None) -> float:
    try:
        return float(value) if value else 0.0
    except ValueError:
        return 0.0


class OmniRouteGateway:
    def __init__(self, base_url: str, api_key: str | None = None, timeout_s: float = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def health(self) -> dict[str, Any]:
        try:
            r = httpx.get(f"{self.base_url}/api/monitoring/health", timeout=5)
            return {"reachable": True, "status": r.status_code}
        except httpx.HTTPError as exc:
            return {"reachable": False, "error": type(exc).__name__}

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
            raise GatewayUnavailable(f"OmniRoute not reachable at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise GatewayTimeout(f"no answer from {model} within the timeout") from exc
        except httpx.HTTPError as exc:
            raise GatewayUnavailable(f"HTTP error talking to OmniRoute: {exc}") from exc
        latency = int((time.monotonic() - started) * 1000)
        if r.status_code >= 400:
            detail = r.text[:400]
            with contextlib.suppress(ValueError, KeyError, TypeError):
                detail = str(r.json()["error"]["message"])[:400]
            raise ProviderError(f"{r.status_code}: {detail}", status=r.status_code)
        try:
            data = r.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unparseable response from {model}: {r.text[:200]}") from exc
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
