import numpy as np
import pytest

from src.excel_dataset import (
    append_measurement, create_excel_dataset, load_training_data,
    load_zone_d_rms_threshold,
    train_model_from_excel,
)


def test_excel_round_trip_and_training(tmp_path):
    path = tmp_path / "training.xlsx"
    base_x = np.array([
        [0.80, 1.2, 1.5, 82, 30, 10], [0.81, 1.3, 1.6, 82, 31, 11],
        [0.82, 1.2, 1.5, 83, 30, 12], [0.83, 1.3, 1.6, 83, 31, 13],
        [1.40, 4.0, 2.8, 92, 64, 100], [1.41, 4.1, 2.9, 92, 65, 101],
        [1.42, 4.0, 2.8, 93, 64, 102], [1.43, 4.1, 2.9, 93, 65, 103],
    ])
    base_y = np.array([0, 0, 0, 0, 3, 3, 3, 3])
    x = np.tile(base_x, (2, 1))[:10]
    y = np.tile(base_y, 2)[:10]
    create_excel_dataset(path, x, y)
    features = dict(zip((
        "rms_velocity_mm_s", "peak_velocity_mm_s", "crest_factor",
        "dominant_frequency_hz", "sound_rms", "operating_hours",
    ), x[0]))
    append_measurement(path, "2026-01-01T00:00:00+00:00", features, 0)
    loaded_x, loaded_y = load_training_data(path)
    assert loaded_x.shape == (11, 6)
    assert loaded_y[-1] == 0
    model, count = train_model_from_excel(path)
    assert count == 10
    assert 0.0 <= model.oob_score_ <= 1.0
    assert int(model.predict(x[:1, :5])[0]) == 0
    expected_threshold = (np.median(x[y == 0, 0]) + np.median(x[y == 3, 0])) / 2
    assert np.isclose(load_zone_d_rms_threshold(path), expected_threshold)


def test_training_is_blocked_with_fewer_than_10_trusted_rows(tmp_path):
    path = tmp_path / "training.xlsx"
    x = np.tile(np.array([
        [0.8, 1.2, 1.5, 82, 30, 10],
        [1.4, 4.0, 2.8, 92, 64, 100],
    ]), (4, 1))
    y = np.tile(np.array([0, 3]), 4)
    create_excel_dataset(path, x, y)

    with pytest.raises(ValueError, match=r"10.*현재 8개.*2개 부족"):
        train_model_from_excel(path)
