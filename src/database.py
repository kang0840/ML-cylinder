"""스마트 실린더 SQLite 저장소."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .data_validator import SensorPacket

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS measurements (
 id INTEGER PRIMARY KEY, measurement_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL,
 sensor_type TEXT NOT NULL, measured_at TEXT NOT NULL, sample_rate INTEGER NOT NULL,
 cylinder_state TEXT NOT NULL, sequence INTEGER NOT NULL, sample_count INTEGER NOT NULL,
 health_score_target REAL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(device_id, sensor_type, sequence)
);
CREATE TABLE IF NOT EXISTS raw_sensor_data (
 id INTEGER PRIMARY KEY, measurement_id TEXT NOT NULL, sample_index INTEGER NOT NULL,
 raw_value REAL NOT NULL, FOREIGN KEY(measurement_id) REFERENCES measurements(measurement_id) ON DELETE CASCADE,
 UNIQUE(measurement_id, sample_index)
);
CREATE TABLE IF NOT EXISTS feature_data (
 id INTEGER PRIMARY KEY, measurement_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL,
 sensor_type TEXT NOT NULL, measured_at TEXT NOT NULL, cylinder_state TEXT NOT NULL,
 mean REAL NOT NULL, standard_deviation REAL NOT NULL, rms REAL NOT NULL,
 maximum REAL NOT NULL, minimum REAL NOT NULL, peak REAL NOT NULL, peak_to_peak REAL NOT NULL,
 crest_factor REAL NOT NULL, dominant_frequency REAL NOT NULL, dominant_amplitude REAL NOT NULL,
 spectral_energy REAL NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(measurement_id) REFERENCES measurements(measurement_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS ml_results (
 id INTEGER PRIMARY KEY, measurement_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL,
 measured_at TEXT NOT NULL, cylinder_state TEXT NOT NULL, prediction TEXT NOT NULL,
 confidence REAL NOT NULL, health_score REAL NOT NULL, model_version TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(measurement_id) REFERENCES measurements(measurement_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS upload_queue (
 id INTEGER PRIMARY KEY, measurement_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 retry_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_measurements_time ON measurements(measured_at);
CREATE INDEX IF NOT EXISTS idx_features_device_time ON feature_data(device_id, measured_at);
CREATE TABLE IF NOT EXISTS service_metadata (
 key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class DuplicateMeasurementError(ValueError):
    """동일 장치·센서·sequence가 이미 저장된 경우."""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.executescript(SCHEMA)
            columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(measurements)")}
            if "health_score_target" not in columns:
                self._connection.execute("ALTER TABLE measurements ADD COLUMN health_score_target REAL")
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

    def save_measurement(self, packet: SensorPacket, features: dict[str, float], ml_result: dict[str, Any]) -> str:
        measurement_id = str(uuid.uuid4())
        measured_at = datetime.fromtimestamp(packet.timestamp, timezone.utc).isoformat()
        try:
            with self.transaction() as connection:
                connection.execute(
                    "INSERT INTO measurements(measurement_id,device_id,sensor_type,measured_at,sample_rate,cylinder_state,sequence,sample_count,health_score_target) VALUES(?,?,?,?,?,?,?,?,?)",
                    (measurement_id, packet.device_id, packet.sensor_type, measured_at, packet.sample_rate, packet.cylinder_state, packet.sequence, len(packet.samples), packet.health_score_target),
                )
                connection.executemany(
                    "INSERT INTO raw_sensor_data(measurement_id,sample_index,raw_value) VALUES(?,?,?)",
                    ((measurement_id, index, value) for index, value in enumerate(packet.samples)),
                )
                connection.execute(
                    "INSERT INTO feature_data(measurement_id,device_id,sensor_type,measured_at,cylinder_state,mean,standard_deviation,rms,maximum,minimum,peak,peak_to_peak,crest_factor,dominant_frequency,dominant_amplitude,spectral_energy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (measurement_id, packet.device_id, packet.sensor_type, measured_at, packet.cylinder_state, *(features[name] for name in ("mean","standard_deviation","rms","maximum","minimum","peak","peak_to_peak","crest_factor","dominant_frequency","dominant_amplitude","spectral_energy"))),
                )
                connection.execute(
                    "INSERT INTO ml_results(measurement_id,device_id,measured_at,cylinder_state,prediction,confidence,health_score,model_version) VALUES(?,?,?,?,?,?,?,?)",
                    (measurement_id, packet.device_id, measured_at, packet.cylinder_state, ml_result["prediction"], ml_result["confidence"], ml_result["health_score"], ml_result["model_version"]),
                )
        except sqlite3.IntegrityError as exc:
            if "measurements.device_id" in str(exc) or "UNIQUE constraint" in str(exc):
                raise DuplicateMeasurementError("이미 처리한 device_id/sensor_type/sequence입니다.") from exc
            raise
        return measurement_id

    def training_progress(self) -> tuple[int, int]:
        """Return (last trained row id, number of newer labelled measurements)."""
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM service_metadata WHERE key='last_trained_measurement_id'"
            ).fetchone()
            last_id = int(row["value"]) if row else 0
            count = self._connection.execute(
                "SELECT COUNT(*) AS value FROM measurements WHERE id>? AND health_score_target IS NOT NULL",
                (last_id,),
            ).fetchone()["value"]
        return last_id, int(count)

    def labelled_training_data(self) -> tuple[list[dict[str, list[float]]], list[float], int]:
        """Load a consistent snapshot of all trusted labelled raw measurements."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT id,measurement_id,sensor_type,health_score_target FROM measurements "
                "WHERE health_score_target IS NOT NULL ORDER BY id"
            ).fetchall()
            samples: list[dict[str, list[float]]] = []
            targets: list[float] = []
            last_id = 0
            for row in rows:
                values = [float(value["raw_value"]) for value in self._connection.execute(
                    "SELECT raw_value FROM raw_sensor_data WHERE measurement_id=? ORDER BY sample_index",
                    (row["measurement_id"],),
                ).fetchall()]
                key = "vibration" if row["sensor_type"] == "sph0645" else "microphone"
                samples.append({key: values})
                targets.append(float(row["health_score_target"]))
                last_id = int(row["id"])
        return samples, targets, last_id

    def mark_training_complete(self, last_measurement_id: int) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO service_metadata(key,value) VALUES('last_trained_measurement_id',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
                (str(last_measurement_id),),
            )

    def last_sequence(self, device_id: str, sensor_type: str) -> int | None:
        with self._lock:
            row = self._connection.execute("SELECT MAX(sequence) AS value FROM measurements WHERE device_id=? AND sensor_type=?", (device_id, sensor_type)).fetchone()
        return None if row["value"] is None else int(row["value"])

    def enqueue_upload(self, measurement_id: str, payload: dict[str, Any], error: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO upload_queue(measurement_id,payload,last_error) VALUES(?,?,?) ON CONFLICT(measurement_id) DO UPDATE SET payload=excluded.payload,last_error=excluded.last_error,retry_count=upload_queue.retry_count+1,updated_at=CURRENT_TIMESTAMP",
                (measurement_id, json.dumps(payload, ensure_ascii=False), error[:1000]),
            )

    def queued_uploads(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute("SELECT * FROM upload_queue ORDER BY id LIMIT ?", (limit,)).fetchall())

    def remove_upload(self, queue_id: int) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM upload_queue WHERE id=?", (queue_id,))

    def mark_upload_failed(self, queue_id: int, error: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE upload_queue SET retry_count=retry_count+1,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (error[:1000], queue_id))

    def close(self) -> None:
        with self._lock:
            self._connection.close()
