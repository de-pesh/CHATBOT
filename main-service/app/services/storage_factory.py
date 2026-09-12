"""Chooses the file storage backend from configuration.

One switch, so swapping object stores is a `STORAGE_TYPE` change and a set of
credentials rather than an edit to the startup path. Adding a backend means
adding a class that implements `FileStorageService` and one branch here.

`indexing-rpc-server` has the matching factory. Both services must be pointed
at the same store and the same `STORAGE_TYPE`, because only paths cross the
gRPC boundary between them.
"""
import logging

from app.config import Settings
from app.services.storage_service import FileStorageService

logger = logging.getLogger(__name__)


def _require(settings: Settings, *names: str) -> list:
    """Pulls required settings, unwrapping SecretStr, and fails loudly if unset."""
    values = []
    missing = []
    for name in names:
        value = getattr(settings, name, None)
        if value is None:
            missing.append(name)
            continue
        values.append(value.get_secret_value() if hasattr(value, "get_secret_value") else value)
    if missing:
        raise ValueError(
            f"STORAGE_TYPE={settings.STORAGE_TYPE!r} requires {', '.join(missing)}."
        )
    return values


def create_storage_service(settings: Settings) -> FileStorageService:
    """Builds the configured storage backend. Call `initialize()` on the result."""
    storage_type = (settings.STORAGE_TYPE or "").lower()

    if storage_type == "local":
        # The OpenShift path: LOCAL_STORAGE_PATH is a mounted PVC, shared with
        # the compute service. See local_file_storage.py for the RWX/fsGroup
        # requirements that come with it.
        from app.services.local_file_storage import LocalFileStorage

        root_path = settings.LOCAL_STORAGE_PATH
        if not root_path:
            raise ValueError("STORAGE_TYPE='local' requires LOCAL_STORAGE_PATH.")
        logger.info("File storage: local filesystem at %s", root_path)
        return LocalFileStorage(
            root_path=root_path,
            public_base_url=settings.LOCAL_STORAGE_PUBLIC_URL,
        )

    if storage_type == "azure":
        from app.services.azure_blob_service import AzureBlobService

        account_url, account_name, account_key, container_name = _require(
            settings,
            "AZURE_STORAGE_ACCOUNT_URL",
            "AZURE_STORAGE_ACCOUNT_NAME",
            "AZURE_STORAGE_ACCOUNT_KEY",
            "AZURE_STORAGE_CONTAINER_NAME",
        )
        logger.info("File storage: Azure Blob Storage, container %s", container_name)
        return AzureBlobService(
            account_url=account_url,
            account_name=account_name,
            account_key=account_key,
            container_name=container_name,
            sas_expiry_minutes=int(settings.AZURE_STORAGE_SAS_EXPIRY_MINUTES),
        )

    if storage_type == "nextcloud":
        from app.services.next_cloud_service import NextCloudFileStorage

        url, user, password = _require(
            settings, "NEXTCLOUD_URL", "NEXTCLOUD_USERNAME", "NEXTCLOUD_PASSWORD"
        )
        logger.info("File storage: Nextcloud at %s", url)
        # Keyword names matter: the startup path used to call this with
        # url=/user_name=/password=, which is a TypeError against the actual
        # signature - the nextcloud branch could never have started.
        return NextCloudFileStorage(webdav_url=url, webdav_user=user, webdav_pass=password)

    raise ValueError(
        f"Unsupported STORAGE_TYPE: {settings.STORAGE_TYPE!r}. "
        f"Expected one of: local, azure, nextcloud."
    )
