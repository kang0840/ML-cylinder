"""Mosquitto에서 Pico W 센서 패킷을 구독한다."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import paho.mqtt.client as mqtt

LOGGER = logging.getLogger(__name__)
SENSOR_TOPICS = ("smartCylinder/+/sph0645/raw", "smartCylinder/+/inmp441/raw")
STATUS_TOPIC = "smartCylinder/+/status"


class MQTTReceiver:
    def __init__(self, host: str, port: int, keepalive: int, on_payload: Callable[[dict], None], username: str | None = None, password: str | None = None):
        self.host, self.port, self.keepalive, self.on_payload = host, port, keepalive, on_payload
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smart-cylinder-pi5", clean_session=False)
        if username:
            self.client.username_pw_set(username, password)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOGGER.error("MQTT 연결 실패: %s", reason_code)
            return
        for topic in (*SENSOR_TOPICS, STATUS_TOPIC):
            client.subscribe(topic, qos=1)
        LOGGER.info("MQTT 연결 및 토픽 구독 완료: %s:%s", self.host, self.port)

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if message.topic.endswith("/status"):
                LOGGER.info("Pico W 상태 [%s]: %s", message.topic, payload)
                return
            self.on_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("잘못된 MQTT JSON을 폐기했습니다: %s", message.topic)
        except Exception:
            LOGGER.exception("MQTT 메시지 처리 중 오류가 발생했습니다.")

    @staticmethod
    def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties) -> None:
        LOGGER.warning("MQTT 연결 종료: %s (자동 재연결 대기)", reason_code)

    def run(self) -> None:
        self.client.connect(self.host, self.port, self.keepalive)
        self.client.loop_forever(retry_first_connection=True)

    def stop(self) -> None:
        self.client.disconnect()
