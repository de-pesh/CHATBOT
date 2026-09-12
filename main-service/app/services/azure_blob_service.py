"""
Concrete implementation of the FileStorageService for Azure Blob Storage.
"""
import logging
from io import BytesIO
from datetime import datetime, timedelta
from urllib.parse import urlsplit, quote

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import (
    generate_blob_sas,
    BlobSasPermissions,
)
from azure.storage.blob.aio import (
    BlobServiceClient,
    BlobClient,
)

from app.services.storage_service import FileStorageService

logger = logging.getLogger(__name__)


class AzureBlobService(FileStorageService):
    """
    Service for interacting with Azure Blob Storage using
    dynamically generated SAS tokens (via account key).
    """

    def __init__(
        self,
        account_url: str,
        account_name: str,
        account_key: str,
        container_name: str,
        sas_expiry_minutes: int = 30,
    ):
        """
        :param account_url: https://<storage-account>.blob.core.windows.net
        :param account_name: Storage account name
        :param account_key: Storage account key
        :param container_name: Blob container name
        :param sas_expiry_minutes: SAS token lifetime
        """
        self._account_url = account_url.rstrip("/")
        self._account_name = account_name
        self._account_key = account_key
        self._container_name = container_name
        self._sas_expiry_minutes = sas_expiry_minutes

        self._blob_service_client: BlobServiceClient | None = None
        logger.info("AzureBlobService configured (dynamic SAS).")

    async def initialize(self):
        """Initializes BlobServiceClient using account key."""
        if self._blob_service_client:
            logger.warning("Azure BlobServiceClient is already initialized.")
            return

        logger.info("Initializing Azure BlobServiceClient...")
        self._blob_service_client = BlobServiceClient(
            account_url=self._account_url,
            credential=self._account_key,
        )
        logger.info("Azure BlobServiceClient initialized.")

    async def close(self):
        """Closes the BlobServiceClient connection."""
        if self._blob_service_client:
            logger.info("Closing Azure BlobServiceClient...")
            await self._blob_service_client.close()
            self._blob_service_client = None
            logger.info("Azure BlobServiceClient closed.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_blob_sas(self, blob_name: str, permissions: BlobSasPermissions) -> str:
        """Generates a short-lived SAS token for a single blob."""
        return generate_blob_sas(
            account_name=self._account_name,
            account_key=self._account_key,
            container_name=self._container_name,
            blob_name=blob_name,
            permission=permissions,
            expiry=datetime.utcnow() + timedelta(minutes=self._sas_expiry_minutes),
        )

    def _get_sas_blob_client(
        self, blob_name: str, permissions: BlobSasPermissions
    ) -> BlobClient:
        """Creates a SAS-scoped BlobClient."""
        sas = self._generate_blob_sas(blob_name, permissions)
        blob_url = (
            f"{self._account_url}/"
            f"{self._container_name}/"
            f"{blob_name}?{sas}"
        )
        return BlobClient.from_blob_url(blob_url)

    def _normalize_blob_name(self, file_reference: str) -> str:
        """Accepts either a blob name or a blob URL and returns the raw blob name."""
        parsed = urlsplit(file_reference)
        if not parsed.scheme:
            return file_reference.lstrip("/")

        path = parsed.path.lstrip("/")
        container_prefix = f"{self._container_name}/"
        if path.startswith(container_prefix):
            path = path[len(container_prefix):]
        return path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def upload_file(self, file_data: BytesIO, file_name: str) -> str:
        """Uploads content from a BytesIO object to Azure Blob Storage."""
        if not self._blob_service_client:
            raise RuntimeError("AzureBlobService not initialized. Call initialize() first.")

        blob_client = self._get_sas_blob_client(
            file_name,
            BlobSasPermissions(write=True, create=True),
        )

        try:
            file_data.seek(0)
            await blob_client.upload_blob(file_data, overwrite=True)
            logger.info(f"Successfully uploaded blob to {self._container_name}/{file_name}")
            return f"{self._account_url}/{self._container_name}/{file_name}"
        except Exception as e:
            logger.exception(
                f"Failed to upload blob to {self._container_name}/{file_name}: {e}"
            )
            raise
        finally:
            await blob_client.close()

    async def download_file(self, file_name: str) -> BytesIO:
        """Downloads a blob and returns it as an in-memory BytesIO buffer."""
        if not self._blob_service_client:
            raise RuntimeError("AzureBlobService not initialized. Call initialize() first.")

        blob_name = self._normalize_blob_name(file_name)

        blob_client = self._get_sas_blob_client(
            blob_name,
            BlobSasPermissions(read=True),
        )

        try:
            download_stream = await blob_client.download_blob()
            buffer = BytesIO()
            await download_stream.readinto(buffer)
            buffer.seek(0)

            logger.info(
                f"Successfully downloaded blob from {self._container_name}/{blob_name}"
            )
            return buffer
        except ResourceNotFoundError:
            logger.warning(f"Blob not found at {self._container_name}/{blob_name}")
            raise FileNotFoundError(f"Blob not found: {blob_name}")
        except Exception as e:
            logger.exception(
                f"Failed to download blob from {self._container_name}/{blob_name}: {e}"
            )
            raise
        finally:
            await blob_client.close()

    async def exists(self, file_name: str) -> bool:
        """Returns True if the blob is present."""
        if not self._blob_service_client:
            raise RuntimeError("AzureBlobService not initialized. Call initialize() first.")

        blob_client = self._get_sas_blob_client(
            self._normalize_blob_name(file_name),
            BlobSasPermissions(read=True),
        )
        try:
            return await blob_client.exists()
        finally:
            await blob_client.close()

    def get_download_url(self, file_name: str) -> str:
        """A read-only SAS URL for the blob, valid for the configured window."""
        blob_name = self._normalize_blob_name(file_name)
        sas = self._generate_blob_sas(blob_name, BlobSasPermissions(read=True))
        return f"{self._account_url}/{self._container_name}/{quote(blob_name)}?{sas}"

    async def delete_file(self, file_name: str) -> bool:
        """Deletes a blob from Azure Blob Storage."""
        if not self._blob_service_client:
            raise RuntimeError("AzureBlobService not initialized. Call initialize() first.")

        blob_name = self._normalize_blob_name(file_name)

        blob_client = self._get_sas_blob_client(
            blob_name,
            BlobSasPermissions(delete=True),
        )

        try:
            await blob_client.delete_blob()
            logger.info(f"Successfully deleted blob {self._container_name}/{blob_name}")
            return True
        except ResourceNotFoundError:
            logger.warning(
                f"Attempted to delete a blob that does not exist: {blob_name}"
            )
            return True  # idempotent
        except Exception as e:
            logger.exception(
                f"Failed to delete blob {self._container_name}/{blob_name}: {e}"
            )
            raise
        finally:
            await blob_client.close()
