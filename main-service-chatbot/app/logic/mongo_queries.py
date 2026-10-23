"""What is sent to MongoDB and how its errors are read - no driver, no connection."""
import re
from collections.abc import Sequence
from typing import Any

# Server error codes meaning "this deployment has no Atlas Search": CommandNotFound
# (59), SearchNotEnabled (31082), 6047401 (the Atlas-only stage on a plain server)
# and 40324 (a server older than 7.0.2 / 6.0.11 does not know $vectorSearch at all).
_ATLAS_UNAVAILABLE_CODES = frozenset({59, 31082, 6047401, 40324})
_ATLAS_UNAVAILABLE_TEXT = (
    "no such command", "only allowed on mongodb atlas", "searchnotenabled", "unrecognized pipeline stage name",
)

# Duplicate-name errors are not pinned to one code across server versions, so the
# common phrasings are matched too.
_ALREADY_EXISTS_CODES = frozenset({68, 85, 86})
_ALREADY_EXISTS_TEXT = ("already exists", "duplicate index")

# Stored chunk schema: the embedding and the text it was made from.
VECTOR_FIELD = "vectorContent"
TEXT_FIELD = "textContent"

# Chunks are soft-deleted, so every read is limited to the active ones.
ACTIVE_FIELD = "metadata.isactive"
ACTIVE = {ACTIVE_FIELD: True}

_PROJECTION = {"id": "$metadata.uuid", TEXT_FIELD: 1, "metadata": 1, "_id": 0}


def is_atlas_search_unavailable(e: Exception) -> bool:
    """True if `e` means "not running on Atlas / Atlas Search disabled"."""
    text = str(e).lower()
    return getattr(e, "code", None) in _ATLAS_UNAVAILABLE_CODES or any(t in text for t in _ATLAS_UNAVAILABLE_TEXT)


def is_index_already_exists(e: Exception) -> bool:
    """True if `e` means "an index with this name already exists"."""
    text = str(e).lower()
    return getattr(e, "code", None) in _ALREADY_EXISTS_CODES or any(t in text for t in _ALREADY_EXISTS_TEXT)


def vector_index_definition(path: str, dimension: int) -> dict[str, Any]:
    """An Atlas "vectorSearch" index. The older "mappings"/"knnVector" shape is for
    the legacy knnBeta operator and is rejected by (or silently not matched by) the
    $vectorSearch stage the store queries with."""
    return {
        "fields": [
            {"type": "vector", "path": path, "numDimensions": dimension, "similarity": "cosine"},
            # $vectorSearch can only filter on fields the index declares as filters;
            # without this the stage is rejected and every search loses its vector half.
            {"type": "filter", "path": ACTIVE_FIELD},
        ]
    }


def lacks_active_filter(index: dict[str, Any]) -> bool:
    """True if `index` (a `list_search_indexes` entry) is a vector index whose
    definition does not declare the isactive filter field - built before that field
    was added, so $vectorSearch cannot filter on it. An entry that does not show its
    fields at all is not judged."""
    if index.get("type", "vectorSearch") != "vectorSearch":
        return False
    fields = (index.get("latestDefinition") or index.get("definition") or {}).get("fields")
    if not isinstance(fields, list):
        return False
    return not any(f.get("type") == "filter" and f.get("path") == ACTIVE_FIELD for f in fields if isinstance(f, dict))


def vector_search_pipeline(
    query_vector: Sequence[float], *, index: str, path: str, limit: int, exact: bool, overfetch_multiplier: int
) -> list[dict[str, Any]]:
    """$vectorSearch over active chunks only.

    ANN over-fetches `limit * overfetch_multiplier` candidates so that enough
    survive the isactive filter to fill the limit. ENN scores every vector behind
    the index and ignores numCandidates, so it is omitted rather than sent as a
    misleading no-op."""
    stage: dict[str, Any] = {
        "index": index,
        "path": path,
        "queryVector": query_vector,
        "limit": limit,
        "filter": ACTIVE,
    }
    if exact:
        stage["exact"] = True
    else:
        stage["numCandidates"] = limit * overfetch_multiplier
    return [{"$vectorSearch": stage}, {"$project": {**_PROJECTION, "score": {"$meta": "vectorSearchScore"}}}]


def text_search_pipeline(query_text: str, limit: int) -> list[dict[str, Any]]:
    """$text over active chunks, best textScore first."""
    return [
        {"$match": {"$text": {"$search": query_text}, **ACTIVE}},
        {"$project": {**_PROJECTION, "score": {"$meta": "textScore"}}},
        {"$sort": {"score": -1}},
        {"$limit": limit},
    ]


def dense_corpus_query() -> tuple[dict[str, Any], dict[str, Any]]:
    """(filter, projection) for every active chunk that has a vector: what the
    in-process cosine fallback loads."""
    return {**ACTIVE, VECTOR_FIELD: {"$exists": True}}, {TEXT_FIELD: 1, "metadata": 1, VECTOR_FIELD: 1, "_id": 0}


def text_corpus_query() -> tuple[dict[str, Any], dict[str, Any]]:
    """(filter, projection) for every active chunk's text: what the BM25 index is built from."""
    return ACTIVE, {TEXT_FIELD: 1, "metadata": 1, "_id": 0}


def split_valid_items(items: Sequence[dict[str, Any]], dim: int) -> tuple[list[dict[str, Any]], list[str]]:
    """(items to insert, one message per skipped item).

    A dimension mismatch does not fail loudly on its own: a vector the index (built
    for `dim`) rejects at query time with a confusing error, or (community MongoDB,
    no index) wrong similarity scores forever. Catching it here names the likely
    cause - EMBEDDING_TYPE/model changed without updating EMBEDDING_DIMENSION."""
    valid, skipped = [], []
    for i, item in enumerate(items):
        vector = item.get(VECTOR_FIELD)
        if not isinstance(vector, list):
            reason = f"missing or invalid '{VECTOR_FIELD}'"
        elif len(vector) != dim:
            reason = f"embedding has {len(vector)} dimensions, expected {dim} (EMBEDDING_DIMENSION)"
        else:
            valid.append(item)
            continue
        skipped.append(f"Skipping item at index {i}: {reason}. Source ID: {item.get('source_id', 'N/A')}")
    return valid, skipped


def clean_uri(uri: str) -> str:
    """Escapes stray '@' in the credentials (all but the last become '%40'): Mongo's
    URI parser takes the *last* '@' as the credentials/host delimiter, so a password
    containing a literal '@' otherwise breaks it.

    Only '@' is handled. A password with other URI-reserved characters ('/', '%',
    '?', '#', ':') still has to be percent-encoded by whoever sets the setting."""
    return re.sub(r"@(?=[^@]*@)", "%40", uri)


def redact_uri(uri: str) -> str:
    """The URI without its credentials, safe for logs."""
    return re.sub(r"//[^@/]+@", "//<redacted>@", uri)
