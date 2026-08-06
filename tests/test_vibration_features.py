import numpy as np
import pytest

from src.vibration_features import classify_iso_zone, extract_vibration_features


def test_sine_acceleration_is_converted_to_velocity():
    fs = 4_000.0
    duration = 2.0
    frequency = 100.0
    accel_amplitude = 2.0 * np.pi * frequency * 0.005  # 5 mm/s velocity peak
    time = np.arange(int(fs * duration)) / fs
    accel = accel_amplitude * np.cos(2.0 * np.pi * frequency * time)

    result = extract_vibration_features(accel, fs, zone_limits_mm_s=(2.0, 4.0, 6.0))

    assert result["rms_velocity_mm_s"] == pytest.approx(5.0 / np.sqrt(2.0), rel=1e-3)
    assert result["peak_velocity_mm_s"] == pytest.approx(5.0, rel=1e-3)
    assert result["crest_factor"] == pytest.approx(np.sqrt(2.0), rel=1e-3)
    assert result["iso_zone"] == "B"


def test_zone_boundaries():
    limits = (2.3, 4.5, 7.1)
    assert classify_iso_zone(2.29, limits) == "A"
    assert classify_iso_zone(2.3, limits) == "B"
    assert classify_iso_zone(4.5, limits) == "C"
    assert classify_iso_zone(7.1, limits) == "D"


def test_sampling_rate_must_cover_requested_band():
    with pytest.raises(ValueError, match="2000"):
        extract_vibration_features(np.zeros(100), fs=1_000.0)
