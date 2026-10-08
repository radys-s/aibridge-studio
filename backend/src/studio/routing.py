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
# Rate limits and missing setup say nothing about the model's quality (ADR 0006).
_CAPACITY = re.compile(r"^(429|401|403)\b")

# Learning from outcomes (ADR 0006): windows of recent history, and the evidence needed
# before it changes the order. Failures age out after 3 days, so a demoted model whose
# provider got fixed comes back; review results are rarer and count for 14 days.
FAILURE_WINDOW_S = 3 * 86400.0
REVIEW_WINDOW_S = 14 * 86400.0
STATS_TTL_S = 60.0
DEMOTE_MIN_CALLS = 5
DEMOTE_FAILURE_RATE = 0.5
BONUS_MIN_REVIEWS = 3


@dataclass
class ModelStats:
    calls: int = 0
    failed: int = 0
    reviewed: int = 0
    accepted: int = 0

    @property
    def demoted(self) -> bool:
        return self.calls >= DEMOTE_MIN_CALLS and self.failed / self.calls >= DEMOTE_FAILURE_RATE

    @property
    def bonus(self) -> float:
        """Up to +1 strength for work that reviews accepted."""
        return self.accepted / self.reviewed if self.reviewed >= BONUS_MIN_REVIEWS else 0.0

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "failed": self.failed,
            "reviewed": self.reviewed,
            "accepted": self.accepted,
        }


def review_passed(review: dict[str, object]) -> bool:
    """The runner's acceptance rule (runner.passes): accept, no blocker or major finding,
    every criterion met."""
    findings = review.get("findings") or []
    criteria = review.get("criteria") or []
    if not isinstance(findings, list) or not isinstance(criteria, list):
        return False
    blocking = any(
        isinstance(f, dict) and f.get("severity") in ("blocker", "major") for f in findings
    )
    met = all(isinstance(c, dict) and c.get("met") is True for c in criteria)
    return review.get("verdict") == "accept" and not blocking and met


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
        stats_ttl_s: float = STATS_TTL_S,
    ) -> None:
        self.db = db
        self.models = models
        self.privacy_floor = privacy_floor
        self.allow_paid = allow_paid
        self.exposed = exposed
        self.sleep = sleep
        # Seconds to wait for a briefly cooling model (Settings.route_wait_s; 0 = never).
        self.max_wait_s = max_wait_s
        # choose() runs every worker turn; the stats change slowly, so they are cached.
        self.stats_ttl_s = stats_ttl_s
        self._stats: tuple[float, dict[str, ModelStats]] | None = None

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

    def stats(self) -> dict[str, ModelStats]:
        """Reliability: failed calls of the last 3 days that point at the model (not rate
        limits, missing setup or OmniRoute being down), and how reviews of the last 14 days
        judged the work it wrote. Cached for `stats_ttl_s`."""
        now = time.monotonic()
        if self._stats and now - self._stats[0] < self.stats_ttl_s:
            return self._stats[1]
        ok, failed = self.db.call_outcomes_since(time.time() - FAILURE_WINDOW_S)
        stats: dict[str, ModelStats] = {}
        for model, n in ok.items():
            stats.setdefault(model, ModelStats()).calls += n
        for model, outcome, error in failed:
            if outcome == "gateway_unavailable" or _CAPACITY.search(error) or _SETUP.search(error):
                continue
            st = stats.setdefault(model, ModelStats())
            st.calls += 1
            st.failed += 1
        for review, model in self.db.reviewed_work_since(time.time() - REVIEW_WINDOW_S):
            st = stats.setdefault(model, ModelStats())
            st.reviewed += 1
            st.accepted += review_passed(review)
        self._stats = (now, stats)
        return stats

    def catalog(self) -> list[dict[str, object]]:
        """Every configured model with why it is (not) used, for the settings view."""
        exposed = self.exposed()
        cooling = self.db.cooldowns()
        reasons = self.db.cooldown_reasons()
        stats = self.stats()
        out: list[dict[str, object]] = []
        for m in self.models:
            st = stats.get(m.id, ModelStats())
            out.append(
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
                    # "setup", "rate_limit", "daily_limit", … (the key before the colon).
                    "cooldown_kind": reasons[m.id].split(":", 1)[0] if m.id in reasons else None,
                    "stats": st.as_dict(),
                    "demoted": st.demoted,
                }
            )
        return out

    def candidates(self, capability: str = "coding", complexity: str = "moderate") -> list[str]:
        usable = self.usable()
        stats = self.stats()

        def ordered(group: list[ModelSpec]) -> list[ModelSpec]:
            if complexity == "hard":
                # Stable sort: equal scores keep the catalog order.
                group = sorted(
                    group, key=lambda m: -(m.strength + stats.get(m.id, ModelStats()).bonus)
                )
            # A model that keeps failing goes below the healthy ones of its group, never out.
            healthy = [m for m in group if not stats.get(m.id, ModelStats()).demoted]
            return healthy + [m for m in group if m not in healthy]

        preferred = ordered([m for m in usable if capability in m.capabilities])
        # Workers may fall back to any model; Autopilot only to models marked `lead`.
        others = (
            []
            if capability == "lead"
            else ordered([m for m in usable if capability not in m.capabilities])
        )
        cooling = self.db.cooldowns()
        result = [m for m in [*preferred, *others] if m.id not in cooling]
        result += [m for m in [*preferred, *others] if m.id in cooling]
        return [m.id for m in result]

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
