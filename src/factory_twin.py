"""Physics-informed dummy sensors and independent ML models for factory assets."""

from __future__ import annotations

import datetime as dt
import threading
from collections import deque

import numpy as np
from sklearn.ensemble import RandomForestClassifier


CONVEYOR_FEATURES = (
    "acceleration_rms_g",
    "acceleration_peak_g",
    "crest_factor",
    "dominant_vibration_frequency_hz",
    "harmonic_energy_ratio",
    "sound_rms_dbfs",
    "acoustic_high_band_ratio",
)
CYLINDER_FEATURES = (
    "acceleration_rms_g",
    "acceleration_peak_g",
    "crest_factor",
    "sound_rms_dbfs",
    "impact_energy_g2_s",
    "stroke_duration_ms",
    "leak_band_energy_ratio",
)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _conveyor_measurement(
    severity: float, shaft_frequency: float, rng: np.random.Generator,
) -> dict[str, float]:
    """Features obtainable from continuous ADXL345 and INMP441 signals."""
    bearing_wear = np.clip(severity + rng.normal(0.0, 0.025), 0.0, 1.15)
    acceleration_rms = 0.028 + 0.19 * bearing_wear ** 1.45 + rng.normal(0.0, 0.003)
    crest = 1.55 + 2.0 * bearing_wear + rng.normal(0.0, 0.07)
    peak = acceleration_rms * crest
    dominant_frequency = shaft_frequency * (1.0 + rng.normal(0.0, 0.004))
    harmonic_ratio = 0.10 + 0.55 * bearing_wear + rng.normal(0.0, 0.018)
    sound = -34.0 + 15.0 * bearing_wear + rng.normal(0.0, 0.7)
    high_band = 0.08 + 0.50 * bearing_wear ** 1.3 + rng.normal(0.0, 0.015)
    return {
        "acceleration_rms_g": float(np.clip(acceleration_rms, 0.005, 0.5)),
        "acceleration_peak_g": float(np.clip(peak, 0.01, 2.0)),
        "crest_factor": float(np.clip(crest, 1.1, 5.0)),
        "dominant_vibration_frequency_hz": float(np.clip(dominant_frequency, 5.0, 200.0)),
        "harmonic_energy_ratio": float(np.clip(harmonic_ratio, 0.0, 1.0)),
        "sound_rms_dbfs": float(np.clip(sound, -80.0, -1.0)),
        "acoustic_high_band_ratio": float(np.clip(high_band, 0.0, 1.0)),
    }


def _cylinder_measurement(
    severity: float, nominal_stroke_ms: float, rng: np.random.Generator,
) -> dict[str, float]:
    """Event features obtainable from ADXL345 impacts and INMP441 audio."""
    acceleration_rms = 0.045 + 0.16 * severity ** 1.25 + rng.normal(0.0, 0.004)
    crest = 2.2 + 3.1 * severity + rng.normal(0.0, 0.09)
    peak = acceleration_rms * crest
    impact_energy = 0.002 + 0.035 * severity ** 1.6 + rng.normal(0.0, 0.0008)
    stroke_duration = nominal_stroke_ms * (1.0 + 0.36 * severity) + rng.normal(0.0, 5.0)
    sound = -39.0 + 21.0 * severity + rng.normal(0.0, 0.7)
    leak_band = 0.06 + 0.67 * severity ** 1.5 + rng.normal(0.0, 0.015)
    return {
        "acceleration_rms_g": float(np.clip(acceleration_rms, 0.005, 0.5)),
        "acceleration_peak_g": float(np.clip(peak, 0.01, 3.0)),
        "crest_factor": float(np.clip(crest, 1.2, 8.0)),
        "sound_rms_dbfs": float(np.clip(sound, -80.0, -1.0)),
        "impact_energy_g2_s": float(np.clip(impact_energy, 0.0001, 0.1)),
        "stroke_duration_ms": float(np.clip(stroke_duration, 80.0, 1_000.0)),
        "leak_band_energy_ratio": float(np.clip(leak_band, 0.0, 1.0)),
    }


def _fit_conveyor_model(seed: int = 42) -> RandomForestClassifier:
    rng = np.random.default_rng(seed)
    rows, labels = [], []
    ranges = ((0.0, 0.32, "NORMAL"), (0.38, 0.68, "WARNING"), (0.75, 1.0, "ERROR"))
    for low, high, label in ranges:
        for _ in range(450):
            values = _conveyor_measurement(
                rng.uniform(low, high), rng.uniform(17.0, 25.0), rng,
            )
            rows.append([values[name] for name in CONVEYOR_FEATURES])
            labels.append(label)
    return RandomForestClassifier(
        n_estimators=180, max_depth=9, min_samples_leaf=3,
        class_weight="balanced", random_state=seed, n_jobs=-1,
    ).fit(np.asarray(rows), labels)


def _fit_cylinder_model(seed: int = 84) -> RandomForestClassifier:
    rng = np.random.default_rng(seed)
    rows, labels = [], []
    ranges = ((0.0, 0.22, "A"), (0.27, 0.48, "B"), (0.53, 0.74, "C"), (0.8, 1.0, "D"))
    for low, high, label in ranges:
        for _ in range(400):
            values = _cylinder_measurement(rng.uniform(low, high), rng.uniform(240.0, 420.0), rng)
            rows.append([values[name] for name in CYLINDER_FEATURES])
            labels.append(label)
    return RandomForestClassifier(
        n_estimators=200, max_depth=10, min_samples_leaf=3,
        class_weight="balanced", random_state=seed, n_jobs=-1,
    ).fit(np.asarray(rows), labels)


class FactoryDigitalTwin:
    """Maintain slowly changing sensor state for two unlike equipment types."""

    def __init__(self, seed: int = 2026, history_size: int = 300):
        self.rng = np.random.default_rng(seed)
        self.lock = threading.Lock()
        self.conveyor_model = _fit_conveyor_model()
        self.cylinder_model = _fit_cylinder_model()
        self.conveyors = {
            "CV-01": {"severity": 0.12, "shaft_frequency": 20.0},
            "CV-02": {"severity": 0.31, "shaft_frequency": 22.5},
        }
        self.cylinders = {
            "CY-01": {"severity": 0.10, "nominal_stroke_ms": 280.0, "detected_cycles": 0},
            "CY-02": {"severity": 0.36, "nominal_stroke_ms": 360.0, "detected_cycles": 0},
        }
        self.history: deque[dict[str, object]] = deque(maxlen=history_size)

    def _advance(self, state: dict[str, float], drift: float) -> None:
        state["severity"] = float(np.clip(
            state["severity"] + drift + self.rng.normal(0.0, drift * 0.35), 0.0, 1.0,
        ))
        if self.rng.random() < 0.002:
            state["severity"] = float(min(1.0, state["severity"] + self.rng.uniform(0.08, 0.2)))

    def read_conveyor(self, equipment_id: str) -> dict[str, object]:
        with self.lock:
            if equipment_id not in self.conveyors:
                raise KeyError(equipment_id)
            state = self.conveyors[equipment_id]
            state["shaft_frequency"] = float(np.clip(
                state["shaft_frequency"] + self.rng.normal(0.0, 0.025), 10.0, 30.0,
            ))
            self._advance(state, 0.000025)
            sensor = _conveyor_measurement(state["severity"], state["shaft_frequency"], self.rng)
            vector = np.asarray([[sensor[name] for name in CONVEYOR_FEATURES]])
            status = str(self.conveyor_model.predict(vector)[0])
            confidence = float(np.max(self.conveyor_model.predict_proba(vector)[0]))
            result: dict[str, object] = {
                "timestamp": _utc_now(), "equipment_type": "conveyor", "conveyor_id": equipment_id,
                "sensors": ["ADXL345", "INMP441"],
                **{name: round(value, 3) for name, value in sensor.items()},
                "status": status,
                "ai": {"model": "conveyor_random_forest", "confidence": round(confidence, 4)},
            }
            self.history.append(dict(result))
            return result

    def read_cylinder(self, equipment_id: str) -> dict[str, object]:
        with self.lock:
            if equipment_id not in self.cylinders:
                raise KeyError(equipment_id)
            state = self.cylinders[equipment_id]
            state["detected_cycles"] += 1
            self._advance(state, 0.00004)
            sensor = _cylinder_measurement(
                state["severity"], state["nominal_stroke_ms"], self.rng,
            )
            vector = np.asarray([[sensor[name] for name in CYLINDER_FEATURES]])
            zone = str(self.cylinder_model.predict(vector)[0])
            confidence = float(np.max(self.cylinder_model.predict_proba(vector)[0]))
            health_score = {"A": 95, "B": 75, "C": 50, "D": 25}[zone]
            # This is a maintenance indicator, not validated real RUL.
            remaining_life = max(0, round((1.0 - state["severity"]) * 360_000))
            result: dict[str, object] = {
                "timestamp": _utc_now(), "equipment_type": "cylinder", "cylinder_id": equipment_id,
                "sensors": ["ADXL345", "INMP441"],
                **{name: round(value, 3) for name, value in sensor.items()},
                "detected_cycle_count_session": int(state["detected_cycles"]), "zone": zone,
                "health_score": health_score, "estimated_remaining_cycles": remaining_life,
                "status": "NORMAL" if zone == "A" else "WARNING" if zone in {"B", "C"} else "ERROR",
                "ai": {"model": "cylinder_random_forest", "confidence": round(confidence, 4)},
            }
            self.history.append(dict(result))
            return result

    def history_items(self, equipment_type: str = "", limit: int = 100) -> list[dict[str, object]]:
        with self.lock:
            items = list(self.history)
        if equipment_type:
            items = [item for item in items if item["equipment_type"] == equipment_type]
        return items[-limit:]
