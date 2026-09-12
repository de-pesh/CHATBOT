import logging
from typing import List, Optional

from fastapi import HTTPException, status

from app.config import get_settings
from app.services import kb_service
from app.services.storage_service import FileStorageService
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
            storage: Optional[FileStorageService] = None,
    ):
        self.db_service = db_service
        self.redis_service = redis_service
        self.storage = storage




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
    ) -> List[FileInDB]:
        """
        Retrieves all active file records.

        Args:
            file_type (Optional[FileType]): The file type to filter by.

        Returns:
            List[FileInDB]: A list of file records.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.GET_FILES_BY_ENTITY,
                [None, None]
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
                # Best effort: the SQL record is already gone, and failing the
                # whole delete because the vector store is unreachable would
                # leave the caller with no way to retry.
                try:
                    await kb_service.delete_document(get_settings(), file_details.kb_file_id)
                except Exception as e:
                    logger.warning(
                        f"File {file_id} deleted, but removing kb_file_id="
                        f"{file_details.kb_file_id} from the knowledge base failed: {e}"
                    )
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