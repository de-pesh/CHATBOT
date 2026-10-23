import logging
import time

from app.logic.fusion import result_fields
from app.services.vector.search_runtime import SearchRuntime

logger = logging.getLogger(__name__)


async def fetch_context(
        runtime: SearchRuntime,
        collection_name: str,
        search_query: str,
        limit: int,
        version_scope=None,
) -> list[dict]:
    """Runs the hybrid search in-process (see SearchRuntime) and returns each hit as
    a plain dict - {"id", "score", "metadata"} - so callers (deduplication, reranking,
    prompt construction) work with ordinary Python data regardless of what the vector
    store's own hit shape is. "metadata" already carries "textContent".

    Raises rather than returning nothing on failure: a total vector-store outage
    cannot look identical to "this query legitimately matched nothing"; the chat
    turn's asyncio.gather(..., return_exceptions=True) handles it per query.
    """
    runtime.last_search_at = time.monotonic()
    hits = await runtime.search(search_query, collection_name, limit, version_scope=version_scope)
    results = []
    for hit in hits:
        doc_id, score, metadata = result_fields(hit)
        results.append({"id": doc_id, "score": score, "metadata": metadata})
    return results
