"""
Knowledge-base document management for the chatbot: upload, list, and delete.

This is a self-contained ingestion path: Azure Blob Storage, in-process
parsing, indexing-rpc-server, and MongoDB. It does not depend on main-service's
file/framework tables - documents pushed through here become searchable by the
chatbot immediately.

Parsing used to be a gRPC call to parsing-rpc-server; that service exposed one
method and is now folded into app.parsing.
"""
import logging
import uuid
from functools import lru_cache
from typing import Annotated, List, Optional

import grpc
import motor.motor_asyncio
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from google.protobuf.struct_pb2 import Struct
from pydantic import BaseModel

from app import indexing_service_pb2, indexing_service_pb2_grpc
from app.config import Settings, get_settings
from app.parsing import AzureBlobService, OllamaService, parsing_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["Knowledge Base"])

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


def _blob_connection_string(settings: Settings) -> str:
    return (
        "DefaultEndpointsProtocol=https;"
        f"AccountName={settings.AZURE_STORAGE_ACCOUNT_NAME};"
        f"AccountKey={settings.AZURE_STORAGE_ACCOUNT_KEY};"
        "EndpointSuffix=core.windows.net"
    )


def _mongo_collection(settings: Settings):
    if not settings.MONGO_CONNECTION_STRING or not settings.MONGO_DATABASE_NAME:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MONGO_CONNECTION_STRING / MONGO_DATABASE_NAME are not configured.",
        )
    client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGO_CONNECTION_STRING.get_secret_value())
    return client[settings.MONGO_DATABASE_NAME][settings.MONGODB_COLLECTION_NAME]


# Built once and reused. These take no arguments because Settings is a pydantic
# model and therefore unhashable - lru_cache on a function taking one would
# raise at call time. get_settings() is itself cached, so this is equivalent.
@lru_cache(maxsize=1)
def _parsing_blob_service() -> AzureBlobService:
    """Blob client used by the parsing code (separate from the upload client)."""
    settings = get_settings()
    return AzureBlobService(
        blob_connection_string=_blob_connection_string(settings),
        container_name=settings.AZURE_STORAGE_CONTAINER_NAME,
    )


@lru_cache(maxsize=1)
def _parsing_ollama_client() -> OllamaService:
    """Vision model, used only for image and scanned-page extraction."""
    settings = get_settings()
    return OllamaService(
        model=settings.OLLAMA_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        api_key=settings.OLLAMA_API_KEY.get_secret_value(),
        temperature=settings.OLLAMA_TEMPERATURE,
    )


async def _upload_one(file: UploadFile, settings: Settings) -> KBUploadResult:
    filename = file.filename or "unnamed"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return KBUploadResult(file_name=filename, success=False, message=f"Unsupported extension '{ext}'.")

    contents = await file.read()
    blob_path = f"chatbot-kb/{uuid.uuid4()}_{filename}"

    # One blob abstraction for the whole service: this used to build its own
    # BlobServiceClient alongside the AzureBlobService the parser already uses.
    await _parsing_blob_service().upload_bytes_to_blob(blob_path, contents)

    try:
        parsed_blob_path = await parsing_handler(
            file_path=blob_path,
            azure_blob_service=_parsing_blob_service(),
            ollama_client=_parsing_ollama_client(),
        )
        if not parsed_blob_path:
            return KBUploadResult(file_name=filename, success=False, message="Parsing returned no output.")
    except Exception as e:
        logger.exception("Parsing failed for %s", filename)
        return KBUploadResult(file_name=filename, success=False, message=f"Parsing failed: {e}")

    metadata = Struct()
    metadata.update({"file_name": filename})

    try:
        async with grpc.aio.insecure_channel(settings.RPC_SERVER) as channel:
            stub = indexing_service_pb2_grpc.VectorServiceStub(channel)
            index_response = await stub.Index(indexing_service_pb2.IndexRequest(
                collection_name=settings.MONGODB_COLLECTION_NAME,
                file_path=parsed_blob_path,
                metadata=metadata,
                is_policy=True,
            ))
        if not index_response.success:
            return KBUploadResult(file_name=filename, success=False, message=index_response.message)
        return KBUploadResult(file_name=filename, success=True, file_id=index_response.file_id)
    except grpc.aio.AioRpcError as e:
        logger.exception("Index RPC failed for %s", filename)
        return KBUploadResult(file_name=filename, success=False, message=f"Indexing failed: {e.details()}")


@router.post(
    "",
    response_model=List[KBUploadResult],
    status_code=status.HTTP_201_CREATED,
    summary="Upload one or more documents into the chatbot's knowledge base",
)
async def upload_documents(
    settings: Annotated[Settings, Depends(get_settings)],
    files: List[UploadFile] = File(...),
) -> List[KBUploadResult]:
    return [await _upload_one(file, settings) for file in files]


@router.get(
    "",
    response_model=List[KBDocument],
    summary="List all documents currently in the knowledge base",
)
async def list_documents(
    settings: Annotated[Settings, Depends(get_settings)],
) -> List[KBDocument]:
    collection = _mongo_collection(settings)
    pipeline = [
        {"$match": {"metadata.isactive": True}},
        {"$group": {
            "_id": "$metadata.source_file_id",
            "file_name": {"$first": "$metadata.file_name"},
            "chunk_count": {"$sum": 1},
        }},
    ]
    documents = []
    async for doc in collection.aggregate(pipeline):
        documents.append(KBDocument(file_id=doc["_id"], file_name=doc.get("file_name"), chunk_count=doc["chunk_count"]))
    return documents


@router.delete(
    "/{file_id}",
    summary="Remove a document from the knowledge base",
)
async def delete_document(
    file_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
):
    try:
        async with grpc.aio.insecure_channel(settings.RPC_SERVER) as channel:
            stub = indexing_service_pb2_grpc.VectorServiceStub(channel)
            response = await stub.Delete(indexing_service_pb2.DeleteRequest(
                collection_name=settings.MONGODB_COLLECTION_NAME,
                file_id=file_id,
            ))
        if not response.success:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=response.message)
        return {"message": response.message}
    except grpc.aio.AioRpcError as e:
        logger.exception("Delete RPC failed for file_id %s", file_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Delete failed: {e.details()}") from e
