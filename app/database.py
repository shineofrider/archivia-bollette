from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    message_id TEXT,
                    uid TEXT,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
                CREATE TABLE IF NOT EXISTS documents (
                    sha256 TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    period TEXT NOT NULL,
                    path TEXT NOT NULL,
                    source_name TEXT,
                    message_key TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS test_force_claims (
                    message_key TEXT PRIMARY KEY,
                    claimed_at TEXT NOT NULL
                );
                """
            )

    def message_status(self, message_key: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT status FROM messages WHERE message_key=?", (message_key,)).fetchone()
            return str(row["status"]) if row else None

    def claim_test_force(self, message_key: str) -> bool:
        """Allow TEST_FORCE_REPROCESS to bypass dedup/retry checks only once per message."""
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO test_force_claims(message_key, claimed_at) VALUES(?, ?)",
                (message_key, utc_now()),
            )
            return cursor.rowcount == 1

    def retry_due(self, message_key: str, retry_seconds: int) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT status, last_attempt FROM messages WHERE message_key=?", (message_key,)
            ).fetchone()
        if row is None:
            return True
        if row["status"] == "done":
            return False
        if not row["last_attempt"]:
            return True
        try:
            last = datetime.fromisoformat(row["last_attempt"])
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        except ValueError:
            return True
        return elapsed >= retry_seconds

    def set_message(
        self, message_key: str, provider: str, status: str, details: str = "",
        message_id: str = "", uid: str = "",
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO messages(message_key, provider, message_id, uid, status, attempts, last_attempt, details, created_at, updated_at)
                   VALUES(?,?,?,?,?,1,?,?,?,?)
                   ON CONFLICT(message_key) DO UPDATE SET
                     status=excluded.status,
                     attempts=messages.attempts+1,
                     last_attempt=excluded.last_attempt,
                     details=excluded.details,
                     updated_at=excluded.updated_at""",
                (message_key, provider, message_id, uid, status, now, details[:1000], now, now),
            )

    def add_document(
        self, digest: str, provider: str, period: str, path: str, source_name: str, message_key: str
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO documents(sha256, provider, period, path, source_name, message_key, created_at) VALUES(?,?,?,?,?,?,?)",
                (digest, provider, period, path, source_name, message_key, utc_now()),
            )

    def find_document(self, digest: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE sha256=?", (digest,)).fetchone()
            return dict(row) if row else None
