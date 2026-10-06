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

SCHEMA_VERSION = 1

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
CREATE TABLE IF NOT EXISTS cooldown (
    model TEXT PRIMARY KEY,
    until REAL NOT NULL,
    reason TEXT NOT NULL
);
"""


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
                    json.dumps(output, ensure_ascii=False) if output is not None else None,
                    error,
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
                    message,
                    json.dumps(data, ensure_ascii=False) if data is not None else None,
                ),
            )

    def events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM event WHERE run_id = ? AND id > ? ORDER BY id", (run_id, after_id)
            )
            return [dict(r) for r in rows.fetchall()]

    def record_model_call(self, **fields: Any) -> None:
        fields.setdefault("at", time.time())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self.connect() as c:
            c.execute(f"INSERT INTO model_call ({cols}) VALUES ({marks})", tuple(fields.values()))

    def model_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM model_call WHERE run_id = ? ORDER BY id", (run_id,))
            return [dict(r) for r in rows.fetchall()]

    def usage(self, run_id: str) -> dict[str, float]:
        with self.connect() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens, "
                "COALESCE(SUM(cost_usd), 0) AS cost, COUNT(*) AS calls "
                "FROM model_call WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return {"tokens": float(row["tokens"]), "cost": float(row["cost"]), "calls": row["calls"]}

    # --- cooldowns (router state; persisted so a restart does not forget a dead provider)

    def set_cooldown(self, model: str, until: float, reason: str) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO cooldown VALUES (?,?,?) ON CONFLICT(model) DO UPDATE SET "
                "until = excluded.until, reason = excluded.reason",
                (model, until, reason),
            )

    def cooldowns(self) -> dict[str, float]:
        with self.connect() as c:
            rows = c.execute("SELECT model, until FROM cooldown WHERE until > ?", (time.time(),))
            return {r["model"]: float(r["until"]) for r in rows.fetchall()}

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
