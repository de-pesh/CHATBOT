'''
Module for interacting with Azure Blob Storage.

This module defines the `AzureBlobService` class, which provides methods for checking
the existence of a blob, uploading files to Azure Blob Storage, and closing connections
to the blob service. The service is designed to handle file storage operations in an
asynchronous manner using the `azure-storage-blob` library.

Functions:
    - `get_azure_storage_service`: Dependency that provides an instance of the
      `AzureBlobService` using the settings for Azure storage.
'''

import datetime
from io import BytesIO
from urllib.parse import urlsplit

from azure.storage.blob.aio import (
    BlobServiceClient,
    BlobClient
)
from azure.storage.blob import (
    generate_blob_sas,
    BlobSasPermissions,
)


class AzureBlobService:
    '''
    Service for interacting with Azure Blob Storage.
    
    This service provides asynchronous methods for uploading files, checking if a blob exists, 
    and closing the Azure Blob Storage client connections.
    '''

    def __init__(self, blob_connection_string: str, container_name: str):
        '''
        Initializes the AzureBlobService with the connection string and container name.

        Parameters:
            blob_connection_string (str): The Azure Blob Storage connection string.
            container_name (str): The name of the container in Azure Blob Storage.
        '''
        self._blob_service_client = BlobServiceClient.from_connection_string(
            conn_str=blob_connection_string
        )
        self._container_name = container_name


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


    def generate_sas_url(
            self,
            account_key: str,
            blob_path: str
    ) -> str:
        '''
        Generates a SAS (Shared Access Signature) URL for accessing a blob in Azure Blob Storage.

        Parameters:
            account_key (str): The storage account key for authentication.
            blob_path (str): The path to the blob in the storage container.

        Returns:
            str: A URL containing the SAS token for accessing the specified blob.
        '''
        # Get blob client
        blob_client = self._create_blob_client(
            blob_path=blob_path
        )

        # Generate start and expiry time for sas token which is 30 minutes right now.
        start_time = datetime.datetime.now(datetime.timezone.utc)
        expiry_time = start_time + datetime.timedelta(hours=.5)

        # Generate SAS token
        sas_token = generate_blob_sas(
            account_name=blob_client.account_name,
            container_name=blob_client.container_name,
            blob_name=blob_client.blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            start=start_time,
            expiry=expiry_time,
        )

        # Create complete url with SAS token.
        blob_sas_url = (
            f"https://{blob_client.account_name}.blob.core.windows.net/{self._container_name}/{blob_client.blob_name}?{sas_token}"
        )

        # Return URL with SAS token
        return blob_sas_url


    async def check_blob_exist(self, blob_path: str) -> bool:
        '''
        Checks if a blob exists in Azure Blob Storage.

        Parameters:
            blob_path (str): The path to the blob in Azure Blob Storage.

        Returns:
            bool: True if the blob exists, False otherwise.
        '''
        blob_client = self._create_blob_client(blob_path)
        return await blob_client.exists()


    async def upload_text_to_blob(self, blob_path: str, file_data: str):
        '''
        Uploads text to Azure Blob Storage.

        Parameters:
            blob_path (str): The path to the blob in Azure Blob Storage where the file should be uploaded.
            text: The file object received from a FastAPI request.
        '''
        blob_client = self._create_blob_client(blob_path)
        await blob_client.upload_blob(file_data, overwrite=True)


    async def upload_bytes_to_blob(self, blob_path: str, data: bytes):
        '''
        Uploads raw bytes to Azure Blob Storage.

        The text variant above cannot carry binary payloads, which is why the
        /kb upload path used to build a second, separate BlobServiceClient.

        Parameters:
            blob_path (str): The path to the blob in Azure Blob Storage.
            data (bytes): The file content to upload.
        '''
        blob_client = self._create_blob_client(blob_path)
        await blob_client.upload_blob(data, overwrite=True)


    async def download_blob_stream(self, blob_path: str) -> BytesIO:
        '''
        Downloads a blob as a stream in chunks and returns it as an in-memory byte buffer.

        Parameters:
        ----------------
        blob_path (str): The path to the blob.
        chunk_size (int): The size of each chunk to download (default: 4MB).

        Returns:
        ----------------
        BytesIO: A stream containing the blob's content.
        '''
        # Create blob client
        blob_client = self._create_blob_client(blob_path=blob_path)

        # Download blob as a stream
        download_stream = await blob_client.download_blob()

        # Create an in-memory buffer
        stream = BytesIO()

        # Read blob in chunks
        async for chunk in download_stream.chunks():
            stream.write(chunk)

        # Close blob client
        await blob_client.close()

        # Reset stream position
        stream.seek(0)

        return stream


    async def close(self):
        '''
        Closes the connection to the Azure Blob Storage service.
        
        This method should be called to ensure that all connections are properly closed after
        operations.
        '''
        await self._blob_service_client.close()
