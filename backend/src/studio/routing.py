"""Model router: the single owner of model choice and fallback.

Provenance: ordering idea (capability first, complexity reorders by strength, cooled-down
models last rather than dropped) derived from Prizma `backend/app/routing/selector.py`
(commit 49022f7). Changes: cooldowns persisted in SQLite (Prizma kept them in RAM and
forgot them on restart); candidates are explicit OmniRoute model ids; budget gate.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from studio.config import PRIVACY_RANK, ModelSpec, Privacy
from studio.db import Database

# Free tiers limit per minute (Groq: 30 RPM) and per day (OpenRouter :free: 50 RPD). A
# per-minute 429 cools briefly; a daily cap or a hard provider error cools for long.
COOLDOWN_S = {
    "provider_error": 300.0,
    "timeout": 120.0,
    "rate_limit": 60.0,
    "daily_limit": 3600.0,
    "transient": 30.0,
    # Missing key or provider setting: nothing changes until Tomáš fixes it in OmniRoute;
    # "Otestovat" in Settings clears the cooldown as soon as it works.
    "setup": 3600.0,
}
MAX_RETRY_AFTER_S = 600.0
_DAILY = re.compile(r"per.?day|daily|requests per day|RPD|free-models-per-day", re.I)
_SETUP = re.compile(
    r"requires an Account ID|No active credentials|invalid api key|unauthorized", re.I
)
_TRANSIENT = re.compile(r"^5\d\d\b|empty|upstream|overloaded|temporarily", re.I)


class BudgetExceeded(Exception):
    pass


class NoModelAvailable(Exception):
    pass


@dataclass
class RouteBudget:
    max_tokens: int
    max_cost_usd: float


class ModelRouter:
    """Orders and picks models. Never returns one below the privacy floor, a paid one
    unless allowed, or one OmniRoute does not expose (provider not connected)."""

    def __init__(
        self,
        db: Database,
        models: tuple[ModelSpec, ...],
        privacy_floor: Privacy = "no_train",
        allow_paid: bool = False,
        exposed: Callable[[], set[str] | None] = lambda: None,
        sleep: Callable[[float], None] = time.sleep,
        max_wait_s: float = 0.0,
    ) -> None:
        self.db = db
        self.models = models
        self.privacy_floor = privacy_floor
        self.allow_paid = allow_paid
        self.exposed = exposed
        self.sleep = sleep
        # Seconds to wait for a briefly cooling model (Settings.route_wait_s; 0 = never).
        self.max_wait_s = max_wait_s

    def blocked(self, model: ModelSpec, exposed: set[str] | None = None) -> str | None:
        """Why this model may not be used, or None."""
        if PRIVACY_RANK[model.privacy] < PRIVACY_RANK[self.privacy_floor]:
            return "privacy"
        if model.paid and not self.allow_paid:
            return "paid"
        if exposed is not None and model.id not in exposed:
            return "not_connected"
        return None

    def usable(self) -> list[ModelSpec]:
        exposed = self.exposed()
        return [m for m in self.models if self.blocked(m, exposed) is None]

    def catalog(self) -> list[dict[str, object]]:
        """Every configured model with why it is (not) used, for the settings view."""
        exposed = self.exposed()
        cooling = self.db.cooldowns()
        return [
            {
                "id": m.id,
                "provider": m.provider,
                "privacy": m.privacy,
                "paid": m.paid,
                "lead": "lead" in m.capabilities,
                "coding": "coding" in m.capabilities,
                "strength": m.strength,
                "blocked": self.blocked(m, exposed),
                "cooldown_until": cooling.get(m.id),
            }
            for m in self.models
        ]

    def candidates(self, capability: str = "coding", complexity: str = "moderate") -> list[str]:
        usable = self.usable()
        preferred = [m for m in usable if capability in m.capabilities]
        # Workers may fall back to any model; Autopilot only to models marked `lead`.
        others = (
            [] if capability == "lead" else [m for m in usable if capability not in m.capabilities]
        )
        if complexity == "hard":
            preferred.sort(key=lambda m: -m.strength)
            others.sort(key=lambda m: -m.strength)
        cooling = self.db.cooldowns()
        ordered = [m for m in [*preferred, *others] if m.id not in cooling]
        ordered += [m for m in [*preferred, *others] if m.id in cooling]
        return [m.id for m in ordered]

    def choose(
        self,
        run_id: str,
        budget: RouteBudget,
        excluded: set[str],
        capability: str = "coding",
        complexity: str = "moderate",
        never: frozenset[str] = frozenset(),
        should_stop: Callable[[], bool] = lambda: False,
    ) -> str:
        """Pick the model for the next gateway call of this run.

        `excluded` holds models that already failed during the current worker turn; the
        router never hands the same failing model back within that turn.
        """
        used = self.db.usage(run_id, billing="api")
        if used["tokens"] >= budget.max_tokens:
            raise BudgetExceeded(
                f"token budget {budget.max_tokens} reached ({int(used['tokens'])})"
            )
        if used["cost"] > budget.max_cost_usd:
            raise BudgetExceeded(
                f"cost budget ${budget.max_cost_usd} exceeded (${used['cost']:.4f})"
            )
        candidates = self.candidates(capability, complexity)
        cooling = self.db.cooldowns()
        for model in candidates:
            if model in excluded or model in never or model in cooling:
                continue
            return model
        # Nothing fresh. A short cooldown (a per-minute limit, a flaky upstream) is worth
        # waiting for rather than stopping the run. Models that failed in this turn
        # (`excluded`) and `never` (the implementer's models during an independent review)
        # stay out even then.
        waitable = [
            (cooling[m], m)
            for m in candidates
            if m in cooling and m not in never and m not in excluded
        ]
        if waitable and self.max_wait_s > 0:
            until, model = min(waitable)
            delay = until - time.time()
            if delay <= self.max_wait_s:
                end = time.time() + max(delay, 0)
                while time.time() < end:
                    if should_stop():
                        break
                    self.sleep(max(0.0, min(1.0, end - time.time())))
                else:
                    self.db.clear_cooldown(model)
                    return model
        if not candidates:
            raise NoModelAvailable(self._why_none(capability))
        raise NoModelAvailable(
            f"all {capability} models failed, are excluded or are cooling down: "
            + ", ".join(f"{m} (cooldown)" if m in cooling else m for m in candidates)
        )

    def _why_none(self, capability: str) -> str:
        exposed = self.exposed()
        reasons = {
            m.id: self.blocked(m, exposed) for m in self.models if capability in m.capabilities
        }
        if any(r == "privacy" for r in reasons.values()) and not any(
            r is None for r in reasons.values()
        ):
            return (
                f"no {capability} model that does not train on your data is connected; "
                "connect Groq, Cloudflare Workers AI or Cerebras in OmniRoute, "
                "or turn off training for free models in OpenRouter and set "
                "STUDIO_OPENROUTER_NO_TRAIN=1"
            )
        return f"no {capability} model is usable: " + ", ".join(
            f"{m} ({r})" for m, r in reasons.items()
        )

    def report_failure(
        self,
        model: str,
        kind: str,
        status: int | None,
        detail: str,
        retry_after: float | None = None,
    ) -> None:
        if kind == "gateway_unavailable":
            return  # the gateway is down, not the model; cooling every model would be wrong
        if status == 429:
            key = "daily_limit" if _DAILY.search(detail) else "rate_limit"
        elif status in (401, 403) or _SETUP.search(detail):
            key = "setup"
        elif kind == "provider_error" and _TRANSIENT.search(detail):
            key = "transient"
        else:
            key = kind
        seconds = COOLDOWN_S.get(key, COOLDOWN_S["provider_error"])
        if retry_after and key != "daily_limit":
            seconds = min(max(retry_after, 1.0), MAX_RETRY_AFTER_S)
        self.db.set_cooldown(model, time.time() + seconds, f"{key}: {detail[:200]}")
