import functools
import logging

from app.config import get_settings
from app.controllers.version_controller import sync_quietly
from app.logic.files import extract_blob_path_from_url
from app.models.files import FileCreate, FileInDB, ProcessingStatus
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import ALL_ROWS, StoredProcedures
from app.services.kb import kb_service
from app.services.storage.storage_service import FileStorageService
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


def _wrap_db_errors(operation: str):
    """Logs an unexpected failure and surfaces it as a 500.

    An `HTTPException` raised deliberately inside the wrapped method (e.g.
    `get_file_by_id`'s 404) passes through untouched - only an exception
    nobody anticipated gets logged and wrapped here, so every FileController
    method does not need to repeat the same try/except.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as e:
                logger.exception(f"Error {operation}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"An error occurred: {e}",
                ) from e
        return wrapper
    return decorator


class FileController:
    """
    Controller for managing file-related operations.

    This class encapsulates the business logic for creating, retrieving,
    updating, and deleting files, interacting with the database
    service to perform these actions.
    """

    def __init__(self,
            db_service: DatabaseService,
            storage: FileStorageService | None = None,
    ):
        self.db_service = db_service
        self.storage = storage

    async def find_file_id_by_vector_store_file_id(self, vector_store_file_id: str) -> int | None:
        """The ID of the file record that owns this vector-store document (a UUID
        string), or None if no file record references it.

        `filter_files` has no vector_store_file_id parameter, so this scans the active
        file records and matches in Python.
        """
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_FILES,
            [None, None, None, True, 1, ALL_ROWS]
        )
        if not results or not results[0]:
            return None
        for row in results[0]:
            if row.get("vector_store_file_id") == vector_store_file_id:
                return int(row["id"])
        return None

    @_wrap_db_errors("creating file record")
    async def create_file(
        self,
        file_data: FileCreate,
        user_id: str,
        vector_store_file_id: str | None = None,
    ) -> FileInDB:
        """Creates a file record and returns it, with its generated ID. `vector_store_file_id` is the
        vector-store document ID, if the file was already pushed into it."""
        results = await self.db_service.call_procedure(
            StoredProcedures.UPSERT_FILE,
            [
                None,
                file_data.name,
                file_data.mimetype,
                file_data.file_path,
                file_data.extracted_text_path,
                True, # is_active is always True on creation
                vector_store_file_id,
                user_id,
            ],
        )
        if not results or not results[0] or not results[0][0].get('id'):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create file record or retrieve its ID.",
            )

        new_file_id = results[0][0]['id']

        return await self.get_file_by_id(new_file_id)


    @_wrap_db_errors("retrieving file record")
    async def get_file_by_id(self, file_id: int) -> FileInDB:
        """The file record with this ID; raises a 404 HTTPException if there is none."""
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_FILES,
            [file_id, None, None, None, 1, 1]
        )
        if not results or not results[0]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File record not found.")
        return FileInDB(**results[0][0])


    async def update_processing_status(
        self,
        file_id: int,
        processing_status: ProcessingStatus,
        processing_error: str | None = None,
    ) -> None:
        """Updates just the processing_status/processing_error columns.

        Used by kb_worker to report where a file's async parse+index job
        stands (pending -> processing -> completed, or failed with a reason).
        Deliberately separate from upsert_file: kb_worker doesn't have (and
        shouldn't need) the rest of the row to flip a status flag.
        """
        await self.db_service.call_procedure(
            StoredProcedures.UPDATE_FILE_PROCESSING_STATUS,
            [file_id, processing_status.value, processing_error],
        )


    @_wrap_db_errors("retrieving all file records")
    async def get_all_files(self) -> list[FileInDB]:
        """All active file records."""
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_FILES,
            [None, None, None, True, 1, ALL_ROWS]
        )
        if not results or not results[0]:
            return []

        return [FileInDB(**file_data) for file_data in results[0]]


    @_wrap_db_errors("deleting file record")
    async def delete_file(self, file_id: int, user_id: str) -> dict:
        """Deletes a file record by its ID, then its knowledge-base document and stored file.
        Raises a 404 HTTPException if there is no such record."""
        file_details = await self.get_file_by_id(file_id)

        await self.db_service.call_procedure(
            StoredProcedures.DELETE_FILE,
            [file_id, user_id]
        )
        if file_details.vector_store_file_id:
            # Best effort: the SQL record is already gone, and failing the
            # whole delete because the vector store is unreachable would
            # leave the caller with no way to retry.
            try:
                await kb_service.delete_document(get_settings(), file_details.vector_store_file_id)
            except Exception as e:
                logger.warning(
                    f"File {file_id} deleted, but removing vector_store_file_id="
                    f"{file_details.vector_store_file_id} from the knowledge base failed: {e}"
                )
        # If the deleted file was the latest version, the one before it becomes the latest again.
        await sync_quietly(self.db_service, get_settings(), file_details.document_key)
        if self.storage and file_details.file_path:
            # Without this the stored object is left behind: on Azure that leaks
            # blobs; on a persistent volume it fills a fixed-size PVC.
            # Best effort, like the vector removal above.
            try:
                await self.storage.delete_file(
                    extract_blob_path_from_url(file_details.file_path)
                )
                logger.info(f"Removed stored file for file_id {file_id}.")
            except Exception as e:
                logger.warning(
                    f"File {file_id} deleted, but removing it from the file "
                    f"store failed: {e}"
                )
        return {"message": f"File record {file_id} deleted successfully."}