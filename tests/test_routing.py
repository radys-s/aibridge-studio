"""Model router: privacy floor, paid models, availability, cooldowns and short waits."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from studio.config import ModelSpec, Settings
from studio.db import Database
from studio.routing import ModelRouter, NoModelAvailable, RouteBudget

LEAD = frozenset({"coding", "tools", "lead"})
PRIVATE = ModelSpec("groq/strong", LEAD, 6, privacy="no_train")
PRIVATE_SMALL = ModelSpec("groq/small", LEAD, 3, privacy="no_train")
OPEN = ModelSpec("openrouter/free:free", LEAD, 9)  # unknown privacy, stronger on paper
PAID = ModelSpec("paid/orchestrator", LEAD, 10, privacy="no_train", paid=True, free=False)
BUDGET = RouteBudget(100_000, 1.0)


def router(tmp_path: Path, *models: ModelSpec, **kw: object) -> ModelRouter:
    db = Database(tmp_path / "studio.sqlite")
    return ModelRouter(db, models, **kw)  # type: ignore[arg-type]


def test_privacy_floor_skips_models_that_may_train(tmp_path: Path) -> None:
    r = router(tmp_path, OPEN, PRIVATE, privacy_floor="no_train")
    assert r.candidates("lead", "hard") == ["groq/strong"]
    relaxed = router(tmp_path, OPEN, PRIVATE, privacy_floor="unknown")
    assert relaxed.candidates("lead", "hard") == ["openrouter/free:free", "groq/strong"]


def test_no_private_model_names_the_provider_to_connect(tmp_path: Path) -> None:
    r = router(tmp_path, OPEN, privacy_floor="no_train")
    with pytest.raises(NoModelAvailable, match="connect Groq or Cloudflare"):
        r.choose("run", BUDGET, set(), capability="lead")


def test_paid_models_only_when_allowed(tmp_path: Path) -> None:
    assert router(tmp_path, PAID, PRIVATE).candidates("lead", "hard") == ["groq/strong"]
    paid = router(tmp_path, PAID, PRIVATE, allow_paid=True)
    assert paid.candidates("lead", "hard")[0] == "paid/orchestrator"


def test_models_omniroute_does_not_expose_are_skipped(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL, exposed=lambda: {"groq/small"})
    assert r.candidates("lead") == ["groq/small"]
    blocked = {m["id"]: m["blocked"] for m in r.catalog()}
    assert blocked == {"groq/strong": "not_connected", "groq/small": None}


def test_cooldown_lengths_follow_the_failure(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE)
    now = time.time()

    def cooled(detail: str, status: int | None = 429, retry: float | None = None) -> float:
        r.report_failure("groq/strong", "rate_limit", status, detail, retry)
        return r.db.cooldowns()["groq/strong"] - now

    assert 55 < cooled("Rate limit reached: requests per minute") < 65
    assert cooled("Rate limit: free-models-per-day exceeded", retry=5) > 3500
    assert 10 < cooled("slow down", retry=12) < 15
    r.report_failure("groq/strong", "provider_error", 502, "502: upstream error")
    assert r.db.cooldowns()["groq/strong"] - now < 35
    r.report_failure("groq/strong", "gateway_unavailable", None, "down")  # never cools


def test_short_cooldown_is_waited_out_but_never_list_stays_out(tmp_path: Path) -> None:
    slept: list[float] = []
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL, max_wait_s=45, sleep=slept.append)
    for m in ("groq/strong", "groq/small"):
        r.db.set_cooldown(m, time.time() + 0.05, "rate_limit")
    # Waits for the soonest cooldown instead of failing the run.
    assert r.choose("run", BUDGET, set(), capability="lead") in {"groq/strong", "groq/small"}
    assert slept
    # A model that must stay out (the implementer during review) is not waited for.
    r.db.set_cooldown("groq/small", time.time() + 0.05, "rate_limit")
    with pytest.raises(NoModelAvailable):
        r.choose("run", BUDGET, {"groq/strong"}, capability="lead", never=frozenset({"groq/small"}))
    # A long cooldown (a daily cap) is not waited for.
    r.db.set_cooldown("groq/small", time.time() + 600, "daily")
    with pytest.raises(NoModelAvailable):
        r.choose("run", BUDGET, {"groq/strong"}, capability="lead")


def test_env_marks_private_providers_and_openrouter_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STUDIO_WORKER_MODELS", raising=False)
    monkeypatch.delenv("STUDIO_LEAD_MODELS", raising=False)
    privacy = {m.id: m.privacy for m in Settings.from_env().worker_models}
    assert privacy["groq/openai/gpt-oss-120b"] == "no_train"
    assert privacy["openrouter/poolside/laguna-s-2.1:free"] == "unknown"
    monkeypatch.setenv("STUDIO_OPENROUTER_NO_TRAIN", "1")
    monkeypatch.setenv("STUDIO_LEAD_MODELS", "paid/orchestrator,groq/openai/gpt-oss-120b")
    monkeypatch.setenv("STUDIO_PAID_MODELS", "paid/orchestrator")
    settings = Settings.from_env()
    models = {m.id: m for m in settings.worker_models}
    assert models["openrouter/poolside/laguna-s-2.1:free"].privacy == "no_train"
    assert models["paid/orchestrator"].paid and not settings.allow_paid
    assert settings.privacy_floor == "no_train"
