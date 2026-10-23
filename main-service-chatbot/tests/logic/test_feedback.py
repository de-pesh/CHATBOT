"""The stored-procedure parameters of a feedback submission (scripts/mssql/06_chatbot_feedback.sql)."""
from app.logic.feedback import (
    existing_feedback_id,
    feedback_lookup_params,
    feedback_params,
)


def test_feedback_lookup_params_matches_filter_chatbot_feedback_positionally():
    assert feedback_lookup_params(9) == [9]


def test_existing_feedback_id_is_none_when_there_is_no_prior_row():
    assert existing_feedback_id([]) is None
    assert existing_feedback_id([[]]) is None


def test_existing_feedback_id_reads_the_first_matching_row():
    assert existing_feedback_id([[{"id": 41}]]) == 41


def test_feedback_params_matches_upsert_chatbot_feedback_positionally():
    assert feedback_params(None, 9, "positive", None, "u@x") == [None, 9, "positive", None, "u@x"]
    assert feedback_params(41, 9, "negative", "wrong page", "u@x") == [41, 9, "negative", "wrong page", "u@x"]
