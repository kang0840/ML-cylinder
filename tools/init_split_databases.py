#!/usr/bin/env python3
"""Initialize the split SQLite databases and import legacy pending packets."""

from __future__ import annotations

from config import Settings
from src.database import Database
from src.ingest_database import IngestDatabase


def main() -> None:
    settings = Settings.load()
    settings.create_directories()
    analysis = Database(settings.database_path)
    ingest = IngestDatabase(settings.ingest_database_path)
    try:
        migrated = ingest.import_legacy_pending(settings.database_path)
        print(f"analysis_database={analysis.path}")
        print(f"ingest_database={ingest.path}")
        print(f"legacy_pending_imported={migrated}")
        print(f"ingest_counts={ingest.counts()}")
    finally:
        ingest.close()
        analysis.close()


if __name__ == "__main__":
    main()
