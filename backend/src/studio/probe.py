"""One-shot connectivity check for a catalog model ("Otestovat" in Settings).

After Tomáš adds a provider key in the OmniRoute dashboard he can check the model right away:
one tiny request with one tool, no code and no notes. It is not a routing decision: it never
picks or substitutes a model, and it never sends anything to a model below the privacy floor.
"""

from __future__ import annotations

import time
from typing import Any

from studio.gateway import ChatGateway, GatewayError
from studio.routing import ModelRouter

PING: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Confirm that you can call tools.",
        "parameters": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}
MESSAGES = [
    {"role": "system", "content": "This is a connectivity check. Call the ping tool with ok=true."},
    {"role": "user", "content": "ping"},
]
PROBE_TIMEOUT_S = 30.0


class UnknownModel(KeyError):
    pass


def probe_model(gateway: ChatGateway, router: ModelRouter, model_id: str) -> dict[str, Any]:
    spec = next((m for m in router.models if m.id == model_id), None)
    if spec is None:
        raise UnknownModel(model_id)
    # The key may have been added a moment ago: ask OmniRoute again instead of the cache.
    exposed = gateway.models(fresh=True)
    reason = router.blocked(spec, exposed)
    out: dict[str, Any] = {"model": model_id, "blocked": reason}
    if reason in ("privacy", "paid"):
        return {**out, "ok": False, "kind": reason, "detail": "not sent: blocked by policy"}
    start = time.monotonic()
    try:
        res = gateway.chat(model_id, MESSAGES, [PING], timeout_s=PROBE_TIMEOUT_S)
    except GatewayError as exc:
        return {
            **out,
            "ok": False,
            "kind": exc.kind,
            "status": exc.status,
            "detail": str(exc)[:300],
            "latency_ms": int((time.monotonic() - start) * 1000),
        }
    calls = res.message.get("tool_calls") or []
    tools = any((c.get("function") or {}).get("name") == "ping" for c in calls)
    if tools:
        # It works now: a cooldown left over from before the key was added is stale.
        router.db.clear_cooldown(model_id)
    return {
        **out,
        "ok": True,
        "tools": tools,
        "actual_model": res.actual_model,
        "provider": res.provider,
        "latency_ms": res.latency_ms or int((time.monotonic() - start) * 1000),
    }
