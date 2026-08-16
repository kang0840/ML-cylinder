import sqlite3
import pytest
from src.data_validator import SensorPacket
from src.database import Database, DuplicateMeasurementError
from src.ingest_database import IngestDatabase
from src.feature_extractor import extract_features
from src.ml_predictor import MLPredictor

def test_save_is_atomic_and_duplicate_safe(tmp_path):
    db = Database(tmp_path/"test.db")
    packet = SensorPacket("pico01","sph0645",100,"idle",1,1785830000,(1.,2.,3.,4.))
    features = extract_features(packet.samples, packet.sample_rate)
    result = MLPredictor(tmp_path/"none.pkl").predict(features, packet.samples, packet.sensor_type)
    db.save_measurement(packet, features, result)
    with pytest.raises(DuplicateMeasurementError): db.save_measurement(packet, features, result)
    with sqlite3.connect(tmp_path/"test.db") as con:
        assert con.execute("select count(*) from measurements").fetchone()[0] == 1
        assert con.execute("select count(*) from raw_sensor_data").fetchone()[0] == 4
    db.close()

def test_ground_truth_and_prediction_are_stored_separately(tmp_path):
    db=Database(tmp_path/"labels.db")
    packet=SensorPacket("pico01","inmp441",100,"extending",1,1785830000,(1.,2.,3.,4.),None,"boot",None,"seal_leak_01","session_01",0.5,0.0,"seal_leak","controlled_experiment")
    features=extract_features(packet.samples,packet.sample_rate)
    result={"prediction":"normal","confidence":0.9,"health_score":80,"model_version":"model-v1"}
    db.save_measurement(packet,features,result)
    with sqlite3.connect(tmp_path/"labels.db") as con:
        truth=con.execute("select ground_truth from measurements").fetchone()[0]
        prediction=con.execute("select prediction from ml_results").fetchone()[0]
        assert (truth,prediction)==("seal_leak","normal")
    db.close()


def test_ingest_queue_is_durable_and_claims_latest_first(tmp_path):
    path = tmp_path / "ingest.db"
    db = IngestDatabase(path)
    for sequence in (1, 2, 3):
        assert db.enqueue({
            "device_id": "pico01", "boot_id": "boot-a",
            "sensor_id": "dual_i2s_mic", "sequence": sequence,
            "timestamp": 1785830000 + sequence,
        })
    assert not db.enqueue({
        "device_id": "pico01", "boot_id": "boot-a",
        "sensor_id": "dual_i2s_mic", "sequence": 3,
        "timestamp": 1785830003,
    })
    newest = db.claim_next()
    assert newest["sequence"] == 3
    db.close()

    reopened = IngestDatabase(path)
    # A packet left in processing state is safely recovered after restart.
    recovered = reopened.claim_next()
    assert recovered["sequence"] == 3
    reopened.complete(recovered["id"])
    assert reopened.claim_next()["sequence"] == 2
    reopened.close()


def test_analysis_and_ingest_are_separate_and_legacy_pending_is_imported(tmp_path):
    analysis_path = tmp_path / "analysis.db"
    analysis = Database(analysis_path)
    analysis.close()
    with sqlite3.connect(analysis_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingest_queue'"
        ).fetchone() is None
        connection.executescript("""
            CREATE TABLE ingest_queue (
              id INTEGER PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL
            );
            INSERT INTO ingest_queue(payload,status) VALUES(
              '{"device_id":"pico01","boot_id":"boot-a","sensor_id":"dual_i2s_mic","sequence":7,"timestamp":1785830007}',
              'pending'
            );
        """)

    ingest = IngestDatabase(tmp_path / "raw_ingest.db")
    assert ingest.import_legacy_pending(analysis_path) == 1
    assert ingest.claim_next()["sequence"] == 7
    assert ingest.import_legacy_pending(analysis_path) == 0
    ingest.close()


def test_same_sequence_from_different_boots_is_not_a_duplicate(tmp_path):
    db = Database(tmp_path / "boots.db")
    predictor = MLPredictor(tmp_path / "none.pkl")
    for boot_id in ("boot-a", "boot-b"):
        packet = SensorPacket(
            "pico01", "sph0645", 100, "idle", 1, 1785830000,
            (1.0, 2.0, 3.0, 4.0), None, boot_id,
        )
        features = extract_features(packet.samples, packet.sample_rate)
        db.save_measurement(packet, features, predictor.predict(features, packet.samples, packet.sensor_type))
    with sqlite3.connect(tmp_path / "boots.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 2
    db.close()


def test_upload_queue_prioritizes_fresh_latest_then_failed_oldest(tmp_path):
    db = Database(tmp_path / "upload.db")
    db.enqueue_upload("old-failed", {"measured_at": "2026-08-01T00:00:00+00:00"})
    old = db.queued_uploads(1)[0]
    db.mark_upload_failed(old["id"], "offline")
    db.enqueue_upload("fresh-1", {"measured_at": "2026-08-02T00:00:00+00:00"})
    db.enqueue_upload("fresh-2", {"measured_at": "2026-08-03T00:00:00+00:00"})
    rows = db.queued_uploads(3)
    assert [row["measurement_id"] for row in rows] == [
        "fresh-2", "fresh-1", "old-failed"
    ]
    db.close()


def test_legacy_sequence_constraint_is_migrated_to_boot_aware_key(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE measurements (
              id INTEGER PRIMARY KEY, measurement_id TEXT NOT NULL UNIQUE,
              device_id TEXT NOT NULL, sensor_type TEXT NOT NULL,
              measured_at TEXT NOT NULL, sample_rate INTEGER NOT NULL,
              cylinder_state TEXT NOT NULL, sequence INTEGER NOT NULL,
              sample_count INTEGER NOT NULL, health_score_target REAL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(device_id, sensor_type, sequence)
            );
            CREATE TABLE raw_sensor_data (
              id INTEGER PRIMARY KEY, measurement_id TEXT NOT NULL,
              sample_index INTEGER NOT NULL, raw_value REAL NOT NULL,
              FOREIGN KEY(measurement_id) REFERENCES measurements(measurement_id)
                ON DELETE CASCADE,
              UNIQUE(measurement_id, sample_index)
            );
            INSERT INTO measurements(
              measurement_id,device_id,sensor_type,measured_at,sample_rate,
              cylinder_state,sequence,sample_count
            ) VALUES('legacy-1','pico01','sph0645','2026-08-01T00:00:00+00:00',
                     100,'idle',1,1);
            INSERT INTO raw_sensor_data(measurement_id,sample_index,raw_value)
              VALUES('legacy-1',0,1.0);
        """)

    db = Database(path)
    predictor = MLPredictor(tmp_path / "none.pkl")
    packet = SensorPacket(
        "pico01", "sph0645", 100, "idle", 1, 1785830000,
        (1.0, 2.0, 3.0, 4.0), None, "new-boot",
    )
    features = extract_features(packet.samples, packet.sample_rate)
    db.save_measurement(packet, features, predictor.predict(features, packet.samples, packet.sensor_type))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()
