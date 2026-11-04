"""Chooses the file storage backend from configuration.

The mirror of main-service's factory. Both services must be pointed at the same
store with the same STORAGE_TYPE: only paths cross the REST boundary, so one
service uploads a document and the other reads it back by path. Under 'local'
that means the same mounted volume, which on OpenShift means one ReadWriteMany
PVC mounted into both pods.
"""
import logging

from app.config import Settings
from app.logic.config_checks import require_settings
from app.logic.storage_paths import parse_dir_mode
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)


def create_storage_service(settings: Settings) -> FileStorageService:
    """Builds the configured storage backend. Call `initialize()` on the result."""
    storage_type = settings.STORAGE_TYPE

    if storage_type == "local":
        from app.services.storage.local_file_storage import LocalFileStorage

        require_settings(settings, "STORAGE_TYPE", "LOCAL_STORAGE_PATH")
        logger.info("File storage: local filesystem at %s", settings.LOCAL_STORAGE_PATH)
        return LocalFileStorage(
            root_path=settings.LOCAL_STORAGE_PATH,
            directory_mode=parse_dir_mode(settings.LOCAL_STORAGE_DIR_MODE),
        )

    if storage_type == "azure":
        from app.services.storage.azure_blob_service import AzureBlobService

        require_settings(settings, "STORAGE_TYPE", "BLOB_CONNECTION_STRING", "BLOB_CONTAINER_NAME")
        logger.info("File storage: Azure Blob Storage, container %s", settings.BLOB_CONTAINER_NAME)
        return AzureBlobService(
            blob_connection_string=settings.BLOB_CONNECTION_STRING.get_secret_value(),
            container_name=settings.BLOB_CONTAINER_NAME,
        )

    if storage_type == "quay":
        from app.services.storage.quay_oras_storage import QuayOrasStorage

        require_settings(settings, "STORAGE_TYPE", "QUAY_REGISTRY", "QUAY_REPOSITORY")
        registry = settings.QUAY_REGISTRY
        repository = settings.QUAY_REPOSITORY
        username = settings.QUAY_USERNAME.get_secret_value() if settings.QUAY_USERNAME else ""
        password = settings.QUAY_PASSWORD.get_secret_value() if settings.QUAY_PASSWORD else ""
        insecure = str(getattr(settings, "QUAY_INSECURE", "false")).lower() == "true"
        logger.info("File storage: Quay OCI registry %s/%s", registry, repository)
        return QuayOrasStorage(
            registry=registry,
            repository=repository,
            username=username,
            password=password,
            insecure=insecure,
        )

    raise ValueError(
        f"Unsupported STORAGE_TYPE: {settings.STORAGE_TYPE!r}. Expected one of: local, azure, quay."
    )
