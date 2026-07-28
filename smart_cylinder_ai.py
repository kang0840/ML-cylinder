"""Smart Cylinder Case v4: features -> RF -> zone -> health score -> RUL."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from adxl345_simulator import SENSOR_NAME as VIBRATION_SENSOR, create_accel_block
from inmp441_microphone import SENSOR_NAME as SOUND_SENSOR, create_pcm_block
from src.health_assessment import (
    ZONE_FEATURES, classify_statistical_zone, estimate_rul_from_rms,
    fit_normal_baseline, health_score,
)
from src.excel_dataset import (
    MODEL_FEATURES, append_measurement, create_excel_dataset, load_rms_history,
    load_zone_d_rms_threshold,
    train_model_from_excel,
)
from src.vibration_features import extract_vibration_features


ZONE_NAMES = "ABCD"
ZONE_STATUS = ("정상", "양호", "주의", "위험")
FEATURE_NAMES = (*ZONE_FEATURES, "operating_hours")


def create_demo_training_data(seed: int = 42) -> tuple[np.ndarray, np.ndarray, dict]:
    """Create replaceable demo data following the v4 examples."""
    rng = np.random.default_rng(seed)
    centers = np.array([
        [0.82, 1.21, 1.47, 82.0, 31.0, 20.0],
        [0.95, 1.74, 1.83, 84.0, 37.0, 45.0],
        [1.16, 2.92, 2.51, 87.0, 49.0, 120.0],
        [1.42, 4.01, 2.82, 92.0, 64.0, 180.0],
    ])
    scales = np.array([0.04, 0.10, 0.05, 0.45, 1.2, 5.0])
    blocks = [rng.normal(center, scales, size=(200, 6)) for center in centers]
    x = np.maximum(np.vstack(blocks), 0.0)
    y = np.repeat(np.arange(4), 200)
    normal_samples = [dict(zip(FEATURE_NAMES, row)) for row in blocks[0][:100]]
    return x, y, fit_normal_baseline(normal_samples)


def train_demo_model(seed: int = 42) -> tuple[RandomForestClassifier, dict]:
    x, y, baseline = create_demo_training_data(seed)
    model = RandomForestClassifier(
        n_estimators=200, max_depth=8, min_samples_leaf=3,
        class_weight="balanced", random_state=seed, n_jobs=-1,
    ).fit(x, y)
    return model, baseline


def analyze_once(fs: float, duration: float, operating_hours: float,
                 model: RandomForestClassifier, baseline: dict,
                 rms_history: list[tuple[float, float]],
                 rng: np.random.Generator) -> dict[str, object]:
    accel = create_accel_block(fs, duration, operating_hours, rng)
    sound_pcm = create_pcm_block(round(fs * duration), rng, operating_hours)
    vibration = extract_vibration_features(accel, fs)
    sound_rms = float(np.sqrt(np.mean(sound_pcm.astype(float) ** 2)) / 1_000.0)
    values = [float(vibration[name]) for name in ZONE_FEATURES[:4]] + [sound_rms, operating_hours]
    features = dict(zip(FEATURE_NAMES, values))
    model_values = [features[name] for name in MODEL_FEATURES]
    prediction = int(model.predict(np.asarray([model_values]))[0])
    probabilities = model.predict_proba(np.asarray([model_values]))[0]
    predicted_probability = float(probabilities[np.where(model.classes_ == prediction)[0][0]])
    statistical_zone = classify_statistical_zone(features, baseline)
    rms_history.append((operating_hours, features["rms_velocity_mm_s"]))
    zone_d_rms = float(baseline["zone_d_rms_threshold"])
    trend_projection = estimate_rul_from_rms(rms_history, zone_d_rms)
    rul_estimate = {
        "basis": "unavailable_without_run_to_failure_data",
        "status": "insufficient_lifecycle_data",
        "hours_to_zone_d": None,
        "rms_rate_mm_s_per_hour": trend_projection.get("rms_rate_mm_s_per_hour"),
        # A linear simulation projection is not a remaining-useful-life value.
        "experimental_zone_d_projection_hours": trend_projection.get("hours_to_zone_d"),
    }
    return {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sensors": {"vibration": VIBRATION_SENSOR, "sound": SOUND_SENSOR},
        "sampling": {"rate_hz": fs, "duration_seconds": duration, "samples": len(accel),
                     "analysis_band_hz": [2.0, 1000.0]},
        "features": features,
        "statistical_zone": statistical_zone,
        "ai_prediction": {"model": "RandomForestClassifier", "label": prediction,
                          "zone": ZONE_NAMES[prediction], "status": ZONE_STATUS[prediction],
                          "confidence": predicted_probability,
                          "oob_validation_accuracy": float(model.oob_score_),
                          "real_sensor_accuracy": None,
                          "accuracy_note": "학습 외 표본 정확도; 실제 센서 정확도가 아님"},
        "health_score": health_score(prediction),
        "rul_estimate": rul_estimate,
        "standards_note": {
            "signal_evaluation": "ISO 20816/ISO 13373 based workflow",
            "zone_health_rul_ml": "Project Criteria; validate on the FSQD cylinder",
        },
    }


def print_report(result: dict[str, object]) -> None:
    f, ai, rul = result["features"], result["ai_prediction"], result["rul_estimate"]
    print(f"\n구역 {ai['zone']} / {ai['status']} | 확신도 {ai['confidence']:.1%} | "
          f"건강도 {result['health_score']}/100")
    print(f"데모 모델 정확도 {ai['oob_validation_accuracy']:.1%} | "
          "실제 센서 정확도 산정 불가 (정답 라벨 필요)")
    print(f"진동 RMS {f['rms_velocity_mm_s']:.3f} mm/s | 최대 진동 {f['peak_velocity_mm_s']:.3f} mm/s | "
          f"파고율 {f['crest_factor']:.3f} | 주요 주파수 {f['dominant_frequency_hz']:.1f} Hz | "
          f"소음 {f['sound_rms']:.2f}")
    remaining = rul["hours_to_zone_d"]
    if rul.get("status") == "insufficient_lifecycle_data":
        print("잔여 수명(RUL): 산정 불가 (실측 고장 수명 데이터 필요)")
        return
    if remaining is None:
        reason = "insufficient trend data" if rul.get("rms_rate_mm_s_per_hour") is None else "no positive degradation trend"
        print(f"RUL: {reason}")
    else:
        print(f"RUL to Zone D: {remaining:.1f} operating hours")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Cylinder AI v4.0 demo")
    parser.add_argument("--fs", type=float, default=3_200.0,
                        help=">=2000 Hz required for the 2-1000 Hz band")
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--count", type=int, default=10,
                        help="number of measurements; 0 runs until Ctrl+C")
    parser.add_argument("--hours-step", type=float, default=1.0,
                        help="simulated operating hours advanced per measurement")
    parser.add_argument("--start-hours", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=Path("smart_cylinder_result.json"))
    parser.add_argument("--excel", type=Path, default=Path("smart_cylinder_training.xlsx"),
                        help="Excel file used for logging and Random Forest training")
    args = parser.parse_args()
    if (args.fs < 2_000 or args.duration <= 0 or args.interval <= 0
            or args.count < 0 or args.hours_step <= 0):
        parser.error("fs >= 2000, positive duration/interval/hours-step, and nonnegative count are required")
    demo_x, demo_y, baseline = create_demo_training_data()
    if not args.excel.exists():
        create_excel_dataset(args.excel, demo_x, demo_y)
        print(f"Created temporary Excel training data: {args.excel}")
    baseline["zone_d_rms_threshold"] = load_zone_d_rms_threshold(args.excel)
    rng = np.random.default_rng()
    history = load_rms_history(args.excel)
    hours = args.start_hours
    if history:
        hours = max(hours, history[-1][0] + args.hours_step)
        print(f"Resumed RUL trend from {len(history)} previous measurement(s).")
    completed = 0
    try:
        while args.count == 0 or completed < args.count:
            model, training_rows = train_model_from_excel(args.excel)
            result = analyze_once(args.fs, args.duration, hours, model, baseline, history, rng)
            result["ai_prediction"]["training_source"] = str(args.excel)
            result["ai_prediction"]["training_rows"] = training_rows
            append_measurement(
                args.excel, result["timestamp"], result["features"],
                result["statistical_zone"]["label"],
            )
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print_report(result)
            completed += 1
            hours += args.hours_step
            if args.count == 0 or completed < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
