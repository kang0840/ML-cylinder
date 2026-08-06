"""ADXL345 acceleration simulator for a pneumatic-cylinder student project."""

from __future__ import annotations

import numpy as np


SENSOR_NAME = "ADXL345"


def create_accel_block(
    fs: float,
    duration: float,
    operating_hours: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return a simulated acceleration block in m/s^2.

    Degradation slowly increases velocity amplitude and dominant frequency.
    This is project simulation logic, not an ISO deterioration formula.
    """
    count = round(fs * duration)
    t = np.arange(count) / fs
    health = np.clip(operating_hours / 250.0, 0.0, 1.2)
    frequency_hz = 82.0 + 4.0 * health
    # Project example curve: 0.82, 1.42, 2.71 and 4.55 mm/s RMS at
    # 20, 80, 160 and 250 operating hours. A sine peak is RMS * sqrt(2).
    target_rms_mm_s = np.interp(
        operating_hours,
        [0.0, 20.0, 80.0, 160.0, 250.0, 300.0],
        [0.65, 0.82, 1.42, 2.71, 4.55, 5.50],
    )
    velocity_peak_m_s = target_rms_mm_s * np.sqrt(2.0) / 1_000.0
    acceleration_amplitude = 2.0 * np.pi * frequency_hz * velocity_peak_m_s
    impact = (rng.random(count) < (0.0005 + health * 0.002)) * rng.normal(
        0.0, 2.0 + 4.0 * health, count
    )
    noise = rng.normal(0.0, 0.04 + 0.08 * health, count)
    return acceleration_amplitude * np.cos(2.0 * np.pi * frequency_hz * t) + impact + noise
