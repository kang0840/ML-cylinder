"""Create a transactionally consistent SQLite backup while the service runs."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
args.destination.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(args.source) as source, sqlite3.connect(args.destination) as destination:
    source.backup(destination)
print(args.destination)
