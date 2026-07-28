"""Project-defined health assessment for the Smart Cylinder Case.

ISO 20816 motivates evaluation using vibration velocity RMS and ISO 13373
motivates consistent signal processing.  Neither standard defines pneumatic
cylinder A-D limits, health score, or RUL; every calculation here is therefore
a project criterion that must be validated with experiments.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


ZONE_FEATURES = (
    "rms_velocity_mm_s",
    "peak_velocity_mm_s",
    "crest_factor",
    "dominant_frequency_hz",
    "sound_rms",
)


def fit_normal_baseline(
    samples: Sequence[Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    """Calculate population mean and standard deviation from normal samples."""
    if len(samples) < 2:
        raise ValueError("at least two normal samples are required")
    baseline: dict[str, dict[str, float]] = {}
    for name in ZONE_FEATURES:
        values = np.asarray([sample[name] for sample in samples], dtype=float)
        if not np.all(np.isfinite(values)):
            raise ValueError(f"normal samples for {name} must be finite")
        sigma = float(np.std(values, ddof=0))
        if sigma <= np.finfo(float).eps:
            raise ValueError(f"normal samples for {name} need non-zero variation")
        baseline[name] = {"mean": float(np.mean(values)), "std": sigma}
    return baseline


def classify_statistical_zone(
    features: Mapping[str, float], baseline: Mapping[str, Mapping[str, float]],
) -> dict[str, object]:
    """Classify by the largest absolute normal-baseline z-score.

    A: z <= 1, B: 1 < z <= 2, C: 2 < z <= 3, D: z > 3.
    The conservative maximum rule prevents one abnormal signal from being
    hidden by averaging it with normal signals.
    """
    scores: dict[str, float] = {}
    for name in ZONE_FEATURES:
        value = float(features[name])
        mean = float(baseline[name]["mean"])
        std = float(baseline[name]["std"])
        if not np.isfinite(value) or not np.isfinite(mean) or not np.isfinite(std) or std <= 0:
            raise ValueError(f"invalid feature or baseline for {name}")
        scores[name] = abs(value - mean) / std
    max_z = max(scores.values())
    label = 0 if max_z <= 1 else 1 if max_z <= 2 else 2 if max_z <= 3 else 3
    return {
        "label": label,
        "zone": "ABCD"[label],
        "max_abs_z_score": float(max_z),
        "controlling_feature": max(scores, key=scores.get),
        "feature_z_scores": scores,
    }


def health_score(zone_label: int) -> int:
    """Return the v4 project score: A=100, B=75, C=50, D=25."""
    if zone_label not in range(4):
        raise ValueError("zone_label must be 0, 1, 2, or 3")
    return 100 - 25 * zone_label


def estimate_rul_from_rms(
    rms_history: Sequence[tuple[float, float]], zone_d_rms: float,
) -> dict[str, float | None | str]:
    """Estimate operating hours to the D threshold using linear RMS trend."""
    if len(rms_history) < 2:
        return {"basis": "project_rms_linear_trend_not_iso", "rms_rate_mm_s_per_hour": None,
                "hours_to_zone_d": None}
    history = np.asarray(rms_history, dtype=float)
    hours, rms = history[:, 0], history[:, 1]
    if not np.all(np.isfinite(history)) or np.ptp(hours) <= 0:
        raise ValueError("RMS history requires finite values at distinct operating hours")
    rate = float(np.polyfit(hours, rms, 1)[0])
    current_rms = float(rms[-1])
    remaining = 0.0 if current_rms >= zone_d_rms else (
        (zone_d_rms - current_rms) / rate if rate > 0 else None
    )
    status = "threshold_reached" if current_rms >= zone_d_rms else (
        "estimated" if rate > 0 else "no_positive_degradation_trend"
    )
    return {"basis": "project_rms_linear_trend_not_iso",
            "status": status, "rms_rate_mm_s_per_hour": rate,
            "hours_to_zone_d": remaining}
