"""Create a three-model JSON input with feature values below dataset averages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FEATURES = [
    "mean", "standard_deviation", "rms", "maximum", "minimum", "peak",
    "peak_to_peak", "crest_factor", "dominant_frequency", "dominant_amplitude",
    "spectral_energy",
]
DEFAULT_VIBRATION = ROOT / "data/export/pycaret_server/vibration_train.csv"
DEFAULT_SOUND = ROOT / "data/export/pycaret_server/sound_train.csv"
DEFAULT_OUTPUT = ROOT / "inputs/below_average_model_input.json"


def lower_than_average(frame: pd.DataFrame, ratio: float) -> dict[str, float]:
    missing = [name for name in FEATURES if name not in frame.columns]
    if missing:
        raise ValueError(f"필수 열 누락: {', '.join(missing)}")
    averages = frame[FEATURES].mean(numeric_only=True)
    result = {}
    for name in FEATURES:
        average = float(averages[name])
        # 음수 평균은 곱하면 숫자가 커지므로 절댓값의 ratio만큼 더 빼준다.
        value = average * ratio if average >= 0 else average - abs(average) * (1 - ratio)
        result[name] = round(value, 8)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="평균보다 낮은 통합 모델 입력 JSON 생성")
    parser.add_argument("--ratio", type=float, default=0.8, help="평균 대비 비율(기본 0.8)")
    parser.add_argument("--vibration", type=Path, default=DEFAULT_VIBRATION)
    parser.add_argument("--sound", type=Path, default=DEFAULT_SOUND)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not 0 < args.ratio < 1:
        parser.error("--ratio는 0보다 크고 1보다 작아야 합니다.")
    payload = {
        "vibration": lower_than_average(pd.read_csv(args.vibration), args.ratio),
        "sound": lower_than_average(pd.read_csv(args.sound), args.ratio),
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
