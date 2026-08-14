"""Supabase 요약 업로드와 SQLite 기반 재시도 큐."""

from __future__ import annotations

import json
import logging
from typing import Any

from .database import Database

LOGGER = logging.getLogger(__name__)


class SupabaseUploader:
    def __init__(self, database: Database, url: str | None, key: str | None, table: str):
        self.database, self.table, self.client = database, table, None
        if url and key:
            from supabase import create_client
            self.client = create_client(url, key)
        else:
            LOGGER.warning("Supabase 설정이 없어 로컬 처리만 실행합니다.")

    def upload_or_queue(self, measurement_id: str, payload: dict[str, Any]) -> bool:
        if self.client is None:
            return False
        try:
            self.client.table(self.table).upsert(payload, on_conflict="measurement_id").execute()
            return True
        except Exception as exc:
            LOGGER.warning("Supabase 업로드 실패, 재시도 큐에 저장합니다: %s", exc)
            self.database.enqueue_upload(measurement_id, payload, str(exc))
            return False

    def retry_pending(self, limit: int = 1) -> tuple[int, int]:
        if self.client is None:
            return 0, 0
        success = failed = 0
        for row in self.database.queued_uploads(limit=limit):
            try:
                self.client.table(self.table).upsert(json.loads(row["payload"]), on_conflict="measurement_id").execute()
                self.database.remove_upload(row["id"])
                success += 1
            except Exception as exc:
                self.database.mark_upload_failed(row["id"], str(exc))
                failed += 1
        return success, failed
