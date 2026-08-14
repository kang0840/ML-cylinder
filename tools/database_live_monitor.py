#!/usr/bin/env python3
"""Live monitor for the split ingest and analysis SQLite databases."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


INGEST_DATABASE = Path("/opt/smart-cylinder-pi5/data/raw_ingest.db")
ANALYSIS_DATABASE = Path("/opt/smart-cylinder-pi5/data/smart_cylinder.db")
KST = ZoneInfo("Asia/Seoul")


def service_status(name: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", name], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def connect_readonly(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def local_time(value: str | None) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def ingest_state() -> tuple[dict[str, int], sqlite3.Row | None]:
    connection = connect_readonly(INGEST_DATABASE)
    if connection is None:
        return {}, None
    try:
        counts = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM ingest_queue GROUP BY status"
            )
        }
        latest = connection.execute(
            """
            SELECT device_id,boot_id,sequence,measured_at,received_at,status
            FROM ingest_queue ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        return counts, latest
    finally:
        connection.close()


def analysis_state() -> tuple[dict[str, int], list[sqlite3.Row]]:
    connection = connect_readonly(ANALYSIS_DATABASE)
    if connection is None:
        return {}, []
    try:
        counts = dict(
            connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM measurements) AS measurements,
                  (SELECT COUNT(*) FROM ml_results) AS inferences,
                  (SELECT COUNT(*) FROM combined_results) AS combined,
                  (SELECT COUNT(*) FROM upload_queue) AS upload_pending
                """
            ).fetchone()
        )
        latest = connection.execute(
            """
            SELECT m.sensor_type,m.sequence,m.measured_at,
                   r.prediction,r.confidence,r.health_score,r.model_version
            FROM measurements AS m
            JOIN ml_results AS r ON r.measurement_id=m.measurement_id
            WHERE m.id IN (
              SELECT MAX(id) FROM measurements GROUP BY sensor_type
            )
            ORDER BY m.sensor_type
            """
        ).fetchall()
        return {key: int(value) for key, value in counts.items()}, list(latest)
    finally:
        connection.close()


def render() -> None:
    ingest_counts, latest_packet = ingest_state()
    analysis_counts, latest_results = analysis_state()
    pending = ingest_counts.get("pending", 0) + ingest_counts.get("processing", 0)
    if os.getenv("TERM"):
        os.system("clear")
    print("스마트 실린더 데이터 실시간 확인 (2개 DB)")
    print("=" * 100)
    print(
        f"현재: {datetime.now(KST):%Y-%m-%d %H:%M:%S KST} | "
        f"Mosquitto={service_status('mosquitto')} | "
        f"추론 서비스={service_status('smart-cylinder')}"
    )
    print("-" * 100)
    print(f"[원본 수신 DB] {INGEST_DATABASE}")
    print(
        f"수신대기={pending} | 처리완료={ingest_counts.get('done', 0)} | "
        f"처리오류={ingest_counts.get('error', 0)}"
    )
    if latest_packet:
        print(
            f"최근 패킷: device={latest_packet['device_id']} "
            f"sequence={latest_packet['sequence']} status={latest_packet['status']} | "
            f"측정={local_time(latest_packet['measured_at'])} | "
            f"수신={local_time(latest_packet['received_at'])}"
        )
    else:
        print("최근 패킷: 아직 수신된 데이터가 없습니다.")
    print("-" * 100)
    print(f"[가공·추론 DB] {ANALYSIS_DATABASE}")
    print(
        f"측정={analysis_counts.get('measurements', 0)} | "
        f"추론={analysis_counts.get('inferences', 0)} | "
        f"통합판정={analysis_counts.get('combined', 0)} | "
        f"Supabase대기={analysis_counts.get('upload_pending', 0)}"
    )
    for row in latest_results:
        role = "진동" if row["sensor_type"] == "sph0645" else "소리"
        print(
            f"{role:2} {row['sensor_type']:8} | sequence={row['sequence']:6} | "
            f"{local_time(row['measured_at'])} | {row['prediction']} | "
            f"신뢰도={row['confidence']:.2f} 건강={row['health_score']:.1f} | "
            f"모델={row['model_version']}"
        )
    print("=" * 100)
    print("2초마다 자동 갱신됩니다. 종료: Ctrl+C")


def main() -> None:
    try:
        while True:
            try:
                render()
            except (sqlite3.Error, OSError) as exc:
                print(f"DB 확인 오류: {exc}")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n실시간 확인을 종료합니다.")


if __name__ == "__main__":
    main()
