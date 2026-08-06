"""Central model training and inference for the smart-cylinder project."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeAlias

import numpy as np
from sklearn.ensemble import RandomForestClassifier


Classifier: TypeAlias = RandomForestClassifier

CYLINDER_CONDITION_PARAMS = {
    "n_estimators": 200,
    "max_depth": 8,
    "min_samples_leaf": 3,
    "class_weight": "balanced",
    "n_jobs": -1,
}
CONVEYOR_PARAMS = {
    "n_estimators": 180,
    "max_depth": 9,
    "min_samples_leaf": 3,
    "class_weight": "balanced",
    "n_jobs": -1,
}
DIGITAL_TWIN_CYLINDER_PARAMS = {
    **CYLINDER_CONDITION_PARAMS,
    "max_depth": 10,
}


def train_cylinder_condition_model(
    features: np.ndarray,
    labels: np.ndarray,
    *,
    seed: int = 42,
    oob_score: bool = False,
) -> Classifier:
    """Train the A-D cylinder condition classifier used by Excel inference."""
    model = RandomForestClassifier(
        **CYLINDER_CONDITION_PARAMS,
        random_state=seed,
        bootstrap=True,
        oob_score=oob_score,
    )
    return model.fit(np.asarray(features, dtype=float), np.asarray(labels))


def train_simulated_asset_model(
    measurement_factory: Callable[[float, np.random.Generator], dict[str, float]],
    feature_names: Sequence[str],
    severity_ranges: Sequence[tuple[float, float, str]],
    *,
    samples_per_class: int,
    seed: int,
    asset: str,
) -> Classifier:
    """Train one digital-twin classifier from its simulated sensor schema."""
    rng = np.random.default_rng(seed)
    rows: list[list[float]] = []
    labels: list[str] = []
    for low, high, label in severity_ranges:
        for _ in range(samples_per_class):
            values = measurement_factory(float(rng.uniform(low, high)), rng)
            rows.append([values[name] for name in feature_names])
            labels.append(label)

    params = CONVEYOR_PARAMS if asset == "conveyor" else DIGITAL_TWIN_CYLINDER_PARAMS
    return RandomForestClassifier(**params, random_state=seed).fit(np.asarray(rows), labels)


def predict_with_confidence(
    model: Classifier, values: Sequence[float],
) -> tuple[object, float]:
    """Run one inference and return its predicted label and class probability."""
    vector = np.asarray([values], dtype=float)
    prediction = model.predict(vector)[0]
    class_index = int(np.flatnonzero(model.classes_ == prediction)[0])
    confidence = float(model.predict_proba(vector)[0][class_index])
    return prediction, confidence
