"""SQLite 테이블을 CSV와 요청 시점의 Excel 보고서로 내보낸다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

TABLES = {"measurements": "Measurements", "raw_sensor_data": "RawData", "feature_data": "Features", "ml_results": "MLResults"}


def export_database(database_path: Path, output_dir: Path, date: str | None = None, raw_limit: int | None = None) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    condition, params = (" WHERE measured_at LIKE ?", [f"{date}%"]) if date else ("", [])
    exported: list[Path] = []
    with sqlite3.connect(database_path) as connection, pd.ExcelWriter(output_dir / "smart_cylinder_report.xlsx", engine="openpyxl") as writer:
        measurement_ids = pd.read_sql_query(f"SELECT measurement_id FROM measurements{condition}", connection, params=params)
        ids = measurement_ids["measurement_id"].tolist()
        for table, sheet in TABLES.items():
            if table == "raw_sensor_data" and date:
                if not ids:
                    frame = pd.read_sql_query("SELECT * FROM raw_sensor_data WHERE 0", connection)
                else:
                    placeholders = ",".join("?" for _ in ids)
                    query = f"SELECT * FROM raw_sensor_data WHERE measurement_id IN ({placeholders})"
                    if raw_limit:
                        query += " LIMIT ?"
                    frame = pd.read_sql_query(query, connection, params=ids + ([raw_limit] if raw_limit else []))
            else:
                query = f"SELECT * FROM {table}"
                query += condition if table != "raw_sensor_data" else ""
                if raw_limit and table == "raw_sensor_data":
                    query += " LIMIT ?"
                frame = pd.read_sql_query(query, connection, params=(params if table != "raw_sensor_data" else ([raw_limit] if raw_limit else [])))
            csv_path = output_dir / f"{table}.csv"
            frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
            frame.to_excel(writer, sheet_name=sheet, index=False)
            exported.append(csv_path)
    exported.append(output_dir / "smart_cylinder_report.xlsx")
    return exported
