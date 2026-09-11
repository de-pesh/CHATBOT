"""
Knowledge-base document routes.

These proxy to the chatbot-inferencing-service's `/kb` endpoints so the
knowledge base can be inspected and pruned from main-service's API surface.

Note the identifier: a knowledge-base document is addressed by its vector-store
ID (a UUID string), which is NOT the integer primary key used by `/files/{file_id}`.
The two are related through `FileInDB.kb_file_id`.
"""
from typing import List

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.core.permissions import Permission
from app.controllers.file_controller import FileController
from app.dependencies import (
    CurrentUserDep,
    get_chatbot_queue,
    get_db_service,
    get_http_client,
    has_permission,
)
from app.models.files import KBDocumentResponse
from app.services.database_service import DatabaseService
from app.services.redis_service import RedisQueueService

router = APIRouter()


def _kb_base_url() -> str:
    """Returns the configured knowledge-base service base URL, or raises."""
    settings = get_settings()
    if not settings.CHATBOT_INFERENCING_SERVICE_URL:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CHATBOT_INFERENCING_SERVICE_URL is not configured; cannot reach the knowledge base.",
        )
    return f"{settings.CHATBOT_INFERENCING_SERVICE_URL.rstrip('/')}/kb"


@router.get(
    "/",
    response_model=List[KBDocumentResponse],
    status_code=status.HTTP_200_OK,
    summary="List knowledge-base documents",
    description="Lists every document currently indexed in the chatbot's knowledge base, along with its UUID document ID and chunk count.",
    dependencies=[Depends(has_permission([Permission.FILE_READ]))]
)
async def list_kb_documents(
    current_user: CurrentUserDep,
    http_client: aiohttp.ClientSession = Depends(get_http_client),
) -> List[KBDocumentResponse]:
    """Endpoint to list the documents held in the knowledge base."""
    try:
        async with http_client.get(_kb_base_url()) as response:
            body = await response.text()
            if response.status >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Knowledge-base service returned {response.status}: {body}",
                )
            return await response.json()
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not reach knowledge-base service: {exc}",
        )


@router.delete(
    "/{kb_file_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a knowledge-base document",
    description="Removes a document from the chatbot's knowledge base using its UUID document ID (as returned by GET /kb). This does not delete the corresponding SQL file record; use DELETE /files/{file_id} for that.",
    dependencies=[Depends(has_permission([Permission.FILE_DELETE]))]
)
async def delete_kb_document(
    kb_file_id: str,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    chatbot_queue: RedisQueueService = Depends(get_chatbot_queue),
    http_client: aiohttp.ClientSession = Depends(get_http_client),
) -> JSONResponse:
    """Endpoint to remove a single document from the knowledge base.

    If a file record owns the document, that record is deleted too; otherwise
    the KB document would vanish from search while its file record stayed
    active and visible.
    """
    controller = FileController(db_service, chatbot_queue, http_client)
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
    await controller.delete_knowledge_base_document(kb_file_id)
    return JSONResponse(
        content={"message": f"Knowledge-base document {kb_file_id} deleted successfully"},
        status_code=status.HTTP_200_OK,
    )
