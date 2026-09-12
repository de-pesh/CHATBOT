"""
Knowledge-base document routes: upload, list and remove.

Upload and delete also exist on /files, which additionally keeps a SQL record
and the original blob. These endpoints work on the vector store alone, which is
what you want when a document was indexed without a file record - or when you
want it gone from search without touching the record.

Note the identifier: a knowledge-base document is addressed by its vector-store
ID (a UUID string), which is NOT the integer primary key used by
`/files/{file_id}`. The two are related through `FileInDB.kb_file_id`.
"""
from typing import Annotated, List

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.controllers.file_controller import FileController
from app.dependencies import (
    CurrentUserDep,
    get_chatbot_queue,
    get_db_service,
    get_file_storage_service,
    has_permission,
)
from app.security import Permission
from app.services import kb_service
from app.services.database_service import DatabaseService
from app.services.kb_service import KBDocument, KBUploadResult
from app.services.queue_service import MessageQueueService
from app.services.storage_service import FileStorageService

router = APIRouter()


@router.post(
    "/",
    response_model=List[KBUploadResult],
    status_code=status.HTTP_201_CREATED,
    summary="Upload documents into the knowledge base",
    description="Uploads one or more documents, parses them and indexes them for the chatbot. One result per file: a failure in one does not abort the rest.",
    dependencies=[Depends(has_permission([Permission.FILE_CREATE]))]
)
async def upload_kb_documents(
    current_user: CurrentUserDep,
    settings: Annotated[Settings, Depends(get_settings)],
    files: List[UploadFile] = File(...),
    storage: FileStorageService = Depends(get_file_storage_service),
) -> List[KBUploadResult]:
    """Endpoint to add documents to the knowledge base without a file record."""
    return [
        await kb_service.ingest_bytes(storage, settings, file.filename or "unnamed", await file.read())
        for file in files
    ]


@router.get(
    "/",
    response_model=List[KBDocument],
    status_code=status.HTTP_200_OK,
    summary="List knowledge-base documents",
    description="Lists every document currently indexed in the chatbot's knowledge base, along with its UUID document ID and chunk count.",
    dependencies=[Depends(has_permission([Permission.FILE_READ]))]
)
async def list_kb_documents(
    current_user: CurrentUserDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> List[KBDocument]:
    """Endpoint to list the documents held in the knowledge base."""
    return await kb_service.list_documents(settings)


@router.delete(
    "/{kb_file_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a knowledge-base document",
    description="Removes a document from the chatbot's knowledge base using its UUID document ID (as returned by GET /kb). If a file record owns the document, that record is deleted too.",
    dependencies=[Depends(has_permission([Permission.FILE_DELETE]))]
)
async def delete_kb_document(
    kb_file_id: str,
    current_user: CurrentUserDep,
    settings: Annotated[Settings, Depends(get_settings)],
    db_service: DatabaseService = Depends(get_db_service),
    chatbot_queue: MessageQueueService = Depends(get_chatbot_queue),
    storage: FileStorageService = Depends(get_file_storage_service),
) -> JSONResponse:
    """Endpoint to remove a single document from the knowledge base.

    If a file record owns the document, that record is deleted too; otherwise
    the KB document would vanish from search while its file record stayed
    active and visible.
    """
    controller = FileController(db_service, chatbot_queue, storage)
    owning_file_id = await controller.find_file_id_by_kb_file_id(kb_file_id)
    if owning_file_id is not None:
        await controller.delete_file(owning_file_id, current_user.email)
        return JSONResponse(
            content={
                "message": f"Knowledge-base document {kb_file_id} and its file record "
                           f"({owning_file_id}) deleted successfully"
            },
            status_code=status.HTTP_200_OK,
        )
    message = await kb_service.delete_document(settings, kb_file_id)
    return JSONResponse(content={"message": message}, status_code=status.HTTP_200_OK)
