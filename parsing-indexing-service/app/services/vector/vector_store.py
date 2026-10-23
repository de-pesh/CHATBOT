"""Backend-neutral vector store interface.

This is the seam that makes the vector database swappable. Used here for
writes (Index, Delete) and the stale-index check. main-service-chatbot keeps
an identical copy of this file (kept in sync by hand - separate images, no
shared package) and uses it for Search, which runs in-process there rather
than as a call into this service - it was the one call that used to sit on a
chat turn's critical path.

The interface is deliberately small - four operations plus a connection - and
described in terms every vector database has, so an implementation is a
translation job rather than a redesign:

    insert_data                 write embedded chunks
    similarity_search_on_query  nearest neighbours for a query vector, optionally
                                fused with a lexical search over query_text
    deactivate_data_by_source_id  soft-delete every chunk of one document
    set_latest_by_source_id       mark every chunk of one document as the latest version, or not
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
    ) -> list[dict[str, Any]]:
        """Nearest neighbours, as dicts with id, score, textContent and metadata.

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

    async def set_latest_by_source_id(
        self,
        source_file_id: str,
        is_latest: bool,
        collection_name: str | None = None,
        version_no: int | None = None,
        version_label: str | None = None,
        document_key: str | None = None,
    ) -> int:
        """Sets `metadata.is_latest` - and `version_no`, `version_label` and `document_key`, when given - on every
        chunk of one document (a version of a knowledge-base document):
        searches answer from the latest versions unless a question asks for older ones. Returns the count affected.

        Not abstract: a backend with no in-place metadata updates may leave versioning unsupported."""
        raise NotImplementedError(f"{self.backend_name} does not support document versions.")

    async def __aenter__(self):
        if not await self.connect():
            raise ConnectionError(f"Failed to connect to {self.backend_name}.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
