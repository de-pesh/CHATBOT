"""Reading and checking what the model sent back."""
import pytest
from app.logic.replies import (
    is_usable_translation,
    markers,
    parse_json_response,
    with_reminder,
)


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 2}\n```', {"a": 2}),
    ('sure! ```json {"b": [1]} ``` done', {"b": [1]}),
    ("not json", None),
    ("```json {bad} ```", None),
    ("", None),
    (None, None),
])
def test_parse_json_response_reads_bare_or_fenced_json(text, expected):
    assert parse_json_response(text) == expected


def test_markers_ignore_spacing_and_order():
    assert markers("A [1, 3]. B [2]") == markers("A [2]. B [1,3]") == ["[1,3]", "[2]"]
    assert markers(None) == []


ORIGINAL = "SAMA regulates the banks of the kingdom [1] and their insurers [2, 3]."


def _reply(text):
    return {"final_answer": text}


def test_a_faithful_translation_is_usable():
    translated = "SAMA reglemente les banques du royaume [1] et leurs assureurs [2,3]."
    assert is_usable_translation(_reply(translated), ORIGINAL, must_change=True, min_ratio=0.5)


@pytest.mark.parametrize("data", [
    {},
    {"final_answer": None},
    {"final_answer": 3},
    {"final_answer": "   "},
])
def test_a_reply_without_an_answer_is_not_usable(data):
    assert not is_usable_translation(data, ORIGINAL, must_change=False, min_ratio=0.5)


def test_a_reply_far_shorter_than_the_answer_is_the_schema_placeholder_not_a_translation():
    assert not is_usable_translation(_reply("The full answer text"), ORIGINAL, must_change=False, min_ratio=0.9)
    assert is_usable_translation(_reply("The full answer text [1] [2,3]"), ORIGINAL, must_change=False, min_ratio=0.2)


def test_a_translation_that_dropped_or_renumbered_a_marker_is_not_usable():
    assert not is_usable_translation(
        _reply("SAMA reglemente les banques du royaume et leurs assureurs [2,3]."), ORIGINAL, False, 0.5)
    assert not is_usable_translation(
        _reply("SAMA reglemente les banques du royaume [1] et leurs assureurs [2]."), ORIGINAL, False, 0.5)


def test_handing_back_the_untranslated_answer_only_counts_when_a_change_was_expected():
    assert not is_usable_translation(_reply(ORIGINAL), ORIGINAL, must_change=True, min_ratio=0.5)
    assert is_usable_translation(_reply(ORIGINAL), ORIGINAL, must_change=False, min_ratio=0.5)


def test_with_reminder_appends_to_the_last_message_only_and_leaves_the_input_alone():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    out = with_reminder(messages, "\n\nIMPORTANT")
    assert out == [{"role": "system", "content": "s"}, {"role": "user", "content": "u\n\nIMPORTANT"}]
    assert messages[-1]["content"] == "u"
