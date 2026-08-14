"""Apply current schema to a DB copy and print integrity evidence."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from src.database import Database

parser = argparse.ArgumentParser()
parser.add_argument("database", type=Path)
args = parser.parse_args()
database = Database(args.database)
database.close()
with sqlite3.connect(args.database) as connection:
    print("measurements", connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0])
    print("foreign_key_check", connection.execute("PRAGMA foreign_key_check").fetchall())
    print("measurement_columns", [row[1] for row in connection.execute("PRAGMA table_info(measurements)")])
    print("new_tables", connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('ingest_queue','combined_results') ORDER BY name"
    ).fetchall())
    print("condition_labels", connection.execute(
        "SELECT sensor_type,COUNT(*) FROM measurements "
        "WHERE condition_target IS NOT NULL GROUP BY sensor_type"
    ).fetchall())
