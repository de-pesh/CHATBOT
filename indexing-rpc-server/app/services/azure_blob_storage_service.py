'''
Azure Blob Storage implementation of FileStorageService.

Method names match the interface rather than the storage product, so the
parsing and indexing code reads the same whichever backend is configured.
They used to be `check_blob_exist` / `upload_text_to_blob` /
`download_blob_stream`, which is why the callers all had Azure in their
vocabulary even though none of them cared which store they were on.
'''

import datetime
import logging
from io import BytesIO
from typing import Optional
from urllib.parse import urlsplit, quote

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob.aio import (
    BlobServiceClient,
    BlobClient
)
from azure.storage.blob import (
    generate_blob_sas,
    BlobSasPermissions,
)

from app.services.storage_service import FileStorageService

logger = logging.getLogger(__name__)


class AzureBlobService(FileStorageService):
    '''
    Service for interacting with Azure Blob Storage.

    This service provides asynchronous methods for uploading files, checking if a blob exists,
    and closing the Azure Blob Storage client connections.
    '''

    def __init__(self, blob_connection_string: str, container_name: str, sas_expiry_minutes: int = 30):
        '''
        Initializes the AzureBlobService with the connection string and container name.

        Parameters:
            blob_connection_string (str): The Azure Blob Storage connection string.
            container_name (str): The name of the container in Azure Blob Storage.
            sas_expiry_minutes (int): Lifetime of URLs from get_download_url.
        '''
        self._blob_service_client = BlobServiceClient.from_connection_string(
            conn_str=blob_connection_string
        )
        self._container_name = container_name
        self._sas_expiry_minutes = sas_expiry_minutes


    def _normalize_blob_path(self, blob_path: str) -> str:
        '''
        Normalizes either a blob URL or a raw blob path to a container-relative blob path.
        '''
        parsed = urlsplit(blob_path)
        if not parsed.scheme:
            return blob_path.lstrip('/')

        path = parsed.path.lstrip('/')
        container_prefix = f"{self._container_name}/"
        if path.startswith(container_prefix):
            path = path[len(container_prefix):]
        return path


    def _create_blob_client(self, blob_path: str) -> BlobClient:
        '''
        Creates and returns a BlobClient instance for the specified blob path.

        Parameters:
            blob_path (str): The path to the blob in Azure Blob Storage.

        Returns:
            BlobClient: The BlobClient instance used to interact with the specific blob.
        '''
        normalized_blob_path = self._normalize_blob_path(blob_path)
        return self._blob_service_client.get_blob_client(
            container=self._container_name,
            blob=normalized_blob_path
        )


    # ------------------------------------------------------------------
    # FileStorageService
    # ------------------------------------------------------------------

    async def exists(self, file_name: str) -> bool:
        '''Returns True if the blob is present.'''
        blob_client = self._create_blob_client(file_name)
        return await blob_client.exists()


    async def upload_file(self, file_data: BytesIO, file_name: str) -> str:
        '''Uploads a blob and returns its container-relative path.'''
        blob_path = self._normalize_blob_path(file_name)
        blob_client = self._create_blob_client(blob_path)
        file_data.seek(0)
        await blob_client.upload_blob(file_data, overwrite=True)
        logger.info("Uploaded %s/%s", self._container_name, blob_path)
        return blob_path


    async def upload_text(self, file_name: str, text: str, encoding: str = "utf-8") -> str:
        '''Uploads text directly; the SDK accepts a str without a buffer.'''
        blob_path = self._normalize_blob_path(file_name)
        blob_client = self._create_blob_client(blob_path)
        await blob_client.upload_blob(text, overwrite=True)
        logger.info("Uploaded text to %s/%s", self._container_name, blob_path)
        return blob_path


    async def download_file(self, file_name: str) -> BytesIO:
        '''
        Downloads a blob in chunks and returns it as an in-memory byte buffer.
        '''
        blob_client = self._create_blob_client(file_name)
        try:
            download_stream = await blob_client.download_blob()
            stream = BytesIO()
            async for chunk in download_stream.chunks():
                stream.write(chunk)
            stream.seek(0)
            return stream
        except ResourceNotFoundError:
            # Normalised so callers do not have to know the backend.
            raise FileNotFoundError(f"File not found: {file_name}") from None
        finally:
            await blob_client.close()


    async def delete_file(self, file_name: str) -> bool:
        '''Deletes a blob. Missing is treated as success.'''
        blob_client = self._create_blob_client(file_name)
        try:
            await blob_client.delete_blob()
            return True
        except ResourceNotFoundError:
            return True
        finally:
            await blob_client.close()


    def get_download_url(self, file_name: str) -> Optional[str]:
        '''A read-only SAS URL for the blob, valid for the configured window.'''
        account_key = getattr(self._blob_service_client.credential, "account_key", None)
        if not account_key:
            # Connection strings using a SAS token rather than an account key
            # cannot mint one; callers degrade rather than fail.
            return None

        blob_path = self._normalize_blob_path(file_name)
        start_time = datetime.datetime.now(datetime.timezone.utc)
        sas_token = generate_blob_sas(
            account_name=self._blob_service_client.account_name,
            container_name=self._container_name,
            blob_name=blob_path,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            start=start_time,
            expiry=start_time + datetime.timedelta(minutes=self._sas_expiry_minutes),
        )
        return (
            f"https://{self._blob_service_client.account_name}.blob.core.windows.net/"
            f"{self._container_name}/{quote(blob_path)}?{sas_token}"
        )


    async def close(self):
        '''
        Closes the connection to the Azure Blob Storage service.
        '''
        await self._blob_service_client.close()
