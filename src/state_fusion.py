"""Combine independently inferred vibration and sound states."""

from __future__ import annotations

from typing import Any

SEVERITY = {
    "normal": 0,
    "pressure_drop": 1,
    "seal_leak": 2,
    "internal_wear": 3,
    "unknown": 4,
}
FUSION_VERSION = "vibration-sound-conservative-v1"


def fuse_sensor_results(
    vibration: dict[str, Any], sound: dict[str, Any]
) -> dict[str, Any]:
    """Use the more severe independent result as the combined state.

    Averaging can hide a fault seen by only one sensor, so the initial fusion
    rule is intentionally conservative and fully traceable.
    """
    for name, result in (("vibration", vibration), ("sound", sound)):
        prediction = str(result.get("prediction", "unknown"))
        if prediction not in SEVERITY:
            raise ValueError(f"invalid {name} prediction: {prediction}")
    controlling_role, controlling = max(
        (("vibration", vibration), ("sound", sound)),
        key=lambda item: SEVERITY[str(item[1]["prediction"])],
    )
    health_score = min(
        float(vibration["health_score"]), float(sound["health_score"])
    )
    return {
        "prediction": str(controlling["prediction"]),
        "confidence": float(controlling["confidence"]),
        "health_score": health_score,
        "remaining_life_percent": None,
        "remaining_hours": None,
        "remaining_cycles": None,
        "rul_lower_bound": None,
        "rul_upper_bound": None,
        "rul_status": "insufficient_lifecycle_data",
        "controlling_role": controlling_role,
        "fusion_version": FUSION_VERSION,
    }
