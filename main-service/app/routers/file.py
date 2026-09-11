from io import BytesIO
from typing import List, Optional
from urllib.parse import urlsplit
import uuid
from uuid import uuid4

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse

from app.controllers.file_controller import FileController
from app.dependencies import (
    CurrentUserDep,
    get_db_service,
    has_permission,
    get_file_storage_service,
    get_chatbot_queue,
    get_http_client,
)
from app.models.files import FileCreate, FileResponse, FileType
from app.services.database_service import DatabaseService
from app.services.storage_service import FileStorageService
from app.services.redis_service import RedisQueueService
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
    assessment_id: Optional[int] = None,
    framework_id: Optional[int] = None,
    db_service: DatabaseService = Depends(get_db_service),
    file_storage_service: FileStorageService = Depends(get_file_storage_service),
    chatbot_queue: RedisQueueService = Depends(get_chatbot_queue),
    http_client: aiohttp.ClientSession = Depends(get_http_client),
) -> List[FileResponse]:
    """Endpoint to upload files and create file records."""
    controller = FileController(db_service, chatbot_queue, http_client)
    file_responses = []
    for file in files:
        file_contents = await file.read()
        unique_blob_name = f"{uuid4()}_{file.filename}"
        file_url = await file_storage_service.upload_file(
            file_data=BytesIO(file_contents),
            file_name=unique_blob_name
        )

        kb_file_id = await FileController.push_to_knowledge_base(
            http_client=http_client,
            filename=file.filename,
            content_type=file.content_type,
            file_bytes=file_contents,
        )

        file_data = FileCreate(
            name=file.filename,
            mimetype=file.content_type,
            file_path=file_url,
            file_type=file_type,
            assessment_id=assessment_id,
            framework_id=framework_id
        )
        file_response = await controller.create_file(
            file_data, current_user.email, assessment_id, framework_id, kb_file_id=kb_file_id
        )
        file_responses.append(file_response)
    return file_responses


@router.get(
    "/{file_id}",
    response_class=StreamingResponse,
    summary="Download a file by ID",
    description="Downloads a file from Azure Blob Storage using its unique ID.",
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
    blob_name = urlsplit(file_record.file_path).path.split('/')[-1]
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
    entity_type: Optional[FileType] = None,
    entity_id: Optional[int] = None,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[FileResponse]:
    """Endpoint to retrieve all file records."""
    controller = FileController(db_service)
    return await controller.get_all_files(file_type, entity_type, entity_id)


@router.get(
    "/by-assessment/{assessment_id}",
    response_model=List[FileResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve evidence files for a specific assessment",
    description=(
        "Returns all evidence files linked to the given assessment via the "
        "assessment_files table. Deduplicated by file id; only active mappings "
        "and files of file_type='evidence' are returned."
    ),
    dependencies=[Depends(has_permission([Permission.FILE_READ]))]
)
async def get_evidence_files_by_assessment(
    assessment_id: int,
    current_user: CurrentUserDep,
    page_number: int = 1,
    page_size: int = 1000,
    db_service: DatabaseService = Depends(get_db_service),
) -> List[FileResponse]:
    """Endpoint to retrieve evidence files for a given assessment."""
    controller = FileController(db_service)
    return await controller.get_evidence_files_by_assessment(
        assessment_id=assessment_id,
        page_number=page_number,
        page_size=page_size,
    )


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
    chatbot_queue: RedisQueueService = Depends(get_chatbot_queue),
    http_client: aiohttp.ClientSession = Depends(get_http_client),
) -> JSONResponse:
    """Endpoint to delete a file record, or a knowledge-base document.

    `file_id` accepts either identifier, since the two are easily confused:
      * an integer  -> the SQL file record (cascades to its KB document, if any)
      * a UUID      -> a knowledge-base document only, as listed by GET /kb
    """
    controller = FileController(db_service, chatbot_queue, http_client)
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
        await controller.delete_knowledge_base_document(file_id)
        return JSONResponse(
            content={"message": f"Knowledge-base document {file_id} deleted successfully"},
            status_code=status.HTTP_200_OK,
        )
    await controller.delete_file(sql_file_id, current_user.email)
    return JSONResponse(content={"message": "File record deleted successfully"}, status_code=status.HTTP_200_OK)
