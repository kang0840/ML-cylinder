"""Raspberry Pi 5 smart-cylinder service entry point."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading

from config import Settings
from src.database import Database
from src.ingest_database import IngestDatabase
from src.inference_excel_store import AsyncInferenceExcelStore
from src.logger_config import configure_logging
from src.ml_predictor import MLPredictor
from src.mqtt_receiver import MQTTReceiver
from src.pipeline import SensorPipeline
from src.three_model_inference import MODEL_PATHS, RULPredictor
from src.supabase_uploader import SupabaseUploader

LOGGER = logging.getLogger("smart_cylinder")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Raspberry Pi 5 smart-cylinder MQTT collection service"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    settings = Settings.load()
    settings.create_directories()
    configure_logging(settings.log_path, args.verbose)

    database = Database(settings.database_path)
    ingest_database = IngestDatabase(settings.ingest_database_path)
    migrated = ingest_database.import_legacy_pending(settings.database_path)
    if migrated:
        LOGGER.info("기존 DB의 미처리 수신 데이터 %s건을 원본 DB로 복사했습니다.", migrated)
    predictor = {
        "sph0645": MLPredictor(MODEL_PATHS["acoustic_vibration"]),
        "inmp441": MLPredictor(MODEL_PATHS["sound"]),
    }
    rul_predictor = RULPredictor(MODEL_PATHS["rul"])
    uploader = SupabaseUploader(
        database,
        settings.supabase_url,
        settings.supabase_key,
        settings.supabase_table,
    )
    excel_store = AsyncInferenceExcelStore(settings.inference_excel_path)
    pipeline = SensorPipeline(
        database,
        predictor,
        uploader,
        settings.use_hann_window,
        None,
        excel_store,
        rul_predictor,
    )

    stopped = threading.Event()
    processing_ready = threading.Event()
    upload_ready = threading.Event()

    def ingest(payload: dict) -> None:
        inserted = ingest_database.enqueue(payload)
        if inserted:
            processing_ready.set()

    receiver = MQTTReceiver(
        settings.mqtt_host,
        settings.mqtt_port,
        settings.mqtt_keepalive,
        ingest,
        settings.mqtt_username,
        settings.mqtt_password,
    )

    def processing_worker() -> None:
        while not stopped.is_set():
            row = ingest_database.claim_next()
            if row is None:
                processing_ready.wait(timeout=0.5)
                processing_ready.clear()
                continue
            try:
                pipeline.process(json.loads(row["payload"]))
                ingest_database.complete(row["id"])
                upload_ready.set()
            except Exception as exc:
                ingest_database.fail(row["id"], str(exc))
                LOGGER.exception(
                    "수신 데이터 처리 실패: queue_id=%s packet_key=%s",
                    row["id"],
                    row["packet_key"],
                )

    def upload_worker() -> None:
        while not stopped.is_set():
            uploaded, failed = uploader.retry_pending(limit=1)
            if uploaded:
                LOGGER.info("Supabase 전송 성공: %s건", uploaded)
                continue
            if failed:
                LOGGER.warning("Supabase 전송 실패: %s건 (재시도 대기)", failed)
                stopped.wait(settings.upload_retry_seconds)
                continue
            upload_ready.wait(timeout=0.5)
            upload_ready.clear()

    processor = threading.Thread(
        target=processing_worker, name="latest-first-processor", daemon=True
    )
    upload = threading.Thread(
        target=upload_worker, name="supabase-uploader", daemon=True
    )
    processor.start()
    upload.start()

    def shutdown(signum, frame) -> None:
        LOGGER.info("종료 신호를 받았습니다.")
        stopped.set()
        processing_ready.set()
        upload_ready.set()
        receiver.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    LOGGER.info(
        "스마트 실린더 Pi 5 서비스를 시작합니다. "
        "수신 저장/추론/Supabase 전송은 서로 독립적으로 실행됩니다."
    )
    try:
        receiver.run()
    finally:
        stopped.set()
        processing_ready.set()
        upload_ready.set()
        processor.join(timeout=5)
        upload.join(timeout=5)
        excel_store.close()
        ingest_database.close()
        database.close()
        LOGGER.info("서비스가 정상 종료되었습니다.")


if __name__ == "__main__":
    main()
