"""SQLite-backed session store for Probe session history.

Stores metadata for each debug session including verdict, root cause,
trace file paths, and timestamps. Provides indexed lookups for fast
retrieval of session records.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


@dataclass
class SessionRecord:
    """A single debug session record stored in SQLite."""

    session_id: str
    created_at: str
    verdict: str
    root_cause: str
    trace_path: str
    html_path: str
    iterations: int = 0
    events_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "verdict": self.verdict,
            "root_cause": self.root_cause,
            "trace_path": self.trace_path,
            "html_path": self.html_path,
            "iterations": self.iterations,
            "events_count": self.events_count,
        }


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    verdict TEXT NOT NULL DEFAULT 'inconclusive',
    root_cause TEXT NOT NULL DEFAULT '',
    trace_path TEXT NOT NULL DEFAULT '',
    html_path TEXT NOT NULL DEFAULT '',
    iterations INTEGER NOT NULL DEFAULT 0,
    events_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_created_at
    ON sessions(created_at);

CREATE INDEX IF NOT EXISTS idx_sessions_verdict
    ON sessions(verdict);

CREATE INDEX IF NOT EXISTS idx_sessions_trace_path
    ON sessions(trace_path);
"""


class SessionStore:
    """SQLite storage for Probe session metadata.

    Thread-safe (each operation acquires its own connection).  Provides
    CRUD operations and lookup methods indexed on trace paths for fast
    retrieval.

    Usage:
        store = SessionStore(db_path="probe_traces/sessions.db")
        store.save_session(record)
        sessions = store.list_sessions()
    """

    def __init__(self, db_path: str | Path = "probe_traces/sessions.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create the schema if it does not already exist."""
        with self._get_conn() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        """Get a new SQLite connection (context manager)."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def save_session(
        self,
        session_id: str = "",
        verdict: str = "inconclusive",
        root_cause: str = "",
        trace_path: str = "",
        html_path: str = "",
        iterations: int = 0,
        events_count: int = 0,
    ) -> str:
        """Save or update a session record. Returns the session_id."""
        if not session_id:
            session_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (session_id, created_at, verdict, root_cause, trace_path,
                    html_path, iterations, events_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       verdict = excluded.verdict,
                       root_cause = excluded.root_cause,
                       trace_path = excluded.trace_path,
                       html_path = excluded.html_path,
                       iterations = excluded.iterations,
                       events_count = excluded.events_count""",
                (session_id, created_at, verdict, root_cause,
                 trace_path, html_path, iterations, events_count),
            )
            conn.commit()

        return session_id

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        """Retrieve a single session by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            created_at=row["created_at"],
            verdict=row["verdict"],
            root_cause=row["root_cause"],
            trace_path=row["trace_path"],
            html_path=row["html_path"],
            iterations=row["iterations"],
            events_count=row["events_count"],
        )

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[SessionRecord]:
        """List recent sessions, newest first."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            SessionRecord(
                session_id=row["session_id"],
                created_at=row["created_at"],
                verdict=row["verdict"],
                root_cause=row["root_cause"],
                trace_path=row["trace_path"],
                html_path=row["html_path"],
                iterations=row["iterations"],
                events_count=row["events_count"],
            )
            for row in rows
        ]

    def find_by_trace_path(self, trace_path: str) -> Optional[SessionRecord]:
        """Find a session by its trace JSONL file path (indexed lookup)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE trace_path = ?",
                (trace_path,),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            created_at=row["created_at"],
            verdict=row["verdict"],
            root_cause=row["root_cause"],
            trace_path=row["trace_path"],
            html_path=row["html_path"],
            iterations=row["iterations"],
            events_count=row["events_count"],
        )

    def count_sessions(self) -> int:
        """Return the total number of stored sessions."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM sessions").fetchone()
        return row["cnt"] if row else 0

    def delete_session(self, session_id: str) -> bool:
        """Delete a session record. Returns True if deleted."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
        return cursor.rowcount > 0

    def get_verdict_counts(self) -> dict[str, int]:
        """Return count of sessions grouped by verdict."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT verdict, COUNT(*) as cnt FROM sessions GROUP BY verdict"
            ).fetchall()
        return {row["verdict"]: row["cnt"] for row in rows}
