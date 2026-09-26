"""Application service for a completed cylinder analysis cycle."""

from typing import Any

from system.DB.Supabase.cylinder_result_repository import (
    CylinderResultRepository,
    RepositoryConfigurationError,
    RepositoryWriteError,
)


class ResultStorageUnavailableError(RuntimeError):
    """Raised when server-only Supabase credentials are unavailable."""


class CylinderResultService:
    """Coordinate result persistence without owning HTTP or database details."""

    def __init__(self, repository: CylinderResultRepository | None = None) -> None:
        self._repository = repository or CylinderResultRepository()

    def record_cycle_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Pass validated feature and ML-result data to the repository.

        Raw sensor arrays are deliberately excluded until their request contract is
        confirmed. Ground truth and prediction remain separate payload values.
        """
        try:
            return self._repository.save_processed_features_and_ml_result(payload)
        except RepositoryConfigurationError as error:
            raise ResultStorageUnavailableError(str(error)) from error
        except RepositoryWriteError as error:
            raise RuntimeError("failed to store cylinder result") from error
