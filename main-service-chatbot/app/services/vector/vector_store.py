"""Backend-neutral vector store interface.

This is the seam that makes the vector database swappable. Search runs
in-process here, on the chat pod, so every chat turn's retrieval reaches it
directly with no RPC hop; the compute service (parsing-indexing-services-chatbot)
holds its own copy for writes (Index, Delete) and reaches it the same way.

The interface is deliberately small - four operations plus a connection - and
described in terms every vector database has, so an implementation is a
translation job rather than a redesign:

    insert_data                 write embedded chunks
    similarity_search_on_query  nearest neighbours for a query vector, optionally
                                fused with a lexical search over query_text
    deactivate_data_by_source_id  soft-delete every chunk of one document
    create_vector_index         ensure the index exists (a no-op where the
                                backend manages indexes itself, which is the
                                case for most hosted services)

A chunk is `{"textContent": str, "vectorContent": list[float], "metadata": dict}`.
`metadata` carries at least `source_file_id`, `file_name` and `isactive`;
deletes are soft, so anything that does not support in-place metadata updates
needs to emulate `deactivate_data_by_source_id` with a delete-by-filter.

Embeddings are produced before this layer - by whatever EMBEDDING_TYPE selects
(see services/embedding/embedding_factory.py), at a fixed EMBEDDING_DIMENSION (config.py;
768 for the default local gte-multilingual-base) - so a backend that insists on
generating its own is not a drop-in.
"""
from abc import ABC, abstractmethod
from typing import Any


class VectorStore(ABC):
    """Abstract Base Class for a vector database backend."""

    # Names the database in the error `async with` raises when connect() fails.
    backend_name = "the vector store"

    @abstractmethod
    async def connect(self) -> bool:
        """Opens the connection. Returns True on success."""

    @abstractmethod
    def close(self) -> None:
        """Closes the connection."""

    @abstractmethod
    async def create_vector_index(self, collection_name: str | None = None) -> bool:
        """Ensures a vector index exists for the collection."""

    @abstractmethod
    async def insert_data(
        self,
        data_items: list[dict[str, Any]],
        collection_name: str | None = None,
    ) -> tuple[list[Any] | None, int]:
        """Writes chunks. Returns (inserted ids, count)."""

    @abstractmethod
    async def similarity_search_on_query(
        self,
        query_vector: list[float],
        collection_name: str | None = None,
        limit: int = 10,
        query_text: str | None = None,
        version_scope: Any = None,
    ) -> list[dict[str, Any]]:
        """Nearest neighbours, as dicts with id, score, textContent and metadata.

        version_scope (app.logic.versions.VersionScope) limits the hits to the latest versions of each document,
        older ones, one named version or all; None = no limit.

        query_text is optional. When given, an implementation that has a lexical
        index (e.g. a text/BM25 index) should fuse lexical hits with the vector
        hits (e.g. via reciprocal rank fusion) instead of ignoring it; an
        implementation with no lexical index may just fall back to vector-only.
        """

    @abstractmethod
    async def deactivate_data_by_source_id(
        self,
        source_file_id: str,
        collection_name: str | None = None,
    ) -> int:
        """Soft-deletes every chunk of one document. Returns the count affected."""

    async def fetch_chunks_for_version(
        self,
        source_file_id: str,
        collection_name: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Active content chunks for one indexed version, in page order.
        Used to gather source text for prebaked comparison generation.
        Default no-op; override in backends that support it."""
        return []

    async def move_chunks_by_source_id(
        self,
        source_file_id: str,
        from_collection: str,
        to_collection: str,
    ) -> int:
        """Moves every chunk for `source_file_id` from `from_collection` to `to_collection`.

        Returns the number of chunks moved. Default implementation: no-op (returns 0).
        Override in backends that support it.
        """
        return 0

    async def __aenter__(self):
        if not await self.connect():
            raise ConnectionError(f"Failed to connect to {self.backend_name}.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
