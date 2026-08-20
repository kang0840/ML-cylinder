"""PyCaret 모델 패키지를 Raspberry Pi용 경량 패키지로 변환한다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import joblib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.portable_models import PortableConditionModel, PortableLifeModel


def step(model: object, name: str) -> object:
    return dict(model.steps)[name]


def export_condition(package: dict, destination: Path) -> None:
    model = package["model"]
    imputer = step(model, "numerical_imputer").transformer
    scaler = step(model, "normalize").transformer
    label_encoder = step(model, "label_encoding").transformer
    portable = PortableConditionModel(
        imputer.statistics_,
        scaler.mean_,
        scaler.scale_,
        step(model, "actual_estimator"),
        label_encoder.classes_,
    )
    output = dict(package)
    output["model"] = portable
    output["runtime"] = "raspberry-pi-portable-v1"
    joblib.dump(output, destination)


def export_life(package: dict, destination: Path) -> None:
    model = package["model"]
    scaler = step(model, "normalize").transformer
    numerical_imputer = step(model, "numerical_imputer").transformer
    categorical_imputer = step(model, "categorical_imputer").transformer
    portable = PortableLifeModel(
        numerical_imputer.statistics_,
        categorical_imputer.statistics_,
        scaler.mean_,
        scaler.scale_,
        step(model, "actual_estimator"),
        list(scaler.feature_names_in_),
    )
    output = dict(package)
    output["model"] = portable
    output["runtime"] = "raspberry-pi-portable-v1"
    joblib.dump(output, destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--life", action="store_true")
    args = parser.parse_args()
    package = joblib.load(args.source)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    if args.life:
        export_life(package, args.destination)
    else:
        export_condition(package, args.destination)
    print(args.destination)


if __name__ == "__main__":
    main()
