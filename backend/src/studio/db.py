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

SCHEMA_VERSION = 2

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

    # --- conversation & live agent output ----------------------------------------------

    def message(self, run_id: str, author: str, text: str, data: Any = None) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO message (run_id, at, author, text, data_json) VALUES (?,?,?,?,?)",
                (
                    run_id,
                    time.time(),
                    author,
                    text,
                    json.dumps(data, ensure_ascii=False) if data is not None else None,
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
                    text,
                    json.dumps(data, ensure_ascii=False) if data is not None else None,
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
