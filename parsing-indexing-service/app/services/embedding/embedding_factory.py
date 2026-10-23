"""Chooses the embedding backend from configuration.

Adding a backend is a class implementing `EmbeddingService` plus a branch
here; see that module for the contract. Nothing outside this file and
api_server.py's single call site names a concrete embedding backend.
"""
import logging
from typing import Any

from app.config import Settings
from app.logic.config_checks import require_settings
from app.logic.embeddings import (
    azure_embeddings_url,
    embeddings_url,
    parse_status_codes,
)
from app.services.embedding.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


def create_embedding_service(settings: Settings) -> EmbeddingService:
    """Builds the configured embedding service."""
    embedding_type = settings.EMBEDDING_TYPE

    if embedding_type == "local":
        from app.services.embedding.local_embedding_service import LocalEmbeddingService

        logger.info("Embedding backend: local (model_path=%s)", settings.EMBEDDING_MODEL_PATH or "<default>")
        return LocalEmbeddingService(
            model_path=settings.EMBEDDING_MODEL_PATH,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
            device=settings.EMBEDDING_DEVICE or None,
            trust_remote_code=settings.EMBEDDING_TRUST_REMOTE_CODE,
        )

    if embedding_type in ("litellm", "remote"):
        from app.services.embedding.remote_embedding_service import (
            RemoteEmbeddingService,
        )

        require_settings(settings, "EMBEDDING_TYPE", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL_NAME")
        logger.info("Embedding backend: %s (%s, model=%s)", embedding_type, settings.EMBEDDING_BASE_URL, settings.EMBEDDING_MODEL_NAME)
        return RemoteEmbeddingService(
            endpoint=embeddings_url(settings.EMBEDDING_BASE_URL),
            model_name=settings.EMBEDDING_MODEL_NAME,
            **_http_options(settings),
        )

    if embedding_type == "azure":
        from app.services.embedding.azure_embedding_service import AzureEmbeddingService

        require_settings(settings, "EMBEDDING_TYPE", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL_NAME", "EMBEDDING_API_KEY")
        logger.info(
            "Embedding backend: azure (endpoint=%s, deployment=%s, api_version=%s)",
            settings.EMBEDDING_BASE_URL, settings.EMBEDDING_MODEL_NAME, settings.EMBEDDING_API_VERSION,
        )
        return AzureEmbeddingService(
            endpoint=azure_embeddings_url(
                settings.EMBEDDING_BASE_URL, settings.EMBEDDING_MODEL_NAME, settings.EMBEDDING_API_VERSION
            ),
            model_name=settings.EMBEDDING_MODEL_NAME,
            **_http_options(settings),
        )

    raise ValueError(
        f"Unsupported EMBEDDING_TYPE: {settings.EMBEDDING_TYPE!r}. Expected: local, litellm, azure, remote."
    )


def _http_options(settings: Settings) -> dict[str, Any]:
    """What the two HTTP backends take beyond where they call and what they ask for."""
    return {
        "api_key": settings.EMBEDDING_API_KEY.get_secret_value() if settings.EMBEDDING_API_KEY else None,
        "batch_size": settings.EMBEDDING_BATCH_SIZE,
        "dimensions": settings.EMBEDDING_DIMENSION if settings.EMBEDDING_SEND_DIMENSIONS else None,
        "timeout": settings.EMBEDDING_TIMEOUT,
        "max_retries": settings.EMBEDDING_MAX_RETRIES,
        "backoff_base": settings.EMBEDDING_RETRY_BACKOFF_BASE,
        "retry_statuses": parse_status_codes(settings.EMBEDDING_RETRY_STATUS_CODES),
    }


def verify_embedding_dimension(service: EmbeddingService, expected: int) -> None:
    """Fails fast if the model does not produce EMBEDDING_DIMENSION-sized vectors.

    The vector index is built for a fixed size, so a mismatch does not fail
    loudly on its own: writes are skipped and searches return nothing, and the
    only symptom is a chatbot that says it knows nothing. One probe embedding at
    startup turns that into a clear error naming both numbers.

    A model or endpoint that cannot be reached right now is only a warning -
    it is checked again by every real call - so a brief outage of the embeddings
    service does not stop this one from starting.
    """
    try:
        vector = service.generate_embeddings("dimension check")
    except Exception as e:
        logger.error("Could not run the embedding dimension check (%s); continuing without it.", e)
        return
    actual = int(vector.shape[-1])
    if actual != expected:
        raise ValueError(
            f"Embedding model {service.model_id!r} produces {actual}-dimension vectors, but "
            f"EMBEDDING_DIMENSION={expected}. Set EMBEDDING_DIMENSION={actual} and re-index "
            f"(python -m app.tools.reindex) - vectors of different sizes cannot be searched together."
        )
    logger.info("Embedding model %r checked: %d dimensions.", service.model_id, actual)
