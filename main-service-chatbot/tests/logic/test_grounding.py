"""Params and verdict-parsing for the after-the-fact grounding check
(scripts/mssql/07_chatbot_grounding.sql)."""
from app.logic.grounding import (
    cited_passage_texts,
    grounding_update_params,
    grounding_verdict_of,
    usable_grounding,
)


def test_cited_passage_texts_numbers_the_same_way_build_sources_does():
    results = [
        {"metadata": {"textContent": "first passage"}},
        {"metadata": {"textContent": "second passage"}},
    ]
    assert cited_passage_texts(results) == {1: "first passage", 2: "second passage"}


def test_cited_passage_texts_is_empty_for_no_results():
    assert cited_passage_texts([]) == {}


def test_a_missing_text_content_is_an_empty_string_not_a_crash():
    assert cited_passage_texts([{"metadata": {}}]) == {1: ""}
    assert cited_passage_texts([{}]) == {1: ""}


def test_usable_grounding_accepts_only_the_three_verdicts():
    assert usable_grounding({"verdict": "grounded"})
    assert usable_grounding({"verdict": "partial"})
    assert usable_grounding({"verdict": "unsupported"})
    assert not usable_grounding({"verdict": "yes"})
    assert not usable_grounding({})


def test_grounding_verdict_of_reads_the_verdict_and_detail():
    assert grounding_verdict_of({"verdict": "partial", "detail": "citation 2 is unrelated"}) == (
        "partial", "citation 2 is unrelated",
    )


def test_grounding_verdict_of_treats_an_empty_detail_as_none():
    assert grounding_verdict_of({"verdict": "grounded", "detail": ""}) == ("grounded", None)
    assert grounding_verdict_of({"verdict": "grounded"}) == ("grounded", None)


def test_grounding_verdict_of_caps_a_long_detail():
    verdict, detail = grounding_verdict_of({"verdict": "unsupported", "detail": "x" * 600})
    assert verdict == "unsupported"
    assert len(detail) == 500


def test_grounding_update_params_matches_the_procedure_positionally():
    assert grounding_update_params(9, "grounded", None, "grounding-check") == [9, "grounded", None, "grounding-check"]
    assert grounding_update_params(9, "partial", "bad citation", "grounding-check") == [
        9, "partial", "bad citation", "grounding-check",
    ]
