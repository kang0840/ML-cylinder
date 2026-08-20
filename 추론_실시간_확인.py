#!/usr/bin/env python3

import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DATABASE = "/opt/smart-cylinder-pi5/data/smart_cylinder.db"
KST = ZoneInfo("Asia/Seoul")


def service_status(name: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", name], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def kst_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return value or "-"


def render() -> None:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT c.sequence,c.measured_at,c.prediction,c.confidence,
               c.remaining_life_percent,c.controlling_role,c.fusion_version,
               vr.prediction AS vibration_prediction,
               vr.health_score AS vibration_health,
               sr.prediction AS sound_prediction,
               sr.health_score AS sound_health
        FROM combined_results AS c
        JOIN ml_results AS vr ON vr.measurement_id=c.vibration_measurement_id
        JOIN ml_results AS sr ON sr.measurement_id=c.sound_measurement_id
        ORDER BY c.id DESC
        LIMIT 12
        """
    ).fetchall()
    counts = dict(connection.execute(
        "SELECT sensor_type,COUNT(*) FROM measurements GROUP BY sensor_type"
    ).fetchall())
    pending = connection.execute("SELECT COUNT(*) FROM upload_queue").fetchone()[0]
    connection.close()

    os.system("clear")
    print("스마트 실린더 2단계 실시간 추론")
    print("=" * 116)
    print(f"현재 시각       : {datetime.now(KST):%Y-%m-%d %H:%M:%S KST}")
    print(f"Mosquitto       : {service_status('mosquitto')}")
    print(f"추론 서비스     : {service_status('smart-cylinder')}")
    print(f"누적 측정       : 진동={counts.get('sph0645', 0)}, 소리={counts.get('inmp441', 0)}")
    print(f"Supabase 대기   : {pending}건")
    print("-" * 116)
    print(f"{'측정 시각(KST)':19} {'순번':>6}  {'진동 추론':14} {'건강':>5}  {'소리 추론':14} {'건강':>5}  {'실린더 상태':14} {'잔여수명':>8}  기준")
    print("-" * 116)
    for row in rows:
        role = "진동" if row["controlling_role"] == "vibration" else "소리"
        print(
            f"{kst_time(row['measured_at']):19} {row['sequence']:6d}  "
            f"{row['vibration_prediction']:14} {row['vibration_health']:5.1f}  "
            f"{row['sound_prediction']:14} {row['sound_health']:5.1f}  "
            f"{row['prediction']:14} {row['remaining_life_percent']:7.1f}%  {role}"
        )
    if not rows:
        print("진동과 소리가 같은 순번으로 수신된 결합 추론 결과가 아직 없습니다.")
    print("\n2초마다 자동 갱신합니다. 종료: Ctrl+C")


while True:
    try:
        render()
        time.sleep(2)
    except KeyboardInterrupt:
        print("\n실시간 추론 확인을 종료합니다.")
        break
    except Exception as exc:
        print(f"화면 갱신 오류: {exc}")
        time.sleep(2)
