import sqlite3

from src.database import Database
from src.ml_predictor import MLPredictor
from src.pipeline import SensorPipeline
from src.state_fusion import fuse_sensor_results


class NoNetwork:
    pass


def test_fusion_uses_more_severe_sensor_result():
    result = fuse_sensor_results(
        {"prediction": "normal", "confidence": 0.8, "health_score": 90},
        {"prediction": "seal_leak", "confidence": 0.7, "health_score": 55},
    )
    assert result["prediction"] == "seal_leak"
    assert result["controlling_role"] == "sound"
    assert result["health_score"] == 55
    assert result["remaining_life_percent"] is None
    assert result["rul_status"] == "insufficient_lifecycle_data"


def test_dual_packet_runs_separate_inference_then_saves_combined_state(tmp_path):
    path = tmp_path / "fusion.db"
    database = Database(path)
    pipeline = SensorPipeline(
        database, MLPredictor(tmp_path / "missing.pkl"), NoNetwork(), True
    )
    pipeline.process({
        "device_id": "pico01",
        "boot_id": "boot-a",
        "sensor_id": "dual_i2s_mic",
        "sequence": 10,
        "timestamp": 1785830000,
        "sample_rate_hz": 24000,
        "cylinder_state": "idle",
        "left_samples": [1, 2, 3, 4],
        "right_samples": [4, 3, 2, 1],
    })
    with sqlite3.connect(path) as connection:
        versions = {
            row[0] for row in connection.execute(
                "SELECT model_version FROM ml_results"
            ).fetchall()
        }
        assert versions == {
            "fallback-acoustic_vibration-threshold-v1",
            "fallback-sound-threshold-v1",
        }
        combined = connection.execute(
            "SELECT prediction,controlling_role,fusion_version,remaining_life_percent FROM combined_results"
        ).fetchone()
        assert combined is not None
        assert combined[2] == "vibration-sound-conservative-v1"
        assert combined[3] is None
    database.close()
