"""Shaping the passage list around the rerank call: dedupe before it, reorder by its reply."""


def dedupe_candidates(all_results: list) -> list:
    """Collapses hits that came back from more than one sub-query search.

    Splitting a multi-intent query and expanding each part into an alternate
    phrasing (fetch_new_query_prompt) means the same chunk can easily surface
    from several of the fanned-out searches - keep the highest-scoring
    occurrence rather than sending duplicates into the rerank prompt.
    """
    best_by_id: dict = {}
    for item in all_results:
        key = item.get("id") or (item.get("metadata") or {}).get("textContent")
        if not key:
            continue
        if key not in best_by_id or item.get("score", 0.0) > best_by_id[key].get("score", 0.0):
            best_by_id[key] = item
    return list(best_by_id.values())


def _hit_key(hit: dict):
    return hit.get("id") or (hit.get("metadata") or {}).get("textContent")


def fuse_query_hits(hit_lists: list, k: int) -> list:
    """One candidate list from the ranked results of several searches, by Reciprocal Rank Fusion.

    A question is split into its parts and each is searched under its own wording
    (fetch_new_query_prompt), so a chunk that several of those searches rank well is
    the one the whole question points at. Keeping each chunk's single best score - what
    dedupe_candidates does - throws that agreement away, and the scores of separate
    searches are not on one scale anyway. Each chunk scores sum(1 / (k + rank)) over the
    searches that returned it, the same rank-only rule the store uses to merge its vector
    and keyword lists (Settings.QUERY_FUSION_RRF_K), and the result is best-first with
    that fused score on each hit.

    One search - the common case - has nothing to agree on, so its hits come back as
    they are, with the store's own scores.
    """
    lists = [hits for hits in hit_lists if hits]
    if len(lists) < 2:
        return list(lists[0]) if lists else []
    scores: dict = {}
    hit_by_key: dict = {}
    for hits in lists:
        seen = set()
        for rank, hit in enumerate(sorted(hits, key=lambda h: h.get("score", 0.0), reverse=True)):
            key = _hit_key(hit)
            if not key or key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            hit_by_key.setdefault(key, hit)
    return [{**hit_by_key[key], "score": scores[key]} for key in sorted(scores, key=scores.get, reverse=True)]


def rerank_short_id(chunk_id, id_chars: int) -> str:
    """What the rerank reply calls a chunk: the start of its id."""
    return str(chunk_id or "")[:id_chars]


def reorder_by_ids(candidates: list, ranked_ids: list, id_chars: int, top_k: int) -> list:
    """The candidates in the order the model ranked them, at most `top_k`; [] if none matched.

    The reply names each chunk by the start of its id (see rerank_short_id); a
    model that writes the whole id anyway still matches. Ids it invented are
    skipped, and one it repeats must not occupy two of the top_k slots.
    """
    by_short_id: dict = {}
    for c in candidates:
        if c.get("id"):
            by_short_id.setdefault(rerank_short_id(c["id"], id_chars), c)
    seen = set()
    reranked = []
    for raw in ranked_ids:
        short = rerank_short_id(str(raw).strip(), id_chars)
        if short in by_short_id and short not in seen:
            seen.add(short)
            reranked.append(by_short_id[short])
    return reranked[:top_k]


def semantic_rerank(query: str, candidates: list, top_k: int = 5) -> list:
    """Reranks candidate search results using semantic similarity scoring between query and textContent."""
    if not candidates or not query:
        return candidates[:top_k]

    query_words = set(query.lower().split())

    def _score(candidate: dict) -> float:
        text = str((candidate.get("metadata") or {}).get("textContent") or candidate.get("text") or "").lower()
        if not text or not query_words:
            return float(candidate.get("score") or 0.0)
        
        # Word match score + term frequency
        text_words = text.split()
        total_words = len(text_words) or 1
        matches = sum(1 for w in text_words if w in query_words)
        
        lexical_density = matches / total_words
        base_score = float(candidate.get("score") or 0.0)
        return round(0.5 * base_score + 0.5 * lexical_density, 4)

    ranked = sorted(candidates, key=_score, reverse=True)
    return ranked[:top_k]
