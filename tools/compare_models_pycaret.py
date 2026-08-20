"""Compare condition classifiers independently for each physical sensor role."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FEATURES = [
    "mean", "standard_deviation", "rms", "maximum", "minimum", "peak",
    "peak_to_peak", "crest_factor", "dominant_frequency",
    "dominant_amplitude", "spectral_energy",
]
SENSOR_ROLES = {"sph0645": "acoustic_vibration", "inmp441": "sound"}


def load_training_frame(database_path: Path, sensor_type: str) -> pd.DataFrame:
    if sensor_type not in SENSOR_ROLES:
        raise ValueError(f"unknown sensor_type: {sensor_type}")
    columns = ", ".join(f"f.{name}" for name in FEATURES)
    query = f"""
        SELECT m.measured_at,m.experiment_id,m.session_id,{columns},m.ground_truth
        FROM measurements AS m
        JOIN feature_data AS f ON f.measurement_id = m.measurement_id
        WHERE m.ground_truth IS NOT NULL
          AND m.ground_truth_source IN ('controlled_experiment','human_verified','maintenance_verified')
          AND m.experiment_id IS NOT NULL AND m.session_id IS NOT NULL
          AND m.sensor_type = ?
        ORDER BY m.measured_at, m.id
    """
    with sqlite3.connect(database_path) as connection:
        measurement_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(measurements)")
        }
        if not {"ground_truth","ground_truth_source","experiment_id","session_id"}.issubset(measurement_columns):
            return pd.DataFrame(
                columns=["measured_at","experiment_id","session_id",*FEATURES,"ground_truth"]
            )
        return pd.read_sql_query(query, connection, params=(sensor_type,))


def validate_training_frame(data: pd.DataFrame, sensor_type: str, min_rows: int) -> None:
    if len(data) < min_rows:
        raise ValueError(
            f"{sensor_type} labelled rows are insufficient: {len(data)} < {min_rows}"
        )
    if data["ground_truth"].nunique() < 2:
        raise ValueError(
            f"{sensor_type} needs at least two distinct ground_truth classes"
        )
    if data["experiment_id"].nunique() < 3:
        raise ValueError(f"{sensor_type} needs at least three independent experiments")
    timestamps = pd.to_datetime(data["measured_at"], utc=True, errors="coerce")
    if timestamps.isna().any() or not timestamps.is_monotonic_increasing:
        raise ValueError(f"{sensor_type} measured_at must be valid and time ordered")


def compare_sensor(
    database: Path,
    output_root: Path,
    sensor_type: str,
    min_rows: int,
    fold: int,
    seed: int,
) -> Path:
    data = load_training_frame(database, sensor_type)
    validate_training_frame(data, sensor_type, min_rows)
    if sys.version_info[:2] not in {(3, 9), (3, 10), (3, 11)}:
        raise RuntimeError(
            f"Use the dedicated Python 3.11 PyCaret environment, not Python "
            f"{sys.version_info.major}.{sys.version_info.minor}."
        )
    role = SENSOR_ROLES[sensor_type]
    output_dir = output_root / f"{sensor_type}_{role}"
    output_dir.mkdir(parents=True, exist_ok=True)

    from pycaret.classification import ClassificationExperiment

    # measured_at determines split order but must never become a model feature.
    from sklearn.model_selection import GroupShuffleSplit, GroupKFold
    splitter=GroupShuffleSplit(n_splits=1,test_size=0.2,random_state=seed)
    train_idx,test_idx=next(splitter.split(data,groups=data["experiment_id"]))
    train=data.iloc[train_idx].copy();test=data.iloc[test_idx].copy()
    train_groups=train["experiment_id"].copy()
    training=train.drop(columns=["measured_at","experiment_id","session_id"])
    testing=test.drop(columns=["measured_at","experiment_id","session_id"])
    folds=max(2,min(fold,train_groups.nunique()))
    experiment = ClassificationExperiment()
    experiment.setup(
        data=training,
        test_data=testing,
        target="ground_truth",
        fold_strategy=GroupKFold(n_splits=folds),
        fold_groups=train_groups,
        fold_shuffle=False,
        session_id=seed,
        normalize=True,
        n_jobs=-1,
        html=False,
        verbose=False,
    )
    candidates = experiment.compare_models(sort="F1", n_select=3, turbo=True)
    leaderboard = experiment.pull()
    leaderboard.to_csv(output_dir / "leaderboard.csv", encoding="utf-8-sig", index=False)
    leaderboard.to_excel(output_dir / "leaderboard.xlsx", index=False)

    best = candidates[0] if isinstance(candidates, list) else candidates
    finalized = experiment.finalize_model(best)
    version = datetime.now(timezone.utc).strftime(
        f"pycaret-{sensor_type}-{role}-%Y%m%dT%H%M%SZ"
    )
    package = {
        "model": finalized,
        "model_type": "pycaret_condition_classifier",
        "model_version": version,
        "sensor_type": sensor_type,
        "sensor_role": role,
        "feature_names": FEATURES,
        "training_rows": len(training),
        "test_rows": len(testing),
        "split_unit": "experiment_id",
        "training_start": str(data["measured_at"].iloc[0]),
        "training_end": str(data["measured_at"].iloc[-1]),
    }
    package_path = output_dir / "model_package.pkl"
    joblib.dump(package, package_path)
    print(f"\n[{sensor_type} -> {role}]\n{leaderboard.head(10).to_string(index=False)}")
    print(f"selected={type(best).__name__} package={package_path.resolve()}")
    return package_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare separate PyCaret models for vibration and sound"
    )
    parser.add_argument("--database", type=Path, default=ROOT / "data/smart_cylinder.db")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/model_comparison")
    parser.add_argument("--sensor", choices=[*SENSOR_ROLES, "all"], default="all")
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--fold", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    sensors = SENSOR_ROLES if args.sensor == "all" else (args.sensor,)
    failures = []
    for sensor_type in sensors:
        try:
            compare_sensor(
                args.database, args.output_dir, sensor_type,
                args.min_rows, args.fold, args.seed,
            )
        except (ValueError, RuntimeError, ImportError) as exc:
            failures.append(str(exc))
    if failures:
        raise SystemExit("Cannot compare models yet:\n- " + "\n- ".join(failures))


if __name__ == "__main__":
    main()
