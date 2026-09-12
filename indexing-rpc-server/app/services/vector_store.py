"""Backend-neutral vector store interface.

This is the seam that makes the vector database swappable. It lives here, in
the compute service, because that is the only process that should ever touch
it: main-service reaches the store through the gRPC surface (Index, Search,
Update, Delete, ListDocuments) and holds no vector-database client of its own.
It used to hold a Mongo client and run its own aggregation for `GET /kb`, which
meant "the vector store is pluggable" stopped being true the moment you looked
at the listing endpoint.

The interface is deliberately small - five operations plus a connection - and
described in terms every vector database has, so an implementation is a
translation job rather than a redesign:

    insert_data                 write embedded chunks
    similarity_search_on_query  nearest neighbours for a query vector
    deactivate_data_by_source_id  soft-delete every chunk of one document
    list_documents              group the live chunks back into documents
    create_vector_index         ensure the index exists (a no-op where the
                                backend manages indexes itself, which is the
                                case for most hosted services)

A chunk is `{"textContent": str, "vectorContent": list[float], "metadata": dict}`.
`metadata` carries at least `source_file_id`, `file_name` and `isactive`;
deletes are soft, so anything that does not support in-place metadata updates
needs to emulate `deactivate_data_by_source_id` with a delete-by-filter.

Embeddings are produced before this layer (768-dimension gte-multilingual-base),
so a backend that insists on generating its own is not a drop-in.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class VectorStore(ABC):
    """Abstract Base Class for a vector database backend."""

    @abstractmethod
    async def connect(self) -> bool:
        """Opens the connection. Returns True on success."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Closes the connection."""
        pass

    @abstractmethod
    async def create_vector_index(self, collection_name: Optional[str] = None) -> bool:
        """Ensures a vector index exists for the collection."""
        pass

    @abstractmethod
    async def insert_data(
        self,
        data_items: List[Dict[str, Any]],
        collection_name: Optional[str] = None,
    ) -> Tuple[Optional[List[Any]], int]:
        """Writes chunks. Returns (inserted ids, count)."""
        pass

    @abstractmethod
    async def similarity_search_on_query(
        self,
        query_vector: List[float],
        collection_name: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Nearest neighbours, as dicts with id, score, textContent and metadata."""
        pass

    @abstractmethod
    async def deactivate_data_by_source_id(
        self,
        source_file_id: str,
        collection_name: Optional[str] = None,
    ) -> int:
        """Soft-deletes every chunk of one document. Returns the count affected."""
        pass

    @abstractmethod
    async def list_documents(
        self,
        collection_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """One entry per live document: {file_id, file_name, chunk_count}."""
        pass

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
