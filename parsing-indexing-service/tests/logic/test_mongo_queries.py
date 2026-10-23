import re

import pytest
from app.logic.mongo_queries import (
    clean_uri,
    is_atlas_search_unavailable,
    is_index_already_exists,
    lacks_active_filter,
    redact_uri,
    split_valid_items,
    text_search_pipeline,
    vector_index_definition,
    vector_search_pipeline,
)
from pymongo.errors import OperationFailure


class FakeError(Exception):
    def __init__(self, text="", code=None):
        super().__init__(text)
        self.code = code


# ------------------------------------------------------------------ pipelines

def test_ann_pipeline_overfetches_and_only_sees_active_chunks():
    pipeline = vector_search_pipeline(
        [0.1, 0.2], index="idx", path="vec", limit=5, exact=False, overfetch_multiplier=10
    )
    stage = pipeline[0]["$vectorSearch"]
    assert stage["filter"] == {"metadata.isactive": True}
    assert stage["numCandidates"] == 50 and "exact" not in stage
    assert stage["index"] == "idx" and stage["path"] == "vec" and stage["limit"] == 5
    assert stage["queryVector"] == [0.1, 0.2]
    assert pipeline[1]["$project"]["score"] == {"$meta": "vectorSearchScore"}
    assert pipeline[1]["$project"]["_id"] == 0


def test_exact_pipeline_sends_exact_and_no_candidate_count():
    pipeline = vector_search_pipeline([1.0], index="i", path="p", limit=3, exact=True, overfetch_multiplier=10)
    stage = pipeline[0]["$vectorSearch"]
    assert stage["exact"] is True and "numCandidates" not in stage


def test_text_pipeline_matches_active_chunks_and_sorts_by_score():
    pipeline = text_search_pipeline("hello world", 7)
    assert pipeline[0]["$match"] == {"$text": {"$search": "hello world"}, "metadata.isactive": True}
    assert pipeline[1]["$project"]["score"] == {"$meta": "textScore"}
    assert pipeline[2] == {"$sort": {"score": -1}}
    assert pipeline[3] == {"$limit": 7}


def test_vector_index_definition_declares_the_isactive_filter_the_search_stage_uses():
    definition = vector_index_definition("vectorContent", 768)
    assert definition == {
        "fields": [
            {"type": "vector", "path": "vectorContent", "numDimensions": 768, "similarity": "cosine"},
            {"type": "filter", "path": "metadata.isactive"},
        ]
    }
    stage = vector_search_pipeline([1.0], index="i", path="p", limit=1, exact=False, overfetch_multiplier=2)[0]["$vectorSearch"]
    (filtered_field,) = stage["filter"]
    assert {"type": "filter", "path": filtered_field} in definition["fields"]


def vector_index(fields, key="latestDefinition", **extra):
    return {"name": "vector_index", "type": "vectorSearch", key: {"fields": fields}, **extra}


def test_an_index_without_the_filter_field_is_detected():
    vector = {"type": "vector", "path": "vectorContent", "numDimensions": 3, "similarity": "cosine"}
    assert lacks_active_filter(vector_index([vector]))
    assert lacks_active_filter(vector_index([vector, {"type": "filter", "path": "other"}]))
    assert lacks_active_filter(vector_index([vector], key="definition"))


def test_an_index_with_the_filter_field_is_fine():
    fields = [{"type": "vector", "path": "v"}, {"type": "filter", "path": "metadata.isactive"}]
    assert not lacks_active_filter(vector_index(fields))
    assert not lacks_active_filter(vector_index(fields, key="definition"))


def test_an_index_that_does_not_show_its_fields_or_is_not_a_vector_index_is_not_judged():
    assert not lacks_active_filter({"name": "vector_index"})
    assert not lacks_active_filter({"name": "x", "type": "vectorSearch", "latestDefinition": {}})
    assert not lacks_active_filter({"name": "x", "type": "search", "latestDefinition": {"fields": []}})


# --------------------------------------------------------------- predicates

@pytest.mark.parametrize("code", [59, 31082, 6047401, 40324])
def test_every_known_atlas_unavailable_code_is_recognised(code):
    assert is_atlas_search_unavailable(FakeError("boom", code=code))


@pytest.mark.parametrize("text", [
    "no such command: createSearchIndexes",
    "$vectorSearch is only allowed on MongoDB Atlas",
    "SearchNotEnabled: nope",
    "Unrecognized pipeline stage name: '$vectorSearch'",  # MongoDB older than 7.0.2 / 6.0.11
])
def test_atlas_unavailable_text_matches_ignore_case(text):
    assert is_atlas_search_unavailable(FakeError(text))


def test_other_failures_are_not_atlas_unavailable():
    assert not is_atlas_search_unavailable(FakeError("connection reset", code=13))
    assert not is_atlas_search_unavailable(ValueError("x"))


def test_predicates_work_on_real_operation_failures():
    assert is_atlas_search_unavailable(OperationFailure("no such command", 59))
    assert is_index_already_exists(OperationFailure("Index already exists with a different name", 85))


@pytest.mark.parametrize("error", [
    FakeError("x", code=68), FakeError("x", code=85), FakeError("x", code=86),
    FakeError("Index ALREADY EXISTS"), FakeError("Duplicate index name"),
])
def test_already_exists_predicate(error):
    assert is_index_already_exists(error)


def test_already_exists_predicate_rejects_others():
    assert not is_index_already_exists(FakeError("timeout", code=50))


# -------------------------------------------------------- split_valid_items

def test_split_valid_items_separates_bad_type_and_wrong_dimension():
    good = {"vectorContent": [0.0, 0.0, 0.0], "source_id": "s1"}
    wrong_dim = {"vectorContent": [0.0, 0.0], "source_id": "s2"}
    not_a_list = {"vectorContent": "abc"}
    missing = {"textContent": "no vector"}
    valid, skipped = split_valid_items([good, wrong_dim, not_a_list, missing], 3)
    assert valid == [good]
    assert len(skipped) == 3
    assert "index 1" in skipped[0] and "2 dimensions, expected 3" in skipped[0] and "s2" in skipped[0]
    assert "index 2" in skipped[1] and "N/A" in skipped[1]
    assert "index 3" in skipped[2]


def test_split_valid_items_of_nothing():
    assert split_valid_items([], 3) == ([], [])


# ---------------------------------------------------------------------- URIs

@pytest.mark.parametrize("uri, expected", [
    ("mongodb://host:27017/db", "mongodb://host:27017/db"),
    ("mongodb://user:pass@host:27017/db", "mongodb://user:pass@host:27017/db"),
    ("mongodb://user:p@ss@host/db", "mongodb://user:p%40ss@host/db"),
    ("mongodb://user:p@@ss@host/db", "mongodb://user:p%40%40ss@host/db"),
    ("mongodb://u:a@b@c@host/db", "mongodb://u:a%40b%40c@host/db"),
    ("", ""),
])
def test_clean_uri_escapes_all_but_the_last_at_sign(uri, expected):
    assert clean_uri(uri) == expected


def _loop_and_list_version(uri):
    """What clean_uri replaced: find every '@', overwrite all but the last."""
    if uri.count("@") > 1:
        chars = list(uri)
        for match in list(re.finditer(r"@", uri))[:-1]:
            chars[match.start()] = "%40"
        return "".join(chars)
    return uri


@pytest.mark.parametrize("uri", ["", "@", "a@", "@a", "a@b", "a@b@", "@@", "@a@b@c@", "x@y@z"])
def test_clean_uri_matches_the_original_implementation(uri):
    assert clean_uri(uri) == _loop_and_list_version(uri)


def test_redact_uri_hides_credentials_only():
    assert redact_uri("mongodb://user:secret@host:27017/db") == "mongodb://<redacted>@host:27017/db"
    assert redact_uri("mongodb://host:27017") == "mongodb://host:27017"


def test_the_corpus_queries_read_only_active_chunks_and_only_the_fields_needed():
    from app.logic.mongo_queries import dense_corpus_query, text_corpus_query
    query, projection = dense_corpus_query()
    assert query == {"metadata.isactive": True, "vectorContent": {"$exists": True}}
    assert projection == {"textContent": 1, "metadata": 1, "vectorContent": 1, "_id": 0}
    assert text_corpus_query() == ({"metadata.isactive": True}, {"textContent": 1, "metadata": 1, "_id": 0})
