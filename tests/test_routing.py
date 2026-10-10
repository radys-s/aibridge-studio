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
    kw.setdefault("stats_ttl_s", 0.0)  # tests change the history between calls
    return ModelRouter(db, models, **kw)  # type: ignore[arg-type]


def test_privacy_floor_skips_models_that_may_train(tmp_path: Path) -> None:
    r = router(tmp_path, OPEN, PRIVATE, privacy_floor="no_train")
    assert r.candidates("lead", "hard") == ["groq/strong"]
    relaxed = router(tmp_path, OPEN, PRIVATE, privacy_floor="unknown")
    assert relaxed.candidates("lead", "hard") == ["openrouter/free:free", "groq/strong"]


def test_no_private_model_names_the_provider_to_connect(tmp_path: Path) -> None:
    r = router(tmp_path, OPEN, privacy_floor="no_train")
    with pytest.raises(NoModelAvailable, match="connect Groq, Cloudflare"):
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
    # Ollama Cloud's free plan: session and weekly quotas, never back within a minute.
    assert cooled("you've reached your weekly usage limit") > 3500
    assert cooled("session usage limit reached, upgrade for more") > 3500
    assert 10 < cooled("slow down", retry=12) < 15
    r.report_failure("groq/strong", "provider_error", 502, "502: upstream error")
    assert r.db.cooldowns()["groq/strong"] - now < 35
    # A provider setting missing in OmniRoute (seen live on Cloudflare) does not fix itself.
    r.report_failure(
        "groq/strong", "provider_error", 502, "502: Workers AI requires an Account ID. Add it"
    )
    assert r.db.cooldowns()["groq/strong"] - now > 3500
    # OmniRoute appends "(reset after 3s)"; that must not shorten a setup cooldown (seen live).
    r.report_failure(
        "groq/strong", "provider_error", 502, "502: requires an Account ID", retry_after=3
    )
    assert r.db.cooldowns()["groq/strong"] - now > 3500
    r.report_failure("groq/strong", "provider_error", 401, "401: bad key")
    assert r.db.cooldowns()["groq/strong"] - now > 3500
    r.report_failure("groq/strong", "gateway_unavailable", None, "down")  # never cools


def test_short_cooldown_is_waited_out_but_never_list_stays_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    r = router(tmp_path, PRIVATE, PRIVATE_SMALL, max_wait_s=45, sleep=sleep)
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


def test_a_model_that_failed_this_turn_is_not_waited_for(tmp_path: Path) -> None:
    slept: list[float] = []
    r = router(tmp_path, PRIVATE, max_wait_s=45, sleep=slept.append)
    r.db.set_cooldown("groq/strong", time.time() + 0.05, "transient")
    with pytest.raises(NoModelAvailable):
        r.choose("run", BUDGET, {"groq/strong"}, capability="lead")
    assert not slept
    with pytest.raises(NoModelAvailable):  # `never` holds even for a fresh model
        r.db.clear_cooldown("groq/strong")
        r.choose("run", BUDGET, set(), capability="lead", never=frozenset({"groq/strong"}))


def test_non_finite_retry_after_is_ignored() -> None:
    from studio.gateway import _float

    assert _float("nan") == 0.0 and _float("inf") == 0.0 and _float("12") == 12.0


def test_failed_models_probe_keeps_the_last_good_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from studio.gateway import OmniRouteGateway

    answers: list[object] = [{"data": [{"id": "groq/strong"}]}, httpx.ReadTimeout("busy")]

    class Resp:
        def __init__(self, body: object) -> None:
            self.body = body

        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            return self.body

    def fake_get(*_: object, **__: object) -> Resp:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Resp(answer)

    clock = [1000.0]
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    gw = OmniRouteGateway("http://omniroute")
    assert gw.models() == {"groq/strong"}
    clock[0] += 61  # cache expired, the probe times out
    assert gw.models() == {"groq/strong"}
    assert gw.models() == {"groq/strong"}  # served from cache, no third probe
    assert answers == []


def test_down_omniroute_is_not_probed_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from studio.gateway import OmniRouteGateway

    calls = []

    def fake_get(*_: object, **__: object) -> object:
        calls.append(1)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", fake_get)
    gw = OmniRouteGateway("http://omniroute")
    assert gw.models() is None
    assert gw.models() is None
    assert len(calls) == 1


def test_default_catalog_privacy_follows_the_provider() -> None:
    from studio.config import DEFAULT_MODELS

    privacy = {m.id: m.privacy for m in DEFAULT_MODELS}
    assert privacy["groq/openai/gpt-oss-120b"] == "no_train"
    assert privacy["cerebras/gpt-oss-120b"] == "no_train"
    assert privacy["cf/@cf/moonshotai/kimi-k2.6"] == "no_train"
    assert privacy["cf/@cf/zai-org/glm-4.7-flash"] == "no_train"
    # GitHub Models was retired on 2026-07-30.
    assert not any(m.provider in ("ghm", "github-models") for m in DEFAULT_MODELS)
    assert privacy["nvidia/moonshotai/kimi-k2.6"] == "may_train"
    assert privacy["mistral/devstral-latest"] == "may_train"
    assert privacy["zai/glm-4.7-flash"] == "unknown"
    # v0.6: Ollama Cloud and OVHcloud AI Endpoints commit to no training on inputs.
    assert privacy["ollamacloud/deepseek-v4-pro"] == "no_train"
    assert privacy["ovh/Mistral-Small-3.2-24B-Instruct-2506"] == "no_train"
    # OmniRoute lists models under the provider alias, never the long provider id.
    long_ids = ("cloudflare-ai/", "github-models/", "ollama-cloud/", "ovhcloud/")
    assert not any(m.id.startswith(long_ids) for m in DEFAULT_MODELS)


def test_opted_out_provider_counts_as_no_train(monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.config import _models_from_env

    monkeypatch.setenv("STUDIO_NO_TRAIN_PROVIDERS", "mistral")
    privacy = {m.id: m.privacy for m in _models_from_env()}
    assert privacy["mistral/devstral-latest"] == "no_train"
    assert privacy["gemini/gemini-3.5-flash"] == "may_train"


def test_may_train_is_never_routed_even_with_a_relaxed_floor(tmp_path: Path) -> None:
    trains = ModelSpec("nvidia/strong", LEAD, 9, privacy="may_train")
    r = router(tmp_path, trains, OPEN, privacy_floor="unknown")
    assert r.choose("run", BUDGET, set(), complexity="hard") == OPEN.id


def _run(db: Database) -> str:
    return db.create_task_and_run(
        '{"goal": "learn", "repo_path": ".", "acceptance_criteria": ["works"]}'
    )


def _calls(db: Database, run_id: str, model: str, outcome: str, n: int, error: str = "") -> None:
    for _ in range(n):
        db.record_model_call(
            run_id=run_id, role="worker", requested_model=model, outcome=outcome, error=error
        )


def _reviewed(
    db: Database, model: str, verdict: str, criteria_met: bool = True, earlier: str = ""
) -> None:
    run_id = _run(db)
    if earlier:  # an interrupted attempt in the same iteration that the review never saw
        first = db.start_step(run_id, "work", 1, "worker")
        _calls(db, run_id, earlier, "ok", 3)
        db.record_model_call(
            run_id=run_id, step_id=first, role="worker", requested_model=earlier, outcome="ok"
        )
        db.finish_step(first, "interrupted", None)
    work = db.start_step(run_id, "work", 1, "lead:autopilot")
    db.record_model_call(
        run_id=run_id,
        step_id=work,
        role="work",
        requested_model=model,
        actual_model=model,
        provider="test",
        outcome="ok",
    )
    db.finish_step(work, "done", {})
    review = db.start_step(run_id, "review", 1, "autopilot")
    db.record_model_call(
        run_id=run_id,
        step_id=review,
        role="review",
        requested_model="test/reviewer",
        actual_model="reviewer",
        provider="test",
        outcome="ok",
    )
    criteria = [{"criterion": "works", "met": criteria_met}]
    db.finish_step(
        review,
        "done",
        {
            "verdict": verdict,
            "summary": "",
            "findings": [],
            "criteria": criteria,
            "work_step_id": work,
            "independent": True,
        },
    )


def test_a_model_that_keeps_failing_is_demoted_not_removed(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL)
    run_id = _run(r.db)
    _calls(r.db, run_id, "groq/strong", "ok", 2)
    _calls(r.db, run_id, "groq/strong", "provider_error", 3, "unparseable response")
    assert r.candidates("lead") == ["groq/small", "groq/strong"]
    info = {m["id"]: m for m in r.catalog()}
    assert info["groq/strong"]["demoted"] is True
    assert info["groq/strong"]["stats"] == {"calls": 5, "failed": 3, "reviewed": 0, "accepted": 0}


def test_too_few_calls_do_not_demote(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL)
    _calls(r.db, _run(r.db), "groq/strong", "timeout", 4)
    assert r.candidates("lead") == ["groq/strong", "groq/small"]


def test_rate_limits_setup_errors_and_a_down_gateway_are_not_held_against_a_model(
    tmp_path: Path,
) -> None:
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL)
    run_id = _run(r.db)
    _calls(r.db, run_id, "groq/strong", "ok", 1)
    _calls(r.db, run_id, "groq/strong", "provider_error", 3, "429: Rate limit reached")
    _calls(r.db, run_id, "groq/strong", "provider_error", 3, "502: requires an Account ID")
    _calls(r.db, run_id, "groq/strong", "provider_error", 3, "401: invalid api key")
    _calls(r.db, run_id, "groq/strong", "gateway_unavailable", 5, "connection refused")
    assert r.candidates("lead") == ["groq/strong", "groq/small"]
    assert r.stats()["groq/strong"].calls == 1


def test_accepted_work_lifts_a_model_for_hard_tasks_only(tmp_path: Path) -> None:
    a = ModelSpec("groq/a", LEAD, 5, privacy="no_train")
    b = ModelSpec("groq/b", LEAD, 5, privacy="no_train")
    r = router(tmp_path, a, b)
    for verdict in ("accept", "accept", "request_changes"):
        _reviewed(r.db, "groq/b", verdict)
    assert r.stats()["groq/b"].reviewed == 3
    assert r.candidates("lead", "hard") == ["groq/b", "groq/a"]
    assert r.candidates("lead") == ["groq/a", "groq/b"]  # catalog order otherwise
    # Two reviews are not enough evidence for a bonus.
    (tmp_path / "two").mkdir()
    r2 = router(tmp_path / "two", a, b)
    _reviewed(r2.db, "groq/b", "accept")
    _reviewed(r2.db, "groq/b", "accept")
    assert r2.candidates("lead", "hard") == ["groq/a", "groq/b"]


def test_learning_never_overrides_the_privacy_floor(tmp_path: Path) -> None:
    r = router(tmp_path, OPEN, PRIVATE, privacy_floor="no_train")
    run_id = _run(r.db)
    _calls(r.db, run_id, "groq/strong", "timeout", 10)
    for _ in range(3):
        _reviewed(r.db, OPEN.id, "accept")
    assert r.candidates("lead", "hard") == ["groq/strong"]


def test_catalog_says_why_a_model_is_paused(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE)
    r.report_failure("groq/strong", "provider_error", 502, "502: requires an Account ID")
    assert r.catalog()[0]["cooldown_kind"] == "setup"


def test_only_the_reviewed_attempt_and_passing_reviews_count(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL)
    _reviewed(r.db, "groq/small", "accept", earlier="groq/strong")
    _reviewed(r.db, "groq/small", "accept", criteria_met=False)  # runner would not accept
    stats = r.stats()
    assert stats["groq/small"].reviewed == 2 and stats["groq/small"].accepted == 1
    assert stats["groq/strong"].reviewed == 0


def test_rate_limit_words_in_a_real_error_still_count(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL)
    _calls(r.db, _run(r.db), "groq/strong", "provider_error", 5, "400: see rate limits docs")
    assert r.candidates("lead") == ["groq/small", "groq/strong"]


def test_stats_are_cached_between_turns(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL, stats_ttl_s=60.0)
    assert r.candidates("lead") == ["groq/strong", "groq/small"]
    _calls(r.db, _run(r.db), "groq/strong", "timeout", 5)
    assert r.candidates("lead") == ["groq/strong", "groq/small"]  # cached
    r._stats = None
    assert r.candidates("lead") == ["groq/small", "groq/strong"]


def test_planning_success_does_not_hide_worker_failures(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE, PRIVATE_SMALL)
    run_id = _run(r.db)
    for _ in range(10):
        r.db.record_model_call(run_id=run_id, role="plan", requested_model=PRIVATE.id, outcome="ok")
    _calls(r.db, run_id, PRIVATE.id, "provider_error", 5, "bad output")
    assert r.candidates("coding", role="worker") == [PRIVATE_SMALL.id, PRIVATE.id]
    assert r.candidates("lead", role="plan") == [PRIVATE.id, PRIVATE_SMALL.id]
    assert r.catalog()[0]["demotion_reasons"]


def test_latest_independent_review_counts_once_and_legacy_does_not(tmp_path: Path) -> None:
    r = router(tmp_path, PRIVATE)
    _reviewed(r.db, PRIVATE.id, "accept")
    run_id = r.db.runs()[0]["id"]
    work = r.db.last_step(run_id, "work")["id"]

    def record(independent: bool | None, verdict: str, model: str) -> None:
        step = r.db.start_step(run_id, "review", 1, "autopilot")
        r.db.record_model_call(
            run_id=run_id,
            step_id=step,
            role="review",
            requested_model=model,
            actual_model=model,
            provider="test",
            outcome="ok",
        )
        payload = {
            "verdict": verdict,
            "findings": [],
            "criteria": [{"criterion": "works", "met": True}],
        }
        if independent is not None:
            payload.update(work_step_id=work, independent=independent)
        r.db.finish_step(step, "done", payload)

    record(True, "reject", "test/reviewer")
    record(False, "accept", PRIVATE.id)
    record(None, "accept", "test/reviewer")
    assert r.stats()[PRIVATE.id].reviewed == 1
    assert r.stats()[PRIVATE.id].accepted == 0


@pytest.mark.parametrize("actual", [None, "groq/shared-model", "cerebras/shared-model"])
def test_unknown_or_same_actual_model_does_not_qualify(tmp_path: Path, actual: str | None) -> None:
    r = router(tmp_path, PRIVATE)
    _reviewed(r.db, PRIVATE.id, "accept")
    run_id = r.db.runs()[0]["id"]
    work, review = r.db.last_step(run_id, "work"), r.db.last_step(run_id, "review")
    with r.db.connect() as conn:
        conn.execute(
            "UPDATE model_call SET actual_model = 'shared-model', provider = 'groq' WHERE step_id = ?",
            (work["id"],),
        )
        conn.execute(
            "UPDATE model_call SET actual_model = ?, provider = 'cerebras' WHERE step_id = ?",
            (actual, review["id"]),
        )
    assert r.stats()[PRIVATE.id].reviewed == 0
