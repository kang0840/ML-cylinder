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
 boot_id TEXT NOT NULL DEFAULT 'legacy',
 sensor_type TEXT NOT NULL, measured_at TEXT NOT NULL, sample_rate INTEGER NOT NULL,
 cylinder_state TEXT NOT NULL, sequence INTEGER NOT NULL, sample_count INTEGER NOT NULL,
 health_score_target REAL, condition_target TEXT,
 experiment_id TEXT, session_id TEXT, pressure_mpa REAL, load_kg REAL,
 ground_truth TEXT, ground_truth_source TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(device_id, boot_id, sensor_type, sequence)
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
CREATE TABLE IF NOT EXISTS combined_results (
 id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
 sequence INTEGER NOT NULL, measured_at TEXT NOT NULL,
 vibration_measurement_id TEXT NOT NULL, sound_measurement_id TEXT NOT NULL,
 prediction TEXT NOT NULL, confidence REAL NOT NULL, health_score REAL NOT NULL,
 remaining_life_percent REAL,
 remaining_hours REAL, remaining_cycles REAL,
 rul_lower_bound REAL, rul_upper_bound REAL,
 rul_status TEXT NOT NULL DEFAULT 'insufficient_lifecycle_data',
 rul_model_version TEXT,
 controlling_role TEXT NOT NULL, fusion_version TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(device_id, boot_id, sequence),
 FOREIGN KEY(vibration_measurement_id) REFERENCES measurements(measurement_id) ON DELETE CASCADE,
 FOREIGN KEY(sound_measurement_id) REFERENCES measurements(measurement_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS upload_queue (
 id INTEGER PRIMARY KEY, measurement_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 retry_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 measured_at TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_measurements_time ON measurements(measured_at);
CREATE INDEX IF NOT EXISTS idx_features_device_time ON feature_data(device_id, measured_at);
CREATE INDEX IF NOT EXISTS idx_combined_device_time ON combined_results(device_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_upload_priority ON upload_queue(retry_count, id);
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
            self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Upgrade databases created by earlier Raspberry Pi releases."""
        measurement_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(measurements)")
        }
        if "health_score_target" not in measurement_columns:
            self._connection.execute(
                "ALTER TABLE measurements ADD COLUMN health_score_target REAL"
            )
        if "condition_target" not in measurement_columns:
            self._connection.execute(
                "ALTER TABLE measurements ADD COLUMN condition_target TEXT"
            )
        if "boot_id" not in measurement_columns:
            self._connection.execute(
                "ALTER TABLE measurements ADD COLUMN boot_id TEXT NOT NULL DEFAULT 'legacy'"
            )
        for name, sql_type in (
            ("experiment_id","TEXT"),("session_id","TEXT"),
            ("pressure_mpa","REAL"),("load_kg","REAL"),
            ("ground_truth","TEXT"),("ground_truth_source","TEXT"),
        ):
            if name not in measurement_columns:
                self._connection.execute(f"ALTER TABLE measurements ADD COLUMN {name} {sql_type}")
        self._connection.commit()
        table_sql = self._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='measurements'"
        ).fetchone()["sql"]
        compact_sql = "".join(table_sql.lower().split())
        if "unique(device_id,sensor_type,sequence)" in compact_sql:
            self._rebuild_measurements_with_boot_id()
        upload_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(upload_queue)")
        }
        if "measured_at" not in upload_columns:
            self._connection.execute(
                "ALTER TABLE upload_queue ADD COLUMN measured_at TEXT NOT NULL DEFAULT ''"
            )
        combined_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(combined_results)")
        }
        if "remaining_life_percent" not in combined_columns:
            self._connection.execute(
                "ALTER TABLE combined_results ADD COLUMN remaining_life_percent REAL NOT NULL DEFAULT 0"
            )
            self._connection.execute(
                "UPDATE combined_results SET remaining_life_percent=health_score"
            )
        for name, sql_type in (("remaining_hours","REAL"),("remaining_cycles","REAL"),("rul_lower_bound","REAL"),("rul_upper_bound","REAL"),("rul_status","TEXT NOT NULL DEFAULT 'insufficient_lifecycle_data'"),("rul_model_version","TEXT")):
            if name not in combined_columns:
                self._connection.execute(f"ALTER TABLE combined_results ADD COLUMN {name} {sql_type}")
        self._connection.commit()
        info = {row["name"]: row for row in self._connection.execute("PRAGMA table_info(combined_results)")}
        if info.get("remaining_life_percent") and info["remaining_life_percent"]["notnull"]:
            self._rebuild_combined_results_nullable()

    def _rebuild_combined_results_nullable(self) -> None:
        """기존 행을 보존하면서 과거의 NOT NULL RUL 열만 nullable로 바꾼다."""
        self._connection.execute("PRAGMA foreign_keys=OFF")
        try:
            self._connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE combined_results_v3 (
             id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
             sequence INTEGER NOT NULL, measured_at TEXT NOT NULL,
             vibration_measurement_id TEXT NOT NULL, sound_measurement_id TEXT NOT NULL,
             prediction TEXT NOT NULL, confidence REAL NOT NULL, health_score REAL NOT NULL,
             remaining_life_percent REAL, remaining_hours REAL, remaining_cycles REAL,
             rul_lower_bound REAL, rul_upper_bound REAL,
             rul_status TEXT NOT NULL DEFAULT 'insufficient_lifecycle_data', rul_model_version TEXT,
             controlling_role TEXT NOT NULL, fusion_version TEXT NOT NULL,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             UNIQUE(device_id,boot_id,sequence),
             FOREIGN KEY(vibration_measurement_id) REFERENCES measurements(measurement_id) ON DELETE CASCADE,
             FOREIGN KEY(sound_measurement_id) REFERENCES measurements(measurement_id) ON DELETE CASCADE
            );
            INSERT INTO combined_results_v3
            SELECT id,device_id,boot_id,sequence,measured_at,vibration_measurement_id,sound_measurement_id,
                   prediction,confidence,health_score,remaining_life_percent,remaining_hours,remaining_cycles,
                   rul_lower_bound,rul_upper_bound,COALESCE(rul_status,'insufficient_lifecycle_data'),rul_model_version,
                   controlling_role,fusion_version,created_at FROM combined_results;
            DROP TABLE combined_results;
            ALTER TABLE combined_results_v3 RENAME TO combined_results;
            CREATE INDEX idx_combined_device_time ON combined_results(device_id,measured_at DESC);
            COMMIT;
            """)
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        finally:
            self._connection.execute("PRAGMA foreign_keys=ON")

    def _rebuild_measurements_with_boot_id(self) -> None:
        self._connection.execute("PRAGMA foreign_keys=OFF")
        try:
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                DROP TABLE IF EXISTS measurements_v2;
                CREATE TABLE measurements_v2 (
                  id INTEGER PRIMARY KEY,
                  measurement_id TEXT NOT NULL UNIQUE,
                  device_id TEXT NOT NULL,
                  boot_id TEXT NOT NULL DEFAULT 'legacy',
                  sensor_type TEXT NOT NULL,
                  measured_at TEXT NOT NULL,
                  sample_rate INTEGER NOT NULL,
                  cylinder_state TEXT NOT NULL,
                  sequence INTEGER NOT NULL,
                  sample_count INTEGER NOT NULL,
                  health_score_target REAL,
                  condition_target TEXT,
                  experiment_id TEXT, session_id TEXT,
                  pressure_mpa REAL, load_kg REAL,
                  ground_truth TEXT, ground_truth_source TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(device_id, boot_id, sensor_type, sequence)
                );
                INSERT INTO measurements_v2(
                  id,measurement_id,device_id,boot_id,sensor_type,measured_at,
                  sample_rate,cylinder_state,sequence,sample_count,
                  health_score_target,condition_target,experiment_id,session_id,
                  pressure_mpa,load_kg,ground_truth,ground_truth_source,created_at
                )
                SELECT id,measurement_id,device_id,COALESCE(boot_id,'legacy'),
                       sensor_type,measured_at,sample_rate,cylinder_state,
                       sequence,sample_count,health_score_target,condition_target,
                       NULL,NULL,NULL,NULL,NULL,NULL,created_at
                FROM measurements;
                DROP TABLE measurements;
                ALTER TABLE measurements_v2 RENAME TO measurements;
                CREATE INDEX IF NOT EXISTS idx_measurements_time
                  ON measurements(measured_at);
                COMMIT;
                """
            )
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        finally:
            self._connection.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def save_measurement(self, packet: SensorPacket, features: dict[str, float], ml_result: dict[str, Any], measurement_id: str | None = None) -> str:
        measurement_id = measurement_id or str(uuid.uuid4())
        measured_at = datetime.fromtimestamp(packet.timestamp, timezone.utc).isoformat()
        try:
            with self.transaction() as connection:
                connection.execute(
                    "INSERT INTO measurements(measurement_id,device_id,boot_id,sensor_type,measured_at,sample_rate,cylinder_state,sequence,sample_count,health_score_target,condition_target,experiment_id,session_id,pressure_mpa,load_kg,ground_truth,ground_truth_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (measurement_id, packet.device_id, packet.boot_id, packet.sensor_type, measured_at, packet.sample_rate, packet.cylinder_state, packet.sequence, len(packet.samples), packet.health_score_target, packet.condition_target, packet.experiment_id, packet.session_id, packet.pressure_mpa, packet.load_kg, packet.ground_truth, packet.ground_truth_source),
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

    def measurement_exists(self, device_id: str, boot_id: str, sensor_type: str, sequence: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM measurements WHERE device_id=? AND boot_id=? AND sensor_type=? AND sequence=?",
                (device_id, boot_id, sensor_type, sequence),
            ).fetchone()
        return row is not None

    def paired_sensor_results(
        self, device_id: str, boot_id: str, sequence: int
    ) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.measurement_id,m.sensor_type,m.measured_at,
                       r.prediction,r.confidence,r.health_score,r.model_version
                FROM measurements AS m
                JOIN ml_results AS r ON r.measurement_id=m.measurement_id
                WHERE m.device_id=? AND m.boot_id=? AND m.sequence=?
                  AND m.sensor_type IN ('sph0645','inmp441')
                """,
                (device_id, boot_id, sequence),
            ).fetchall()
        return {str(row["sensor_type"]): dict(row) for row in rows}

    def save_combined_result(
        self,
        device_id: str,
        boot_id: str,
        sequence: int,
        vibration: dict[str, Any],
        sound: dict[str, Any],
        combined: dict[str, Any],
    ) -> None:
        measured_at = max(str(vibration["measured_at"]), str(sound["measured_at"]))
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO combined_results(
                  device_id,boot_id,sequence,measured_at,
                  vibration_measurement_id,sound_measurement_id,
                  prediction,confidence,health_score,remaining_life_percent,
                  remaining_hours,remaining_cycles,rul_lower_bound,rul_upper_bound,
                  rul_status,rul_model_version,controlling_role,fusion_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(device_id,boot_id,sequence) DO UPDATE SET
                  measured_at=excluded.measured_at,
                  vibration_measurement_id=excluded.vibration_measurement_id,
                  sound_measurement_id=excluded.sound_measurement_id,
                  prediction=excluded.prediction,
                  confidence=excluded.confidence,
                  health_score=excluded.health_score,
                  remaining_life_percent=excluded.remaining_life_percent,
                  remaining_hours=excluded.remaining_hours,
                  remaining_cycles=excluded.remaining_cycles,
                  rul_lower_bound=excluded.rul_lower_bound,
                  rul_upper_bound=excluded.rul_upper_bound,
                  rul_status=excluded.rul_status,
                  rul_model_version=excluded.rul_model_version,
                  controlling_role=excluded.controlling_role,
                  fusion_version=excluded.fusion_version
                """,
                (
                    device_id,
                    boot_id,
                    sequence,
                    measured_at,
                    vibration["measurement_id"],
                    sound["measurement_id"],
                    combined["prediction"],
                    combined["confidence"],
                    combined["health_score"],
                    combined["remaining_life_percent"],
                    combined.get("remaining_hours"),
                    combined.get("remaining_cycles"),
                    combined.get("rul_lower_bound"),
                    combined.get("rul_upper_bound"),
                    combined.get("rul_status", "insufficient_lifecycle_data"),
                    combined.get("rul_model_version"),
                    combined["controlling_role"],
                    combined["fusion_version"],
                ),
            )

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

    def last_sequence(self, device_id: str, boot_id: str, sensor_type: str) -> int | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT MAX(sequence) AS value FROM measurements WHERE device_id=? AND boot_id=? AND sensor_type=?",
                (device_id, boot_id, sensor_type),
            ).fetchone()
        return None if row["value"] is None else int(row["value"])

    def enqueue_upload(self, measurement_id: str, payload: dict[str, Any], error: str = "") -> None:
        measured_at = str(payload.get("measured_at", ""))
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO upload_queue(measurement_id,payload,last_error,measured_at) VALUES(?,?,?,?) ON CONFLICT(measurement_id) DO UPDATE SET payload=excluded.payload,last_error=excluded.last_error,measured_at=excluded.measured_at,updated_at=CURRENT_TIMESTAMP",
                (measurement_id, json.dumps(payload, ensure_ascii=False), error[:1000], measured_at),
            )

    def queued_uploads(self, limit: int = 50) -> list[sqlite3.Row]:
        """Return fresh results newest-first, then failed backlog oldest-first."""
        with self._lock:
            return list(
                self._connection.execute(
                    """
                    SELECT * FROM upload_queue
                    ORDER BY
                      CASE WHEN retry_count=0 THEN 0 ELSE 1 END,
                      CASE WHEN retry_count=0 THEN id END DESC,
                      CASE WHEN retry_count>0 THEN measured_at END ASC,
                      id ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            )

    def remove_upload(self, queue_id: int) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM upload_queue WHERE id=?", (queue_id,))

    def mark_upload_failed(self, queue_id: int, error: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE upload_queue SET retry_count=retry_count+1,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (error[:1000], queue_id))

    def close(self) -> None:
        with self._lock:
            self._connection.close()
