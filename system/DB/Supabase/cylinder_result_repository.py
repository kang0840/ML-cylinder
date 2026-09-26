"""Supabase repository for cylinder result persistence."""

import os
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client


class RepositoryConfigurationError(RuntimeError):
    """Raised when the server-only Supabase credentials are unavailable."""


class RepositoryWriteError(RuntimeError):
    """Raised when Supabase rejects a persistence request."""


class CylinderResultRepository:
    """Persistence boundary for processed features and ML results."""

    def __init__(self, client: Client | None = None) -> None:
        self._client = client

    def save_processed_features_and_ml_result(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Upsert Cycle features first, then the related ML result."""
        client = self._get_client()
        updated_at = datetime.now(timezone.utc).isoformat()
        features = payload.get("features") or {}
        feature_row = {
            "cylinder_id": payload["cylinder_id"],
            "cycle_id": payload["cycle_id"],
            "measured_at": payload["timestamp"],
            "sph0645_rms": features.get("sph0645_rms"),
            "inmp441_rms": features.get("inmp441_rms"),
            "sph0645_peak": features.get("sph0645_peak"),
            "inmp441_peak": features.get("inmp441_peak"),
            "sph0645_peak_to_peak": features.get("sph0645_peak_to_peak"),
            "inmp441_peak_to_peak": features.get("inmp441_peak_to_peak"),
            "sph0645_crest_factor": features.get("sph0645_crest_factor"),
            "inmp441_crest_factor": features.get("inmp441_crest_factor"),
            "fft_features": features.get("fft_features"),
            "updated_at": updated_at,
        }
        result_row = {
            "cylinder_id": payload["cylinder_id"],
            "cycle_id": payload["cycle_id"],
            "measured_at": payload["timestamp"],
            "prediction": payload["prediction"],
            "ground_truth": payload.get("ground_truth"),
            "leakage_score": payload.get("leakage_score"),
            "updated_at": updated_at,
        }

        try:
            client.table("processed_features").upsert(
                feature_row,
                on_conflict="cylinder_id,cycle_id",
            ).execute()
            client.table("ml_results").upsert(
                result_row,
                on_conflict="cylinder_id,cycle_id",
            ).execute()
        except Exception as error:
            raise RepositoryWriteError("Supabase write failed") from error

        return {
            "cylinder_id": payload["cylinder_id"],
            "cycle_id": payload["cycle_id"],
        }

    def _get_client(self) -> Client:
        if self._client is not None:
            return self._client

        supabase_url = os.environ.get("SUPABASE_URL")
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not supabase_url or not service_role_key:
            raise RepositoryConfigurationError(
                "Supabase server credentials are not configured"
            )

        self._client = create_client(supabase_url, service_role_key)
        return self._client
