"""
Concrete implementation of the FileStorageService for Nextcloud using WebDAV.
"""
import asyncio
import logging
import os
from io import BytesIO

from webdav3.client import Client

from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)

class NextCloudFileStorage(FileStorageService):
    """
    Service for interacting with Nextcloud for file storage via WebDAV.
    
    Uses asyncio.to_thread to run synchronous WebDAV operations without
    blocking the application's event loop.
    """
    def __init__(self, webdav_url: str, webdav_user: str, webdav_pass: str):
        self.webdav_options = {
            'webdav_hostname': webdav_url,
            'webdav_login': webdav_user,
            'webdav_password': webdav_pass
        }
        # The client itself is synchronous, so it's created on-demand in helper methods

    def _blocking_upload(self, file_data_bytes: bytes, remote_path: str):
        """Synchronous helper for uploading."""
        client = Client(self.webdav_options)
        
        # Ensure parent directory exists
        remote_directory = os.path.dirname(remote_path)
        if remote_directory and not client.check(remote_directory):
            client.mkdir(remote_directory) # Note: webdav3 mkdir is recursive
            
        client.put(remote_path, file_data_bytes)
        
    def _blocking_download(self, remote_path: str) -> bytes:
        """Synchronous helper for downloading."""
        client = Client(self.webdav_options)
        return client.get(remote_path)

    def _blocking_delete(self, remote_path: str) -> bool:
        """Synchronous helper for deleting."""
        client = Client(self.webdav_options)
        if client.check(remote_path):
            client.clean(remote_path)
        return True

    async def upload_file(self, file_data: BytesIO, file_name: str) -> str:
        """Uploads a file to Nextcloud."""
        try:
            file_bytes = file_data.getvalue()
            # Run the synchronous upload function in a separate thread
            await asyncio.to_thread(self._blocking_upload, file_bytes, file_name)
            
            # IMPORTANT: Construct the public URL. This often requires combining the
            # hostname with the file path. This pattern may need adjustment
            # based on your specific Nextcloud configuration.
            file_url = f"{self.webdav_options['webdav_hostname'].rstrip('/')}/{file_name}"
            logger.info(f"Successfully queued upload for Nextcloud file: {file_name}")
            return file_url
        except Exception:
            logger.exception(f"Failed to upload file to Nextcloud: {file_name}")
            raise

    async def download_file(self, file_name: str) -> BytesIO:
        """Downloads a file from Nextcloud."""
        try:
            # Run the synchronous download function in a separate thread
            file_content = await asyncio.to_thread(self._blocking_download, file_name)
            logger.info(f"Successfully downloaded file from Nextcloud: {file_name}")
            return BytesIO(file_content)
        except Exception as e:
            # webdav3 may raise specific exceptions for not found, handle as needed
            logger.exception(f"Failed to download file from Nextcloud: {file_name}")
            raise FileNotFoundError(f"File not found on Nextcloud: {file_name}") from e

    async def delete_file(self, file_name: str) -> bool:
        """Deletes a file from Nextcloud."""
        try:
            # Run the synchronous delete function in a separate thread
            await asyncio.to_thread(self._blocking_delete, file_name)
            logger.info(f"Successfully deleted file from Nextcloud: {file_name}")
            return True
        except Exception:
            logger.exception(f"Failed to delete file from Nextcloud: {file_name}")
            return False

    def _blocking_exists(self, remote_path: str) -> bool:
        """Synchronous helper for existence checks."""
        client = Client(self.webdav_options)
        return bool(client.check(remote_path))

    async def exists(self, file_name: str) -> bool:
        """Part of the FileStorageService contract; used before a parse."""
        return await asyncio.to_thread(self._blocking_exists, file_name)
