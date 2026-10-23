"""REST surface of the compute service: build services -> register routes -> serve.

Document ingestion only - Index, Delete, ParseFile. Search used to live here too, but
it was the one call that sat on a chat turn's critical path, so it moved in-process
into main-service-chatbot (see that repo's app/services/vector/search_runtime.py);
everything left here only runs off the user-facing path (kb_worker), so plain
REST/JSON replaces what used to be gRPC - simpler, and nothing here needed gRPC's
streaming or cross-language codegen to begin with.

Nothing here names a concrete backend. The file store and the vector database both
come from factories driven by config, so swapping either is a setting plus an
implementation of the corresponding interface - see services/storage/storage_service.py
and services/vector/vector_store.py.
"""
import logging
from contextlib import asynccontextmanager
from typing import Any

from app.config import Settings, get_settings
from app.indexing.handler import index_handler
from app.logic.index_health import stale_index_verdict
from app.parsing.handler import parsing_handler
from app.parsing.vision.service import VisionService
from app.parsing.vision.vision_llm_factory import create_vision_llm_client
from app.services.embedding.embedding_factory import (
    create_embedding_service,
    verify_embedding_dimension,
)
from app.services.storage.storage_factory import create_storage_service
from app.services.vector.vector_store_factory import create_vector_store
from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request/response shapes - the REST equivalent of the old .proto messages
# ---------------------------------------------------------------------------
class IndexRequest(BaseModel):
    collection_name: str
    file_path: str
    metadata: dict[str, Any] = {}
    is_policy: bool = False


class IndexResponse(BaseModel):
    success: bool
    message: str = ""
    file_id: str = ""


class DeleteRequest(BaseModel):
    collection_name: str
    file_id: str


class DeleteResponse(BaseModel):
    success: bool
    message: str = ""


class SetLatestRequest(BaseModel):
    """`file_id` is the document's source file id (what Index returned). Only the fields that are given are written."""
    collection_name: str
    file_id: str
    is_latest: bool
    version_no: int | None = None
    version_label: str | None = None
    document_key: str | None = None


class SetLatestResponse(BaseModel):
    success: bool
    message: str = ""


class ParseFileRequest(BaseModel):
    file_path: str


class ParseFileResponse(BaseModel):
    success: bool
    message: str = ""
    blob_path: str = ""


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
async def _warn_if_index_is_stale(settings: Settings, embedding) -> None:
    """Says so, loudly, when the stored vectors were not made by the model now
    configured. Indexing still runs but a mismatch means poor search until the
    collection is re-embedded, and nothing else would point at the cause."""
    try:
        async with create_vector_store(settings) as store:
            info = await store.stored_vector_info(settings.VECTOR_COLLECTION_NAME)
    except Exception as e:
        logger.warning("Could not inspect the stored vectors at startup: %s", e)
        return
    verdict = stale_index_verdict(
        info, settings.EMBEDDING_DIMENSION, embedding.model_id, settings.LEGACY_EMBEDDING_MODEL_ID
    )
    if verdict:
        logger.log(*verdict)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    embedding = create_embedding_service(settings)
    # Refuses to start if the model's vectors are not EMBEDDING_DIMENSION wide, and
    # loads a local model now instead of on the first document.
    verify_embedding_dimension(embedding, settings.EMBEDDING_DIMENSION)
    storage = create_storage_service(settings)
    await storage.initialize()
    vision = VisionService(llm=create_vision_llm_client(settings))

    app.state.settings = settings
    app.state.embedding = embedding
    app.state.storage = storage
    app.state.vision = vision

    await _warn_if_index_is_stale(settings, embedding)
    logger.info(
        f"Compute service ready on {settings.HOST}:{settings.PORT} (STORAGE_TYPE={settings.STORAGE_TYPE}, "
        f"VECTOR_STORE={settings.VECTOR_STORE}, EMBEDDING_TYPE={settings.EMBEDDING_TYPE})..."
    )
    yield
    await storage.close()


app = FastAPI(title="parsing-indexing-services-chatbot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/index", response_model=IndexResponse)
async def index(request: IndexRequest) -> IndexResponse:
    """Indexes a document and returns the source file ID for the whole operation.
    `is_policy` makes the chunks carry their page number."""
    settings = app.state.settings
    try:
        async with create_vector_store(settings) as vector_store:
            source_file_id = await index_handler(
                collection_name=request.collection_name,
                file_path=request.file_path,
                metadata=request.metadata,
                storage_service=app.state.storage,
                vector_store=vector_store,
                embedding_service=app.state.embedding,
                settings=settings,
                is_policy=request.is_policy,
            )
        return IndexResponse(success=True, message="Indexing completed successfully.", file_id=source_file_id)
    except Exception as e:
        logger.exception("Index failed for %s", request.file_path)
        return IndexResponse(success=False, message=str(e))


@app.post("/delete", response_model=DeleteResponse)
async def delete(request: DeleteRequest) -> DeleteResponse:
    """A "soft delete": sets 'isactive' to false for all document chunks of one
    source file ID."""
    settings = app.state.settings
    try:
        async with create_vector_store(settings) as vector_store:
            deactivated_count = await vector_store.deactivate_data_by_source_id(
                collection_name=request.collection_name, source_file_id=request.file_id
            )
        return DeleteResponse(success=True, message=f"Successfully deactivated {deactivated_count} document chunks.")
    except Exception as e:
        logger.exception("Delete failed for %s", request.file_id)
        return DeleteResponse(success=False, message=str(e))


@app.post("/set-latest", response_model=SetLatestResponse)
async def set_latest(request: SetLatestRequest) -> SetLatestResponse:
    """Marks every chunk of one document (one version of a knowledge-base document) as the latest version,
    or as a replaced one. Searches answer from the latest versions unless a question asks for older ones."""
    try:
        async with create_vector_store(app.state.settings) as vector_store:
            matched = await vector_store.set_latest_by_source_id(
                source_file_id=request.file_id, is_latest=request.is_latest, collection_name=request.collection_name,
                version_no=request.version_no, version_label=request.version_label, document_key=request.document_key,
            )
        return SetLatestResponse(success=True, message=f"Marked {matched} document chunks is_latest={request.is_latest}.")
    except Exception as e:
        logger.exception("Set-latest failed for %s", request.file_id)
        return SetLatestResponse(success=False, message=str(e))


@app.post("/parse-file", response_model=ParseFileResponse)
async def parse_file(request: ParseFileRequest) -> ParseFileResponse:
    """Extracts text from the stored file at request.file_path.

    The caller uploads the original and passes its store path; the extracted text
    is written back to the store and that path returned, ready to hand straight to
    Index. Failures come back as success=False with the reason rather than an
    empty path, so the caller does not have to guess.
    """
    try:
        blob_path = await parsing_handler(
            request.file_path, app.state.storage, app.state.vision, getattr(app.state.settings, "MAX_BLANK_PAGE_SHARE", 1.0),
        )
        return ParseFileResponse(success=True, blob_path=blob_path)
    except Exception as e:
        # A bad document must not take the server down, and the caller needs the
        # reason: an unsupported extension and a corrupt PDF are different problems.
        logger.error(f"ParseFile failed for {request.file_path}: {e}")
        return ParseFileResponse(success=False, message=f"{type(e).__name__}: {e}")
