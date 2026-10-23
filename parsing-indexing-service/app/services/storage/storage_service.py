"""Backend-neutral file storage interface.

Both services address the same store with the same path, which is what lets
only paths - never payloads - cross the REST boundary to main-service (Index,
Delete, ParseFile). So this interface, and
the `STORAGE_TYPE` that selects an implementation, must agree on both sides:
`main-service-chatbot/app/services/storage/storage_service.py` is the original of this file.
They are separate images with no shared package, so the duplication is
deliberate: upload, download, exists and delete must stay mirrored, so change
both together. main-service keeps its own get_download_url (for citation links);
this service has no use for one.

Paths are store-relative and backend-agnostic ("chatbot-kb/abc123_report.pdf").
A backend maps that onto whatever it actually uses - a blob name under a
container, a path under a mounted volume - and is responsible for accepting
back any reference it previously handed out, including a full URL, since file
records written by earlier deployments hold absolute Azure URLs.
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

    async def close(self):
        """Closes any underlying clients or connections. Optional to implement."""

    @abstractmethod
    async def upload_file(self, file_data: BytesIO, file_name: str) -> str:
        """Uploads a file and returns a reference that `download_file` accepts."""

    @abstractmethod
    async def download_file(self, file_name: str) -> BytesIO:
        """Downloads a file.

        Raises:
            FileNotFoundError: if the file is not in the store. Backends must
                normalise their own not-found error to this, so callers do not
                have to know which backend they are on.
        """

    @abstractmethod
    async def delete_file(self, file_name: str) -> bool:
        """Deletes a file. Idempotent: deleting what is not there succeeds."""

    @abstractmethod
    async def exists(self, file_name: str) -> bool:
        """Returns True if the file is in the store."""

    async def upload_text(self, file_name: str, text: str, encoding: str = "utf-8") -> str:
        """Convenience wrapper over `upload_file` for extracted text."""
        return await self.upload_file(BytesIO(text.encode(encoding)), file_name)
