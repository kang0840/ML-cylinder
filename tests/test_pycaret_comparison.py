import sqlite3
import pandas as pd
import pytest

from tools.compare_models_pycaret import (
    FEATURES, load_training_frame, validate_training_frame,
)


def test_load_training_frame_uses_only_labelled_rows(tmp_path):
    path = tmp_path / "training.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY, measurement_id TEXT, sensor_type TEXT,
                measured_at TEXT, condition_target TEXT
            );
            CREATE TABLE feature_data (
                measurement_id TEXT, mean REAL, standard_deviation REAL, rms REAL,
                maximum REAL, minimum REAL, peak REAL, peak_to_peak REAL,
                crest_factor REAL, dominant_frequency REAL,
                dominant_amplitude REAL, spectral_energy REAL
            );
        """)
        for row_id, target in ((1, "normal"), (2, None)):
            measurement_id = f"m-{row_id}"
            connection.execute(
                "INSERT INTO measurements VALUES(?,?,?,?,?)",
                (row_id, measurement_id, "sph0645", f"2026-08-0{row_id}T00:00:00+00:00", target),
            )
            connection.execute(
                "INSERT INTO feature_data VALUES(" + ",".join("?" for _ in range(12)) + ")",
                (measurement_id, *range(1, 12)),
            )
    frame = load_training_frame(path, "sph0645")
    assert list(frame.columns) == ["measured_at", *FEATURES, "condition_target"]
    assert len(frame) == 1
    assert frame.iloc[0]["condition_target"] == "normal"


def test_rejects_unlabelled_or_too_small_sensor_dataset(tmp_path):
    data = pd.DataFrame({"measured_at": [], "condition_target": []})
    with pytest.raises(ValueError, match="insufficient"):
        validate_training_frame(data, "sph0645", min_rows=30)
