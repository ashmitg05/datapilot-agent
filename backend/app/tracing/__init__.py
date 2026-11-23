"""Trace storage: one row per run, one row per step (SQLite, stdlib only).

ponytail: SQLite file; on Cloud Run it is per-instance and ephemeral. Point DATABASE_PATH at a
mounted volume, or swap for Postgres, if traces must survive restarts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from app.agent.state import AgentConfig, AgentState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    dataset TEXT, question TEXT, model TEXT, config TEXT,
    status TEXT, final TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    trace_id TEXT REFERENCES traces(id), idx INTEGER, thought TEXT, tool TEXT, input TEXT,
    ok INTEGER, output TEXT, error TEXT, duration_ms INTEGER,
    input_tokens INTEGER, output_tokens INTEGER, n_images INTEGER,
    PRIMARY KEY (trace_id, idx)
);
"""


class TraceStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = str(path or os.environ.get("DATABASE_PATH", "datapilot.sqlite3"))
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def save(self, state: AgentState, dataset: str, model: str, config: AgentConfig) -> str:
        trace_id = uuid.uuid4().hex
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO traces (id, dataset, question, model, config, status, final, summary)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_id,
                    dataset,
                    state.question,
                    model,
                    json.dumps(config.__dict__),
                    state.status,
                    json.dumps(state.final, default=str),
                    json.dumps(state.summary()),
                ),  # fmt: skip
            )
            self._conn.executemany(
                "INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        trace_id,
                        s.index,
                        s.thought,
                        s.tool,
                        json.dumps(s.input),
                        int(s.ok),
                        s.output,
                        s.error,
                        s.duration_ms,
                        s.input_tokens,
                        s.output_tokens,
                        len(s.images),
                    )  # fmt: skip
                    for s in state.steps
                ],
            )
        return trace_id

    def get(self, trace_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
            if row is None:
                return None
            steps = self._conn.execute(
                "SELECT * FROM steps WHERE trace_id = ? ORDER BY idx", (trace_id,)
            ).fetchall()
        trace = dict(row)
        for k in ("config", "final", "summary"):
            trace[k] = json.loads(trace[k]) if trace[k] else None
        trace["steps"] = [
            {**dict(s), "input": json.loads(s["input"]), "ok": bool(s["ok"])} for s in steps
        ]
        return trace
