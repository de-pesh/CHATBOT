import pytest
from app.logic.embeddings import (
    azure_embeddings_url,
    backoff_seconds,
    batches,
    embeddings_url,
    normalize_texts,
    parse_status_codes,
    request_body,
    vectors_from_response,
)


def test_a_string_is_wrapped_and_a_list_is_passed_through():
    assert normalize_texts("a") == (["a"], True)
    assert normalize_texts(["a", "b"]) == (["a", "b"], False)


@pytest.mark.parametrize("bad", ["", [], [1], ["a", None], 5, None])
def test_bad_input_is_a_value_error(bad):
    with pytest.raises(ValueError):
        normalize_texts(bad)


def test_batches_are_consecutive_slices_and_a_size_below_one_means_one():
    assert [list(b) for b in batches(["a", "b", "c"], 2)] == [["a", "b"], ["c"]]
    assert [list(b) for b in batches(["a", "b"], 0)] == [["a"], ["b"]]
    assert list(batches([], 5)) == []


def test_urls():
    assert embeddings_url("http://h/v1/") == "http://h/v1/embeddings"
    assert azure_embeddings_url("https://r.openai.azure.com/", "dep", "2024-10-21") == \
        "https://r.openai.azure.com/openai/deployments/dep/embeddings?api-version=2024-10-21"


def test_the_body_carries_dimensions_only_when_set():
    assert request_body("m", ["a"], None) == {"model": "m", "input": ["a"]}
    assert request_body("m", ["a"], 256) == {"model": "m", "input": ["a"], "dimensions": 256}


def test_status_codes_parse_from_a_comma_list():
    assert parse_status_codes("429, 503,") == frozenset({429, 503})
    assert parse_status_codes("") == frozenset()


def test_backoff_doubles_by_default_and_follows_the_base():
    assert [backoff_seconds(n, 2.0) for n in range(3)] == [1, 2, 4]
    assert backoff_seconds(2, 3.0) == 9


def test_rows_are_returned_in_request_order_whatever_order_they_arrive_in():
    payload = {"data": [{"index": 1, "embedding": [2]}, {"index": 0, "embedding": [1]}]}
    assert vectors_from_response(payload) == [[1], [2]]
    assert vectors_from_response({"data": [{"embedding": [7]}]}) == [[7]]


@pytest.mark.parametrize("payload", [None, {}, {"data": []}, {"error": "x"}])
def test_a_response_without_rows_is_an_error(payload):
    with pytest.raises(ValueError, match="no 'data'"):
        vectors_from_response(payload)
