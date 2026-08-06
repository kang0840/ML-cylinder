"""SQLite 데이터를 CSV/Excel로 내보내는 CLI."""

from __future__ import annotations
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings
from src.export_manager import export_database

def main() -> None:
    parser = argparse.ArgumentParser(description="스마트 실린더 데이터 내보내기")
    parser.add_argument("--date", help="YYYY-MM-DD")
    parser.add_argument("--raw-limit", type=int)
    args = parser.parse_args()
    settings = Settings.load()
    for path in export_database(settings.database_path, settings.database_path.parent / "export", args.date, args.raw_limit):
        print(path)

if __name__ == "__main__":
    main()
