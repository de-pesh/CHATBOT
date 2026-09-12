from io import BytesIO
from typing import List, Optional
import uuid
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse

from app.controllers.file_controller import FileController
from app.dependencies import (
    CurrentUserDep,
    get_db_service,
    has_permission,
    get_file_storage_service,
    get_chatbot_queue,
)
from app.config import Settings, get_settings
from app.models.files import FileCreate, FileResponse, FileType
from app.services import kb_service
from app.services.database_service import DatabaseService
from app.services.storage_service import FileStorageService
from app.utils import extract_blob_path_from_url
from app.services.queue_service import MessageQueueService
from app.security import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=List[FileResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Upload files and create file records",
    description="Uploads one or more files to Azure Blob Storage and creates corresponding file records in the database.",
    dependencies=[Depends(has_permission([Permission.FILE_CREATE]))]
)
async def create_files(
    current_user: CurrentUserDep,
    files: List[UploadFile] = File(...),
    file_type: FileType = FileType.FRAMEWORK,
    db_service: DatabaseService = Depends(get_db_service),
    file_storage_service: FileStorageService = Depends(get_file_storage_service),
    chatbot_queue: MessageQueueService = Depends(get_chatbot_queue),
    settings: Settings = Depends(get_settings),
) -> List[FileResponse]:
    """Endpoint to upload files and create file records."""
    controller = FileController(db_service, chatbot_queue, file_storage_service)
    file_responses = []
    for file in files:
        file_contents = await file.read()
        unique_blob_name = f"{uuid4()}_{file.filename}"
        file_url = await file_storage_service.upload_file(
            file_data=BytesIO(file_contents),
            file_name=unique_blob_name
        )

        # The original is kept at file_url above; ingest_bytes stores its own
        # copy under chatbot-kb/ for the parser to read. Indexing is not
        # best-effort here: the caller asked for a searchable document, so a
        # failure is surfaced rather than leaving an unindexed file record.
        result = await kb_service.ingest_bytes(
            file_storage_service, settings, file.filename or "unnamed", file_contents
        )
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Knowledge-base ingestion failed for {file.filename}: {result.message}",
            )
        kb_file_id = result.file_id

        file_data = FileCreate(
            name=file.filename,
            mimetype=file.content_type,
            file_path=file_url,
            file_type=file_type,
        )
        file_response = await controller.create_file(
            file_data, current_user.email, kb_file_id=kb_file_id
        )
        file_responses.append(file_response)
    return file_responses


@router.get(
    "/{file_id}",
    response_class=StreamingResponse,
    summary="Download a file by ID",
    description="Downloads a file from the configured file store using its unique ID.",
    dependencies=[Depends(has_permission([Permission.FILE_READ]))]
)
async def get_file_by_id(
    file_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    file_storage_service: FileStorageService = Depends(get_file_storage_service)
) -> StreamingResponse:
    """Endpoint to download a file by its ID."""
    controller = FileController(db_service)
    file_record = await controller.get_file_by_id(file_id)
    # file_path is whatever the backend returned from upload_file: an absolute
    # URL under Azure, a store-relative path under a mounted volume. This
    # normalises both. It used to take the last path segment, which happened to
    # work only because uploads land at the top level of the store.
    blob_name = extract_blob_path_from_url(file_record.file_path)
    file_stream = await file_storage_service.download_file(blob_name)
    return StreamingResponse(file_stream, media_type=file_record.mimetype)


@router.get(
    "/",
    response_model=List[FileResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve all file records",
    description="Retrieves a list of all file records currently in the system.",
    dependencies=[Depends(has_permission([Permission.FILE_READ]))]
)
async def get_all_files(
    current_user: CurrentUserDep,
    file_type: Optional[FileType] = None,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[FileResponse]:
    """Endpoint to retrieve all file records."""
    controller = FileController(db_service)
    return await controller.get_all_files(file_type)


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a file record",
    description="Deletes a file record from the system using its unique ID.",
    dependencies=[Depends(has_permission([Permission.FILE_DELETE]))]
)
async def delete_file(
    file_id: str,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    chatbot_queue: MessageQueueService = Depends(get_chatbot_queue),
    settings: Settings = Depends(get_settings),
    file_storage_service: FileStorageService = Depends(get_file_storage_service),
) -> JSONResponse:
    """Endpoint to delete a file record, or a knowledge-base document.

    `file_id` accepts either identifier, since the two are easily confused:
      * an integer  -> the SQL file record (cascades to its KB document, if any)
      * a UUID      -> a knowledge-base document only, as listed by GET /kb
    """
    controller = FileController(db_service, chatbot_queue, file_storage_service)
    try:
        sql_file_id = int(file_id)
    except ValueError:
        try:
            uuid.UUID(file_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{file_id}' is neither an integer file ID nor a knowledge-base UUID.",
            )
        # A KB document is usually owned by a file record. Deleting only the
        # vector chunks would leave that record active and still visible, so
        # resolve the owner and delete the whole thing when there is one.
        owning_file_id = await controller.find_file_id_by_kb_file_id(file_id)
        if owning_file_id is not None:
            await controller.delete_file(owning_file_id, current_user.email)
            return JSONResponse(
                content={
                    "message": f"Knowledge-base document {file_id} and its file record "
                               f"({owning_file_id}) deleted successfully"
                },
                status_code=status.HTTP_200_OK,
            )
        await kb_service.delete_document(settings, file_id)
        return JSONResponse(
            content={"message": f"Knowledge-base document {file_id} deleted successfully"},
            status_code=status.HTTP_200_OK,
        )
    await controller.delete_file(sql_file_id, current_user.email)
    return JSONResponse(content={"message": "File record deleted successfully"}, status_code=status.HTTP_200_OK)
