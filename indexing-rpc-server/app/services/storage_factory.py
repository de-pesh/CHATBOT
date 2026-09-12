"""Chooses the file storage backend from configuration.

The mirror of main-service's factory. Both services must be pointed at the same
store with the same STORAGE_TYPE: only paths cross the gRPC boundary, so one
service uploads a document and the other reads it back by path. Under 'local'
that means the same mounted volume, which on OpenShift means one ReadWriteMany
PVC mounted into both pods.
"""
import logging

from app.config import Settings
from app.services.storage_service import FileStorageService

logger = logging.getLogger(__name__)


def create_storage_service(settings: Settings) -> FileStorageService:
    """Builds the configured storage backend. Call `initialize()` on the result."""
    storage_type = (settings.STORAGE_TYPE or "").lower()

    if storage_type == "local":
        from app.services.local_file_storage import LocalFileStorage

        if not settings.LOCAL_STORAGE_PATH:
            raise ValueError("STORAGE_TYPE='local' requires LOCAL_STORAGE_PATH.")
        logger.info("File storage: local filesystem at %s", settings.LOCAL_STORAGE_PATH)
        return LocalFileStorage(root_path=settings.LOCAL_STORAGE_PATH)

    if storage_type == "azure":
        from app.services.azure_blob_storage_service import AzureBlobService

        if not settings.BLOB_CONNECTION_STRING or not settings.BLOB_CONTAINER_NAME:
            raise ValueError(
                "STORAGE_TYPE='azure' requires BLOB_CONNECTION_STRING and BLOB_CONTAINER_NAME."
            )
        logger.info("File storage: Azure Blob Storage, container %s", settings.BLOB_CONTAINER_NAME)
        return AzureBlobService(
            blob_connection_string=settings.BLOB_CONNECTION_STRING.get_secret_value(),
            container_name=settings.BLOB_CONTAINER_NAME,
        )

    raise ValueError(
        f"Unsupported STORAGE_TYPE: {settings.STORAGE_TYPE!r}. Expected one of: local, azure."
    )
