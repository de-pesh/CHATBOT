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
        assessment_id: Optional[int] = None,
        framework_id: Optional[int] = None,
        kb_file_id: Optional[str] = None,
    ) -> FileInDB:
        """
        Creates a new file record and links it to an assessment or framework if specified.

        Args:
            file_data (FileCreate): The data for the new file record.
            user_id (str): The ID of the user creating the file record.
            assessment_id (Optional[int]): The ID of the assessment to link the file to.
            framework_id (Optional[int]): The ID of the framework to link the file to.
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

            # Link the file to the appropriate entity
            await self.link_file_to_entity(
                file_id=created_file.id,
                user_id=user_id,
                file_data = created_file,
                assessment_id=assessment_id,
                framework_id=framework_id,
            )

            return created_file
        except Exception as e:
            logger.exception(f"Error creating file record: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred: {e}",
            )


    async def link_file_to_entity(
        self,
        file_id: int,
        user_id: str,
        file_data: dict,
        assessment_id: Optional[int] = None,
        framework_id: Optional[int] = None,
    ) -> None:
        """
        Links a file to either an assessment or the latest version of a framework.

        Args:
            file_id (int): The ID of the file to link.
            user_id (str): The ID of the user performing the action.
            assessment_id (Optional[int]): The ID of the assessment.
            framework_id (Optional[int]): The ID of the framework.
        """
        if framework_id:
            # Get the latest version of the framework
            versions = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSIONS,
                [None, framework_id, None, None, True, 1, 1],  # Assuming the latest is what we want
            )
            if not versions or not versions[0]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No active versions found for framework {framework_id}",
                )
            latest_version_id = versions[0][0]["id"]

            # Link the file to the latest version
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_VERSION_FILE,
                [None, latest_version_id, file_id, True, user_id],
            )
            if self.redis_service:
                message = {
                    "file_id": file_id,
                    "name": file_data.name,
                    "mimetype":file_data.mimetype,
                    "file_path":file_data.file_path,
                    "framework_id": framework_id,
                    "latest_version_id": latest_version_id,
                    "user_id": user_id,
                    "action_type": "create"
                }
                await self.redis_service.send_message(message)
                logger.info(f"Sent framework file info to Redis queue for file_id: {file_id}")

        elif assessment_id:
            # Link the file to the assessment
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_FILE,
                [None, assessment_id, file_id, True, user_id],
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


    async def get_evidence_files_by_assessment(
        self,
        assessment_id: int,
        page_number: int = 1,
        page_size: int = 1000,
    ) -> List[FileInDB]:
        """
        Retrieves all evidence files linked to a given assessment via assessment_files.

        Returns deduplicated file records (by file_id), only those with file_type=evidence
        and is_active=True on both the mapping and the file.
        """
        try:
            mapping_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_FILES,
                [None, assessment_id, None, True, page_number, page_size],
            )
            mapping_rows = mapping_results[0] if mapping_results and mapping_results[0] else []
            if not mapping_rows:
                return []

            # Dedupe file_ids while preserving order (latest mapping wins)
            seen_file_ids: set = set()
            ordered_file_ids: List[int] = []
            for row in mapping_rows:
                fid = row.get("file_id")
                if fid is None or fid in seen_file_ids:
                    continue
                seen_file_ids.add(fid)
                ordered_file_ids.append(int(fid))

            files: List[FileInDB] = []
            for fid in ordered_file_ids:
                file_results = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_FILES,
                    [fid, None, None, None, True, 1, 1],
                )
                if not file_results or not file_results[0]:
                    continue
                file_record = FileInDB(**file_results[0][0])
                if file_record.file_type == FileType.EVIDENCE:
                    files.append(file_record)

            return files
        except Exception as e:
            logger.exception(
                f"Error retrieving evidence files for assessment {assessment_id}: {e}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred while retrieving evidence files: {e}",
            )


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
            if file_details.file_type == FileType.FRAMEWORK:
                # get the id for the version file mapping, if one exists (a file
                # can be unlinked, e.g. it was uploaded without a framework_id)
                version_file_data = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_VERSION_FILES,
                    [None, None, file_id, True, 1, 1]
                )
                if version_file_data and version_file_data[0]:
                    version_file_id = version_file_data[0][0]["id"]
                    await self.db_service.call_procedure(
                        StoredProcedures.DELETE_VERSION_FILE,
                        [version_file_id, user_id]
                    )

            if file_details.file_type == FileType.EVIDENCE:
                # get the id for the assessment file mapping, if one exists (a file
                # can be unlinked, e.g. it was uploaded without an assessment_id)
                assessment_file_data = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ASSESSMENT_FILES,
                    [None, None, file_id, True, 1, 1]
                )
                if assessment_file_data and assessment_file_data[0]:
                    assessment_file_id = assessment_file_data[0][0]["id"]
                    await self.db_service.call_procedure(
                        StoredProcedures.DELETE_ASSESSMENT_FILE,
                        [assessment_file_id, user_id]
                    )

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