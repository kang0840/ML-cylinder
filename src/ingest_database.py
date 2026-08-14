"""Durable MQTT ingest queue stored separately from analysis results."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


INGEST_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS ingest_queue (
 id INTEGER PRIMARY KEY, packet_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 device_id TEXT NOT NULL, boot_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 measured_at TEXT NOT NULL, received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 status TEXT NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0,
 last_error TEXT, processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_pending ON ingest_queue(status, id DESC);
"""


class IngestDatabase:
    """Small, independent SQLite database for loss-resistant MQTT reception."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.execute("PRAGMA busy_timeout=30000")
            self._connection.executescript(INGEST_SCHEMA)
            # A process may have stopped after claiming but before completing a row.
            self._connection.execute(
                "UPDATE ingest_queue SET status='pending' WHERE status='processing'"
            )
            self._connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def enqueue(self, payload: dict[str, Any]) -> bool:
        """Persist an MQTT packet before FFT, inference, or network upload."""
        stored_payload = dict(payload)
        stored_payload.setdefault("timestamp", datetime.now(timezone.utc).timestamp())
        device_id = str(stored_payload.get("device_id", "unknown"))
        boot_id = str(stored_payload.get("boot_id", "legacy"))
        sequence = int(stored_payload.get("sequence", -1))
        stream = str(
            stored_payload.get("sensor_id")
            or stored_payload.get("sensor_type")
            or "unknown"
        )
        packet_key = f"{device_id}:{boot_id}:{stream}:{sequence}"
        measured_at = datetime.fromtimestamp(
            float(stored_payload["timestamp"]), timezone.utc
        ).isoformat()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO ingest_queue(
                  packet_key,payload,device_id,boot_id,sequence,measured_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    packet_key,
                    json.dumps(stored_payload, ensure_ascii=False),
                    device_id,
                    boot_id,
                    sequence,
                    measured_at,
                ),
            )
        return cursor.rowcount == 1

    def claim_next(self) -> sqlite3.Row | None:
        """Claim newest live data first while retaining older rows for later."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM ingest_queue WHERE status='pending' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE ingest_queue
                SET status='processing',attempt_count=attempt_count+1,last_error=NULL
                WHERE id=? AND status='pending'
                """,
                (row["id"],),
            )
            return row if updated.rowcount == 1 else None

    def complete(self, queue_id: int) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE ingest_queue SET status='done',processed_at=CURRENT_TIMESTAMP WHERE id=?",
                (queue_id,),
            )

    def fail(self, queue_id: int, error: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE ingest_queue SET status='error',last_error=?,processed_at=CURRENT_TIMESTAMP WHERE id=?",
                (error[:1000], queue_id),
            )

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status,COUNT(*) AS value FROM ingest_queue GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["value"]) for row in rows}

    def import_legacy_pending(self, analysis_path: Path) -> int:
        """Copy unfinished rows from the former single-database layout once."""
        if not analysis_path.exists() or analysis_path.resolve() == self.path.resolve():
            return 0
        source = sqlite3.connect(analysis_path, timeout=30)
        source.row_factory = sqlite3.Row
        try:
            table = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingest_queue'"
            ).fetchone()
            if table is None:
                return 0
            rows = source.execute(
                "SELECT payload FROM ingest_queue WHERE status IN ('pending','processing') ORDER BY id"
            ).fetchall()
        finally:
            source.close()
        return sum(self.enqueue(json.loads(row["payload"])) for row in rows)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
