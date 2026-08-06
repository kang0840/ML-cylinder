"""실제 Mosquitto로 Pico W 형식의 시험 데이터를 발행한다."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import sys

import numpy as np
import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings


def generate_samples(condition: str, sensor: str, sample_rate: int, count: int, rng: np.random.Generator) -> list[int]:
    t = np.arange(count) / sample_rate
    profiles = {"normal": (80, 120), "pressure_drop": (45, 80), "seal_leak": (240, 300), "internal_wear": (110, 500), "random_noise": (0, 800)}
    frequency, amplitude = profiles[condition]
    signal = amplitude * np.sin(2 * np.pi * frequency * t) + rng.normal(0, amplitude * 0.12 + 5, count)
    if sensor == "inmp441":
        signal *= 20
    return np.rint(signal).astype(int).tolist()


def main() -> None:
    parser = argparse.ArgumentParser(description="스마트 실린더 MQTT 더미 발행기")
    parser.add_argument("--condition", choices=["normal", "pressure_drop", "seal_leak", "internal_wear", "random_noise"], default="normal")
    parser.add_argument("--device-id", default="pico01")
    parser.add_argument("--sample-rate", type=int, default=1600)
    parser.add_argument("--samples", type=int, default=1600)
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    settings, rng = Settings.load(), np.random.default_rng()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    client.connect(settings.mqtt_host, settings.mqtt_port, settings.mqtt_keepalive)
    client.loop_start()
    try:
        for sequence in range(1, args.count + 1):
            for sensor in ("sph0645", "inmp441"):
                payload = {"device_id": args.device_id, "sensor_type": sensor, "sample_rate": args.sample_rate, "cylinder_state": "forward", "sequence": sequence, "timestamp": time.time(), "samples": generate_samples(args.condition, sensor, args.sample_rate, args.samples, rng)}
                topic = f"smartCylinder/{args.device_id}/{sensor}/raw"
                client.publish(topic, json.dumps(payload), qos=1).wait_for_publish()
                print(f"발행 완료: {topic} sequence={sequence}")
            time.sleep(0.2)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
