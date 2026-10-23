"""The stored-procedure parameters of a chat, and how a paged reply is read."""
import pytest
from app.logic.chat_records import (
    rag_collections_label,
    delete_session_params,
    history_error_params,
    history_list_params,
    history_success_params,
    inserted_id,
    new_history_params,
    new_session_params,
    owned_history_params,
    owned_session_params,
    page_of,
    rename_session_params,
    session_list_params,
    session_name,
)
from app.models.chat import ChatHistoryFilter, ChatSessionFilter


def test_a_short_prompt_names_its_session_as_it_is():
    assert session_name("What is X?") == "What is X?"
    assert session_name("x" * 255) == "x" * 255


def test_a_long_prompt_is_cut_to_the_column_width_ending_in_an_ellipsis():
    name = session_name("y" * 400)
    assert len(name) == 255 and name == "y" * 252 + "..."


def test_session_procedures_get_their_parameters_in_declared_order():
    assert owned_session_params(7, "u@x") == [7, None, "u@x", True, 1, 1]
    assert new_session_params("hello", "u@x") == [None, "hello", True, "u@x"]
    assert rename_session_params(7, "new", "u@x") == [7, "new", None, "u@x"]
    assert delete_session_params(7, "u@x") == [7, "u@x"]


def test_owned_history_params_matches_filter_chatbot_history_positionally():
    assert owned_history_params(9, "u@x") == [9, None, None, None, "u@x", None, 1, 1]


def test_history_rows_are_inserted_then_completed_or_failed():
    # id, conversation_id, prompt, response, error, is_active, actor, rag_collections
    assert new_history_params(5, "q", "u") == [None, 5, "q", None, None, True, "u", None]
    assert history_success_params(9, 5, "a", "u", "c1") == [9, 5, None, "a", None, None, "u", "c1"]
    assert history_error_params(9, 5, "boom", "u", None) == [9, 5, None, None, "boom", None, "u", None]


def test_listings_are_scoped_to_the_caller_not_to_a_filter():
    history = ChatHistoryFilter(id=1, conversation_id=2, prompt="p", response="r", is_active=True)
    assert history_list_params(history, "me", 3, 20) == [1, 2, "p", "r", "me", True, 3, 20]
    sessions = ChatSessionFilter(id=4, name="n", is_active=False)
    assert session_list_params(sessions, "me", 1, 10) == [4, "n", "me", False, 1, 10]


def test_an_upsert_reply_yields_the_new_id():
    assert inserted_id([[{"id": 42}]]) == 42


def test_a_page_is_its_rows_and_the_total():
    assert page_of([[{"a": 1}], [{"total_records": 12}]]) == ([{"a": 1}], 12)


def test_a_page_with_no_totals_row_counts_zero():
    assert page_of([[], []]) == ([], 0)
    assert page_of([[], [{}]]) == ([], 0)


@pytest.mark.parametrize("results", [[], [[{"a": 1}]]])
def test_a_reply_missing_the_totals_set_is_refused(results):
    with pytest.raises(ValueError, match="Invalid response from DB."):
        page_of(results)


def test_collections_are_joined_for_the_column_or_none():
    assert rag_collections_label(["a", "b"]) == "a, b"
    assert rag_collections_label([]) is None
