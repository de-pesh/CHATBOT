"""Generates and stores prebaked comparison summaries between consecutive document versions.

When a question asks to compare versions (Mode.ALL), the retrieval path runs three parallel
searches: latest content, older content, and these comparison chunks. The comparison chunks
give the LLM a pre-computed structured diff, which makes comparison answers more accurate
and consistent than pure live-chunk retrieval alone.

Generation is O(n): only consecutive pairs (v1→v2, v2→v3) are built. The admin calls
POST /admin/documents/{document_key}/generate-comparison after uploading a new version.
"""
import asyncio
import logging
from uuid import uuid4

from app.config import Settings
from app.logic.prompts.compare import comparison_prompt
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.llm.chat_llm import ChatLLM
from app.services.vector.search_runtime import SearchRuntime

logger = logging.getLogger(__name__)

# synthetic source_file_id written onto comparison chunks so they can be found and
# replaced without touching real version chunks.
def _comparison_source_id(document_key: str, from_no: int, to_no: int) -> str:
    return f"cmp:{document_key}:{from_no}:{to_no}"


async def generate_comparison(
    db_service: DatabaseService,
    search_runtime: SearchRuntime,
    chat_llm: ChatLLM,
    settings: Settings,
    document_key: str,
    force: bool = False,
) -> dict:
    """Generates a comparison chunk between the two most recent versions of a document.

    Returns a summary dict with what was done. Raises ValueError when fewer than two
    indexed versions exist. If COMPARISON_ENABLED is False, returns immediately.
    """
    if not settings.COMPARISON_ENABLED:
        return {"skipped": True, "reason": "COMPARISON_ENABLED is False"}

    # 1. Fetch the two most recent versions that have been fully indexed
    result = await db_service.call_procedure(StoredProcedures.LIST_DOCUMENT_VERSIONS, [document_key])
    all_versions = result[0] if result else []
    # Keep only versions that have been indexed (vector_store_file_id is set)
    indexed = [v for v in all_versions if v.get("vector_store_file_id")]
    if len(indexed) < 2:
        raise ValueError(
            f"Document '{document_key}' has {len(indexed)} indexed version(s); need at least 2 to compare."
        )

    # Sort by version_number ascending; the last two are the consecutive pair to compare.
    indexed.sort(key=lambda v: int(v.get("version_number") or 0))
    older_v = indexed[-2]
    newer_v = indexed[-1]
    from_no = int(older_v["version_number"])
    to_no = int(newer_v["version_number"])
    synthetic_id = _comparison_source_id(document_key, from_no, to_no)

    # 2. Skip if comparison chunks already exist and force is not set
    store = await search_runtime.store()
    collection = settings.MONGODB_COLLECTION_NAME
    from app.services.vector.mongo_service import MongoEmbeddingStore
    if isinstance(store, MongoEmbeddingStore):
        already_exists = await store.comparison_chunks_exist(synthetic_id, collection)
    else:
        already_exists = False

    if already_exists and not force:
        logger.info("Comparison chunks for '%s' v%d→v%d already exist; skipping.", document_key, from_no, to_no)
        return {
            "document_key": document_key,
            "from_version": from_no,
            "to_version": to_no,
            "skipped": True,
            "reason": "already_exists",
        }

    # 3. Fetch content chunks for each version
    max_chars = settings.COMPARISON_MAX_CHARS_PER_VERSION
    older_chunks = await store.fetch_chunks_for_version(older_v["vector_store_file_id"], collection)
    newer_chunks = await store.fetch_chunks_for_version(newer_v["vector_store_file_id"], collection)

    if not older_chunks or not newer_chunks:
        raise ValueError(
            f"No content chunks found for one or both versions of '{document_key}' "
            f"(v{from_no}: {len(older_chunks)} chunks, v{to_no}: {len(newer_chunks)} chunks). "
            f"Ensure both versions are fully indexed before generating a comparison."
        )

    # Join chunk text in page order and truncate to the prompt budget
    older_text = " ".join(c.get("textContent", "") for c in older_chunks)[:max_chars]
    newer_text = " ".join(c.get("textContent", "") for c in newer_chunks)[:max_chars]

    document_name = older_v.get("document_key") or document_key
    from_label = older_v.get("version_label") or str(from_no)
    to_label = newer_v.get("version_label") or str(to_no)

    # 4. Generate the comparison text
    logger.info("Generating comparison for '%s' v%s→v%s ...", document_key, from_label, to_label)
    messages = comparison_prompt(document_name, from_label, to_label, older_text, newer_text)
    comparison_text: str = await chat_llm.invoke_helper(messages, task="compare")

    if not comparison_text or not comparison_text.strip():
        raise ValueError("The LLM returned an empty comparison. No chunks stored.")

    # 5. Embed the comparison text
    vector = await asyncio.to_thread(search_runtime.embedding.generate_embeddings, comparison_text)

    # 6. If force, deactivate the old comparison chunks first
    if force and already_exists and isinstance(store, MongoEmbeddingStore):
        removed = await store.deactivate_comparison_chunks(synthetic_id, collection)
        logger.info("Deactivated %d stale comparison chunk(s) for '%s' v%d→v%d.", removed, document_key, from_no, to_no)

    # 7. Store the comparison chunk
    chunk = {
        "textContent": comparison_text,
        "vectorContent": vector.tolist(),
        "metadata": {
            "uuid": str(uuid4()),
            "source_file_id": synthetic_id,
            "file_name": f"{document_key} (v{from_label} → v{to_label} comparison)",
            "isactive": True,
            "is_latest": False,          # excluded from LATEST-mode searches
            "is_comparison": True,       # retrieved only in comparison (ALL-mode) searches
            "document_key": document_key,
            "compares_from_no": from_no,
            "compares_to_no": to_no,
        },
    }
    _, inserted = await store.insert_data([chunk], collection)

    logger.info(
        "Stored %d comparison chunk(s) for '%s' v%s→v%s (synthetic_id=%s).",
        inserted, document_key, from_label, to_label, synthetic_id,
    )
    return {
        "document_key": document_key,
        "from_version": from_no,
        "to_version": to_no,
        "from_label": from_label,
        "to_label": to_label,
        "comparison_chars": len(comparison_text),
        "chunks_stored": inserted,
        "synthetic_source_id": synthetic_id,
    }
