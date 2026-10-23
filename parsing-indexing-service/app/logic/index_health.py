"""Whether the stored vectors were made by the embedding model now configured."""
import logging
from collections.abc import Sequence
from typing import Any


def not_made_by(model_id: str, legacy_model_id: str) -> dict[str, Any]:
    """Mongo filter on `embeddingModel` for chunks `model_id` did not embed. Chunks
    indexed before `embeddingModel` was recorded carry none and count as
    `legacy_model_id` (LEGACY_EMBEDDING_MODEL_ID), so they only match when that is
    not the current model."""
    return {"$exists": True, "$ne": model_id} if model_id == legacy_model_id else {"$ne": model_id}


def reindex_filter(vector_field: str, model_id: str, legacy_model_id: str, force: bool) -> dict[str, Any]:
    """Which chunks a re-embed run visits: every chunk with a vector when forced,
    otherwise only the ones the current model did not make."""
    query: dict[str, Any] = {vector_field: {"$exists": True}}
    if not force:
        query["embeddingModel"] = not_made_by(model_id, legacy_model_id)
    return query


def stored_vector_summary(count: int, sample_vector: Sequence[float] | None, models: Sequence[str | None], unrecorded: int) -> dict[str, Any]:
    """The shape `stale_index_verdict` reads: chunk count, the dimension of a sample
    vector, which models made them, and how many have no recorded model."""
    return {
        "count": count,
        "dimension": len(sample_vector) if sample_vector is not None else None,
        "models": [m for m in models if m],
        "unrecorded": unrecorded,
    }


def stale_index_verdict(
    info: dict[str, Any], expected_dim: int, model_id: str, legacy_model_id: str
) -> tuple[int, str] | None:
    """(log level, message) if the stored vectors will not search well with the
    configured model, else None. `info` is the store's stored_vector_info()."""
    if not info["count"]:
        return None
    if info["dimension"] != expected_dim:
        return logging.ERROR, (
            f"The knowledge base holds {info['dimension']}-dimension vectors but EMBEDDING_DIMENSION={expected_dim}: "
            "search will return nothing. Run `python -m app.tools.reindex` to re-embed it."
        )
    made_by = set(info["models"]) | ({legacy_model_id} if info["unrecorded"] else set())
    if made_by != {model_id}:
        return logging.WARNING, (
            f"The knowledge base has vectors from {sorted(made_by)} but the embedding model is now {model_id!r}. "
            "Results will be poor until it is re-embedded: `python -m app.tools.reindex`."
        )
    return None
