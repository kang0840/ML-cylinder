"""Raspberry Pi 5 스마트 실린더 서비스 진입점."""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from config import Settings
from src.auto_trainer import AutoTrainer
from src.database import Database
from src.logger_config import configure_logging
from src.ml_predictor import MLPredictor
from src.mqtt_receiver import MQTTReceiver
from src.pipeline import SensorPipeline
from src.supabase_uploader import SupabaseUploader

LOGGER = logging.getLogger("smart_cylinder")


def main() -> None:
    parser = argparse.ArgumentParser(description="Raspberry Pi 5 스마트 실린더 MQTT 수집 서비스")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    settings.create_directories()
    configure_logging(settings.log_path, args.verbose)
    database = Database(settings.database_path)
    predictor = MLPredictor(settings.model_path)
    trainer = AutoTrainer(database, predictor, settings.model_path, settings.auto_train_batch_size)
    uploader = SupabaseUploader(database, settings.supabase_url, settings.supabase_key, settings.supabase_table)
    pipeline = SensorPipeline(database, predictor, uploader, settings.use_hann_window, trainer.notify)
    receiver = MQTTReceiver(settings.mqtt_host, settings.mqtt_port, settings.mqtt_keepalive, pipeline.process, settings.mqtt_username, settings.mqtt_password)
    stopped = threading.Event()

    def retry_worker() -> None:
        while not stopped.wait(settings.upload_retry_seconds):
            uploaded, failed = uploader.retry_pending()
            if uploaded or failed:
                LOGGER.info("Supabase 재시도: 성공=%s 실패=%s", uploaded, failed)

    worker = threading.Thread(target=retry_worker, name="supabase-retry", daemon=True)
    worker.start()
    trainer.start()

    def shutdown(signum, frame) -> None:
        LOGGER.info("종료 신호를 받았습니다.")
        stopped.set()
        receiver.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    LOGGER.info("스마트 실린더 Pi 5 서비스를 시작합니다.")
    try:
        receiver.run()
    finally:
        stopped.set()
        worker.join(timeout=3)
        trainer.stop()
        database.close()
        LOGGER.info("서비스가 정상 종료되었습니다.")


if __name__ == "__main__":
    main()
