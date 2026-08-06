"""Raspberry Pi 5에서 센서 수신과 ML 추론을 동시에 실행한다.

Pico W가 발행한 MQTT 센서 패킷은 수신 스레드에서 즉시 큐에 넣고,
별도의 작업 스레드가 FFT 특징 추출, 모델 추론, SQLite 저장 및
Supabase 업로드를 수행한다. 따라서 추론 중에도 MQTT 수신 루프는 계속 돈다.
"""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import threading
from typing import Any

from config import Settings
from src.auto_trainer import AutoTrainer
from src.database import Database
from src.logger_config import configure_logging
from src.ml_predictor import MLPredictor
from src.mqtt_receiver import MQTTReceiver
from src.pipeline import SensorPipeline
from src.supabase_uploader import SupabaseUploader

LOGGER = logging.getLogger("pi5.sensor_model")
STOP = object()


class SensorModelRunner:
    """MQTT 수신과 모델 처리를 서로 다른 스레드에서 실행한다."""

    def __init__(self, settings: Settings, queue_size: int = 32) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        predictor = MLPredictor(settings.model_path)
        self.trainer = AutoTrainer(
            self.database, predictor, settings.model_path, settings.auto_train_batch_size
        )
        self.uploader = SupabaseUploader(
            self.database,
            settings.supabase_url,
            settings.supabase_key,
            settings.supabase_table,
        )
        self.pipeline = SensorPipeline(
            self.database, predictor, self.uploader, settings.use_hann_window,
            self.trainer.notify,
        )
        self.payloads: queue.Queue[dict[str, Any] | object] = queue.Queue(
            maxsize=queue_size
        )
        self.stopped = threading.Event()
        self.receiver = MQTTReceiver(
            settings.mqtt_host,
            settings.mqtt_port,
            settings.mqtt_keepalive,
            self.enqueue,
            settings.mqtt_username,
            settings.mqtt_password,
        )
        self.model_worker = threading.Thread(
            target=self._model_loop, name="model-inference", daemon=True
        )
        self.retry_worker = threading.Thread(
            target=self._retry_loop, name="supabase-retry", daemon=True
        )

    def enqueue(self, payload: dict[str, Any]) -> None:
        """수신 콜백을 막지 않고 센서 패킷을 추론 큐에 넣는다."""
        try:
            self.payloads.put_nowait(payload)
        except queue.Full:
            LOGGER.error(
                "처리 큐가 가득 차 센서 패킷을 버렸습니다. queue_size=%s device=%s sequence=%s",
                self.payloads.maxsize,
                payload.get("device_id", "unknown"),
                payload.get("sequence", "unknown"),
            )

    def _model_loop(self) -> None:
        while True:
            payload = self.payloads.get()
            try:
                if payload is STOP:
                    return
                self.pipeline.process(payload)
            except Exception:
                LOGGER.exception("센서 패킷의 모델 처리 중 오류가 발생했습니다.")
            finally:
                self.payloads.task_done()

    def _retry_loop(self) -> None:
        while not self.stopped.wait(self.settings.upload_retry_seconds):
            uploaded, failed = self.uploader.retry_pending()
            if uploaded or failed:
                LOGGER.info("Supabase 재시도: 성공=%s 실패=%s", uploaded, failed)

    def run(self) -> None:
        self.model_worker.start()
        self.retry_worker.start()
        self.trainer.start()
        LOGGER.info(
            "센서 수신과 모델 추론을 시작합니다. broker=%s:%s queue=%s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
            self.payloads.maxsize,
        )
        try:
            self.receiver.run()
        finally:
            self.close()

    def close(self) -> None:
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.receiver.stop()

        # 이미 받은 센서 패킷은 모두 처리한 다음 모델 작업자를 종료한다.
        self.payloads.put(STOP)
        self.model_worker.join(timeout=30)
        if self.model_worker.is_alive():
            LOGGER.warning("30초 안에 남은 센서 패킷 처리를 마치지 못했습니다.")
        self.retry_worker.join(timeout=3)
        self.trainer.stop()
        self.database.close()
        LOGGER.info("센서/모델 서비스를 종료했습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Raspberry Pi 5 센서 수신 + ML 모델 추론 서비스"
    )
    parser.add_argument(
        "--queue-size",
        type=int,
        default=32,
        help="메모리에 대기시킬 최대 센서 패킷 수 (기본값: 32)",
    )
    parser.add_argument("--verbose", action="store_true", help="디버그 로그 출력")
    args = parser.parse_args()
    if args.queue_size < 1:
        parser.error("--queue-size는 1 이상이어야 합니다.")

    settings = Settings.load()
    settings.create_directories()
    configure_logging(settings.log_path, args.verbose)
    runner = SensorModelRunner(settings, args.queue_size)

    def shutdown(signum: int, frame: object) -> None:
        LOGGER.info("종료 신호를 받았습니다: %s", signum)
        runner.close()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    runner.run()


if __name__ == "__main__":
    main()
