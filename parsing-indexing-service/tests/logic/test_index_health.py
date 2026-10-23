import logging

from app.logic.index_health import (
    not_made_by,
    reindex_filter,
    stale_index_verdict,
    stored_vector_summary,
)

LEGACY_MODEL_ID = "legacy-model"


def info(count=10, dimension=768, models=(), unrecorded=0):
    return {"count": count, "dimension": dimension, "models": list(models), "unrecorded": unrecorded}


def test_an_empty_collection_is_never_stale():
    assert stale_index_verdict(info(count=0, dimension=None), 768, "m", LEGACY_MODEL_ID) is None


def test_matching_dimension_and_model_is_healthy():
    assert stale_index_verdict(info(models=["m"]), 768, "m", LEGACY_MODEL_ID) is None


def test_a_dimension_mismatch_is_an_error_naming_both_sizes():
    level, message = stale_index_verdict(info(dimension=384, models=["m"]), 768, "m", LEGACY_MODEL_ID)
    assert level == logging.ERROR
    assert "384-dimension" in message and "EMBEDDING_DIMENSION=768" in message and "reindex" in message


def test_vectors_from_another_model_are_a_warning():
    level, message = stale_index_verdict(info(models=["old"]), 768, "new", LEGACY_MODEL_ID)
    assert level == logging.WARNING
    assert "['old']" in message and "'new'" in message


def test_a_mix_of_models_is_a_warning_even_if_one_matches():
    assert stale_index_verdict(info(models=["m", "old"]), 768, "m", LEGACY_MODEL_ID)[0] == logging.WARNING


def test_unrecorded_chunks_count_as_the_legacy_model():
    assert stale_index_verdict(info(unrecorded=10), 768, LEGACY_MODEL_ID, LEGACY_MODEL_ID) is None
    level, message = stale_index_verdict(info(unrecorded=10), 768, "other", LEGACY_MODEL_ID)
    assert level == logging.WARNING and LEGACY_MODEL_ID in message


def test_the_reindex_filter_uses_the_same_legacy_rule():
    # Current model is the legacy one: unrecorded chunks are already right, so only
    # chunks that name a different model match.
    assert not_made_by(LEGACY_MODEL_ID, LEGACY_MODEL_ID) == {"$exists": True, "$ne": LEGACY_MODEL_ID}
    # Any other model: unrecorded chunks are legacy, so they match too.
    assert not_made_by("other", LEGACY_MODEL_ID) == {"$ne": "other"}


def test_reindex_visits_everything_when_forced_and_only_stale_chunks_otherwise():
    assert reindex_filter("vec", "m", LEGACY_MODEL_ID, force=True) == {"vec": {"$exists": True}}
    assert reindex_filter("vec", "m", LEGACY_MODEL_ID, force=False) == {"vec": {"$exists": True}, "embeddingModel": {"$ne": "m"}}


def test_the_stored_summary_reads_the_dimension_from_a_sample_and_drops_blank_model_names():
    assert stored_vector_summary(5, [0.1, 0.2, 0.3], ["a", None, ""], 2) == {
        "count": 5, "dimension": 3, "models": ["a"], "unrecorded": 2}
    assert stored_vector_summary(0, None, [], 0)["dimension"] is None
