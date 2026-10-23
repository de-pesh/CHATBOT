"""Removing a document from the vector store, and marking its version, over the compute service's REST API.

Ingestion (parse + index) happens off the request path in kb_worker (see
app/kb_worker/worker.py); deleting stays on the request path.
"""
import logging

import httpx
from fastapi import HTTPException, status

from app.config import Settings

logger = logging.getLogger(__name__)


async def set_document_version(
    settings: Settings, file_id: str, *, is_latest: bool, version_no: int | None = None,
    version_label: str | None = None, document_key: str | None = None,
) -> str:
    """Writes one document's version onto its chunks: whether it is the latest, and its number, label and document
    key. Returns the message."""
    try:
        async with httpx.AsyncClient(timeout=settings.RPC_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{settings.INDEXING_SERVICE_URL}/set-latest",
                json={
                    "collection_name": settings.MONGODB_COLLECTION_NAME, "file_id": file_id, "is_latest": is_latest,
                    "version_no": version_no, "version_label": version_label, "document_key": document_key,
                },
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as e:
        logger.exception("Set-latest failed for file_id %s", file_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Set-latest failed: {e}") from e
    if not body["success"]:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=body["message"])
    return body["message"]


async def delete_document(settings: Settings, file_id: str) -> str:
    """Removes one document's chunks from the vector store. Returns the message."""
    try:
        async with httpx.AsyncClient(timeout=settings.RPC_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{settings.INDEXING_SERVICE_URL}/delete",
                json={"collection_name": settings.MONGODB_COLLECTION_NAME, "file_id": file_id},
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as e:
        logger.exception("Delete failed for file_id %s", file_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Delete failed: {e}") from e
    if not body["success"]:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=body["message"])
    return body["message"]
