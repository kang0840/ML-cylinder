"""Background retraining with the project's SensorPolynomialRegressor."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import joblib

from .algorithms import SensorPolynomialRegressor
from .database import Database
from .ml_predictor import MLPredictor

LOGGER = logging.getLogger(__name__)


class AutoTrainer:
    def __init__(self, database: Database, predictor: MLPredictor, model_path: Path, batch_size: int = 50):
        self.database = database
        self.predictor = predictor
        self.model_path = model_path
        self.batch_size = batch_size
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="model-training", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self.notify()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopped.set()
        self._wake.set()
        self._thread.join(timeout=30)

    def _run(self) -> None:
        while not self._stopped.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stopped.is_set():
                return
            try:
                _, pending = self.database.training_progress()
                if pending < self.batch_size:
                    continue
                samples, targets, last_id = self.database.labelled_training_data()
                model = SensorPolynomialRegressor(max_degree=3, alpha=1e-3).fit(samples, targets)
                version = datetime.now(timezone.utc).strftime("sensor-poly-%Y%m%dT%H%M%SZ")
                package = {
                    "model": model,
                    "model_type": "sensor_polynomial_regressor",
                    "model_version": version,
                    "training_rows": len(targets),
                }
                temporary = self.model_path.with_suffix(self.model_path.suffix + ".tmp")
                joblib.dump(package, temporary)
                os.replace(temporary, self.model_path)
                self.predictor.install(model, version, "sensor_polynomial_regressor")
                self.database.mark_training_complete(last_id)
                LOGGER.info("자동 재학습 완료: version=%s rows=%s", version, len(targets))
            except Exception:
                LOGGER.exception("자동 재학습 실패; 기존 모델을 계속 사용합니다.")
