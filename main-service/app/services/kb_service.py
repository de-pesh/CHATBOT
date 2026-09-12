"""Knowledge-base ingestion, listing and removal.

The one place that talks to the compute service. Ingestion is three steps:

    upload the original to blob
      -> ParsingService.ParseFile(blob_path)   -> parsed text, back in blob
      -> VectorService.Index(parsed_blob_path) -> embedded and stored in Mongo

The payload never crosses the RPC: both sides reach Azure Blob Storage, so only
paths travel. Parsing used to run in-process in the chat service; it sits behind
ParseFile now so the OCR toolchain stays out of this image.

Listing reads Mongo directly rather than adding an RPC for it - it is a grouped
count over metadata the indexer already writes, with no model involved.
"""
import logging
import uuid
from io import BytesIO
from typing import List, Optional

import grpc
import motor.motor_asyncio
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


def _mongo_collection(settings: Settings):
    if not settings.MONGO_CONNECTION_STRING or not settings.MONGO_DATABASE_NAME:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MONGO_CONNECTION_STRING / MONGO_DATABASE_NAME are not configured.",
        )
    client = motor.motor_asyncio.AsyncIOMotorClient(
        settings.MONGO_CONNECTION_STRING.get_secret_value()
    )
    return client[settings.MONGO_DATABASE_NAME][settings.MONGODB_COLLECTION_NAME]


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
    """Groups the indexed chunks back into one row per source document."""
    collection = _mongo_collection(settings)
    pipeline = [
        {"$match": {"metadata.isactive": True}},
        {"$group": {
            "_id": "$metadata.source_file_id",
            "file_name": {"$first": "$metadata.file_name"},
            "chunk_count": {"$sum": 1},
        }},
    ]
    return [
        KBDocument(file_id=d["_id"], file_name=d.get("file_name"), chunk_count=d["chunk_count"])
        async for d in collection.aggregate(pipeline)
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
