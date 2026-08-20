"""검증부터 업로드까지 한 측정값의 처리 파이프라인."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from .data_validator import expand_dual_microphone_payload, validate_sensor_payload
from .database import Database, DuplicateMeasurementError
from .acoustic_features import extract_acoustic_features
from .sensor_roles import metadata_for
from .ml_predictor import MLPredictor
from .inference_excel_store import InferenceExcelStore
from .supabase_uploader import SupabaseUploader
from .state_fusion import fuse_sensor_results
from .session_tracker import SessionTracker

LOGGER = logging.getLogger(__name__)


class SensorPipeline:
    def __init__(self, database: Database, predictor, uploader: SupabaseUploader, use_hann_window: bool, on_measurement_saved: Callable[[], None] | None = None, excel_store: InferenceExcelStore | None = None, rul_predictor=None):
        self.database, self.predictor, self.uploader = database, predictor, uploader
        self.use_hann_window = use_hann_window
        self.on_measurement_saved = on_measurement_saved
        self.excel_store = excel_store
        self.rul_predictor = rul_predictor
        self.session_tracker = SessionTracker()

    def process(self, payload: dict) -> str | None:
        payload = self.session_tracker.enrich(payload)
        expanded = expand_dual_microphone_payload(payload)
        if len(expanded) > 1:
            saved = [self._process_one(item) for item in expanded]
            return next((value for value in saved if value is not None), None)
        return self._process_one(expanded[0])

    def _process_one(self, payload: dict) -> str | None:
        packet = validate_sensor_payload(payload)
        if self.database.measurement_exists(
            packet.device_id, packet.boot_id, packet.sensor_type, packet.sequence
        ):
            LOGGER.info("중복 패킷을 건너뜁니다: %s/%s/%s", packet.device_id, packet.sensor_type, packet.sequence)
            return None
        previous = self.database.last_sequence(
            packet.device_id, packet.boot_id, packet.sensor_type
        )
        if previous is not None and packet.sequence > previous + 1:
            LOGGER.warning("sequence 누락: %s/%s에서 %s~%s", packet.device_id, packet.sensor_type, previous + 1, packet.sequence - 1)
        if previous is not None and packet.sequence < previous:
            LOGGER.warning("순서가 뒤바뀐 패킷: 이전=%s, 수신=%s", previous, packet.sequence)
        metadata = metadata_for(packet.sensor_type)
        features = extract_acoustic_features(
            packet.samples, packet.sample_rate, role=metadata["sensor_role"]
        )
        active_predictor = self.predictor[packet.sensor_type] if isinstance(self.predictor, dict) else self.predictor
        result = active_predictor.predict(features, packet.samples, packet.sensor_type)
        measurement_id = str(uuid.uuid4())
        measured_at = datetime.fromtimestamp(packet.timestamp, timezone.utc).isoformat()
        if self.excel_store is not None:
            result = self.excel_store.record_pending(
                measurement_id, measured_at, packet.device_id, packet.sensor_type,
                packet.cylinder_state, result,
                metadata={**metadata,"experiment_id":packet.experiment_id,
                          "session_id":packet.session_id,"pressure_mpa":packet.pressure_mpa,
                          "load_kg":packet.load_kg,"ground_truth":packet.ground_truth,
                          "ground_truth_source":packet.ground_truth_source},
                features=features,
            )
        try:
            measurement_id = self.database.save_measurement(packet, features, result, measurement_id)
        except DuplicateMeasurementError:
            LOGGER.info("중복 패킷을 건너뜁니다: %s/%s/%s", packet.device_id, packet.sensor_type, packet.sequence)
            if self.excel_store is not None:
                self.excel_store.mark_transferred(measurement_id, "duplicate measurement")
            return None
        except Exception as exc:
            if self.excel_store is not None:
                self.excel_store.mark_transferred(measurement_id, str(exc)[:1000])
            raise
        if self.excel_store is not None:
            self.excel_store.mark_transferred(measurement_id)
        if packet.health_score_target is not None and self.on_measurement_saved is not None:
            self.on_measurement_saved()
        upload_payload = {
            "measurement_id": measurement_id, "device_id": packet.device_id,
            "sensor_type": packet.sensor_type,
            "experiment_id": packet.experiment_id,
            "session_id": packet.session_id,
            "measured_at": measured_at,
            "cylinder_state": packet.cylinder_state,
            "pressure_mpa": packet.pressure_mpa,
            "load_kg": packet.load_kg,
            "ground_truth": packet.ground_truth,
            "ground_truth_source": packet.ground_truth_source,
            "vibration_rms": features["rms"] if packet.sensor_type == "sph0645" else None,
            "sound_rms": features["rms"] if packet.sensor_type == "inmp441" else None,
            "dominant_frequency": features["dominant_frequency"],
            "dominant_amplitude": features["dominant_amplitude"], **metadata, **result,
        }
        # Never wait for the network in the processing path.  The dedicated
        # uploader worker drains this durable queue independently.
        self.database.enqueue_upload(measurement_id, upload_payload)
        paired = self.database.paired_sensor_results(
            packet.device_id, packet.boot_id, packet.sequence
        )
        if {"sph0645", "inmp441"}.issubset(paired):
            combined = fuse_sensor_results(
                paired["sph0645"], paired["inmp441"]
            )
            if self.rul_predictor is not None:
                combined.update(self.rul_predictor.predict({
                    "vibration_prediction": paired["sph0645"]["prediction"],
                    "vibration_confidence": paired["sph0645"]["confidence"],
                    "sound_prediction": paired["inmp441"]["prediction"],
                    "sound_confidence": paired["inmp441"]["confidence"],
                }))
            self.database.save_combined_result(
                packet.device_id,
                packet.boot_id,
                packet.sequence,
                paired["sph0645"],
                paired["inmp441"],
                combined,
            )
            LOGGER.info(
                "통합 상태 판정: device=%s boot=%s sequence=%s prediction=%s controlling=%s",
                packet.device_id,
                packet.boot_id,
                packet.sequence,
                combined["prediction"],
                combined["controlling_role"],
            )
        LOGGER.info("측정 처리 완료: id=%s device=%s sensor=%s prediction=%s", measurement_id, packet.device_id, packet.sensor_type, result["prediction"])
        return measurement_id
