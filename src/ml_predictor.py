"""joblib 모델 및 모델 부재 시 안전한 시험용 판정."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from .feature_extractor import FEATURE_NAMES

LOGGER = logging.getLogger(__name__)
PREDICTIONS = {"normal", "pressure_drop", "seal_leak", "internal_wear", "unknown"}


class MLPredictor:
    def __init__(self, model_path: Path):
        self.model: Any | None = None
        self.feature_names = list(FEATURE_NAMES)
        self.model_version = "fallback-threshold-v1"
        self.model_type = "classifier"
        self._lock = threading.RLock()
        if model_path.exists():
            try:
                loaded = joblib.load(model_path)
                self.model = loaded["model"] if isinstance(loaded, dict) and "model" in loaded else loaded
                if isinstance(loaded, dict):
                    self.feature_names = loaded.get("feature_names", self.feature_names)
                    self.model_version = str(loaded.get("model_version", model_path.name))
                    self.model_type = str(loaded.get("model_type", "classifier"))
                else:
                    self.model_version = model_path.name
                LOGGER.info("머신러닝 모델을 불러왔습니다: %s", model_path)
            except Exception:
                LOGGER.exception("모델 로딩 실패, 시험용 판정 모드로 계속합니다.")

    def install(self, model: Any, model_version: str, model_type: str) -> None:
        """Swap the active model without interrupting MQTT collection."""
        with self._lock:
            self.model = model
            self.model_version = model_version
            self.model_type = model_type

    def predict(self, features: dict[str, float], samples=None, sensor_type: str | None = None) -> dict[str, float | str]:
        vector = np.asarray([[features[name] for name in self.feature_names]], dtype=float)
        with self._lock:
            model, model_version, model_type = self.model, self.model_version, self.model_type
        if model is not None and model_type == "sensor_polynomial_regressor":
            key = "vibration" if sensor_type == "sph0645" else "microphone"
            health = float(model.predict([{key: list(samples or [])}])[0])
            health = max(0.0, min(100.0, health))
            if health >= 80:
                prediction = "normal"
            elif health >= 60:
                prediction = "pressure_drop"
            elif health >= 40:
                prediction = "seal_leak"
            else:
                prediction = "internal_wear"
            confidence = min(1.0, abs(health - 50.0) / 50.0)
        elif model is not None:
            prediction = str(model.predict(vector)[0])
            if prediction not in PREDICTIONS:
                prediction = "unknown"
            confidence = 0.0
            if hasattr(model, "predict_proba"):
                probabilities = model.predict_proba(vector)[0]
                confidence = float(np.max(probabilities))
            health = max(0.0, min(100.0, 100.0 * confidence if prediction == "normal" else 100.0 * (1.0 - confidence)))
        else:
            crest, rms = features["crest_factor"], features["rms"]
            if not np.isfinite(rms):
                prediction, confidence, health = "unknown", 0.0, 0.0
            elif crest >= 6.0:
                prediction, confidence, health = "internal_wear", 0.65, 45.0
            elif crest >= 4.0:
                prediction, confidence, health = "seal_leak", 0.60, 60.0
            else:
                prediction, confidence, health = "normal", 0.55, 90.0
        return {"prediction": prediction, "confidence": round(confidence, 6), "health_score": round(health, 2), "model_version": model_version}
