"""SQLite persistence. Studio owns this database; it never opens AI Bridge or Prizma databases.

Every state change is written before the action it describes continues, so a crash at any
point leaves a row that `recover()` can classify.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from studio.review import model_identity
from studio.sandbox import safe_text, safe_value

SCHEMA_VERSION = 2

# Message of the decision event the runner writes for an automatic draft approval; the
# overview does not count it as Tomáš's decision.
AUTO_APPROVED = "Zadání schváleno automaticky"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
    id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS run (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task(id),
    state TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'setup',
    iteration INTEGER NOT NULL DEFAULT 1,
    feedback_json TEXT,
    workspace TEXT,
    source_commit TEXT,
    iteration_base TEXT,
    plan_json TEXT,
    decision_reason TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS step (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run(id),
    kind TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    status TEXT NOT NULL,
    actor TEXT,
    output_json TEXT,
    error TEXT,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS step_run ON step(run_id, id);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run(id),
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS event_run ON event(run_id, id);
CREATE TABLE IF NOT EXISTS model_call (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run(id),
    step_id INTEGER REFERENCES step(id),
    role TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    actual_model TEXT,
    provider TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    outcome TEXT NOT NULL,
    error TEXT,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS model_call_run ON model_call(run_id);
CREATE INDEX IF NOT EXISTS model_call_at ON model_call(at);
CREATE INDEX IF NOT EXISTS model_call_step ON model_call(step_id);
CREATE TABLE IF NOT EXISTS cooldown (
    model TEXT PRIMARY KEY,
    until REAL NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run(id),
    at REAL NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS message_run ON message(run_id, id);
CREATE TABLE IF NOT EXISTS agent_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run(id),
    step_id INTEGER REFERENCES step(id),
    lead TEXT NOT NULL,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS agent_event_run ON agent_event(run_id, id);
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""

# Columns added after schema version 1. SQLite has no ADD COLUMN IF NOT EXISTS.
_RUN_COLUMNS_V2 = {
    "implementer": "TEXT",  # effective implementer after auto/escalation; NULL = from spec
    "escalations": "INTEGER NOT NULL DEFAULT 0",
    # what the user is asked to decide: draft | plan | takeover | self_review | sensitive
    "pending": "TEXT",
    "sensitive_ok": "INTEGER NOT NULL DEFAULT 0",  # user confirmed flagged files are not secret
}
_MODEL_CALL_COLUMNS_V2 = {"billing": "TEXT NOT NULL DEFAULT 'api'"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Database:
    """Thin wrapper; one connection per call, serialized writes via a lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            for table, columns in (
                ("run", _RUN_COLUMNS_V2),
                ("model_call", _MODEL_CALL_COLUMNS_V2),
            ):
                have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns.items():
                    if name not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    # --- tasks & runs -----------------------------------------------------------------

    def create_task_and_run(self, spec_json: str) -> str:
        task_id, run_id, now = new_id(), new_id(), time.time()
        with self.connect() as c:
            c.execute("INSERT INTO task VALUES (?,?,?)", (task_id, spec_json, now))
            c.execute(
                "INSERT INTO run (id, task_id, state, created_at, updated_at) "
                "VALUES (?,?,'queued',?,?)",
                (run_id, task_id, now, now),
            )
        self.event(run_id, "created", "Úkol přijat a zařazen do fronty")
        return run_id

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as c:
            row = c.execute(
                "SELECT run.*, task.spec_json FROM run JOIN task ON task.id = run.task_id "
                "WHERE run.id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT run.*, task.spec_json FROM run JOIN task ON task.id = run.task_id "
                "ORDER BY run.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_spec(self, run_id: str, spec_json: str) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE task SET spec_json = ? WHERE id = (SELECT task_id FROM run WHERE id = ?)",
                (spec_json, run_id),
            )

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as c:
            c.execute(f"UPDATE run SET {cols} WHERE id = ?", (*fields.values(), run_id))

    def next_queued(self) -> str | None:
        with self.connect() as c:
            row = c.execute(
                "SELECT id FROM run WHERE state = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return str(row["id"]) if row else None

    # --- steps ------------------------------------------------------------------------

    def start_step(self, run_id: str, kind: str, iteration: int, actor: str | None) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO step (run_id, kind, iteration, status, actor, started_at) "
                "VALUES (?,?,?,'running',?,?)",
                (run_id, kind, iteration, actor, time.time()),
            )
            return int(cur.lastrowid or 0)

    def finish_step(
        self, step_id: int, status: str, output: Any = None, error: str | None = None
    ) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE step SET status = ?, output_json = ?, error = ?, finished_at = ? "
                "WHERE id = ?",
                (
                    status,
                    json.dumps(safe_value(output), ensure_ascii=False)
                    if output is not None
                    else None,
                    safe_text(error) if error else error,
                    time.time(),
                    step_id,
                ),
            )

    def steps(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM step WHERE run_id = ? ORDER BY id", (run_id,))
            return [dict(r) for r in rows.fetchall()]

    def last_step(self, run_id: str, kind: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM step WHERE run_id = ?"
        args: tuple[Any, ...] = (run_id,)
        if kind:
            sql += " AND kind = ?"
            args = (run_id, kind)
        with self.connect() as c:
            row = c.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return dict(row) if row else None

    # --- events & model calls ---------------------------------------------------------

    def event(self, run_id: str, kind: str, message: str, data: Any = None) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO event (run_id, at, kind, message, data_json) VALUES (?,?,?,?,?)",
                (
                    run_id,
                    time.time(),
                    kind,
                    safe_text(message),
                    json.dumps(safe_value(data), ensure_ascii=False) if data is not None else None,
                ),
            )

    def events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM event WHERE run_id = ? AND id > ? ORDER BY id", (run_id, after_id)
            )
            return [dict(r) for r in rows.fetchall()]

    def record_model_call(self, **fields: Any) -> None:
        fields = safe_value(fields)
        fields.setdefault("at", time.time())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self.connect() as c:
            c.execute(f"INSERT INTO model_call ({cols}) VALUES ({marks})", tuple(fields.values()))

    def model_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM model_call WHERE run_id = ? ORDER BY id", (run_id,))
            return [dict(r) for r in rows.fetchall()]

    def usage(self, run_id: str, billing: str | None = None) -> dict[str, float]:
        """Totals for a run. billing='api' is what the run's budget limits (gateway calls);
        'subscription' is lead CLI use on the user's own login (cost is API-equivalent)."""
        sql = (
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens, "
            "COALESCE(SUM(cost_usd), 0) AS cost, COUNT(*) AS calls "
            "FROM model_call WHERE run_id = ?"
        )
        args: tuple[Any, ...] = (run_id,)
        if billing:
            sql += " AND billing = ?"
            args = (run_id, billing)
        with self.connect() as c:
            row = c.execute(sql, args).fetchone()
        return {"tokens": float(row["tokens"]), "cost": float(row["cost"]), "calls": row["calls"]}

    def api_calls_since(self, since: float) -> int:
        """Gateway calls (all runs) since a timestamp: what the shared free pool has used."""
        with self.connect() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM model_call WHERE billing = 'api' AND at >= ?", (since,)
            ).fetchone()
        return int(row["n"])

    def api_calls_by_model_since(self, since: float) -> dict[str, int]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT requested_model AS m, COUNT(*) AS n FROM model_call "
                "WHERE billing = 'api' AND at >= ? GROUP BY requested_model",
                (since,),
            ).fetchall()
        return {str(r["m"]): int(r["n"]) for r in rows}

    def call_outcomes_since(
        self, since: float, role: str | None = None
    ) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
        """Gateway calls since a timestamp, for the router's reliability stats: successful
        calls per model, and (model, outcome, error) of every failed one."""
        with self.connect() as c:
            role_filter = " AND role = ?" if role else ""
            params: tuple[object, ...] = (since, role) if role else (since,)
            ok = c.execute(
                "SELECT requested_model AS m, COUNT(*) AS n FROM model_call "
                "WHERE billing = 'api' AND at >= ? AND outcome = 'ok'"
                + role_filter
                + " GROUP BY requested_model",
                params,
            ).fetchall()
            failed = c.execute(
                "SELECT requested_model AS m, outcome, COALESCE(error, '') AS error "
                "FROM model_call WHERE billing = 'api' AND at >= ? AND outcome != 'ok'"
                + role_filter,
                params,
            ).fetchall()
        return (
            {str(r["m"]): int(r["n"]) for r in ok},
            [(str(r["m"]), str(r["outcome"]), str(r["error"])) for r in failed],
        )

    def reviewed_work_since(self, since: float) -> list[tuple[dict[str, Any], str]]:
        """(review output, model that wrote the reviewed change) for each finished review
        since a timestamp. The author is the model with most successful calls in the last
        work step of the same run and iteration: the attempt the review actually saw."""
        with self.connect() as c:
            reviews = c.execute(
                "SELECT id, run_id, iteration, output_json FROM step WHERE kind = 'review' "
                "AND status = 'done' AND output_json IS NOT NULL AND started_at >= ? "
                "ORDER BY id DESC",
                (since,),
            ).fetchall()
            out: list[tuple[dict[str, Any], str]] = []
            seen: set[int] = set()
            for r in reviews:
                try:
                    review = json.loads(r["output_json"])
                except ValueError:
                    continue
                if not isinstance(review, dict) or review.get("independent") is not True:
                    continue
                work_id = review.get("work_step_id")
                if not isinstance(work_id, int) or work_id in seen:
                    continue
                work = c.execute(
                    "SELECT * FROM step WHERE id = ? AND run_id = ? AND iteration = ? "
                    "AND kind = 'work' AND status = 'done'",
                    (work_id, r["run_id"], r["iteration"]),
                ).fetchone()
                if not work or work["actor"] in ("freebuff", "external:freebuff"):
                    continue
                author_models = {
                    str(row["requested_model"])
                    for row in c.execute(
                        "SELECT DISTINCT requested_model FROM model_call WHERE step_id = ? "
                        "AND billing = 'api' AND outcome = 'ok'",
                        (work_id,),
                    )
                }
                reviewer_models = {
                    str(row["requested_model"])
                    for row in c.execute(
                        "SELECT DISTINCT requested_model FROM model_call WHERE step_id = ? "
                        "AND billing = 'api' AND outcome = 'ok'",
                        (r["id"],),
                    )
                }

                def identities(step_id: int) -> set[str] | None:
                    calls = c.execute(
                        "SELECT actual_model FROM model_call WHERE step_id = ? "
                        "AND billing = 'api' AND outcome = 'ok'",
                        (step_id,),
                    ).fetchall()
                    values = [model_identity(row["actual_model"]) for row in calls]
                    if not values or any(v is None for v in values):
                        return None
                    return {v for v in values if v is not None}

                author_actual, reviewer_actual = identities(work_id), identities(r["id"])

                if (
                    not author_models
                    or not reviewer_models
                    or author_models & reviewer_models
                    or not author_actual
                    or not reviewer_actual
                    or author_actual & reviewer_actual
                ):
                    continue
                seen.add(work_id)
                author = c.execute(
                    "SELECT requested_model AS m, COUNT(*) AS n FROM model_call "
                    "WHERE outcome = 'ok' AND billing = 'api' AND step_id = ? "
                    "GROUP BY requested_model ORDER BY n DESC, MAX(id) DESC LIMIT 1",
                    (work_id,),
                ).fetchone()
                if author:
                    spec = c.execute(
                        "SELECT task.spec_json FROM task JOIN run ON run.task_id = task.id "
                        "WHERE run.id = ?",
                        (r["run_id"],),
                    ).fetchone()
                    review["expected_criteria"] = json.loads(spec["spec_json"]).get(
                        "acceptance_criteria", []
                    )
                    out.append((review, str(author["m"])))
        return out

    def overview(self, since: float) -> dict[str, Any]:
        """Read-only totals over runs created since a timestamp, for the measurement protocol
        (docs/PLAN.md): outcome, time to accepted, fix rounds, consumption, decisions."""
        with self.connect() as c:
            runs = c.execute(
                "SELECT id, state, iteration, created_at, finished_at FROM run "
                "WHERE created_at >= ?",
                (since,),
            ).fetchall()
            usage = c.execute(
                "SELECT model_call.billing AS billing, COUNT(*) AS calls, "
                "SUM(model_call.outcome != 'ok') AS failed, "
                "COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens, "
                "COALESCE(SUM(cost_usd), 0) AS cost "
                "FROM model_call JOIN run ON run.id = model_call.run_id "
                "WHERE run.created_at >= ? GROUP BY model_call.billing",
                (since,),
            ).fetchall()
            accepted_tokens = c.execute(
                "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens, "
                "COUNT(DISTINCT model_call.run_id) AS runs "
                "FROM model_call JOIN run ON run.id = model_call.run_id "
                "WHERE run.created_at >= ? AND run.state = 'accepted' "
                "AND model_call.billing = 'api'",
                (since,),
            ).fetchone()
            # Decisions Tomáš made himself; the automatic draft approval is not one.
            decisions = c.execute(
                "SELECT COUNT(*) AS n FROM event JOIN run ON run.id = event.run_id "
                "WHERE run.created_at >= ? AND event.kind = 'decision' "
                "AND event.message != ?",
                (since, AUTO_APPROVED),
            ).fetchone()
            # Work an external agent did by hand: Studio does not know what it used.
            external = c.execute(
                "SELECT COUNT(DISTINCT model_call.run_id) AS n FROM model_call "
                "JOIN run ON run.id = model_call.run_id "
                "WHERE run.created_at >= ? AND model_call.billing = 'external'",
                (since,),
            ).fetchone()
            applied = c.execute(
                "SELECT COUNT(DISTINCT event.run_id) AS n FROM event "
                "JOIN run ON run.id = event.run_id "
                "WHERE run.created_at >= ? AND event.kind = 'applied'",
                (since,),
            ).fetchone()
            models = c.execute(
                "SELECT requested_model AS model, COUNT(*) AS calls, "
                "SUM(outcome = 'ok') AS ok FROM model_call JOIN run ON run.id = model_call.run_id "
                "WHERE run.created_at >= ? AND model_call.billing = 'api' "
                "GROUP BY requested_model ORDER BY calls DESC, model LIMIT 5",
                (since,),
            ).fetchall()
        by_state: dict[str, int] = {}
        for r in runs:
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        accepted = [r for r in runs if r["state"] == "accepted"]
        finished = len(accepted) + by_state.get("abandoned", 0)
        durations = sorted(
            float(r["finished_at"]) - float(r["created_at"]) for r in accepted if r["finished_at"]
        )
        median: float | None = None
        if durations:
            mid = len(durations) // 2
            median = (
                durations[mid] if len(durations) % 2 else (durations[mid - 1] + durations[mid]) / 2
            )
        rounds = [int(r["iteration"]) - 1 for r in accepted]
        spend = {
            str(u["billing"]): {
                "calls": int(u["calls"]),
                "failed": int(u["failed"] or 0),
                "tokens": int(u["tokens"]),
                "cost": float(u["cost"]),
            }
            for u in usage
        }
        empty = {"calls": 0, "failed": 0, "tokens": 0, "cost": 0.0}
        return {
            "since": since,
            "runs": len(runs),
            "by_state": by_state,
            "accepted": len(accepted),
            "finished": finished,
            "accept_rate": len(accepted) / finished if finished else None,
            "median_seconds_to_accepted": median,
            "fix_rounds_avg": sum(rounds) / len(rounds) if rounds else None,
            "first_try": sum(1 for n in rounds if n == 0),
            "decisions": int(decisions["n"]),
            "applied": int(applied["n"]),
            "external_runs": int(external["n"]),
            "api": spend.get("api", empty),
            "subscription": spend.get("subscription", empty),
            # Gateway tokens only, over accepted runs that made gateway calls: lead CLI use on
            # a subscription and external agents are reported apart, not averaged in.
            "api_tokens_per_accepted": (
                int(accepted_tokens["tokens"]) / int(accepted_tokens["runs"])
                if accepted_tokens["runs"]
                else None
            ),
            "top_models": [
                {"model": str(m["model"]), "calls": int(m["calls"]), "ok": int(m["ok"] or 0)}
                for m in models
            ],
        }

    # --- conversation & live agent output ----------------------------------------------

    def message(self, run_id: str, author: str, text: str, data: Any = None) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO message (run_id, at, author, text, data_json) VALUES (?,?,?,?,?)",
                (
                    run_id,
                    time.time(),
                    author,
                    safe_text(text),
                    json.dumps(safe_value(data), ensure_ascii=False) if data is not None else None,
                ),
            )
            return int(cur.lastrowid or 0)

    def messages(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM message WHERE run_id = ? AND id > ? ORDER BY id", (run_id, after_id)
            )
            return [dict(r) for r in rows.fetchall()]

    def agent_event(
        self, run_id: str, step_id: int, lead: str, kind: str, text: str, data: Any = None
    ) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO agent_event (run_id, step_id, lead, at, kind, text, data_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    step_id,
                    lead,
                    time.time(),
                    kind,
                    safe_text(text),
                    json.dumps(safe_value(data), ensure_ascii=False) if data is not None else None,
                ),
            )

    def agent_events(
        self, run_id: str, after_id: int = 0, limit: int = 2000
    ) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM agent_event WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                (run_id, after_id, limit),
            )
            return [dict(r) for r in rows.fetchall()]

    # --- settings ---------------------------------------------------------------------

    def setting(self, key: str) -> Any:
        with self.connect() as c:
            row = c.execute("SELECT value_json FROM setting WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else None

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET "
                "value_json = excluded.value_json",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # --- cooldowns (router state; persisted so a restart does not forget a dead provider)

    def set_cooldown(self, model: str, until: float, reason: str) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO cooldown VALUES (?,?,?) ON CONFLICT(model) DO UPDATE SET "
                "until = excluded.until, reason = excluded.reason",
                (model, until, reason),
            )

    def clear_cooldown(self, model: str) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM cooldown WHERE model = ?", (model,))

    def cooldowns(self) -> dict[str, float]:
        with self.connect() as c:
            rows = c.execute("SELECT model, until FROM cooldown WHERE until > ?", (time.time(),))
            return {r["model"]: float(r["until"]) for r in rows.fetchall()}

    def cooldown_reasons(self) -> dict[str, str]:
        """Why each active cooldown started ("setup: 502: Workers AI requires …")."""
        with self.connect() as c:
            rows = c.execute("SELECT model, reason FROM cooldown WHERE until > ?", (time.time(),))
            return {r["model"]: str(r["reason"]) for r in rows.fetchall()}

    # --- recovery ---------------------------------------------------------------------

    def recover(self) -> list[str]:
        """Mark everything that was running when the process died as interrupted.

        Nothing is resumed automatically: the user decides, so no step repeats on its own.
        """
        active = ("queued", "planning", "working", "verifying", "reviewing")
        with self.connect() as c:
            now = time.time()
            c.execute(
                "UPDATE step SET status = 'interrupted', finished_at = ?, "
                "error = COALESCE(error, 'process stopped while step was running') "
                "WHERE status = 'running'",
                (now,),
            )
            rows = c.execute(
                f"SELECT id FROM run WHERE state IN ({','.join('?' * len(active))}) "
                "AND state != 'queued'",
                active,
            ).fetchall()
            ids = [str(r["id"]) for r in rows]
            for rid in ids:
                c.execute(
                    "UPDATE run SET state = 'interrupted', updated_at = ? WHERE id = ?", (now, rid)
                )
        for rid in ids:
            self.event(rid, "interrupted", "Běh přerušen restartem; čeká na pokračování")
        return ids
