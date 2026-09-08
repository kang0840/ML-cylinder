from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.three_model_inference import ThreeModelInference

FEATURES = ["mean", "standard_deviation", "rms", "maximum", "minimum", "peak", "peak_to_peak", "crest_factor", "dominant_frequency", "dominant_amplitude", "spectral_energy"]
NAMESPACE = uuid.UUID("9ccda03d-1bb5-4a83-a6b8-9e6a7f0e4ea1")


def source_row(path: Path) -> sqlite3.Row:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("""
            SELECT c.id AS combined_id,c.measured_at AS source_measured_at,m.cylinder_state,
              vf.mean AS vibration_mean,vf.standard_deviation AS vibration_standard_deviation,
              vf.rms AS vibration_rms,vf.maximum AS vibration_maximum,vf.minimum AS vibration_minimum,
              vf.peak AS vibration_peak,vf.peak_to_peak AS vibration_peak_to_peak,
              vf.crest_factor AS vibration_crest_factor,vf.dominant_frequency AS vibration_dominant_frequency,
              vf.dominant_amplitude AS vibration_dominant_amplitude,vf.spectral_energy AS vibration_spectral_energy,
              sf.mean AS sound_mean,sf.standard_deviation AS sound_standard_deviation,
              sf.rms AS sound_rms,sf.maximum AS sound_maximum,sf.minimum AS sound_minimum,
              sf.peak AS sound_peak,sf.peak_to_peak AS sound_peak_to_peak,
              sf.crest_factor AS sound_crest_factor,sf.dominant_frequency AS sound_dominant_frequency,
              sf.dominant_amplitude AS sound_dominant_amplitude,sf.spectral_energy AS sound_spectral_energy
            FROM combined_results c
            JOIN measurements m ON m.measurement_id=c.vibration_measurement_id
            JOIN feature_data vf ON vf.measurement_id=c.vibration_measurement_id
            JOIN feature_data sf ON sf.measurement_id=c.sound_measurement_id
            ORDER BY c.id ASC LIMIT 1
        """).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("No paired historical sensor data is available.")
    return row


def feature_values(row: sqlite3.Row, prefix: str) -> dict[str, float]:
    return {name: float(row[f"{prefix}_{name}"]) for name in FEATURES}


def replay_payload(source_db: Path, row: sqlite3.Row, result: dict, run_id: str) -> dict:
    replay_id = str(uuid.uuid5(NAMESPACE, f"{source_db.resolve()}:{row['combined_id']}:dashboard-replay-v1"))
    return {
        "measurement_id": replay_id, "device_id": "historical-replay", "sensor_type": "sph0645",
        "measured_at": datetime.now(timezone.utc).isoformat(), "data_source": "replay",
        "replay_source_measured_at": row["source_measured_at"], "replay_run_id": run_id,
        "cylinder_state": row["cylinder_state"] or "idle",
        "vibration_rms": float(row["vibration_rms"]), "sound_rms": float(row["sound_rms"]),
        "dominant_frequency": float(row["vibration_dominant_frequency"]),
        "dominant_amplitude": float(row["vibration_dominant_amplitude"]),
        "prediction": result["overall_status"],
        "confidence": max(float(result["vibration"]["confidence"]), float(result["sound"]["confidence"])),
        "health_score": float(result["health_score"]), "model_version": "historical-replay-v1",
    }


def upload(payload: dict) -> tuple[int | None, int | None, str | None]:
    load_dotenv(ROOT / ".env")
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_KEY", "").strip()
    table = os.environ.get("SUPABASE_TABLE", "smart_cylinder_analysis").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY is not configured.")
    from supabase import create_client
    client = create_client(url, key)
    before = client.table(table).select("measurement_id", count="exact").eq("data_source", "replay").execute()
    client.table(table).upsert(payload, on_conflict="measurement_id").execute()
    after = client.table(table).select("measurement_id,measured_at", count="exact").eq("data_source", "replay").order("measured_at", desc=True).limit(1).execute()
    return before.count, after.count, after.data[0]["measured_at"] if after.data else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--run-id", default="manual-replay")
    args = parser.parse_args()
    if not args.source_db.is_file():
        parser.error(f"Source DB not found: {args.source_db}")

    row = source_row(args.source_db)
    result = ThreeModelInference().predict(feature_values(row, "vibration"), feature_values(row, "sound"))
    payload = replay_payload(args.source_db, row, result, args.run_id)
    print("=== replay inference ===")
    print(f"source_combined_id={row['combined_id']}")
    print(f"replay_measurement_id={payload['measurement_id']}")
    print(f"prediction={result['overall_status']}")
    print(f"vibration_confidence={result['vibration']['confidence']:.4f}")
    print(f"sound_confidence={result['sound']['confidence']:.4f}")
    print(f"health_score={result['health_score']:.1f}")
    print(f"vibration_rms={payload['vibration_rms']:.6f}")
    print(f"sound_rms={payload['sound_rms']:.6f}")
    if not args.upload:
        print("dry_run=true; SQLite and Supabase were not modified")
        return
    before, after, latest = upload(payload)
    print(f"replay_count_before={before}")
    print(f"replay_count_after={after}")
    print(f"replay_latest_measured_at={latest}")
    print("uploaded_data_source=replay")


if __name__ == "__main__":
    main()
