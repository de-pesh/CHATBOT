from typing import Any

from app.logic.chunking import chunk_records, split_document
from app.services.embedding.embedding_service import EmbeddingService


def prepare_document(
    input_text: str,
    embedding_service: EmbeddingService,
    is_policy: bool,
    chunk_size: int,
    chunk_overlap: int,
) -> list[dict[str, Any]]:
    """Splits `input_text` into chunks and embeds them: a list of
    `{"textContent", "vectorContent", "embeddingModel", "metadata"}`.

    is_policy splits on the page delimiter first and puts `page_number` in each
    chunk's metadata; otherwise the whole text is chunked and the metadata is empty.
    """
    if not input_text:
        raise ValueError("input_text must be a non-empty string.")
    chunks = split_document(input_text, chunk_size, chunk_overlap, by_page=is_policy)
    if not chunks:
        return []
    vectors = embedding_service.generate_embeddings([chunk["text"] for chunk in chunks])
    return chunk_records(chunks, vectors, embedding_service.model_id)
