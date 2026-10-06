"""Model router: the single owner of model choice and fallback.

Provenance: ordering idea (capability first, complexity reorders by strength, cooled-down
models last rather than dropped) derived from Prizma `backend/app/routing/selector.py`
(commit 49022f7). Changes: cooldowns persisted in SQLite (Prizma kept them in RAM and
forgot them on restart); candidates are explicit OmniRoute model ids; budget gate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from studio.config import ModelSpec
from studio.db import Database

COOLDOWN_S = {"provider_error": 300.0, "timeout": 120.0, "rate_limit": 600.0}


class BudgetExceeded(Exception):
    pass


class NoModelAvailable(Exception):
    pass


@dataclass
class RouteBudget:
    max_tokens: int
    max_cost_usd: float


class ModelRouter:
    def __init__(self, db: Database, models: tuple[ModelSpec, ...]) -> None:
        self.db = db
        self.models = models

    def candidates(self, capability: str = "coding", complexity: str = "moderate") -> list[str]:
        preferred = [m for m in self.models if capability in m.capabilities]
        others = [m for m in self.models if capability not in m.capabilities]
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
        cooling = self.db.cooldowns()
        for model in self.candidates(capability, complexity):
            if model in excluded or model in cooling:
                continue
            return model
        raise NoModelAvailable(
            "all worker models failed or are cooling down: "
            + ", ".join(f"{m} (cooldown)" if m in cooling else m for m in self.candidates())
        )

    def report_failure(self, model: str, kind: str, status: int | None, detail: str) -> None:
        if kind == "gateway_unavailable":
            return  # the gateway is down, not the model; cooling every model would be wrong
        key = "rate_limit" if status == 429 else kind
        seconds = COOLDOWN_S.get(key, COOLDOWN_S["provider_error"])
        self.db.set_cooldown(model, time.time() + seconds, f"{kind}: {detail[:200]}")
