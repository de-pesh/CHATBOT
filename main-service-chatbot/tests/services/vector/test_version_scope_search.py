"""MongoEmbeddingStore.similarity_search_on_query keeps only the hits in the question's version scope."""
import asyncio
from types import SimpleNamespace

import pytest

from app.logic.mongo_queries import IS_COMPARISON_FIELD, IS_LATEST_FIELD
from app.logic.versions import LATEST, Mode, VersionScope
from app.services.vector.mongo_service import MongoEmbeddingStore, _mql_matches_hit


def _store(hits, version_filter_in_index=False):
    store = MongoEmbeddingStore.__new__(MongoEmbeddingStore)
    store.settings = SimpleNamespace(VERSION_OVERFETCH_MULTIPLIER=3, HYBRID_RRF_K=10)
    store.asked = []
    # Temporarily patch the class-level flag for this test.
    MongoEmbeddingStore._version_filter_in_index = version_filter_in_index
    MongoEmbeddingStore._atlas_vector_search_unavailable = False

    async def hybrid(query_vector, collection_name, limit, query_text, version_filter=None):
        store.asked.append((limit, version_filter))
        # Apply the filter to simulate what Atlas would do.
        filtered = [h for h in hits if version_filter is None or _mql_matches_hit(h, version_filter)]
        return filtered[:limit]

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


# ------------------------------------------------------------------ old index (post-filter path)

def test_no_scope_is_the_plain_hybrid_search():
    store = _store(HITS)
    assert _search(store, None) == ["old", "new"] and store.asked == [(2, None)]


def test_the_latest_scope_drops_replaced_versions_and_overfetches_to_fill_the_limit():
    store = _store(HITS)
    assert _search(store, LATEST) == ["new", "legacy"]  # a chunk from before versioning counts as latest
    assert store.asked == [(6, None)]                   # limit 2 x VERSION_OVERFETCH_MULTIPLIER 3


def test_previous_and_specific_scopes_pick_the_older_versions():
    assert _search(_store(HITS), VersionScope(Mode.PREVIOUS)) == ["old", "older"]
    assert _search(_store(HITS), VersionScope(Mode.SPECIFIC, "3")) == ["new"]


def test_the_all_scope_keeps_everything_and_does_not_overfetch():
    store = _store(HITS)
    assert _search(store, VersionScope(Mode.ALL), limit=3) == ["old", "new", "legacy"] and store.asked == [(3, None)]


# ------------------------------------------------------------------ new index (in-query filter path)

def test_latest_scope_pushes_filter_into_search_on_new_index():
    store = _store(HITS, version_filter_in_index=True)
    result = _search(store, LATEST, limit=2)
    assert result == ["new", "legacy"]
    limit_asked, vf = store.asked[0]
    assert limit_asked == 2 and vf == {IS_LATEST_FIELD: {"$ne": False}}


def test_specific_scope_pushes_version_no_filter_on_new_index():
    store = _store(HITS, version_filter_in_index=True)
    result = _search(store, VersionScope(Mode.SPECIFIC, "2"), limit=2)
    assert result == ["old"]
    _, vf = store.asked[0]
    assert vf == {"metadata.version_no": 2}


def test_all_scope_runs_three_passes_on_new_index():
    store = _store(HITS, version_filter_in_index=True)
    result = _search(store, VersionScope(Mode.ALL), limit=2)
    # Three hybrid calls: latest content, older content, comparison chunks.
    assert len(store.asked) == 3
    filters = [vf for _, vf in store.asked]
    # One pass for latest (is_latest != False, not comparison)
    assert any(IS_LATEST_FIELD in f and "$ne" in str(f[IS_LATEST_FIELD]) and IS_COMPARISON_FIELD in f for f in filters)
    # One pass for older (is_latest = False, not comparison)
    assert any(f.get(IS_LATEST_FIELD) is False and IS_COMPARISON_FIELD in f for f in filters)
    # One pass for comparison chunks only
    assert any(f.get(IS_COMPARISON_FIELD) is True for f in filters)
    # Result contains both latest and older content (comparison chunks empty in test hits)
    ids = set(result)
    assert any(h["id"] in ids for h in HITS if h["metadata"].get("is_latest") is True or "is_latest" not in h["metadata"])
    assert any(h["id"] in ids for h in HITS if h["metadata"].get("is_latest") is False)


# ------------------------------------------------------------------ _mql_matches_hit helper

def test_mql_ne_filter_excludes_matching_value():
    hit = _hit("x", is_latest=False)
    assert _mql_matches_hit(hit, {IS_LATEST_FIELD: {"$ne": False}}) is False


def test_mql_ne_filter_passes_non_matching_value():
    hit = _hit("x", is_latest=True)
    assert _mql_matches_hit(hit, {IS_LATEST_FIELD: {"$ne": False}}) is True


def test_mql_ne_filter_treats_missing_field_as_not_equal():
    hit = _hit("x")  # no is_latest
    assert _mql_matches_hit(hit, {IS_LATEST_FIELD: {"$ne": False}}) is True


def test_mql_eq_filter():
    hit = _hit("x", version_no=4)
    assert _mql_matches_hit(hit, {"metadata.version_no": 4}) is True
    assert _mql_matches_hit(hit, {"metadata.version_no": 5}) is False
