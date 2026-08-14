#!/usr/bin/env python3
"""Live inference monitor with a persistent manual PLC ON/OFF simulation."""

from __future__ import annotations

import csv
import os
import select
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DATABASE = Path("/opt/smart-cylinder-pi5/data/smart_cylinder.db")
INGEST_DATABASE = Path("/opt/smart-cylinder-pi5/data/raw_ingest.db")
COMPARISON_ROOT = Path("/opt/smart-cylinder-pi5/data/model_comparison")
MANUAL_STATE_KEY = "manual_plc_test_state"
KST = ZoneInfo("Asia/Seoul")
SENSORS = {
    "sph0645": ("진동", "sph0645_vibration"),
    "inmp441": ("소리", "inmp441_sound"),
}


def service_status(name: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def kst_time(value: str | None) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def pycaret_winner(directory: str) -> str:
    leaderboard = COMPARISON_ROOT / directory / "leaderboard.csv"
    if not leaderboard.exists():
        return "비교 전 (leaderboard.csv 없음)"
    try:
        with leaderboard.open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle), None)
        if not row:
            return "비교 결과 없음"
        model = row.get("Model") or row.get("Name") or row.get("Estimator") or "이름 미확인"
        f1 = row.get("F1")
        return f"{model}" + (f" / F1={f1}" if f1 else "")
    except (OSError, csv.Error) as exc:
        return f"리더보드 읽기 실패: {exc}"


def set_manual_state(state: str) -> None:
    if state not in {"ON", "OFF"}:
        raise ValueError("state must be ON or OFF")
    with sqlite3.connect(DATABASE, timeout=3) as connection:
        connection.execute(
            """
            INSERT INTO service_metadata(key, value, updated_at)
            VALUES(?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
              value=excluded.value,
              updated_at=CURRENT_TIMESTAMP
            """,
            (MANUAL_STATE_KEY, state),
        )


def manual_state(connection: sqlite3.Connection) -> tuple[str, str | None]:
    row = connection.execute(
        "SELECT value, updated_at FROM service_metadata WHERE key=?",
        (MANUAL_STATE_KEY,),
    ).fetchone()
    if row is None:
        return "OFF", None
    return ("ON" if row["value"] == "ON" else "OFF"), row["updated_at"]


def latest_sensor(connection: sqlite3.Connection, sensor_type: str):
    return connection.execute(
        """
        SELECT m.measured_at, m.sequence,
               f.rms, f.crest_factor, f.dominant_frequency, f.spectral_energy,
               r.prediction, r.confidence, r.health_score, r.model_version
        FROM measurements AS m
        JOIN feature_data AS f ON f.measurement_id = m.measurement_id
        JOIN ml_results AS r ON r.measurement_id = m.measurement_id
        WHERE m.sensor_type = ?
        ORDER BY m.id DESC LIMIT 1
        """,
        (sensor_type,),
    ).fetchone()


def render() -> None:
    connection = sqlite3.connect(DATABASE, timeout=3)
    connection.row_factory = sqlite3.Row
    state, state_updated_at = manual_state(connection)
    latest = {sensor: latest_sensor(connection, sensor) for sensor in SENSORS}
    labelled = dict(
        connection.execute(
            """
            SELECT sensor_type, COUNT(*) FROM measurements
            WHERE condition_target IS NOT NULL AND condition_target <> ''
            GROUP BY sensor_type
            """
        ).fetchall()
    )
    combined = connection.execute(
        """
        SELECT measured_at, sequence, prediction, confidence, health_score,
               controlling_role, fusion_version
        FROM combined_results ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    counts = connection.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM measurements) AS measurements,
          (SELECT COUNT(*) FROM ml_results) AS inferences,
          (SELECT COUNT(*) FROM combined_results) AS combined,
          (SELECT COUNT(*) FROM upload_queue) AS upload_pending
        """
    ).fetchone()
    connection.close()
    ingest_pending = 0
    if INGEST_DATABASE.exists():
        with sqlite3.connect(INGEST_DATABASE, timeout=3) as ingest_connection:
            ingest_pending = ingest_connection.execute(
                "SELECT COUNT(*) FROM ingest_queue WHERE status <> 'done'"
            ).fetchone()[0]

    if os.getenv("TERM"):
        os.system("clear")
    color = "\033[92m" if state == "ON" else "\033[91m"
    reset = "\033[0m"
    print("스마트 실린더 실시간 추론 (진동 + 소리)")
    print("=" * 112)
    print(
        f"수동 테스트: {color}● {state}{reset} (실제 PLC 출력 아님) | "
        f"현재: {datetime.now(KST):%Y-%m-%d %H:%M:%S KST} | 변경: {state_updated_at or '-'}"
    )
    print(
        f"서비스: mosquitto={service_status('mosquitto')}  "
        f"smart-cylinder={service_status('smart-cylinder')} | "
        f"측정={counts['measurements']} 추론={counts['inferences']} 통합={counts['combined']} "
        f"수신대기={ingest_pending} Supabase대기={counts['upload_pending']}"
    )
    print("-" * 112)
    print(f"{'구분':4} {'센서':9} {'시각':8} {'순번':>6} {'RMS':>12} {'Crest':>7} {'주파수':>9} {'판정':14} {'신뢰':>5} {'건강':>5}")
    print("-" * 112)
    for sensor_type, (role, comparison_dir) in SENSORS.items():
        row = latest[sensor_type]
        if row is None:
            print(f"{role:4} {sensor_type:9} 아직 저장된 추론 결과가 없습니다.")
            continue
        measured = kst_time(row["measured_at"])
        print(
            f"{role:4} {sensor_type:9} {measured[-8:]:8} {row['sequence']:6d} "
            f"{row['rms']:12.1f} {row['crest_factor']:7.2f} "
            f"{row['dominant_frequency']:8.1f}Hz {row['prediction']:14} "
            f"{row['confidence']:5.2f} {row['health_score']:5.1f}"
        )
        print(
            f"     모델={row['model_version']} | PyCaret={pycaret_winner(comparison_dir)} "
            f"| 정답라벨={labelled.get(sensor_type, 0)}건"
        )

    print("=" * 112)
    if combined is None:
        print("통합 판정: 아직 결과가 없습니다.")
    else:
        print(
            f"통합 판정: {kst_time(combined['measured_at'])} / sequence={combined['sequence']} / "
            f"판정={combined['prediction']} / 신뢰도={combined['confidence']:.2f} / "
            f"건강도={combined['health_score']:.1f} / 지배={combined['controlling_role']}"
        )
    print("\n명령: ON=켜기, OFF=끄기, EXIT=종료")
    print("명령 입력 > ", end="", flush=True)


def main() -> None:
    while True:
        try:
            render()
            readable, _, _ = select.select([sys.stdin], [], [], 2.0)
            if not readable:
                continue
            line = sys.stdin.readline()
            if line == "":
                return
            command = line.strip().upper()
            if command in {"ON", "OFF"}:
                set_manual_state(command)
            elif command in {"EXIT", "QUIT"}:
                print("실시간 확인을 종료합니다.")
                return
            elif command:
                print("ON, OFF 또는 EXIT만 입력하세요.")
        except KeyboardInterrupt:
            print("\n실시간 확인을 종료합니다.")
            return
        except Exception as exc:
            print(f"화면 갱신 오류: {exc}")


if __name__ == "__main__":
    main()
