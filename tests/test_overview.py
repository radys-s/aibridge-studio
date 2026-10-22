"""Přehled: read-only totals for the measurement protocol (v0.4)."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from studio.api import create_app
from studio.config import Settings
from studio.db import AUTO_APPROVED, Database


def call(
    db: Database, run_id: str, model: str, tokens: int, outcome: str = "ok", **kw: object
) -> None:
    db.record_model_call(
        run_id=run_id,
        role="worker",
        requested_model=model,
        tokens_in=tokens,
        tokens_out=0,
        outcome=outcome,
        **kw,
    )


def seed(db: Database) -> None:
    now = time.time()
    a = db.create_task_and_run('{"title": "a"}')
    db.update_run(a, state="accepted", iteration=1, created_at=now - 600, finished_at=now - 300)
    call(db, a, "groq/x", 1000)
    call(db, a, "groq/x", 0, outcome="rate_limited")
    db.event(a, "decision", AUTO_APPROVED)
    db.event(a, "applied", "Změna je ve větvi studio/a")
    b = db.create_task_and_run('{"title": "b"}')
    db.update_run(b, state="accepted", iteration=3, created_at=now - 1000, finished_at=now - 100)
    call(db, b, "cerebras/y", 3000)
    call(db, b, "claude", 500, billing="subscription", cost_usd=0.2)
    db.event(b, "decision", "Uživatel vrátil úkol k další opravě")
    db.event(b, "decision", "Uživatel schválil zadání")
    c = db.create_task_and_run('{"title": "c"}')
    db.update_run(c, state="abandoned", finished_at=now)
    d = db.create_task_and_run('{"title": "d"}')
    db.update_run(d, state="needs_decision")
    call(db, d, "external:freebuff", 0, billing="external")
    old = db.create_task_and_run('{"title": "old"}')
    db.update_run(old, state="accepted", created_at=now - 40 * 86400, finished_at=now)
    call(db, old, "groq/x", 99999)
    db.event(old, "decision", "Uživatel schválil zadání")


def test_overview_counts_only_runs_in_the_window(tmp_path: Path) -> None:
    db = Database(tmp_path / "studio.sqlite")
    seed(db)
    o = db.overview(time.time() - 30 * 86400)
    assert o["runs"] == 4
    assert o["by_state"] == {"accepted": 2, "abandoned": 1, "needs_decision": 1}
    assert o["accepted"] == 2 and o["finished"] == 3
    assert abs(o["accept_rate"] - 2 / 3) < 1e-9
    assert abs(o["median_seconds_to_accepted"] - 600) < 1  # median of 300 s and 900 s
    assert o["fix_rounds_avg"] == 1.0 and o["first_try"] == 1
    assert o["decisions"] == 2  # the automatic draft approval is not the user's decision
    assert o["applied"] == 1
    assert o["api"] == {"calls": 3, "failed": 1, "tokens": 4000, "cost": 0.0}
    assert o["subscription"]["calls"] == 1 and o["subscription"]["cost"] == 0.2
    # Gateway tokens of accepted runs (1000 + 3000) over the 2 that made gateway calls;
    # b's subscription tokens are reported apart.
    assert o["api_tokens_per_accepted"] == 2000
    assert o["external_runs"] == 1
    assert o["top_models"] == [
        {"model": "groq/x", "calls": 2, "ok": 1},
        {"model": "cerebras/y", "calls": 1, "ok": 1},
    ]


def test_empty_overview_has_no_made_up_rates(tmp_path: Path) -> None:
    o = Database(tmp_path / "studio.sqlite").overview(0)
    assert o["runs"] == 0 and o["accept_rate"] is None
    assert o["median_seconds_to_accepted"] is None and o["fix_rounds_avg"] is None
    assert (
        o["api_tokens_per_accepted"] is None and o["external_runs"] == 0 and o["top_models"] == []
    )


def test_overview_endpoint_clamps_the_window(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d")
    with TestClient(create_app(settings, gateway=None, leads={}, start_runner=False)) as c:
        seed(c.app.state.db)  # type: ignore[attr-defined]
        res = c.get("/api/overview?days=7").json()
        assert res["days"] == 7 and res["runs"] == 4
        assert c.get("/api/overview?days=0").json()["days"] == 1
        assert c.get("/api/overview?days=5000").json()["days"] == 365
        assert c.get("/api/overview").json()["days"] == 30
