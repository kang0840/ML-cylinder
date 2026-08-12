"""Search random PyCaret model blends using separate train and test files.

The winner is selected only from cross-validation on the training file.  The
test file remains unseen until every candidate has been trained and is used to
report the final, independent scores.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)


CLASSIFICATION_MODELS = (
    "lr", "knn", "nb", "dt", "svm", "ridge", "rf", "qda", "ada",
    "gbc", "lda", "et",
)
REGRESSION_MODELS = (
    "lr", "lasso", "ridge", "en", "lar", "llar", "omp", "br", "par",
    "ransac", "tr", "huber", "kr", "svm", "knn", "dt", "rf", "et",
    "ada", "gbr",
)
DEFAULT_IGNORED_COLUMNS = {
    "measurement_id", "device_id", "boot_id", "measured_at", "created_at",
    "updated_at", "sample_index", "sequence",
}


def read_table(path: Path, sheet_name: str | int = 0) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(path, sheet_name=sheet_name)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t")
    raise ValueError(f"unsupported data file: {path} (use CSV, TSV, XLS, or XLSX)")


def infer_task(target: pd.Series) -> str:
    non_null = target.dropna()
    if non_null.empty:
        raise ValueError("target column has no labelled rows")
    if (
        isinstance(non_null.dtype, pd.CategoricalDtype)
        or pd.api.types.is_bool_dtype(non_null)
        or pd.api.types.is_object_dtype(non_null)
        or pd.api.types.is_string_dtype(non_null)
    ):
        return "classification"
    unique = int(non_null.nunique())
    classification_limit = max(10, min(30, int(len(non_null) * 0.05)))
    return "classification" if unique <= classification_limit else "regression"


def prepare_frames(
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: str,
    features: Iterable[str] | None = None,
    ignore: Iterable[str] = (),
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    if target not in train.columns:
        raise ValueError(f"training file is missing target column: {target}")
    if target not in test.columns:
        raise ValueError(f"test file is missing target column: {target}")

    ignored = DEFAULT_IGNORED_COLUMNS | set(ignore)
    if features is None:
        selected = [
            name for name in train.columns
            if name != target and name not in ignored and name in test.columns
        ]
    else:
        selected = list(dict.fromkeys(features))
    if not selected:
        raise ValueError("no feature columns were selected")
    missing_train = [name for name in selected if name not in train.columns]
    missing_test = [name for name in selected if name not in test.columns]
    if missing_train or missing_test:
        raise ValueError(
            f"missing feature columns: train={missing_train}, test={missing_test}"
        )

    train_frame = train[[*selected, target]].dropna(subset=[target]).copy()
    test_frame = test[[*selected, target]].dropna(subset=[target]).copy()
    if len(train_frame) < 30:
        raise ValueError(f"at least 30 labelled training rows are required: {len(train_frame)}")
    if test_frame.empty:
        raise ValueError("test file has no labelled rows")
    return train_frame, test_frame, selected


def choose_random_models(
    available: Iterable[str], task: str, count: int, seed: int,
    requested_pool: Iterable[str] | None = None,
) -> list[str]:
    if count < 2:
        raise ValueError("random model count must be at least 2")
    preferred = tuple(requested_pool or (
        CLASSIFICATION_MODELS if task == "classification" else REGRESSION_MODELS
    ))
    available_set = set(available)
    pool = [model_id for model_id in preferred if model_id in available_set]
    if len(pool) < count:
        raise ValueError(
            f"only {len(pool)} usable PyCaret models are available; {count} required"
        )
    return random.Random(seed).sample(pool, count)


def ensemble_combinations(model_ids: Iterable[str]) -> list[tuple[str, ...]]:
    ids = tuple(model_ids)
    return [
        combination
        for size in range(2, len(ids) + 1)
        for combination in itertools.combinations(ids, size)
    ]


def _mean_cv_score(grid: pd.DataFrame, metric: str) -> float:
    if metric not in grid.columns:
        raise ValueError(f"PyCaret result has no {metric!r} metric: {list(grid.columns)}")
    for index, row in grid.iterrows():
        if str(index).strip().lower() == "mean":
            return float(row[metric])
        if len(row) and str(row.iloc[0]).strip().lower() == "mean":
            return float(row[metric])
    numeric = pd.to_numeric(grid[metric], errors="coerce").dropna()
    if numeric.empty:
        raise ValueError(f"PyCaret did not return a numeric {metric} score")
    # Fold rows normally precede Mean and Std.  This fallback is compatible
    # with PyCaret grids that omit the textual summary labels.
    return float(numeric.iloc[:-1].mean() if len(numeric) > 2 else numeric.mean())


def _prediction_column(frame: pd.DataFrame) -> str:
    for name in ("prediction_label", "Label"):
        if name in frame.columns:
            return name
    raise ValueError("PyCaret prediction output has no prediction label column")


def independent_scores(task: str, truth: pd.Series, prediction: pd.Series) -> dict[str, float]:
    if task == "classification":
        return {
            "test_accuracy": float(accuracy_score(truth, prediction)),
            "test_f1": float(f1_score(truth, prediction, average="weighted", zero_division=0)),
            "test_precision": float(precision_score(truth, prediction, average="weighted", zero_division=0)),
            "test_recall": float(recall_score(truth, prediction, average="weighted", zero_division=0)),
        }
    truth_values = pd.to_numeric(truth, errors="raise").to_numpy(dtype=float)
    prediction_values = pd.to_numeric(prediction, errors="raise").to_numpy(dtype=float)
    return {
        "test_r2": float(r2_score(truth_values, prediction_values)),
        "test_mae": float(mean_absolute_error(truth_values, prediction_values)),
        "test_rmse": float(mean_squared_error(truth_values, prediction_values) ** 0.5),
    }


def select_winner(
    configurations: list[dict[str, Any]], model_count: int, scope: str
) -> dict[str, Any]:
    eligible = configurations
    if scope == "full_blend":
        eligible = [
            item for item in configurations
            if item["kind"] == "blend"
            and len(str(item["models"]).split("+")) == model_count
        ]
    if not eligible:
        raise ValueError(f"no eligible model configuration for selection scope: {scope}")
    return max(eligible, key=lambda item: item["cv_score"])


def run_search(
    train_path: Path,
    test_path: Path,
    target: str,
    output_dir: Path,
    task: str = "auto",
    seed: int = 42,
    fold: int = 5,
    model_count: int = 3,
    features: Iterable[str] | None = None,
    ignore: Iterable[str] = (),
    candidate_pool: Iterable[str] | None = None,
    train_sheet: str | int = 0,
    test_sheet: str | int = 0,
    selection_scope: str = "all",
) -> Path:
    if sys.version_info[:2] not in {(3, 9), (3, 10), (3, 11)}:
        raise RuntimeError(
            f"PyCaret 3.3.2 requires the dedicated Python 3.9-3.11 environment; "
            f"current Python is {sys.version_info.major}.{sys.version_info.minor}."
        )
    train_raw = read_table(train_path, train_sheet)
    test_raw = read_table(test_path, test_sheet)
    train, test, feature_names = prepare_frames(
        train_raw, test_raw, target, features, ignore
    )
    resolved_task = infer_task(train[target]) if task == "auto" else task
    metric = "F1" if resolved_task == "classification" else "R2"
    fold = max(2, min(fold, len(train) // 5))

    if resolved_task == "classification":
        from pycaret.classification import ClassificationExperiment
        experiment: Any = ClassificationExperiment()
        setup_kwargs = {"fix_imbalance": False}
    else:
        from pycaret.regression import RegressionExperiment
        experiment = RegressionExperiment()
        setup_kwargs = {}

    experiment.setup(
        data=train,
        target=target,
        fold=fold,
        session_id=seed,
        normalize=True,
        n_jobs=-1,
        html=False,
        verbose=False,
        **setup_kwargs,
    )
    selected_ids = choose_random_models(
        experiment.models().index, resolved_task, model_count, seed, candidate_pool
    )

    trained: dict[str, Any] = {}
    configurations: list[dict[str, Any]] = []
    for model_id in selected_ids:
        model = experiment.create_model(model_id, fold=fold, verbose=False)
        trained[model_id] = model
        configurations.append({
            "configuration": model_id,
            "kind": "single",
            "models": model_id,
            "cv_score": _mean_cv_score(experiment.pull(), metric),
            "model": model,
        })

    for combination in ensemble_combinations(selected_ids):
        name = "blend(" + "+".join(combination) + ")"
        model = experiment.blend_models(
            estimator_list=[trained[item] for item in combination],
            fold=fold,
            optimize=metric,
            choose_better=False,
            verbose=False,
        )
        configurations.append({
            "configuration": name,
            "kind": "blend",
            "models": "+".join(combination),
            "cv_score": _mean_cv_score(experiment.pull(), metric),
            "model": model,
        })

    # Select before looking at the independent test scores.
    winner = select_winner(configurations, model_count, selection_scope)
    winner_predictions: pd.DataFrame | None = None
    for configuration in configurations:
        finalized = experiment.finalize_model(configuration["model"])
        predictions = experiment.predict_model(finalized, data=test, verbose=False)
        prediction_name = _prediction_column(predictions)
        configuration.update(independent_scores(
            resolved_task, predictions[target], predictions[prediction_name]
        ))
        if configuration is winner:
            configuration["finalized_model"] = finalized
            winner_predictions = predictions

    output_dir.mkdir(parents=True, exist_ok=True)
    report_rows = [{k: v for k, v in row.items() if k not in {"model", "finalized_model"}}
                   for row in configurations]
    leaderboard = pd.DataFrame(report_rows).sort_values("cv_score", ascending=False)
    leaderboard.insert(0, "selected_by_train_cv", leaderboard["configuration"] == winner["configuration"])
    leaderboard.to_csv(output_dir / "ensemble_leaderboard.csv", index=False, encoding="utf-8-sig")
    leaderboard.to_excel(output_dir / "ensemble_leaderboard.xlsx", index=False)
    assert winner_predictions is not None
    winner_predictions.to_csv(output_dir / "test_predictions.csv", index=False, encoding="utf-8-sig")

    version = datetime.now(timezone.utc).strftime("pycaret-random-ensemble-%Y%m%dT%H%M%SZ")
    package = {
        "model": winner["finalized_model"],
        "model_type": f"pycaret_{resolved_task}_random_blend",
        "model_version": version,
        "target": target,
        "feature_names": feature_names,
        "selected_random_models": selected_ids,
        "selected_configuration": winner["configuration"],
        "selection_metric": metric,
        "selection_scope": selection_scope,
        "selection_cv_score": winner["cv_score"],
        "training_rows": len(train),
        "test_rows": len(test),
        "seed": seed,
    }
    package_path = output_dir / "best_model_package.pkl"
    joblib.dump(package, package_path)
    metadata = {key: value for key, value in package.items() if key != "model"}
    (output_dir / "run_summary.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"random models: {', '.join(selected_ids)}")
    print(leaderboard.to_string(index=False))
    print(f"selected by training CV: {winner['configuration']}")
    print(f"saved: {package_path.resolve()}")
    return package_path


def _split_names(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _sheet(value: str | int) -> str | int:
    if isinstance(value, int):
        return value
    return int(value) if value.isdigit() else value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Randomly select PyCaret models and compare every blend"
    )
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/model_comparison/random_ensemble"))
    parser.add_argument("--task", choices=["auto", "classification", "regression"], default="auto")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fold", type=int, default=5)
    parser.add_argument("--model-count", type=int, default=3)
    parser.add_argument("--features", help="comma-separated feature columns")
    parser.add_argument("--ignore", help="comma-separated columns to exclude")
    parser.add_argument("--candidate-pool", help="comma-separated PyCaret model IDs")
    parser.add_argument("--train-sheet", default=0)
    parser.add_argument("--test-sheet", default=0)
    parser.add_argument(
        "--selection-scope", choices=["all", "full_blend"], default="all",
        help="select from every configuration or require all random models in the blend",
    )
    args = parser.parse_args()
    run_search(
        args.train, args.test, args.target, args.output_dir, args.task,
        args.seed, args.fold, args.model_count, _split_names(args.features),
        _split_names(args.ignore) or (), _split_names(args.candidate_pool),
        _sheet(args.train_sheet), _sheet(args.test_sheet), args.selection_scope,
    )


if __name__ == "__main__":
    main()
