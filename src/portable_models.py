"""PyCaret 없이 Raspberry Pi에서 실행하는 경량 모델 래퍼."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np


class PortableConditionModel:
    def __init__(self, imputer_values: Any, scaler_mean: Any, scaler_scale: Any, estimator: Any, labels: Any):
        self.imputer_values = np.asarray(imputer_values, dtype=float)
        self.scaler_mean = np.asarray(scaler_mean, dtype=float)
        self.scaler_scale = np.asarray(scaler_scale, dtype=float)
        self.estimator = estimator
        self.labels = np.asarray(labels)

    def _transform(self, frame: Any) -> np.ndarray:
        values = np.asarray(frame, dtype=float)
        missing = np.isnan(values)
        if missing.any():
            values = values.copy()
            values[missing] = np.take(self.imputer_values, np.where(missing)[1])
        return (values - self.scaler_mean) / self.scaler_scale

    def predict(self, frame: Any) -> np.ndarray:
        encoded = self.estimator.predict(self._transform(frame)).astype(int)
        return self.labels[encoded]

    def predict_proba(self, frame: Any) -> np.ndarray:
        return self.estimator.predict_proba(self._transform(frame))


class PortableLifeModel:
    def __init__(
        self,
        numerical_imputer_values: Any,
        categorical_imputer_values: Any,
        scaler_mean: Any,
        scaler_scale: Any,
        estimator: Any,
        output_features: Sequence[str],
    ):
        self.numerical_imputer_values = np.asarray(numerical_imputer_values, dtype=float)
        self.categorical_imputer_values = np.asarray(categorical_imputer_values, dtype=object)
        self.scaler_mean = np.asarray(scaler_mean, dtype=float)
        self.scaler_scale = np.asarray(scaler_scale, dtype=float)
        self.estimator = estimator
        self.output_features = list(output_features)

    def _transform(self, frame: Any) -> np.ndarray:
        numeric_names = ["vibration_confidence", "sound_confidence"]
        category_names = ["vibration_prediction", "sound_prediction"]
        numeric = np.asarray(frame[numeric_names], dtype=float)
        numeric_missing = np.isnan(numeric)
        if numeric_missing.any():
            numeric = numeric.copy()
            numeric[numeric_missing] = np.take(
                self.numerical_imputer_values, np.where(numeric_missing)[1]
            )
        categories = np.asarray(frame[category_names], dtype=object)
        for index in range(categories.shape[1]):
            missing = np.equal(categories[:, index], None)
            categories[missing, index] = self.categorical_imputer_values[index]
        rows: list[list[float]] = []
        for row_index in range(len(frame)):
            values = {
                numeric_names[0]: float(numeric[row_index, 0]),
                numeric_names[1]: float(numeric[row_index, 1]),
            }
            for name_index, name in enumerate(category_names):
                selected = str(categories[row_index, name_index])
                prefix = f"{name}_"
                for output_name in self.output_features:
                    if output_name.startswith(prefix):
                        values[output_name] = float(output_name == f"{prefix}{selected}")
            rows.append([values.get(name, 0.0) for name in self.output_features])
        values = np.asarray(rows, dtype=float)
        return (values - self.scaler_mean) / self.scaler_scale

    def predict(self, frame: Any) -> np.ndarray:
        return self.estimator.predict(self._transform(frame))
