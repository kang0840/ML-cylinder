import sqlite3

from src.database import Database
from src.ml_predictor import MLPredictor
from src.pipeline import SensorPipeline


class NetworkMustNotBeCalled:
    def upload_or_queue(self, *args, **kwargs):
        raise AssertionError("the processing path must not call Supabase")


def test_processing_saves_locally_and_only_enqueues_upload(tmp_path):
    path = tmp_path / "pipeline.db"
    database = Database(path)
    pipeline = SensorPipeline(
        database,
        MLPredictor(tmp_path / "missing.pkl"),
        NetworkMustNotBeCalled(),
        True,
    )
    measurement_id = pipeline.process({
        "device_id": "pico01",
        "boot_id": "boot-1",
        "sensor_type": "sph0645",
        "sample_rate": 24000,
        "cylinder_state": "idle",
        "sequence": 1,
        "timestamp": 1785830000,
        "samples": [1, 2, 3, 4],
    })
    assert measurement_id is not None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM upload_queue").fetchone()[0] == 1
    database.close()
