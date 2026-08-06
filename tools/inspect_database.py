"""SQLite 테이블별 행 수와 최근 측정값을 확인한다."""

from __future__ import annotations
import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings

def main() -> None:
    path = Settings.load().database_path
    if not path.exists():
        raise SystemExit(f"데이터베이스가 없습니다: {path}")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        for table in ("measurements", "raw_sensor_data", "feature_data", "ml_results", "upload_queue"):
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {count:,}행")
        for row in connection.execute("SELECT measurement_id,device_id,sensor_type,measured_at,sequence FROM measurements ORDER BY id DESC LIMIT 10"):
            print(dict(row))

if __name__ == "__main__":
    main()
