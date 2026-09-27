"""Cylinder analysis result API routes."""

from datetime import datetime
from typing import Any
from uuid import UUID

from flask import Blueprint, jsonify, request

from system.Backend.Service.cylinder_result_service import (
    ResultStorageUnavailableError,
    CylinderResultService,
)

cylinder_result_blueprint = Blueprint("cylinder_result", __name__, url_prefix="/api")
_service = CylinderResultService()


@cylinder_result_blueprint.post("/cylinder-result")
def create_cylinder_result() -> tuple[Any, int]:
    """Validate a Pi cycle result and pass it to the application service."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "invalid JSON body"}), 400

    missing_fields = [
        field
        for field in ("cylinder_id", "cycle_id", "timestamp", "prediction")
        if field not in payload or payload[field] is None or payload[field] == ""
    ]
    if missing_fields:
        return jsonify({"status": "error", "message": "missing required field"}), 400

    validation_error = _validate_payload(payload)
    if validation_error:
        return jsonify({"status": "error", "message": validation_error}), 400

    try:
        result = _service.record_cycle_result(payload)
    except ResultStorageUnavailableError as error:
        return jsonify({"status": "error", "message": str(error)}), 503
    except RuntimeError:
        return jsonify({"status": "error", "message": "failed to store result"}), 502

    return (
        jsonify(
            {
                "status": "ok",
                "cylinder_id": result["cylinder_id"],
                "cycle_id": result["cycle_id"],
            }
        ),
        200,
    )


def _validate_payload(payload: dict[str, Any]) -> str | None:
    """Return a client-safe validation error, if one applies."""
    if not isinstance(payload["cylinder_id"], str):
        return "cylinder_id must be a string"
    if not isinstance(payload["cycle_id"], str):
        return "cycle_id must be a UUID string"
    try:
        UUID(payload["cycle_id"])
    except ValueError:
        return "cycle_id must be a UUID string"
    if not isinstance(payload["timestamp"], str):
        return "timestamp must be an ISO 8601 string"
    try:
        measured_at = datetime.fromisoformat(
            payload["timestamp"].replace("Z", "+00:00")
        )
    except ValueError:
        return "timestamp must be an ISO 8601 string"
    if measured_at.tzinfo is None:
        return "timestamp must include a timezone"
    if not isinstance(payload["prediction"], str) or payload["prediction"] not in {
        "NORMAL",
        "ABNORMAL",
    }:
        return "prediction must be NORMAL or ABNORMAL"
    if "features" in payload and not isinstance(payload["features"], dict):
        return "features must be an object"
    if "leakage_score" in payload and (
        isinstance(payload["leakage_score"], bool)
        or not isinstance(payload["leakage_score"], (int, float))
    ):
        return "leakage_score must be a number"
    return None
