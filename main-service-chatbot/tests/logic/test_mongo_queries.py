"""Version filter helpers and pipeline builders in mongo_queries."""
import pytest

from app.logic.mongo_queries import (
    IS_COMPARISON_FIELD,
    IS_LATEST_FIELD,
    VERSION_NO_FIELD,
    lacks_version_filter,
    text_search_pipeline,
    vector_index_definition,
    vector_search_pipeline,
    version_filter_for,
)
from app.logic.versions import Mode, VersionScope


# ------------------------------------------------------------------ index definition

def test_index_definition_includes_version_filter_fields():
    defn = vector_index_definition("vectorContent", 1536)
    paths = {f["path"] for f in defn["fields"] if f["type"] == "filter"}
    assert IS_LATEST_FIELD in paths and VERSION_NO_FIELD in paths and IS_COMPARISON_FIELD in paths


# ------------------------------------------------------------------ lacks_version_filter

def _index_with_filters(*paths):
    return {
        "type": "vectorSearch",
        "latestDefinition": {"fields": [{"type": "filter", "path": p} for p in paths]},
    }


def test_lacks_version_filter_true_when_fields_absent():
    idx = _index_with_filters("metadata.isactive")
    assert lacks_version_filter(idx) is True


def test_lacks_version_filter_false_when_all_fields_declared():
    idx = _index_with_filters("metadata.isactive", IS_LATEST_FIELD, VERSION_NO_FIELD, IS_COMPARISON_FIELD)
    assert lacks_version_filter(idx) is False


def test_lacks_version_filter_true_when_comparison_field_absent():
    idx = _index_with_filters("metadata.isactive", IS_LATEST_FIELD, VERSION_NO_FIELD)
    assert lacks_version_filter(idx) is True


def test_lacks_version_filter_ignores_non_vector_indexes():
    assert lacks_version_filter({"type": "search"}) is False


# ------------------------------------------------------------------ version_filter_for

def test_latest_scope_produces_ne_false_filter():
    vf = version_filter_for(VersionScope(Mode.LATEST))
    assert vf == {IS_LATEST_FIELD: {"$ne": False}}


def test_previous_scope_excludes_comparison_chunks():
    vf = version_filter_for(VersionScope(Mode.PREVIOUS))
    assert vf == {IS_LATEST_FIELD: False, IS_COMPARISON_FIELD: {"$ne": True}}


def test_specific_scope_matches_integer_version_no():
    vf = version_filter_for(VersionScope(Mode.SPECIFIC, "4"))
    assert vf == {VERSION_NO_FIELD: 4}


def test_all_scope_returns_none_because_caller_uses_two_passes():
    assert version_filter_for(VersionScope(Mode.ALL)) is None


def test_none_scope_returns_none():
    assert version_filter_for(None) is None


# ------------------------------------------------------------------ pipeline builders with extra_filter

def test_vector_search_pipeline_merges_extra_filter():
    pipeline = vector_search_pipeline(
        [0.1] * 4, index="idx", path="vec", limit=5, exact=False,
        overfetch_multiplier=4, extra_filter={IS_LATEST_FIELD: {"$ne": False}},
    )
    stage_filter = pipeline[0]["$vectorSearch"]["filter"]
    assert stage_filter["metadata.isactive"] is True
    assert stage_filter[IS_LATEST_FIELD] == {"$ne": False}


def test_vector_search_pipeline_without_extra_filter_keeps_active_only():
    pipeline = vector_search_pipeline(
        [0.1] * 4, index="idx", path="vec", limit=5, exact=False, overfetch_multiplier=4,
    )
    assert pipeline[0]["$vectorSearch"]["filter"] == {"metadata.isactive": True}


def test_text_search_pipeline_merges_extra_filter():
    pipeline = text_search_pipeline("query", 10, extra_filter={IS_LATEST_FIELD: False})
    match = pipeline[0]["$match"]
    assert match["metadata.isactive"] is True
    assert match[IS_LATEST_FIELD] is False


def test_text_search_pipeline_without_extra_filter_is_unchanged():
    pipeline = text_search_pipeline("query", 10)
    assert pipeline[0]["$match"] == {"$text": {"$search": "query"}, "metadata.isactive": True}
