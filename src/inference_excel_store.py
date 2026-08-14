"""Persist inference results to Excel before forwarding them to SQLite."""

from __future__ import annotations

import threading
import queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill


SHEET_NAME = "InferenceResults"
HEADERS = (
    "measurement_id", "measured_at", "device_id", "sensor_type",
    "cylinder_state", "prediction", "confidence", "health_score",
    "model_version", "db_status", "db_saved_at", "db_error",
)


class InferenceExcelStore:
    """Append inference rows and expose the saved values for DB transfer."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _create_workbook(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = SHEET_NAME
        sheet.append(HEADERS)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:L1"
        sheet.sheet_view.showGridLines = False
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
        widths = (38, 28, 16, 16, 16, 18, 12, 14, 28, 14, 28, 36)
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[chr(64 + index)].width = width
        workbook.save(self.path)

    def record_pending(
        self,
        measurement_id: str,
        measured_at: str,
        device_id: str,
        sensor_type: str,
        cylinder_state: str,
        result: dict[str, Any],
    ) -> dict[str, float | str]:
        """Write a pending row, then read back the values used by SQLite."""
        with self._lock:
            if not self.path.exists():
                self._create_workbook()
            workbook = load_workbook(self.path)
            sheet = workbook[SHEET_NAME]
            row_number = sheet.max_row + 1
            sheet.append((
                measurement_id, measured_at, device_id, sensor_type,
                cylinder_state, result["prediction"], float(result["confidence"]),
                float(result["health_score"]), result["model_version"],
                "pending", None, None,
            ))
            sheet.cell(row_number, 7).number_format = "0.0%"
            sheet.cell(row_number, 8).number_format = "0.00"
            sheet.auto_filter.ref = f"A1:L{row_number}"
            workbook.save(self.path)

            workbook = load_workbook(self.path, read_only=True, data_only=True)
            values = list(workbook[SHEET_NAME].iter_rows(
                min_row=row_number, max_row=row_number, values_only=True
            ))[0]
            workbook.close()
            return {
                "prediction": str(values[5]),
                "confidence": float(values[6]),
                "health_score": float(values[7]),
                "model_version": str(values[8]),
            }

    def mark_transferred(self, measurement_id: str, error: str | None = None) -> None:
        with self._lock:
            workbook = load_workbook(self.path)
            sheet = workbook[SHEET_NAME]
            for row in range(sheet.max_row, 1, -1):
                if sheet.cell(row, 1).value == measurement_id:
                    sheet.cell(row, 10).value = "failed" if error else "saved"
                    sheet.cell(row, 11).value = (
                        None if error else datetime.now(timezone.utc).isoformat()
                    )
                    sheet.cell(row, 12).value = error
                    workbook.save(self.path)
                    return
            workbook.close()
            raise KeyError(f"Excel inference row not found: {measurement_id}")

    def append_saved_batch(self, rows: list[tuple[Any, ...]]) -> None:
        """Append already-saved DB results with a single workbook save."""
        if not rows:
            return
        with self._lock:
            if not self.path.exists():
                self._create_workbook()
            workbook = load_workbook(self.path)
            sheet = workbook[SHEET_NAME]
            for values in rows:
                sheet.append(values)
                row_number = sheet.max_row
                sheet.cell(row_number, 7).number_format = "0.0%"
                sheet.cell(row_number, 8).number_format = "0.00"
            sheet.auto_filter.ref = f"A1:L{sheet.max_row}"
            workbook.save(self.path)
            workbook.close()


class AsyncInferenceExcelStore:
    """Keep Excel I/O out of the real-time FFT and inference path."""

    def __init__(self, path: Path, flush_seconds: float = 2.0, batch_size: int = 100):
        self.store = InferenceExcelStore(path)
        self.flush_seconds = flush_seconds
        self.batch_size = batch_size
        self._pending: dict[str, tuple[Any, ...]] = {}
        self._lock = threading.RLock()
        self._queue: queue.Queue[tuple[Any, ...] | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="excel-batch-writer", daemon=True
        )
        self._thread.start()

    def record_pending(
        self,
        measurement_id: str,
        measured_at: str,
        device_id: str,
        sensor_type: str,
        cylinder_state: str,
        result: dict[str, Any],
    ) -> dict[str, float | str]:
        normalized = {
            "prediction": str(result["prediction"]),
            "confidence": float(result["confidence"]),
            "health_score": float(result["health_score"]),
            "model_version": str(result["model_version"]),
        }
        with self._lock:
            self._pending[measurement_id] = (
                measurement_id, measured_at, device_id, sensor_type,
                cylinder_state, normalized["prediction"], normalized["confidence"],
                normalized["health_score"], normalized["model_version"],
                "saved", datetime.now(timezone.utc).isoformat(), None,
            )
        return normalized

    def mark_transferred(self, measurement_id: str, error: str | None = None) -> None:
        with self._lock:
            row = self._pending.pop(measurement_id, None)
        if row is None:
            return
        if error:
            row = (*row[:9], "failed", None, error)
        self._queue.put(row)

    def _run(self) -> None:
        while True:
            first = self._queue.get()
            if first is None:
                return
            batch = [first]
            deadline = datetime.now().timestamp() + self.flush_seconds
            while len(batch) < self.batch_size:
                remaining = deadline - datetime.now().timestamp()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is None:
                    self.store.append_saved_batch(batch)
                    return
                batch.append(item)
            self.store.append_saved_batch(batch)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=max(5.0, self.flush_seconds + 2.0))
