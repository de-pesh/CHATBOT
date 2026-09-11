import logging
from app.config import Settings
from app.services.database_service import DatabaseService
from app.services.parsing_rpc_service import ParsingRPCService
from app.services.indexing_rpc_service import IndexingRPCService
from app.core.stored_procedures import StoredProcedures
from app.utils.general_utils import extract_blob_path_from_url

logger = logging.getLogger(__name__)

async def add_file_handler(
    payload: dict,
    settings: Settings,
    db_service: DatabaseService,
):
    """
    Handles parsing, indexing, and updating a file record using existing stored procedures.

    The main service has already created the file record. This worker's job is to:
    1. Parse the file to get a `parsed_path`.
    2. Index the file in the vector database.
    3. Update the original file record with the `parsed_path`.
    """
    try:
        original_file_path = payload.get("file_path")
        if original_file_path is None:
            raise ValueError("original_file_path must be provided for CREATE action.")
        file_id=payload.get("file_id")
        blob_path = extract_blob_path_from_url(original_file_path)
        # 1. Parse the file using the gRPC service
        parsed_file_path = await ParsingRPCService.parse_file(settings=settings, file_path=blob_path)
        if not parsed_file_path:
            raise ValueError("Parsing service did not return a valid parsed file path.")
        logger.info(f"File {file_id} parsed to: {parsed_file_path}")

        # 2. Index the file in the vector database
        metadata = {"source_file_id": str(file_id)} # Link vector doc back to our SQL record
        indexed_id = await IndexingRPCService.indexing_add_file(
            settings=settings, parsed_file_path=parsed_file_path, metadata=metadata
        )
        if not indexed_id:
            raise ValueError("Indexing service did not return a valid ID.")
        logger.info(f"File {file_id} indexed with vector ID: {indexed_id}")

        file_name = payload.get("name") or payload.get("file_name")
        mimetype = payload.get("mimetype")
        user_id = payload.get("user_id")

        if not file_name:
            raise ValueError("File name must be provided in payload for CREATE action.")
        if not mimetype:
            raise ValueError("MIME type must be provided in payload for CREATE action.")
        if not user_id:
            raise ValueError("user_id must be provided in payload for CREATE action.")

        # 3. Update the original file record with the parsed_path using the existing `upsert_file` SP
        await db_service.call_procedure(
            StoredProcedures.UPSERT_FILE,
            [
                file_id,                            # IN `id` - Specifies which record to update
                file_name,                          # IN `name`
                mimetype,                           # IN `mimetype`
                original_file_path,                 # IN `file_path`
                parsed_file_path,                   # IN `parsed_path` - This is the new information
                "framework",                        # IN `file_type`
                True,                               # IN `is_active`
                user_id                             # IN `user_id`
            ]
        )
        logger.info(f"Updated file record {file_id} with parsed path via stored procedure.")

    except Exception as e:
        error_message = f"An error occurred during add_file_handler for file_id {file_id}: {e}"
        logger.exception(error_message)
        raise # Re-raise to allow the worker to handle retries/DLQ

async def delete_file_handler(
    payload: dict,
    settings: Settings,
    db_service: DatabaseService
):
    """
    Handles deleting a file from the index and soft-deleting it via the `delete_file` stored procedure.
    """
    try:
        # 1. Delete the file from the vector index
        file_id=payload.get("file_id")
        if file_id is None:
            raise ValueError("file_id must be provided for DELETE action.")
        deleted_by=payload.get("user_id"),
        await IndexingRPCService.indexing_delete_file(settings=settings, source_file_id=str(file_id))
        logger.info(f"File {file_id} deleted from vector index.")

        # 2. Soft-delete the file record in the database using the existing `delete_file` SP
        await db_service.call_procedure(
            StoredProcedures.DELETE_FILE,
            [file_id, deleted_by]
        )
        logger.info(f"File record {file_id} marked as inactive via stored procedure.")

    except Exception as e:
        error_message = f"An error occurred during delete_file_handler for file_id {file_id}: {e}"
        logger.exception(error_message)
        raise