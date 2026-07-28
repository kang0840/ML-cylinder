"""Glue-sealed INMP441 microphone 24-bit PCM simulator."""

import argparse
import datetime as dt
import json
import random
import time
from pathlib import Path

import numpy as np


PCM_MIN = -8_388_608
PCM_MAX = 8_388_607
SENSOR_NAME = "INMP441 (\uae00\ub8e8\uac74 \ucc98\ub9ac)"


def create_pcm_block(
    sample_count: int,
    rng: np.random.Generator,
    operating_hours: float = 0.0,
) -> np.ndarray:
    """Generate a block of near-zero signed 24-bit PCM samples."""
    health = np.clip(operating_hours / 250.0, 0.0, 1.2)
    noise_rms = 25_000 + 45_000 * health
    samples = np.rint(rng.normal(0, noise_rms, sample_count))
    return np.clip(samples, PCM_MIN, PCM_MAX).astype(np.int32)


def create_reading() -> dict:
    raw_pcm = max(PCM_MIN, min(PCM_MAX, round(random.gauss(0, 40_000))))
    return {
        "sensor": SENSOR_NAME,
        "format": "I2S PCM 24bit",
        "raw_pcm": raw_pcm,
        "normalized": round(raw_pcm / 8_388_608, 8),
        "pcm_min": PCM_MIN,
        "pcm_max": PCM_MAX,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="INMP441 24-bit PCM simulator")
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("inmp441_microphone_data.json"))
    args = parser.parse_args()
    if args.interval <= 0 or args.count < 0:
        parser.error("interval must be positive and count cannot be negative")

    completed = 0
    try:
        while args.count == 0 or completed < args.count:
            reading = create_reading()
            args.output.write_text(json.dumps(reading, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(reading, ensure_ascii=False), flush=True)
            completed += 1
            if args.count == 0 or completed < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
