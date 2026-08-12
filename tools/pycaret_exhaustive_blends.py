"""Exhaustively evaluate every fixed-size PyCaret blend on training CV."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pycaret_random_ensemble import (
    _mean_cv_score,
    _prediction_column,
    independent_scores,
    infer_task,
    prepare_frames,
    read_table,
)


def exhaustive_search(
    train_path: Path,
    test_path: Path,
    target: str,
    model_ids: list[str],
    output_dir: Path,
    task: str = "auto",
    blend_size: int = 3,
    fold: int = 3,
    seed: int = 42,
) -> Path:
    if sys.version_info[:2] not in {(3, 9), (3, 10), (3, 11)}:
        raise RuntimeError("run with the dedicated Python 3.11 PyCaret environment")
    if not 2 <= blend_size <= len(model_ids):
        raise ValueError("blend_size must be between 2 and the number of models")
    train_raw, test_raw = read_table(train_path), read_table(test_path)
    train, test, feature_names = prepare_frames(train_raw, test_raw, target)
    resolved_task = infer_task(train[target]) if task == "auto" else task
    metric = "F1" if resolved_task == "classification" else "R2"
    fold = max(2, min(fold, len(train) // 5))

    if resolved_task == "classification":
        from pycaret.classification import ClassificationExperiment
        experiment: Any = ClassificationExperiment()
    else:
        from pycaret.regression import RegressionExperiment
        experiment = RegressionExperiment()
    experiment.setup(
        data=train, target=target, fold=fold, session_id=seed,
        normalize=True, n_jobs=-1, html=False, verbose=False,
    )
    available = set(experiment.models().index)
    missing = [model_id for model_id in model_ids if model_id not in available]
    if missing:
        raise ValueError(f"unavailable PyCaret model IDs: {missing}")

    trained = {
        model_id: experiment.create_model(model_id, fold=fold, verbose=False)
        for model_id in model_ids
    }
    rows: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    combinations = list(itertools.combinations(model_ids, blend_size))
    for index, combination in enumerate(combinations, 1):
        model = experiment.blend_models(
            estimator_list=[trained[item] for item in combination],
            fold=fold, optimize=metric, choose_better=False, verbose=False,
        )
        score = _mean_cv_score(experiment.pull(), metric)
        row = {
            "rank_pending": index,
            "models": "+".join(combination),
            "cv_metric": metric,
            "cv_score": score,
            "model": model,
        }
        rows.append(row)
        if best is None or score > best["cv_score"]:
            best = row
        print(f"[{index}/{len(combinations)}] {row['models']} {metric}={score:.6f}")

    assert best is not None
    finalized = experiment.finalize_model(best["model"])
    predictions = experiment.predict_model(finalized, data=test, verbose=False)
    best.update(independent_scores(
        resolved_task, predictions[target], predictions[_prediction_column(predictions)]
    ))
    leaderboard = pd.DataFrame(
        [{k: v for k, v in row.items() if k != "model"} for row in rows]
    ).sort_values("cv_score", ascending=False).reset_index(drop=True)
    leaderboard.insert(0, "rank", range(1, len(leaderboard) + 1))
    leaderboard["selected"] = leaderboard["models"] == best["models"]
    for key, value in best.items():
        if key.startswith("test_"):
            leaderboard.loc[leaderboard["selected"], key] = value

    output_dir.mkdir(parents=True, exist_ok=True)
    leaderboard.to_csv(output_dir / "exhaustive_leaderboard.csv", index=False, encoding="utf-8-sig")
    leaderboard.to_excel(output_dir / "exhaustive_leaderboard.xlsx", index=False)
    predictions.to_csv(output_dir / "winner_test_predictions.csv", index=False, encoding="utf-8-sig")
    package = {
        "model": finalized,
        "model_type": f"pycaret_{resolved_task}_exhaustive_{blend_size}_blend",
        "model_version": datetime.now(timezone.utc).strftime("pycaret-exhaustive-%Y%m%dT%H%M%SZ"),
        "target": target,
        "feature_names": feature_names,
        "candidate_models": model_ids,
        "evaluated_combinations": len(combinations),
        "selected_configuration": best["models"],
        "selection_metric": metric,
        "selection_cv_score": best["cv_score"],
        "seed": seed,
    }
    package_path = output_dir / "best_model_package.pkl"
    joblib.dump(package, package_path)
    (output_dir / "run_summary.json").write_text(
        json.dumps({k: v for k, v in package.items() if k != "model"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"winner={best['models']} cv_{metric}={best['cv_score']:.6f}")
    return package_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Exhaustively compare PyCaret model blends")
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--models", required=True, help="comma-separated PyCaret model IDs")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task", choices=["auto", "classification", "regression"], default="auto")
    parser.add_argument("--blend-size", type=int, default=3)
    parser.add_argument("--fold", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    exhaustive_search(
        args.train, args.test, args.target,
        [item.strip() for item in args.models.split(",") if item.strip()],
        args.output_dir, args.task, args.blend_size, args.fold, args.seed,
    )


if __name__ == "__main__":
    main()
