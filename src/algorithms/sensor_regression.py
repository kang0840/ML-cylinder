from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np


def _to_1d(values: Sequence[float] | np.ndarray | None) -> np.ndarray:
    if values is None:
        return np.zeros(1, dtype=float)
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size == 0:
        return np.zeros(1, dtype=float)
    return arr


def _resize_to_length(values: np.ndarray, length: int) -> np.ndarray:
    if length <= 0:
        return np.zeros(0, dtype=float)
    if values.size >= length:
        return values[:length].astype(float)
    padded = np.zeros(length, dtype=float)
    padded[: values.size] = values
    return padded


def build_fft_feature_vector(sensor_data: Dict[str, Any] | Sequence[float] | None, feature_size: int = 128) -> List[float]:
    """진동/마이크 센서 시계열을 FFT 기반 특징 벡터로 변환한다.

    데이터가 아직 준비되지 않았더라도, 입력 형태가 들어오면 바로 처리할 수 있도록
    기본적인 전처리와 패딩을 포함한 스켈레톤 구현이다.
    """
    if isinstance(sensor_data, dict):
        vibration = _to_1d(sensor_data.get("vibration"))
        microphone = _to_1d(sensor_data.get("microphone"))
    elif sensor_data is None:
        vibration = np.zeros(4, dtype=float)
        microphone = np.zeros(4, dtype=float)
    else:
        values = _to_1d(sensor_data)
        vibration = values
        microphone = values

    vib_fft = np.fft.rfft(vibration)
    mic_fft = np.fft.rfft(microphone)

    vibration_mag = np.abs(vib_fft)
    microphone_mag = np.abs(mic_fft)

    half_size = max(1, feature_size // 2)
    vibration_features = _resize_to_length(vibration_mag, half_size)
    microphone_features = _resize_to_length(microphone_mag, half_size)

    features = np.concatenate([vibration_features, microphone_features]).astype(float)
    if features.size < feature_size:
        features = np.pad(features, (0, feature_size - features.size), mode="constant")
    return features[:feature_size].tolist()


class SensorPolynomialRegressor:
    """FFT 특징 벡터를 이용한 지도 학습 기반 다항 회귀 모델 스켈레톤."""

    def __init__(self, max_degree: int = 3, alpha: float = 1e-3, random_state: int | None = None):
        if max_degree < 1:
            raise ValueError("max_degree must be >= 1")
        self.max_degree = int(max_degree)
        self.alpha = float(alpha)
        self.random_state = random_state
        self.degree_ = 1
        self.coef_ = None
        self.intercept_ = 0.0
        self.r2_ = None
        self.overfitting_guard_ = None

    def _prepare_inputs(self, samples: Sequence[Dict[str, Any]]) -> np.ndarray:
        X = [build_fft_feature_vector(sample) for sample in samples]
        return np.asarray(X, dtype=float)

    def _build_polynomial_features(self, X: np.ndarray, degree: int) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X.reshape(-1, 1)
        design = np.ones((X.shape[0], 1), dtype=float)
        for power in range(1, degree + 1):
            for col in range(X.shape[1]):
                design = np.hstack([design, X[:, col : col + 1] ** power])
        return design

    def _fit_with_degree(self, X: np.ndarray, y: np.ndarray, degree: int) -> Tuple[np.ndarray, float]:
        design = self._build_polynomial_features(X, degree)
        reg_matrix = design.T @ design + self.alpha * np.eye(design.shape[1])
        rhs = design.T @ y
        try:
            coef = np.linalg.solve(reg_matrix, rhs)
        except np.linalg.LinAlgError:
            coef = np.linalg.pinv(reg_matrix) @ rhs
        intercept = float(coef[0])
        coeffs = coef[1:]
        return np.concatenate([[intercept], coeffs]), float(np.dot(coef, coef))

    def fit(self, samples: Sequence[Dict[str, Any]], targets: Sequence[float]) -> "SensorPolynomialRegressor":
        if len(samples) != len(targets):
            raise ValueError("samples and targets must have the same length")
        if len(samples) == 0:
            raise ValueError("At least one sample is required")

        X = self._prepare_inputs(samples)
        y = np.asarray(targets, dtype=float)

        if X.shape[0] != y.shape[0]:
            raise ValueError("Feature matrix and target vector must have matching rows")

        if len(samples) >= 6:
            split_idx = max(2, int(len(samples) * 0.2))
            train_size = len(samples) - split_idx
            X_train, X_val = X[:train_size], X[train_size:]
            y_train, y_val = y[:train_size], y[train_size:]
        else:
            X_train, X_val = X, X[:1]
            y_train, y_val = y, y[:1]

        best_degree = 1
        best_score = -float("inf")
        self.overfitting_guard_ = "validation-split"

        for degree in range(1, self.max_degree + 1):
            coef, _ = self._fit_with_degree(X_train, y_train, degree)
            pred_val = self._predict_from_design(self._build_polynomial_features(X_val, degree), coef)
            val_score = self._r2_score(y_val, pred_val)
            if val_score > best_score:
                best_score = val_score
                best_degree = degree

        self.degree_ = best_degree
        self.coef_, _ = self._fit_with_degree(X, y, self.degree_)
        self.intercept_ = float(self.coef_[0])
        self.r2_ = self.score(X, y)
        return self

    def _predict_from_design(self, design: np.ndarray, coef: np.ndarray) -> np.ndarray:
        return design @ coef

    def predict(self, samples: Sequence[Dict[str, Any]]) -> List[float]:
        if self.coef_ is None:
            raise ValueError("Model is not fitted yet")
        X = self._prepare_inputs(samples)
        design = self._build_polynomial_features(X, self.degree_)
        preds = self._predict_from_design(design, self.coef_)
        return preds.astype(float).tolist()

    def score(self, samples: Sequence[Dict[str, Any]], targets: Sequence[float]) -> float:
        if self.coef_ is None:
            raise ValueError("Model is not fitted yet")
        preds = self.predict(samples)
        y_true = np.asarray(targets, dtype=float)
        return float(self._r2_score(y_true, np.asarray(preds, dtype=float)))

    def _r2_score(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        y_true = np.asarray(y_true, dtype=float)
        y_pred = np.asarray(y_pred, dtype=float)
        ss_res = np.sum((y_true - y_pred) ** 2)
        ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
        if ss_tot == 0.0:
            return 1.0 if ss_res == 0.0 else 0.0
        return 1.0 - (ss_res / ss_tot)
