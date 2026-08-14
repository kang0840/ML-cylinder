"""기존 Voting 패키지에서 검증 완료된 첫 번째 결정트리만 남긴다."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import joblib

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "pycaret_final"

def convert(path: Path) -> None:
    package = joblib.load(path)
    pipeline = deepcopy(package["model"])
    voting = pipeline.steps[-1][1]
    if not hasattr(voting, "estimators_"):
        print(f"이미 단일 모델: {path.name}"); return
    pipeline.steps[-1] = ("actual_estimator", deepcopy(voting.estimators_[0]))
    package["model"] = pipeline
    package["model_type"] = "pycaret_single_decision_tree"
    package["selected_model"] = "dt"
    package["selected_random_models"] = ["dt"]
    package["model_version"] = "pycaret-single-dt-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    joblib.dump(package, path)
    print(f"변환 완료: {path.name}")

if __name__ == "__main__":
    for model_path in sorted(MODEL_DIR.glob("*.pkl")): convert(model_path)
