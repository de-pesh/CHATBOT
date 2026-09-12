"""Chooses the vector database backend from configuration.

Adding a backend is a class implementing `VectorStore` plus a branch here; see
that module for the contract. Nothing outside this file names a concrete vector
database, so a swap does not reach the RPC handlers, the indexer, or
main-service at all.

Pinecone, Qdrant, pgvector and the rest are deliberately *not* stubbed in here.
An implementation that has never been run against the real service is worse
than an honest gap: it would ship a client dependency into the image and read
as supported. What is here instead is the seam and the contract, so writing one
is a self-contained job.
"""
import logging

from app.config import Settings
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


def create_vector_store(settings: Settings, collection_name: str = "") -> VectorStore:
    """Builds the configured vector store. Use it as an async context manager."""
    store_type = (settings.VECTOR_STORE or "").lower()

    if store_type == "mongo":
        from app.services.mongo_service import MongoEmbeddingStore

        if not settings.MONGO_CONNECTION_STRING or not settings.MONGO_DATABASE_NAME:
            raise ValueError(
                "VECTOR_STORE='mongo' requires MONGO_CONNECTION_STRING and MONGO_DATABASE_NAME."
            )
        return MongoEmbeddingStore(
            mongo_uri=settings.MONGO_CONNECTION_STRING.get_secret_value(),
            db_name=settings.MONGO_DATABASE_NAME,
            default_collection_name=collection_name or settings.VECTOR_COLLECTION_NAME,
        )

    raise ValueError(
        f"Unsupported VECTOR_STORE: {settings.VECTOR_STORE!r}. Expected: mongo."
    )
