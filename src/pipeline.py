"""검증부터 업로드까지 한 측정값의 처리 파이프라인."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from .data_validator import validate_sensor_payload
from .database import Database, DuplicateMeasurementError
from .feature_extractor import extract_features
from .ml_predictor import MLPredictor
from .supabase_uploader import SupabaseUploader

LOGGER = logging.getLogger(__name__)


class SensorPipeline:
    def __init__(self, database: Database, predictor: MLPredictor, uploader: SupabaseUploader, use_hann_window: bool, on_measurement_saved: Callable[[], None] | None = None):
        self.database, self.predictor, self.uploader = database, predictor, uploader
        self.use_hann_window = use_hann_window
        self.on_measurement_saved = on_measurement_saved

    def process(self, payload: dict) -> str | None:
        packet = validate_sensor_payload(payload)
        previous = self.database.last_sequence(packet.device_id, packet.sensor_type)
        if previous is not None and packet.sequence > previous + 1:
            LOGGER.warning("sequence 누락: %s/%s에서 %s~%s", packet.device_id, packet.sensor_type, previous + 1, packet.sequence - 1)
        if previous is not None and packet.sequence < previous:
            LOGGER.warning("순서가 뒤바뀐 패킷: 이전=%s, 수신=%s", previous, packet.sequence)
        features = extract_features(packet.samples, packet.sample_rate, self.use_hann_window)
        result = self.predictor.predict(features, packet.samples, packet.sensor_type)
        try:
            measurement_id = self.database.save_measurement(packet, features, result)
        except DuplicateMeasurementError:
            LOGGER.info("중복 패킷을 건너뜁니다: %s/%s/%s", packet.device_id, packet.sensor_type, packet.sequence)
            return None
        if packet.health_score_target is not None and self.on_measurement_saved is not None:
            self.on_measurement_saved()
        upload_payload = {
            "measurement_id": measurement_id, "device_id": packet.device_id,
            "sensor_type": packet.sensor_type,
            "measured_at": datetime.fromtimestamp(packet.timestamp, timezone.utc).isoformat(),
            "cylinder_state": packet.cylinder_state,
            "vibration_rms": features["rms"] if packet.sensor_type == "sph0645" else None,
            "sound_rms": features["rms"] if packet.sensor_type == "inmp441" else None,
            "dominant_frequency": features["dominant_frequency"],
            "dominant_amplitude": features["dominant_amplitude"], **result,
        }
        self.uploader.upload_or_queue(measurement_id, upload_payload)
        LOGGER.info("측정 처리 완료: id=%s device=%s sensor=%s prediction=%s", measurement_id, packet.device_id, packet.sensor_type, result["prediction"])
        return measurement_id
