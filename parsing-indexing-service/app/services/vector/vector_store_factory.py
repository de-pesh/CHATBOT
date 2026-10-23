"""Chooses the vector database backend from configuration.

Adding a backend is a class implementing `VectorStore` plus a branch here; see
that module for the contract. Nothing outside this file names a concrete vector
database, so a swap does not reach the RPC handlers, the indexer, or
main-service at all.
"""

from app.config import Settings
from app.logic.config_checks import require_settings
from app.services.vector.vector_store import VectorStore


def create_vector_store(settings: Settings, collection_name: str = "") -> VectorStore:
    """Builds the configured vector store. Use it as an async context manager."""
    store_type = settings.VECTOR_STORE

    if store_type == "mongo":
        from app.services.vector.mongo_service import MongoEmbeddingStore

        require_settings(settings, "VECTOR_STORE", "MONGO_CONNECTION_STRING", "MONGO_DATABASE_NAME")
        return MongoEmbeddingStore(settings, default_collection_name=collection_name or settings.VECTOR_COLLECTION_NAME)

    if store_type == "qdrant":
        from app.services.vector.qdrant_service import QdrantVectorStore

        require_settings(settings, "VECTOR_STORE", "QDRANT_URL")
        return QdrantVectorStore(settings, default_collection_name=collection_name or settings.VECTOR_COLLECTION_NAME)

    raise ValueError(
        f"Unsupported VECTOR_STORE: {settings.VECTOR_STORE!r}. Expected: mongo, qdrant."
    )
