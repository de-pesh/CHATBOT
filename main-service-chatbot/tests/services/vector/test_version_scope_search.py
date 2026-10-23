"""MongoEmbeddingStore.similarity_search_on_query keeps only the hits in the question's version scope."""
import asyncio
from types import SimpleNamespace

from app.logic.versions import LATEST, Mode, VersionScope
from app.services.vector.mongo_service import MongoEmbeddingStore


def _store(hits):
    store = MongoEmbeddingStore.__new__(MongoEmbeddingStore)
    store.settings = SimpleNamespace(VERSION_OVERFETCH_MULTIPLIER=3)
    store.asked = []

    async def hybrid(query_vector, collection_name, limit, query_text):
        store.asked.append(limit)
        return hits[:limit]

    store._hybrid = hybrid
    return store


def _hit(name, **metadata):
    return {"id": name, "score": 1.0, "metadata": metadata}


HITS = [
    _hit("old", version_no=2, is_latest=False), _hit("new", version_no=3, is_latest=True),
    _hit("legacy"), _hit("older", version_no=1, is_latest=False),
]


def _search(store, scope, limit=2):
    return [h["id"] for h in asyncio.run(store.similarity_search_on_query([0.0], "kb", limit, "q", scope))]


def test_no_scope_is_the_plain_hybrid_search():
    store = _store(HITS)
    assert _search(store, None) == ["old", "new"] and store.asked == [2]


def test_the_latest_scope_drops_replaced_versions_and_overfetches_to_fill_the_limit():
    store = _store(HITS)
    assert _search(store, LATEST) == ["new", "legacy"]  # a chunk from before versioning counts as latest
    assert store.asked == [6]                           # limit 2 x VERSION_OVERFETCH_MULTIPLIER 3


def test_previous_and_specific_scopes_pick_the_older_versions():
    assert _search(_store(HITS), VersionScope(Mode.PREVIOUS)) == ["old", "older"]
    assert _search(_store(HITS), VersionScope(Mode.SPECIFIC, "3")) == ["new"]


def test_the_all_scope_keeps_everything_and_does_not_overfetch():
    store = _store(HITS)
    assert _search(store, VersionScope(Mode.ALL), limit=3) == ["old", "new", "legacy"] and store.asked == [3]
