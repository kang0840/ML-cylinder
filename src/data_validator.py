"""Pico W MQTT JSON 데이터 검증."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

VALID_SENSORS = {"sph0645", "inmp441"}
SENSOR_ROLES = {"sph0645": "acoustic_vibration", "inmp441": "sound"}
VALID_STATES = {"forward", "backward", "idle"}
REQUIRED_FIELDS = {"device_id", "sensor_type", "sample_rate", "cylinder_state", "sequence", "timestamp", "samples"}
MAX_SAMPLES = 100_000


@dataclass(frozen=True, slots=True)
class SensorPacket:
    device_id: str
    sensor_type: str
    sample_rate: int
    cylinder_state: str
    sequence: int
    timestamp: float
    samples: tuple[float, ...]
    health_score_target: float | None = None
    boot_id: str = "legacy"
    condition_target: str | None = None


def validate_sensor_payload(payload: Any) -> SensorPacket:
    """JSON 객체를 검증하고 변경 불가능한 센서 패킷을 반환한다."""
    if not isinstance(payload, dict):
        raise ValueError("메시지는 JSON 객체여야 합니다.")
    missing = REQUIRED_FIELDS - payload.keys()
    if missing:
        raise ValueError(f"필수 항목이 없습니다: {', '.join(sorted(missing))}")
    device_id = payload["device_id"]
    sensor = payload["sensor_type"]
    state = payload["cylinder_state"]
    sample_rate = payload["sample_rate"]
    sequence = payload["sequence"]
    timestamp = payload["timestamp"]
    samples = payload["samples"]
    target = payload.get("health_score_target")
    condition_target = payload.get("condition_target")
    if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 64:
        raise ValueError("device_id는 1~64자의 문자열이어야 합니다.")
    if sensor not in VALID_SENSORS:
        raise ValueError(f"sensor_type은 {sorted(VALID_SENSORS)} 중 하나여야 합니다.")
    if state not in VALID_STATES:
        raise ValueError(f"cylinder_state는 {sorted(VALID_STATES)} 중 하나여야 합니다.")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 1 <= sample_rate <= 192_000:
        raise ValueError("sample_rate는 1~192000 범위의 정수여야 합니다.")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("sequence는 0 이상의 정수여야 합니다.")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError("timestamp는 양의 Unix 시간이어야 합니다.")
    if not isinstance(samples, list) or not 2 <= len(samples) <= MAX_SAMPLES:
        raise ValueError(f"samples는 2~{MAX_SAMPLES}개 값의 배열이어야 합니다.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in samples):
        raise ValueError("samples에는 유한한 숫자만 사용할 수 있습니다.")
    if target is not None and (
        isinstance(target, bool)
        or not isinstance(target, (int, float))
        or not math.isfinite(target)
        or not 0 <= target <= 100
    ):
        raise ValueError("health_score_target은 0~100 사이의 숫자여야 합니다.")
    boot_id = payload.get("boot_id", "legacy")
    if not isinstance(boot_id, str) or not boot_id.strip() or len(boot_id) > 64:
        raise ValueError("boot_id must be a non-empty string of at most 64 characters")
    valid_conditions = {"normal", "pressure_drop", "seal_leak", "internal_wear"}
    if condition_target is not None and condition_target not in valid_conditions:
        raise ValueError(f"condition_target must be one of {sorted(valid_conditions)}")
    return SensorPacket(
        device_id.strip(), sensor, sample_rate, state, sequence, float(timestamp),
        tuple(float(v) for v in samples), None if target is None else float(target),
        boot_id.strip(), condition_target,
    )


def expand_dual_microphone_payload(payload: Any) -> tuple[dict[str, Any], ...]:
    """Convert a dual-channel Pico W packet into normal sensor packets."""
    if not isinstance(payload, dict) or payload.get("sensor_id") != "dual_i2s_mic":
        return (payload,)
    required = {"device_id", "sequence", "sample_rate_hz", "left_samples", "right_samples"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"dual microphone payload missing: {', '.join(sorted(missing))}")
    common = {
        "device_id": payload["device_id"], "sample_rate": payload["sample_rate_hz"],
        "cylinder_state": payload.get("cylinder_state", "idle"),
        "sequence": payload["sequence"], "timestamp": payload.get("timestamp", time.time()),
        "boot_id": payload.get("boot_id", "legacy"),
    }
    return (
        {**common, "sensor_type": "sph0645", "samples": payload["left_samples"]},
        {**common, "sensor_type": "inmp441", "samples": payload["right_samples"]},
    )
