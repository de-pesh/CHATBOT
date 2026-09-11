"""
Module for the Abstract Base Class for file storage services.
"""
from abc import ABC, abstractmethod
from io import BytesIO

class FileStorageService(ABC):
    """
    Abstract Base Class for a file storage service.

    Defines a common interface for different file storage backends,
    allowing them to be used interchangeably throughout the application.
    """

    async def initialize(self):
        """Initializes any underlying clients or connections. Optional to implement."""
        pass

    async def close(self):
        """Closes any underlying clients or connections. Optional to implement."""
        pass

    @abstractmethod
    async def upload_file(self, file_data: BytesIO, file_name: str) -> str:
        """Uploads a file and returns its public URL or unique path."""
        pass

    @abstractmethod
    async def download_file(self, file_name: str) -> BytesIO:
        """Downloads a file and returns its content as a BytesIO object."""
        pass

    @abstractmethod
    async def delete_file(self, file_name: str) -> bool:
        """Deletes a file and returns True on success."""
        pass