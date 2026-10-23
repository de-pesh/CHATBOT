"""Indexing entry point: download, chunk, embed, write to the vector store."""
import asyncio
import logging
import time
import uuid

from app.config import Settings
from app.indexing.prepare import prepare_document
from app.logic.chunking import stamp_metadata
from app.services.embedding.embedding_service import EmbeddingService
from app.services.storage.storage_service import FileStorageService
from app.services.vector.vector_store import VectorStore

logger = logging.getLogger(__name__)


async def index_handler(
        file_path: str,
        collection_name: str,
        metadata: dict,
        storage_service: FileStorageService,
        vector_store: VectorStore,
        embedding_service: EmbeddingService,
        settings: Settings,
        is_policy: bool = False,
) -> str:
    """Indexes the extracted-text file at `file_path` and returns the new source
    file id its chunks are tagged with. `is_policy` numbers chunks by page."""
    source_file_id = str(uuid.uuid4())

    text_data = (await storage_service.download_file(file_path)).read().decode("utf-8")

    # prepare_document is synchronous and embeds every chunk of the document - a
    # CPU/GPU-bound call. api_server.py is one asyncio event loop, not a thread
    # per request, so running it inline would stall every other concurrent
    # request to this service for as long as this document takes to embed.
    t0 = time.perf_counter()
    records = await asyncio.to_thread(
        prepare_document,
        input_text=text_data,
        embedding_service=embedding_service,
        is_policy=is_policy,
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
    )
    prepare_ms = round((time.perf_counter() - t0) * 1000)

    if not records:
        logger.warning(f"No data prepared from {file_path}. Nothing to index.")
        return source_file_id

    for record in records:
        record["metadata"] = stamp_metadata(
            record["metadata"], metadata,
            uuid=str(uuid.uuid4()), source_file_id=source_file_id, original_file_path=file_path,
        )

    try:
        t1 = time.perf_counter()
        inserted_ids, inserted_count = await vector_store.insert_data(collection_name=collection_name, data_items=records)
        insert_ms = round((time.perf_counter() - t1) * 1000)
        if inserted_ids is None or inserted_count == 0:
            raise RuntimeError(f"Failed to insert chunks into collection '{collection_name}'.")
        logger.info(
            "Index timing: chunks=%d prepare_ms=%d insert_ms=%d",
            inserted_count, prepare_ms, insert_ms,
        )
        logger.info(f"Inserted {inserted_count} chunks from {file_path} into '{collection_name}'.")

        if not await vector_store.create_vector_index(collection_name=collection_name):
            raise RuntimeError(f"Failed to ensure vector index for collection '{collection_name}'.")
    except Exception:
        # The caller retries with a new source_file_id, so chunks this attempt already
        # wrote (a partial insert_many included) would stay active beside the retry's.
        try:
            await vector_store.deactivate_data_by_source_id(source_file_id, collection_name)
        except Exception as cleanup_error:
            logger.error(f"Could not deactivate the chunks of failed index {source_file_id}: {cleanup_error}")
        raise
    return source_file_id
