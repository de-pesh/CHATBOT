import logging
from typing import List, Optional

import aiohttp
from fastapi import HTTPException, status

from app.config import get_settings
from app.stored_procedures import StoredProcedures
from app.models.files import FileCreate, FileInDB, FileType
from app.services.database_service import DatabaseService
from app.services.redis_service import RedisQueueService

logger = logging.getLogger(__name__)

class FileController:
    """
    Controller for managing file-related operations.

    This class encapsulates the business logic for creating, retrieving,
    updating, and deleting files, interacting with the database
    service to perform these actions.
    """

    def __init__(self,
            db_service: DatabaseService,
            redis_service: Optional[RedisQueueService] = None,
            http_client: Optional[aiohttp.ClientSession] = None,
    ):
        self.db_service = db_service
        self.redis_service = redis_service
        self.http_client = http_client


    @staticmethod
    async def push_to_knowledge_base(
        http_client: aiohttp.ClientSession,
        filename: str,
        content_type: Optional[str],
        file_bytes: bytes,
    ) -> str:
        """
        Pushes a document into the chatbot-inferencing-service's knowledge base
        (POST {CHATBOT_INFERENCING_SERVICE_URL}/kb) and returns its kb file_id.

        This is a synchronous, opt-in call: if it fails, the caller should
        surface the error rather than silently uploading without indexing,
        since the caller explicitly asked for the document to be searchable.
        """
        settings = get_settings()
        if not settings.CHATBOT_INFERENCING_SERVICE_URL:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="CHATBOT_INFERENCING_SERVICE_URL is not configured; cannot add file to knowledge base.",
            )

        form = aiohttp.FormData()
        form.add_field(
            "files",
            file_bytes,
            filename=filename,
            content_type=content_type or "application/octet-stream",
        )

        kb_url = f"{settings.CHATBOT_INFERENCING_SERVICE_URL.rstrip('/')}/kb"
        try:
            async with http_client.post(kb_url, data=form) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Knowledge-base service returned {response.status}: {body}",
                    )
                results = await response.json()
        except aiohttp.ClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not reach knowledge-base service: {exc}",
            )

        if not results:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Knowledge-base service returned an empty response.",
            )
        result = results[0]
        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Knowledge-base ingestion failed: {result.get('message')}",
            )
        return result["file_id"]


    async def find_file_id_by_kb_file_id(self, kb_file_id: str) -> Optional[int]:
        """
        Finds the SQL file record that owns a given knowledge-base document.

        `filter_files` has no kb_file_id parameter, so this scans the active
        file records and matches in Python.

        Args:
            kb_file_id (str): The knowledge-base document ID (a UUID string).

        Returns:
            Optional[int]: The owning file record's ID, or None if the
                knowledge-base document is not referenced by any file record.
        """
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_FILES,
            [None, None, None, None, True, 1, 1000000]
        )
        if not results or not results[0]:
            return None
        for row in results[0]:
            if row.get("kb_file_id") == kb_file_id:
                return int(row["id"])
        return None


    async def delete_knowledge_base_document(self, kb_file_id: str) -> None:
        """
        Removes a document from the knowledge base by its vector-store ID,
        surfacing any failure to the caller.

        Unlike `_delete_from_knowledge_base` (which is best-effort, used when
        cascading from a SQL file delete), this is for deletes the user asked
        for directly, so errors must not be swallowed.

        Args:
            kb_file_id (str): The knowledge-base document ID (a UUID string).

        Raises:
            HTTPException: If the knowledge-base service is unconfigured,
                unreachable, or reports a failure.
        """
        settings = get_settings()
        if not self.http_client or not settings.CHATBOT_INFERENCING_SERVICE_URL:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="CHATBOT_INFERENCING_SERVICE_URL is not configured; cannot delete from the knowledge base.",
            )
        kb_url = f"{settings.CHATBOT_INFERENCING_SERVICE_URL.rstrip('/')}/kb/{kb_file_id}"
        try:
            async with self.http_client.delete(kb_url) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Knowledge-base service returned {response.status}: {body}",
                    )
        except aiohttp.ClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not reach knowledge-base service: {exc}",
            )


    async def _delete_from_knowledge_base(self, kb_file_id: str) -> None:
        """Best-effort removal of a document from the knowledge base on file delete."""
        settings = get_settings()
        if not self.http_client or not settings.CHATBOT_INFERENCING_SERVICE_URL:
            logger.warning(
                f"Cannot delete kb_file_id={kb_file_id}: knowledge-base service not configured."
            )
            return
        kb_url = f"{settings.CHATBOT_INFERENCING_SERVICE_URL.rstrip('/')}/kb/{kb_file_id}"
        try:
            async with self.http_client.delete(kb_url) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.warning(
                        f"Failed to delete kb_file_id={kb_file_id} from knowledge base: {response.status} {body}"
                    )
        except aiohttp.ClientError as exc:
            logger.warning(f"Could not reach knowledge-base service to delete kb_file_id={kb_file_id}: {exc}")


    async def create_file(
        self,
        file_data: FileCreate,
        user_id: str,
        kb_file_id: Optional[str] = None,
    ) -> FileInDB:
        """
        Creates a new file record.

        Args:
            file_data (FileCreate): The data for the new file record.
            user_id (str): The ID of the user creating the file record.
            kb_file_id (Optional[str]): The knowledge-base document ID, if this file
                was already pushed into the chatbot's knowledge base.

        Returns:
            FileInDB: The created file record's data including its generated ID.

        Raises:
            HTTPException: If the file record cannot be created or linked.
        """
        try:
            # Create the file record, which returns the new ID
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_FILE,
                [
                    None,
                    file_data.name,
                    file_data.mimetype,
                    file_data.file_path,
                    file_data.parsed_path,
                    file_data.file_type.value,
                    True, # is_active is always True on creation
                    kb_file_id,
                    user_id,
                ],
            )
            if not results or not results[0] or not results[0][0].get('id'):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create file record or retrieve its ID.",
                )

            new_file_id = results[0][0]['id']

            # Fetch the full file record using the new ID
            created_file = await self.get_file_by_id(new_file_id)

            return created_file
        except Exception as e:
            logger.exception(f"Error creating file record: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred: {e}",
            )


    async def get_file_by_id(self, file_id: int) -> FileInDB:
        """
        Retrieves a file record by its ID.

        Args:
            file_id (str): The unique identifier of the file record.

        Returns:
            FileInDB: The file record's data.

        Raises:
            HTTPException: If the file record is not found.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FILES,
                [file_id, None, None, None, None, 1, 1]
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File record not found.")
            return FileInDB(**results[0][0])
        except Exception as e:
            logger.exception(f"Error retrieving file record {file_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_all_files(
        self,
        file_type: Optional[FileType] = None,
        entity_type: Optional[FileType] = None,
        entity_id: Optional[int] = None,
    ) -> List[FileInDB]:
        """
        Retrieves all file records, optionally filtered by a linked entity.

        Args:
            file_type (Optional[FileType]): The file type to filter by.
            entity_type (Optional[FileType]): The type of entity to filter by.
            entity_id (Optional[int]): The ID of the entity.

        Returns:
            List[FileInDB]: A list of file records.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.GET_FILES_BY_ENTITY,
                [entity_type.value if entity_type else None, entity_id]
            )
            if not results or not results[0]:
                return []

            files = [FileInDB(**file_data) for file_data in results[0]]
            if file_type:
                files = [file for file in files if file.file_type == file_type]

            return files
        except Exception as e:
            logger.exception(f"Error retrieving all file records: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_file_parsed_path(self, file_id: int, parsed_path: str, user_id: str) -> FileInDB:
        """Updates the parsed_path for an existing file record."""
        try:
            existing_file = await self.get_file_by_id(file_id)
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_FILE,
                [
                    file_id,
                    existing_file.name,
                    existing_file.mimetype,
                    existing_file.file_path,
                    parsed_path,
                    existing_file.file_type.value,
                    existing_file.is_active,
                    existing_file.kb_file_id,
                    user_id,
                ],
            )
            if not results or not results[0] or not results[0][0].get('id'):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update parsed file path.",
                )
            return await self.get_file_by_id(file_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating parsed path for file {file_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred: {e}",
            )


    async def delete_file(self, file_id: int, user_id: str) -> dict:
        """
        Deletes a file record by its ID.

        Args:
            file_id (str): The unique identifier of the file record to delete.
            user_id (str): The ID of the user performing the deletion.

        Returns:
            dict: A confirmation message.

        Raises:
            HTTPException: If the file record is not found or cannot be deleted.
        """
        try:
            file_details = await self.get_file_by_id(file_id)

            await self.db_service.call_procedure(
                StoredProcedures.DELETE_FILE,
                [file_id, user_id]
            )
            if file_details.kb_file_id:
                await self._delete_from_knowledge_base(file_details.kb_file_id)
            if self.redis_service:
                message = {
                    "file_id": file_id,
                    "user_id": user_id,
                    "action_type": "delete"
                }
                await self.redis_service.send_message(message)
                logger.info(f"Sent framework file info to Redis queue for file_id: {file_id}")
            return {"message": f"File record {file_id} deleted successfully."}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting file record {file_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")