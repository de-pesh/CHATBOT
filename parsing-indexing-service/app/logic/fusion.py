"""Ranking maths for hybrid search: hit shaping, brute-force cosine, rank fusion."""
from collections.abc import Sequence
from typing import Any

import numpy as np


def fusion_depth(limit: int, multiplier: int, minimum: int) -> int:
    """How deep each list is fetched before fusing: a chunk that is 15th by meaning
    and 2nd by keywords belongs in the top 10, but only if both lists reach it."""
    return max(limit * multiplier, minimum)


def result_fields(hit: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    """(id, score, metadata) of a hit as the Search RPC returns it: the chunk text
    travels inside the metadata."""
    return hit.get("id", ""), hit.get("score", 0.0), {**hit.get("metadata", {}), "textContent": hit.get("textContent", "")}


def to_hit(doc: dict[str, Any], score: float) -> dict[str, Any]:
    """A stored chunk as a search hit. The metadata is copied: chunks are shared
    with concurrent searches, and the caller adds keys to the metadata it gets."""
    metadata = dict(doc.get("metadata", {}))
    return {"id": metadata.get("uuid"), "textContent": doc.get("textContent"), "metadata": metadata, "score": score}


def usable_docs(docs: Sequence[dict[str, Any]], field: str, dim: int) -> list[dict[str, Any]]:
    """Chunks whose vector is a list of `dim` numbers. Rows of the wrong length (a
    dimension mismatch, which indexing rejects but cannot rule out for older
    data) are dropped rather than failing the search."""
    return [d for d in docs if isinstance(d.get(field), list) and len(d[field]) == dim]


def cosine_top_k(matrix: np.ndarray, norms: np.ndarray, query: Sequence[float], limit: int) -> list[tuple[int, float]]:
    """(row index, cosine similarity) of the `limit` rows closest to `query`, best
    first. One matrix product over every row; a zero vector scores 0."""
    query_vec = np.asarray(query, dtype=np.float32)
    denom = norms * np.linalg.norm(query_vec)
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = np.where(denom > 0, matrix @ query_vec / denom, 0.0)
    return [(int(i), float(scores[i])) for i in np.argsort(scores)[::-1][:limit]]


def reciprocal_rank_fusion(
    vector_hits: list[dict[str, Any]],
    text_hits: list[dict[str, Any]],
    limit: int,
    k: int,
    lexical_weight: float,
) -> list[dict[str, Any]]:
    """Merges a cosine ranking and a lexical ranking by rank rather than by raw
    score - cosine similarity and BM25 live on unrelated scales, so averaging them
    directly would be meaningless. Each document scores sum(weight / (k + rank))
    across the lists it appears in, which rewards documents both methods agree on
    without needing the scores to be comparable.

    The settings (HYBRID_LEXICAL_WEIGHT=0.5, HYBRID_RRF_K=10) were chosen on a
    labelled set of 180 queries: the lexical list counts half as much as the
    vector list, and k is small so the top of each list dominates. Equal weights
    let keyword noise push out good semantic hits on natural-language questions.
    """
    fused_scores: dict[str, float] = {}
    docs_by_id: dict[str, dict[str, Any]] = {}

    for hits, weight in ((vector_hits, 1.0), (text_hits, lexical_weight)):
        for rank, hit in enumerate(hits):
            doc_id = hit.get("id") or hit.get("textContent") or ""
            fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + weight / (k + rank + 1)
            docs_by_id.setdefault(doc_id, hit)

    ranked_ids = sorted(fused_scores, key=lambda doc_id: fused_scores[doc_id], reverse=True)
    return [{**docs_by_id[doc_id], "score": fused_scores[doc_id]} for doc_id in ranked_ids[:limit]]
