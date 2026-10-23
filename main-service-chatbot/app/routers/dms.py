"""GET endpoints for DMS documents and versions.

Read-only: the chatbot consumes DMS content, it does not write it.  Writes go
through the DMS service itself.

Endpoints:
  GET /dms/documents                  - paginated list of documents
  GET /dms/documents/{id}/versions    - all versions of one document
  GET /dms/versions/{id}              - single version (includes pvc_path and markdown)
  GET /dms/versions/{id}/download     - serve the file off the PVC
"""
import logging
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse

from app.dependencies import CurrentUserDep, get_db_service, get_file_storage_service
from app.logic.chat_records import page_of
from app.models.dms import DmsDocumentVersionDetail, PaginatedDmsDocuments
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dms", tags=["DMS"])


@router.get(
    "/documents",
    response_model=PaginatedDmsDocuments,
    summary="List DMS documents",
)
async def list_documents(
    current_user: CurrentUserDep,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    dms_category_id: int | None = Query(None, description="Filter by category"),
    name: str | None = Query(None, description="Partial name match"),
    is_archived: bool | None = Query(False, description="Include archived documents"),
    page_number: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    try:
        results = await db_service.call_procedure(
            StoredProcedures.FILTER_DMS_DOCUMENTS,
            [None, dms_category_id, name, is_archived, page_number, page_size],
        )
        rows, total = page_of(results)
        return PaginatedDmsDocuments(
            total_records=total, page_number=page_number, page_size=page_size, data=rows,
        )
    except Exception:
        logger.exception("Error listing DMS documents.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not list documents.")


@router.get(
    "/documents/{document_id}/versions",
    summary="List versions of a DMS document",
)
async def list_document_versions(
    current_user: CurrentUserDep,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    document_id: int = Path(...),
    is_archived: bool | None = Query(False),
):
    try:
        results = await db_service.call_procedure(
            StoredProcedures.FILTER_DMS_DOCUMENT_VERSIONS,
            [document_id, is_archived],
        )
        return results[0] if results else []
    except Exception:
        logger.exception("Error listing DMS document versions for %s.", document_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not list versions.")


@router.get(
    "/versions/{version_id}",
    response_model=DmsDocumentVersionDetail,
    summary="Get a single DMS document version",
)
async def get_version(
    current_user: CurrentUserDep,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    version_id: int = Path(...),
):
    results = await db_service.call_procedure(
        StoredProcedures.GET_DMS_DOCUMENT_VERSION, [version_id],
    )
    rows = results[0] if results else []
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {version_id} not found.")
    return rows[0]


@router.get(
    "/versions/{version_id}/download",
    summary="Download the file for a DMS document version",
    description=(
        "Streams the file stored at `pvc_path` off the shared PVC (or object store). "
        "Returns 404 when `pvc_path` is not set on the version, or 410 when the file "
        "is no longer present on the store."
    ),
)
async def download_version(
    current_user: CurrentUserDep,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    storage: Annotated[FileStorageService, Depends(get_file_storage_service)],
    version_id: int = Path(...),
):
    results = await db_service.call_procedure(
        StoredProcedures.GET_DMS_DOCUMENT_VERSION, [version_id],
    )
    rows = results[0] if results else []
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {version_id} not found.")
    row = rows[0]
    pvc_path: str | None = row.get("pvc_path")
    if not pvc_path:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This version has no file on the store (pvc_path is not set).",
        )
    try:
        file_data: BytesIO = await storage.download_file(pvc_path)
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_410_GONE,
            f"File {pvc_path!r} is no longer present on the store.",
        )
    filename = pvc_path.rsplit("/", 1)[-1]
    return StreamingResponse(
        file_data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
