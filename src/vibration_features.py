"""Vibration feature extraction for reciprocating machinery.

Signal processing follows the requested 2 Hz to 1,000 Hz measurement band.
ISO 20816-8 zone limits depend on the compressor, measurement location and
operating conditions, so production limits should be supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


FREQ_LOW_HZ = 2.0
FREQ_HIGH_HZ = 1_000.0

# These generic limits are retained only for the low-level helper and tests.
# A pneumatic cylinder is not assigned limits by ISO 20816; the application
# builds its actual limits from normal measurements (project criteria).
DEFAULT_ZONE_LIMITS_MM_S = (2.3, 4.5, 7.1)


def classify_iso_zone(
    rms_velocity_mm_s: float,
    zone_limits_mm_s: Sequence[float] = DEFAULT_ZONE_LIMITS_MM_S,
) -> str:
    """Classify velocity RMS into A/B/C/D using three ascending boundaries."""
    limits = np.asarray(zone_limits_mm_s, dtype=float)
    if limits.shape != (3,) or not np.all(np.isfinite(limits)):
        raise ValueError("zone_limits_mm_s must contain three finite values")
    if np.any(limits <= 0) or np.any(np.diff(limits) <= 0):
        raise ValueError("zone limits must be positive and strictly increasing")

    if rms_velocity_mm_s < limits[0]:
        return "A"
    if rms_velocity_mm_s < limits[1]:
        return "B"
    if rms_velocity_mm_s < limits[2]:
        return "C"
    return "D"


def extract_vibration_features(
    accel_data: np.ndarray,
    fs: float,
    zone_limits_mm_s: Sequence[float] = DEFAULT_ZONE_LIMITS_MM_S,
) -> dict[str, float | str]:
    """Extract velocity features from acceleration samples.

    Args:
        accel_data: One-dimensional acceleration signal in m/s^2.
        fs: Sampling rate in Hz. It must be at least 2,000 Hz so that the
            requested 1,000 Hz upper band edge is represented.
        zone_limits_mm_s: A/B, B/C and C/D velocity-RMS boundaries in mm/s.

    Returns:
        Dictionary containing velocity RMS, absolute peak velocity, crest
        factor and evaluation zone.
    """
    accel = np.asarray(accel_data, dtype=float)
    if accel.ndim != 1 or accel.size < 2:
        raise ValueError("accel_data must be a one-dimensional array with at least 2 samples")
    if not np.all(np.isfinite(accel)):
        raise ValueError("accel_data must contain only finite values")
    if not np.isfinite(fs) or fs < 2 * FREQ_HIGH_HZ:
        raise ValueError("fs must be at least 2000 Hz for the 2-1000 Hz band")

    # Remove sensor DC bias, then integrate in the frequency domain. This
    # avoids the drift produced by direct time-domain integration.
    accel = accel - np.mean(accel)
    frequencies = np.fft.rfftfreq(accel.size, d=1.0 / fs)
    accel_spectrum = np.fft.rfft(accel)
    passband = (frequencies >= FREQ_LOW_HZ) & (frequencies <= FREQ_HIGH_HZ)

    velocity_spectrum = np.zeros_like(accel_spectrum, dtype=complex)
    velocity_spectrum[passband] = accel_spectrum[passband] / (
        1j * 2.0 * np.pi * frequencies[passband]
    )
    velocity_mm_s = np.fft.irfft(velocity_spectrum, n=accel.size) * 1_000.0

    rms_velocity = float(np.sqrt(np.mean(np.square(velocity_mm_s))))
    peak_velocity = float(np.max(np.abs(velocity_mm_s)))
    crest_factor = peak_velocity / rms_velocity if rms_velocity > np.finfo(float).eps else 0.0
    velocity_magnitude = np.abs(velocity_spectrum)
    dominant_frequency = (
        float(frequencies[np.argmax(velocity_magnitude)])
        if np.any(velocity_magnitude > 0)
        else 0.0
    )

    return {
        "rms_velocity_mm_s": rms_velocity,
        "peak_velocity_mm_s": peak_velocity,
        "crest_factor": float(crest_factor),
        "dominant_frequency_hz": dominant_frequency,
        "iso_zone": classify_iso_zone(rms_velocity, zone_limits_mm_s),
    }
