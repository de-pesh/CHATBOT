"""Which model does what, and reading a streamed chunk."""
from types import SimpleNamespace

from app.logic.llm_models import (
    HELPER_ROLES,
    chunk_text,
    helper_model_names,
    reasoning_efforts,
)


def test_every_role_uses_the_main_model_when_nothing_is_set():
    assert helper_model_names({}, None) == {role: None for role in HELPER_ROLES}


def test_a_roles_own_model_wins_over_the_shared_helper_model():
    names = helper_model_names({"rewrite": "nano", "rerank": None}, "mini")
    assert names["rewrite"] == "nano"
    assert names["rerank"] == "mini"
    assert names["describe"] == "mini"


def test_main_or_blank_means_the_main_model_even_when_a_helper_default_is_set():
    names = helper_model_names({"rewrite": "MAIN", "translate": "   "}, "mini")
    assert names["rewrite"] is None
    assert names["translate"] is None, "whitespace is a setting, and it means the main model"
    assert names["rerank"] == "mini", "only a truly unset role falls through to the default"
    assert helper_model_names({}, " main ")["intent"] is None


def test_the_effort_is_bound_only_for_a_backend_that_takes_it():
    assert reasoning_efforts("minimal", "low", True) == ("minimal", "low")
    assert reasoning_efforts("minimal", "low", False) == (None, None)
    assert reasoning_efforts("", None, True) == (None, None)


def test_a_chunks_text_is_a_string_or_the_text_blocks_of_a_list():
    assert chunk_text(SimpleNamespace(content="hello")) == "hello"
    assert chunk_text(SimpleNamespace(content=["a", {"text": "b"}, {"type": "image"}, {"text": 5}, "c"])) == "abc"
    assert chunk_text(SimpleNamespace(content=None)) == ""
    assert chunk_text(object()) == ""
