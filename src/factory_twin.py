"""Physics-informed dummy sensors and independent ML models for factory assets."""

from __future__ import annotations

import datetime as dt
import threading
from collections import deque

import numpy as np
from sklearn.ensemble import RandomForestClassifier


CONVEYOR_FEATURES = (
    "motor_speed_rpm",
    "motor_current_a",
    "temperature_c",
    "vibration_rms_mm_s",
    "belt_slip_percent",
)
CYLINDER_FEATURES = (
    "pressure_mpa",
    "vibration_rms_mm_s",
    "peak_mm_s",
    "crest_factor",
    "sound_rms_db",
    "dominant_frequency_hz",
)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _conveyor_measurement(
    severity: float, load: float, setpoint: float, rng: np.random.Generator,
) -> dict[str, float]:
    """Model a geared conveyor motor with load, bearing wear and belt slip."""
    bearing_wear = np.clip(severity + rng.normal(0.0, 0.025), 0.0, 1.15)
    slip = np.clip(0.15 + 5.8 * severity ** 2 + rng.normal(0.0, 0.12), 0.0, 8.0)
    speed = setpoint * (1.0 - slip / 100.0) + rng.normal(0.0, 5.0)
    current = 0.75 + 2.55 * load + 2.1 * bearing_wear + rng.normal(0.0, 0.08)
    temperature = 23.0 + 5.8 * current + 13.5 * bearing_wear + rng.normal(0.0, 0.6)
    vibration = 0.42 + 0.25 * load + 4.9 * bearing_wear ** 1.45 + rng.normal(0.0, 0.07)
    return {
        "motor_speed_rpm": float(np.clip(speed, 750.0, 1_650.0)),
        "motor_current_a": float(np.clip(current, 0.5, 8.0)),
        "temperature_c": float(np.clip(temperature, 20.0, 105.0)),
        "vibration_rms_mm_s": float(np.clip(vibration, 0.1, 9.0)),
        "belt_slip_percent": float(slip),
    }


def _cylinder_measurement(
    severity: float, supply_pressure: float, rng: np.random.Generator,
) -> dict[str, float]:
    """Model a pneumatic cylinder with leakage, seal friction and end impact."""
    leakage = np.clip(0.008 + 0.17 * severity ** 1.7 + rng.normal(0.0, 0.003), 0.0, 0.22)
    pressure = supply_pressure - leakage - 0.018 * severity + rng.normal(0.0, 0.004)
    vibration = 0.62 + 4.35 * severity ** 1.55 + rng.normal(0.0, 0.055)
    crest = 1.42 + 1.48 * severity + rng.normal(0.0, 0.045)
    peak = vibration * crest + rng.normal(0.0, 0.06)
    sound = 31.0 + 22.0 * leakage / 0.22 + 12.0 * severity + rng.normal(0.0, 0.5)
    frequency = 82.0 + 11.0 * severity + rng.normal(0.0, 0.45)
    return {
        "pressure_mpa": float(np.clip(pressure, 0.2, 0.58)),
        "vibration_rms_mm_s": float(np.clip(vibration, 0.1, 8.0)),
        "peak_mm_s": float(np.clip(peak, 0.1, 20.0)),
        "crest_factor": float(np.clip(crest, 1.0, 4.5)),
        "sound_rms_db": float(np.clip(sound, 25.0, 85.0)),
        "dominant_frequency_hz": float(np.clip(frequency, 60.0, 120.0)),
    }


def _fit_conveyor_model(seed: int = 42) -> RandomForestClassifier:
    rng = np.random.default_rng(seed)
    rows, labels = [], []
    ranges = ((0.0, 0.32, "NORMAL"), (0.38, 0.68, "WARNING"), (0.75, 1.0, "ERROR"))
    for low, high, label in ranges:
        for _ in range(450):
            values = _conveyor_measurement(
                rng.uniform(low, high), rng.uniform(0.35, 0.95), rng.uniform(1_050, 1_450), rng,
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
            values = _cylinder_measurement(rng.uniform(low, high), rng.uniform(0.48, 0.52), rng)
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
            "CV-01": {"severity": 0.12, "load": 0.58, "setpoint": 1_200.0, "hours": 812.0},
            "CV-02": {"severity": 0.31, "load": 0.72, "setpoint": 1_350.0, "hours": 1_426.0},
        }
        self.cylinders = {
            "CY-01": {"severity": 0.10, "supply": 0.50, "cycles": 186_420},
            "CY-02": {"severity": 0.36, "supply": 0.49, "cycles": 294_810},
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
            state["load"] = float(np.clip(state["load"] + self.rng.normal(0.0, 0.012), 0.3, 1.0))
            state["hours"] += 1.0 / 3_600.0
            self._advance(state, 0.000025)
            sensor = _conveyor_measurement(state["severity"], state["load"], state["setpoint"], self.rng)
            vector = np.asarray([[sensor[name] for name in CONVEYOR_FEATURES]])
            status = str(self.conveyor_model.predict(vector)[0])
            confidence = float(np.max(self.conveyor_model.predict_proba(vector)[0]))
            result: dict[str, object] = {
                "timestamp": _utc_now(), "equipment_type": "conveyor", "conveyor_id": equipment_id,
                **{name: round(value, 3) for name, value in sensor.items()},
                "load_percent": round(state["load"] * 100.0, 1),
                "operating_hours": round(state["hours"], 3),
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
            state["supply"] = float(np.clip(state["supply"] + self.rng.normal(0.0, 0.0012), 0.46, 0.53))
            state["cycles"] += 1
            self._advance(state, 0.00004)
            sensor = _cylinder_measurement(state["severity"], state["supply"], self.rng)
            vector = np.asarray([[sensor[name] for name in CYLINDER_FEATURES]])
            zone = str(self.cylinder_model.predict(vector)[0])
            confidence = float(np.max(self.cylinder_model.predict_proba(vector)[0]))
            health_score = {"A": 95, "B": 75, "C": 50, "D": 25}[zone]
            # This is a maintenance indicator, not validated real RUL.
            remaining_life = max(0, round((1.0 - state["severity"]) * 360_000))
            result: dict[str, object] = {
                "timestamp": _utc_now(), "equipment_type": "cylinder", "cylinder_id": equipment_id,
                **{name: round(value, 3) for name, value in sensor.items()},
                "cycle_count": int(state["cycles"]), "zone": zone,
                "health_score": health_score, "remaining_life_cycles": remaining_life,
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

