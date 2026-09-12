"""Knowledge-base ingestion, listing and removal.

The one place that talks to the compute service. Ingestion is three steps:

    upload the original to the file store
      -> ParsingService.ParseFile(path)   -> parsed text, back in the store
      -> VectorService.Index(parsed_path) -> embedded into the vector store

The payload never crosses the RPC: both services are configured against the
same file store, so only paths travel. That is why STORAGE_TYPE has to match on
both sides. Parsing used to run in-process in the chat service; it sits behind
ParseFile now so the OCR toolchain stays out of this image.

Listing goes through VectorService.ListDocuments. It used to run the grouping
aggregation against Mongo from this process, which meant the control plane held
a vector-database client and knew the chunk schema - so the "pluggable" vector
store was only pluggable for writes. The compute service is now the only
process that knows which vector database is in use.
"""
import logging
import uuid
from io import BytesIO
from typing import List, Optional

import grpc
from fastapi import HTTPException, status
from google.protobuf.struct_pb2 import Struct
from pydantic import BaseModel

from app import indexing_service_pb2, indexing_service_pb2_grpc
from app.config import Settings
from app.services.storage_service import FileStorageService

logger = logging.getLogger(__name__)

# The parser rejects anything it cannot handle, but checking here avoids a blob
# upload and two RPCs for a file that was never going to work.
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".png", ".jpeg", ".jpg"}


class KBDocument(BaseModel):
    file_id: str
    file_name: Optional[str] = None
    chunk_count: int


class KBUploadResult(BaseModel):
    file_name: str
    success: bool
    file_id: Optional[str] = None
    message: Optional[str] = None


async def ingest_bytes(
    storage: FileStorageService,
    settings: Settings,
    filename: str,
    data: bytes,
) -> KBUploadResult:
    """Uploads, parses and indexes one document. Never raises; reports per file."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return KBUploadResult(file_name=filename, success=False,
                              message=f"Unsupported extension '{ext}'.")

    blob_path = f"chatbot-kb/{uuid.uuid4()}_{filename}"
    try:
        await storage.upload_file(BytesIO(data), blob_path)
    except Exception as e:
        logger.exception("Blob upload failed for %s", filename)
        return KBUploadResult(file_name=filename, success=False,
                              message=f"Upload failed: {e}")

    try:
        async with grpc.aio.insecure_channel(settings.RPC_SERVER) as channel:
            parse_stub = indexing_service_pb2_grpc.ParsingServiceStub(channel)
            parsed = await parse_stub.ParseFile(
                indexing_service_pb2.ParseFileRequest(file_path=blob_path)
            )
            if not parsed.success:
                return KBUploadResult(file_name=filename, success=False,
                                      message=f"Parsing failed: {parsed.message}")

            metadata = Struct()
            metadata.update({"file_name": filename})
            vector_stub = indexing_service_pb2_grpc.VectorServiceStub(channel)
            indexed = await vector_stub.Index(indexing_service_pb2.IndexRequest(
                collection_name=settings.MONGODB_COLLECTION_NAME,
                file_path=parsed.blob_path,
                metadata=metadata,
                is_policy=True,
            ))
        if not indexed.success:
            return KBUploadResult(file_name=filename, success=False, message=indexed.message)
        return KBUploadResult(file_name=filename, success=True, file_id=indexed.file_id)
    except grpc.aio.AioRpcError as e:
        logger.exception("Compute service RPC failed for %s", filename)
        return KBUploadResult(file_name=filename, success=False,
                              message=f"Compute service unavailable: {e.details()}")


async def list_documents(settings: Settings) -> List[KBDocument]:
    """One row per indexed document, from the compute service."""
    try:
        async with grpc.aio.insecure_channel(settings.RPC_SERVER) as channel:
            stub = indexing_service_pb2_grpc.VectorServiceStub(channel)
            response = await stub.ListDocuments(
                indexing_service_pb2.ListDocumentsRequest(
                    collection_name=settings.MONGODB_COLLECTION_NAME,
                )
            )
    except grpc.aio.AioRpcError as e:
        logger.exception("ListDocuments RPC failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Compute service unavailable: {e.details()}",
        ) from e

    if not response.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=response.message
        )
    return [
        KBDocument(
            file_id=d.file_id,
            file_name=d.file_name or None,
            chunk_count=d.chunk_count,
        )
        for d in response.documents
    ]


async def delete_document(settings: Settings, file_id: str) -> str:
    """Removes one document's chunks from the vector store. Returns the message."""
    try:
        async with grpc.aio.insecure_channel(settings.RPC_SERVER) as channel:
            stub = indexing_service_pb2_grpc.VectorServiceStub(channel)
            response = await stub.Delete(indexing_service_pb2.DeleteRequest(
                collection_name=settings.MONGODB_COLLECTION_NAME,
                file_id=file_id,
            ))
        if not response.success:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=response.message)
        return response.message
    except grpc.aio.AioRpcError as e:
        logger.exception("Delete RPC failed for file_id %s", file_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Delete failed: {e.details()}") from e
