"""The runtime `Search` needs, built once at startup (app/lifespan_manager.py) and
shared by every chat turn - see app/controllers/chat_context.fetch_context, the one
caller.

Trimmed to what Search alone needs: an embedding client and a lazily-connected
vector store, shared rather than reconnected per call. Moving Search in-process
(no RPC hop) is what keeps a chat turn's retrieval step off the network entirely -
parsing-indexing-services-chatbot keeps ingestion (Index, Delete, ParseFile), reached
over REST since none of it sits on the chat path.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request, status

from app.config import Settings
from app.services.embedding.embedding_factory import (
    create_embedding_service,
    verify_embedding_dimension,
)
from app.services.embedding.embedding_service import EmbeddingService
from app.services.vector.vector_store import VectorStore
from app.services.vector.vector_store_factory import create_vector_store

logger = logging.getLogger(__name__)


@dataclass
class SearchRuntime:
    """Built once by create_search_runtime() and put on app.state.search_runtime."""

    settings: Settings
    embedding: EmbeddingService
    _store: VectorStore | None = field(default=None, init=False, repr=False)
    _store_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # When a real search last started. The warm-up only fires after a quiet spell,
    # and does not count as activity itself - see chat_context.fetch_context, which
    # sets this, and keep_search_warm below, which reads it.
    last_search_at: float = field(default_factory=time.monotonic, init=False)

    async def store(self) -> VectorStore:
        """The long-lived vector store connection, opened on first use and shared by
        every concurrent search: the database driver pools connections and is built to
        be long-lived, and sharing it lets simultaneous searches share work (see
        MongoEmbeddingStore._cosine_fallback)."""
        if self._store is None:
            async with self._store_lock:
                if self._store is None:
                    store = create_vector_store(self.settings)
                    await store.__aenter__()
                    self._store = store
        return self._store

    async def search(
        self, query: str, collection_name: str, limit: int, level: int = logging.INFO, version_scope=None,
    ) -> list[dict[str, Any]]:
        """Embeds `query` and runs the hybrid search - the whole Search path, shared
        by a real chat turn and the warm-up."""
        started = time.perf_counter()
        vector = await asyncio.to_thread(self.embedding.generate_embeddings, query)
        embedded = time.perf_counter()
        hits = await (await self.store()).similarity_search_on_query(
            query_vector=vector.tolist(), collection_name=collection_name, limit=limit, query_text=query,
            version_scope=version_scope,
        )
        # Where a slow search went: turning the query into a vector, or querying the store.
        logger.log(
            level, "Search timing: embed_ms=%d store_ms=%d hits=%d",
            (embedded - started) * 1000, (time.perf_counter() - embedded) * 1000, len(hits),
        )
        return hits

    async def close(self) -> None:
        if self._store is not None:
            self._store.close()


def create_search_runtime(settings: Settings) -> SearchRuntime:
    """Builds the embedding client and checks its output dimension now rather than on
    the first question. The vector store itself connects lazily on first search
    (SearchRuntime.store) - same as the old compute-service Services.search_store."""
    embedding = create_embedding_service(settings)
    verify_embedding_dimension(embedding, settings.EMBEDDING_DIMENSION)
    return SearchRuntime(settings=settings, embedding=embedding)


async def keep_search_warm(runtime: SearchRuntime, collection_name: str, interval: float) -> None:
    """Runs the whole Search path on a dummy query whenever none has run for
    `interval` seconds, so the first real question after a quiet spell does not pay
    for a cold embedding call and a cold database connection - the "chat must stay
    fast" case this whole move exists for."""
    while True:
        if time.monotonic() - runtime.last_search_at >= interval:
            try:
                await runtime.search("warm-up", collection_name, limit=1, level=logging.DEBUG)
            except Exception as e:
                logger.warning("Search warm-up failed (%s); will try again.", e)
        await asyncio.sleep(interval)


async def get_search_runtime(request: Request) -> SearchRuntime:
    """FastAPI dependency: the search runtime built from what the lifespan put on app.state."""
    runtime = getattr(request.app.state, "search_runtime", None)
    if runtime is None:
        logger.error("Search runtime not initialized on app.state.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search is not available (not initialized)",
        )
    return runtime
