"""The decisions of one chat turn."""
import pytest
from app.logic.chat_turn import (
    history_for_model,
    intent_verdict,
    is_simple_greeting,
    needs_translation,
    query_text_of,
    rerank_shortlist,
    split_chat,
    split_search_results,
    usable_rerank,
    usable_rewrite,
    wants_intent_check,
)

REFUSAL = "Please rephrase your question."
NAMES = "SAMA Cyber Framework; UAE IA Regulation"
SCRIPT = dict(query_min_letters=4, answer_min_letters=12, script_share=0.6)


def test_the_query_is_its_content_or_the_text_itself():
    assert query_text_of({"role": "user", "content": "hello"}) == "hello"
    assert query_text_of({"role": "user"}) == ""
    assert query_text_of("plain") == "plain"


def test_the_latest_message_is_split_from_the_conversation():
    history, query = split_chat([{"content": "a"}, {"content": "b"}, {"content": "c"}])
    assert history == [{"content": "a"}, {"content": "b"}] and query == {"content": "c"}
    assert split_chat([{"content": "only"}]) == ([], {"content": "only"})


def test_earlier_answers_lose_their_citation_links_but_questions_are_untouched():
    history = [
        {"role": "user", "content": "What is X [1]?"},
        {"role": "assistant", "content": "It is so [Policy, Pg: 2]."},
    ]
    assert history_for_model(history) == [
        {"role": "user", "content": "What is X [1]?"},
        {"role": "assistant", "content": "It is so."},
    ]


@pytest.mark.parametrize("enabled,text,limit,expected", [
    (True, "hi there", 12, True),
    (True, "one two three", 3, True),
    (True, "one two three four", 3, False),
    (False, "hi", 12, False),
])
def test_only_a_short_message_is_offered_to_the_intent_step(enabled, text, limit, expected):
    assert wants_intent_check(enabled, text, limit) is expected


@pytest.mark.parametrize("text", [
    "hi", "Hi", "HELLO", "hey", "hiya", "yo", "howdy", "greetings",
    "hi there", "Hello there", "good morning", "Good Evening",
    " hi ", "hi!", "hello.", "hey?", "hi ~",
])
def test_a_bare_greeting_is_recognised_regardless_of_case_punctuation_or_padding(text):
    assert is_simple_greeting(text)


@pytest.mark.parametrize("text", [
    "hi, what is SAMA", "hi there, quick question", "hello! how are you today",
    "hi hi hi", "thanks", "bye", "what's up", "", "   ",
])
def test_anything_more_than_a_bare_greeting_is_not(text):
    assert not is_simple_greeting(text)


def test_a_rewrite_is_usable_with_either_question_shape():
    assert usable_rewrite({"questions": [{"query": "a"}]})
    assert usable_rewrite({"new_questions": ["a"]})
    assert not usable_rewrite({"language": "English"})
    assert not usable_rewrite({"questions": []})


def test_a_rerank_is_usable_only_with_a_non_empty_list_of_ids():
    assert usable_rerank({"ranked_ids": ["a"]})
    assert not usable_rerank({"ranked_ids": []})
    assert not usable_rerank({"ranked_ids": "a"})
    assert not usable_rerank({})


@pytest.mark.parametrize("data", [
    None, {}, {"intent": "knowledge", "reply": ""}, {"intent": "weird", "reply": "x"},
    {"intent": "chitchat", "reply": ""}, {"intent": "chitchat", "reply": None}, {"intent": "kb_overview"},
])
def test_every_doubt_about_the_intent_means_no_diversion(data):
    assert intent_verdict(data, True, NAMES, 3, REFUSAL) is None


def test_a_small_talk_reply_is_diverted_with_its_intent_normalised():
    assert intent_verdict({"intent": " Chitchat ", "reply": " Hello! "}, True, NAMES, 3, REFUSAL) == ("chitchat", "Hello!")


def test_with_the_overview_off_a_reply_naming_a_document_is_replaced_but_only_then():
    leaked = {"intent": "kb_overview", "reply": "I hold the SAMA Cyber Framework."}
    assert intent_verdict(leaked, False, NAMES, 3, REFUSAL) == ("kb_overview", REFUSAL)
    assert intent_verdict(leaked, True, NAMES, 3, REFUSAL) == ("kb_overview", "I hold the SAMA Cyber Framework.")
    assert intent_verdict({"intent": "chitchat", "reply": "Hi!"}, False, NAMES, 3, REFUSAL) == ("chitchat", "Hi!")


def test_the_shortlist_keeps_the_best_scores_in_order():
    hits = [{"id": "a", "score": 0.1}, {"id": "b", "score": 0.9}, {"id": "c"}, {"id": "d", "score": 0.5}]
    assert [h["id"] for h in rerank_shortlist(hits, 3)] == ["b", "d", "a"]
    assert rerank_shortlist([], 3) == []


def test_search_results_are_split_into_hits_failures_and_empties():
    boom = RuntimeError("down")
    hit_lists, failures, empty = split_search_results([[{"id": 1}], boom, None, [], [{"id": 2}, {"id": 3}]])
    assert [[h["id"] for h in hits] for hits in hit_lists] == [[1], [], [2, 3]]
    assert failures == [boom]
    assert empty == 1


ARABIC = "ساما هي البنك المركزي في المملكة العربية السعودية"
URDU = "ساما سعودی عرب کا مرکزی بینک ہے اور بینکوں کو ریگولیٹ کرتا ہے"
PERSIAN = "ساما بانک مرکزی عربستان سعودی است و بانک‌ها را نظارت می‌کند"
HINDI = "सामा सऊदी अरब का केंद्रीय बैंक है और बैंकों को नियंत्रित करता है"


@pytest.mark.parametrize("language,query,answer,expected", [
    ("english", "what is x", "X is a thing", False),
    ("arabic", "ما هي ساما؟", ARABIC, False),          # already in the query's script
    ("arabic", "ما هي ساما؟", "SAMA is the central bank", True),
    ("french", "qu'est-ce que X", "X is a thing in the policy", True),   # Latin proves nothing
    (None, "what is x", "X is a thing in the policy", True),           # unknown: run the check anyway
    ("hindi", "सामा क्या है?", HINDI, False),                          # Devanagari is Hindi's alone here
    ("urdu", "ساما کیا ہے؟", ARABIC, True),                            # Arabic letters cannot show it is Urdu
    ("persian", "ساما چیست؟", ARABIC, True),
    ("urdu", "ساما کیا ہے؟", URDU, False),                             # its own letters prove it
    ("persian", "ساما چیست؟", PERSIAN, False),
    ("marathi", "सामा म्हणजे काय?", HINDI, True),                       # Devanagari is shared with Hindi
])
def test_translation_is_skipped_only_when_it_has_nothing_to_do(language, query, answer, expected):
    assert needs_translation(language, query, answer, **SCRIPT) is expected
