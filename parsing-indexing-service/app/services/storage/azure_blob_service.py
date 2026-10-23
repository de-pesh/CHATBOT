"""Azure Blob Storage implementation of FileStorageService, through the async SDK client."""
import logging
from io import BytesIO

from app.logic.storage_paths import blob_name
from app.services.storage.storage_service import FileStorageService
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob.aio import BlobClient, BlobServiceClient

logger = logging.getLogger(__name__)


class AzureBlobService(FileStorageService):

    def __init__(self, blob_connection_string: str, container_name: str):
        self._blob_service_client = BlobServiceClient.from_connection_string(conn_str=blob_connection_string)
        self._container_name = container_name

    def _create_blob_client(self, blob_path: str) -> BlobClient:
        return self._blob_service_client.get_blob_client(
            container=self._container_name, blob=blob_name(blob_path, self._container_name)
        )

    async def exists(self, file_name: str) -> bool:
        return await self._create_blob_client(file_name).exists()

    async def upload_file(self, file_data: BytesIO, file_name: str) -> str:
        """Uploads a blob and returns its container-relative path."""
        path = blob_name(file_name, self._container_name)
        file_data.seek(0)
        await self._create_blob_client(path).upload_blob(file_data, overwrite=True)
        logger.info("Uploaded %s/%s", self._container_name, path)
        return path

    async def download_file(self, file_name: str) -> BytesIO:
        """Downloads a blob in chunks into an in-memory buffer."""
        blob_client = self._create_blob_client(file_name)
        try:
            stream = BytesIO()
            async for chunk in (await blob_client.download_blob()).chunks():
                stream.write(chunk)
            stream.seek(0)
            return stream
        except ResourceNotFoundError:
            # Normalised so callers do not have to know the backend.
            raise FileNotFoundError(f"File not found: {file_name}") from None
        finally:
            await blob_client.close()

    async def delete_file(self, file_name: str) -> bool:
        """Deletes a blob. Missing is treated as success."""
        blob_client = self._create_blob_client(file_name)
        try:
            await blob_client.delete_blob()
        except ResourceNotFoundError:
            pass
        finally:
            await blob_client.close()
        return True

    async def close(self):
        await self._blob_service_client.close()
