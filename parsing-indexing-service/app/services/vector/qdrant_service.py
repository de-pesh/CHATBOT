"""Qdrant implementation of VectorStore.

Uses qdrant-client's async API. Dense vectors are stored under the name
"dense"; sparse BM25 vectors are computed by FastEmbed (bundled with the
qdrant-client[fastembed] extra) and stored under "sparse". Hybrid search
fuses the two ranked lists with Reciprocal Rank Fusion, matching the
approach used by MongoEmbeddingStore.

Tenant isolation uses Qdrant payload filters: every chunk's payload carries
`isactive` and `source_file_id`, so deactivation and per-document deletes
never scan outside the right document.

Collection creation is idempotent: create_vector_index does nothing if the
collection already exists and its vector config matches.
"""
import logging
from typing import Any

from app.config import Settings
from app.services.vector.vector_store import VectorStore

logger = logging.getLogger(__name__)

_DENSE_VECTOR = "dense"
_SPARSE_VECTOR = "sparse"

# BM25 model served by FastEmbed (bundled in qdrant-client[fastembed]).
_BM25_MODEL = "Qdrant/bm25"


class QdrantVectorStore(VectorStore):
    """Qdrant-backed vector store with dense + sparse hybrid search."""

    backend_name = "Qdrant"

    def __init__(self, settings: Settings, default_collection_name: str):
        self.settings = settings
        self.default_collection_name = default_collection_name
        self.dimension = settings.EMBEDDING_DIMENSION
        self._client = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        from qdrant_client import AsyncQdrantClient

        url = self.settings.QDRANT_URL
        api_key = self.settings.QDRANT_API_KEY.get_secret_value() if self.settings.QDRANT_API_KEY else None
        try:
            self._client = AsyncQdrantClient(url=url, api_key=api_key, timeout=self.settings.QDRANT_TIMEOUT)
            # Lightweight health check.
            await self._client.get_collections()
            logger.info("Connected to Qdrant at %s", url)
            return True
        except Exception as exc:
            logger.error("Qdrant connection failed: %s", exc)
            return False

    def close(self) -> None:
        if self._client is not None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._client.close())
                else:
                    loop.run_until_complete(self._client.close())
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    async def create_vector_index(self, collection_name: str | None = None) -> bool:
        from qdrant_client.models import (
            Distance,
            SparseIndexParams,
            SparseVectorParams,
            VectorParams,
            VectorsConfig,
        )

        name = collection_name or self.default_collection_name
        try:
            existing = await self._client.get_collection(name)
            logger.debug("Qdrant collection '%s' already exists.", name)
            return True
        except Exception:
            pass  # collection does not exist yet

        try:
            await self._client.create_collection(
                collection_name=name,
                vectors_config={
                    _DENSE_VECTOR: VectorParams(size=self.dimension, distance=Distance.COSINE),
                },
                sparse_vectors_config={
                    _SPARSE_VECTOR: SparseVectorParams(index=SparseIndexParams(on_disk=False)),
                },
            )
            logger.info("Created Qdrant collection '%s' (dim=%d).", name, self.dimension)
            return True
        except Exception as exc:
            logger.error("Failed to create Qdrant collection '%s': %s", name, exc)
            return False

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert_data(
        self,
        data_items: list[dict[str, Any]],
        collection_name: str | None = None,
    ) -> tuple[list[Any] | None, int]:
        from qdrant_client.models import PointStruct, SparseVector

        name = collection_name or self.default_collection_name
        if not data_items:
            return [], 0

        # Build sparse vectors via FastEmbed BM25.
        texts = [item["textContent"] for item in data_items]
        sparse_vectors = await self._sparse_encode(texts)

        points = []
        for item, sparse in zip(data_items, sparse_vectors):
            payload = {"textContent": item["textContent"], **item.get("metadata", {})}
            points.append(
                PointStruct(
                    id=_uuid_to_str(item.get("metadata", {}).get("uuid") or _new_uuid()),
                    vector={
                        _DENSE_VECTOR: item["vectorContent"],
                        _SPARSE_VECTOR: SparseVector(indices=sparse.indices.tolist(), values=sparse.values.tolist()),
                    },
                    payload=payload,
                )
            )

        await self._client.upsert(collection_name=name, points=points)
        ids = [p.id for p in points]
        return ids, len(ids)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def similarity_search_on_query(
        self,
        query_vector: list[float],
        collection_name: str | None = None,
        limit: int = 10,
        query_text: str | None = None,
    ) -> list[dict[str, Any]]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, NamedVector, NamedSparseVector, SparseVector

        name = collection_name or self.default_collection_name
        active_filter = Filter(
            must=[FieldCondition(key="isactive", match=MatchValue(value=True))]
        )

        depth = max(limit * self.settings.HYBRID_FUSION_DEPTH_MULTIPLIER, self.settings.HYBRID_FUSION_DEPTH_MIN)

        # Dense search.
        dense_results = await self._client.search(
            collection_name=name,
            query_vector=NamedVector(name=_DENSE_VECTOR, vector=query_vector),
            query_filter=active_filter,
            limit=depth,
            with_payload=True,
        )

        if not query_text:
            return [_to_hit(r) for r in dense_results[:limit]]

        # Sparse search.
        sparse_vecs = await self._sparse_encode([query_text])
        sv = sparse_vecs[0]
        sparse_results = await self._client.search(
            collection_name=name,
            query_vector=NamedSparseVector(
                name=_SPARSE_VECTOR,
                vector=SparseVector(indices=sv.indices.tolist(), values=sv.values.tolist()),
            ),
            query_filter=active_filter,
            limit=depth,
            with_payload=True,
        )

        fused = _reciprocal_rank_fusion(
            dense_results, sparse_results,
            lexical_weight=self.settings.HYBRID_LEXICAL_WEIGHT,
            k=self.settings.HYBRID_RRF_K,
            limit=limit,
        )
        return fused

    # ------------------------------------------------------------------
    # Soft-delete and versioning
    # ------------------------------------------------------------------

    async def deactivate_data_by_source_id(
        self,
        source_file_id: str,
        collection_name: str | None = None,
    ) -> int:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, SetPayload

        name = collection_name or self.default_collection_name
        flt = Filter(must=[FieldCondition(key="source_file_id", match=MatchValue(value=source_file_id))])
        await self._client.set_payload(
            collection_name=name,
            payload={"isactive": False},
            points=flt,
        )
        # Qdrant set_payload doesn't return a count; use scroll to estimate.
        return await self._count_by_source(name, source_file_id)

    async def set_latest_by_source_id(
        self,
        source_file_id: str,
        is_latest: bool,
        collection_name: str | None = None,
        version_no: int | None = None,
        version_label: str | None = None,
        document_key: str | None = None,
    ) -> int:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        name = collection_name or self.default_collection_name
        flt = Filter(must=[FieldCondition(key="source_file_id", match=MatchValue(value=source_file_id))])
        payload: dict[str, Any] = {"is_latest": is_latest}
        if version_no is not None:
            payload["version_no"] = version_no
        if version_label is not None:
            payload["version_label"] = version_label
        if document_key is not None:
            payload["document_key"] = document_key
        await self._client.set_payload(collection_name=name, payload=payload, points=flt)
        return await self._count_by_source(name, source_file_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _sparse_encode(self, texts: list[str]):
        """BM25 sparse vectors via FastEmbed (synchronous; runs in thread)."""
        import asyncio
        from fastembed import SparseTextEmbedding

        def _encode():
            model = SparseTextEmbedding(model_name=_BM25_MODEL)
            return list(model.embed(texts))

        return await asyncio.to_thread(_encode)

    async def _count_by_source(self, collection_name: str, source_file_id: str) -> int:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, CountRequest

        flt = Filter(must=[FieldCondition(key="source_file_id", match=MatchValue(value=source_file_id))])
        result = await self._client.count(collection_name=collection_name, count_filter=flt)
        return result.count


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _uuid_to_str(value: Any) -> str:
    return str(value)


def _new_uuid() -> str:
    import uuid
    return str(uuid.uuid4())


def _to_hit(point) -> dict[str, Any]:
    payload = point.payload or {}
    return {
        "id": str(point.id),
        "score": point.score,
        "textContent": payload.get("textContent", ""),
        "metadata": {k: v for k, v in payload.items() if k != "textContent"},
    }


def _reciprocal_rank_fusion(
    dense_hits,
    sparse_hits,
    lexical_weight: float,
    k: int,
    limit: int,
) -> list[dict[str, Any]]:
    """RRF fusion of dense and sparse result lists."""
    scores: dict[str, float] = {}
    hits_by_id: dict[str, Any] = {}

    for rank, hit in enumerate(dense_hits, start=1):
        hid = str(hit.id)
        scores[hid] = scores.get(hid, 0.0) + 1.0 / (k + rank)
        hits_by_id[hid] = hit

    for rank, hit in enumerate(sparse_hits, start=1):
        hid = str(hit.id)
        scores[hid] = scores.get(hid, 0.0) + lexical_weight / (k + rank)
        if hid not in hits_by_id:
            hits_by_id[hid] = hit

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    results = []
    for hid, score in ranked:
        hit = hits_by_id[hid]
        payload = hit.payload or {}
        results.append({
            "id": hid,
            "score": score,
            "textContent": payload.get("textContent", ""),
            "metadata": {k: v for k, v in payload.items() if k != "textContent"},
        })
    return results
