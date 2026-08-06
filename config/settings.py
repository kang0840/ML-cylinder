"""환경 변수 기반 애플리케이션 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Raspberry Pi 5 서비스에서 사용하는 설정값."""

    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_keepalive: int
    database_path: Path
    model_path: Path
    log_path: Path
    supabase_url: str | None
    supabase_key: str | None
    supabase_table: str
    use_hann_window: bool
    upload_retry_seconds: int
    auto_train_batch_size: int

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        settings = cls(
            mqtt_host=os.getenv("MQTT_BROKER_HOST", "localhost").strip(),
            mqtt_port=int(os.getenv("MQTT_BROKER_PORT", "1883")),
            mqtt_username=os.getenv("MQTT_USERNAME") or None,
            mqtt_password=os.getenv("MQTT_PASSWORD") or None,
            mqtt_keepalive=int(os.getenv("MQTT_KEEPALIVE", "60")),
            database_path=ROOT / os.getenv("DATABASE_PATH", "data/smart_cylinder.db"),
            model_path=ROOT / os.getenv("MODEL_PATH", "models/cylinder_model.pkl"),
            log_path=ROOT / os.getenv("LOG_PATH", "logs/smart_cylinder.log"),
            supabase_url=os.getenv("SUPABASE_URL") or None,
            supabase_key=os.getenv("SUPABASE_KEY") or None,
            supabase_table=os.getenv("SUPABASE_TABLE", "smart_cylinder_analysis"),
            use_hann_window=_bool("FFT_USE_HANN_WINDOW", True),
            upload_retry_seconds=int(os.getenv("UPLOAD_RETRY_SECONDS", "30")),
            auto_train_batch_size=int(os.getenv("AUTO_TRAIN_BATCH_SIZE", "50")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.mqtt_host:
            raise ValueError("MQTT_BROKER_HOST가 비어 있습니다.")
        if not 1 <= self.mqtt_port <= 65535:
            raise ValueError("MQTT_BROKER_PORT는 1~65535여야 합니다.")
        if self.upload_retry_seconds < 5:
            raise ValueError("UPLOAD_RETRY_SECONDS는 5초 이상이어야 합니다.")
        if self.auto_train_batch_size < 2:
            raise ValueError("AUTO_TRAIN_BATCH_SIZE는 2 이상이어야 합니다.")
        if bool(self.supabase_url) != bool(self.supabase_key):
            raise ValueError("SUPABASE_URL과 SUPABASE_KEY는 함께 설정해야 합니다.")

    def create_directories(self) -> None:
        for path in (self.database_path.parent, self.model_path.parent, self.log_path.parent, ROOT / "data/export", ROOT / "data/backup"):
            path.mkdir(parents=True, exist_ok=True)
