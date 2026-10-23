"""Script detection: settled from the letters alone, with no model."""
import pytest
from app.logic.language import answered_in_query_script, script_of, wrong_script

ARABIC_Q = "ما هي ساما؟"
ARABIC_A = "ساما هي البنك المركزي السعودي وتشرف على الأمن السيبراني للبنوك."
ENGLISH_A = "SAMA is the Saudi central bank and it supervises cyber security."
CJK_A = "这是一个关于网络安全的框架的说明，适用于所有银行机构和金融公司。"
LIMITS = {"query_min_letters": 4, "answer_min_letters": 12, "script_share": 0.6}


@pytest.mark.parametrize("text,script", [
    ("What is SAMA?", "latin"),
    (ARABIC_Q, "arabic"),
    ("Что такое SAMA?", "cyrillic"),
    (CJK_A, "cjk"),
    ("नमस्ते आप कैसे हैं", "devanagari"),
])
def test_script_of_names_the_script_the_text_is_written_in(text, script):
    assert script_of(text, 4, 0.6) == script


def test_script_of_declines_short_or_mixed_text():
    assert script_of("ok", 4, 0.6) is None
    assert script_of("", 4, 0.6) is None
    assert script_of(None, 4, 0.6) is None
    # Half Latin, half Arabic: neither holds 60% of the letters.
    assert script_of("abcdefgh " + "ابجدهوزح", 4, 0.6) is None


def test_borrowed_latin_words_do_not_change_the_script():
    """An Arabic answer still writes "SAMA" and "IFRS 9" in Latin letters."""
    assert script_of("SAMA IFRS 9 ساما ماذا تعني هذه الوثيقة بالضبط في السعودية", 4, 0.6) == "arabic"


def test_an_english_answer_to_an_arabic_question_is_the_wrong_script():
    assert wrong_script(ARABIC_Q, ENGLISH_A, **LIMITS)
    assert not wrong_script(ARABIC_Q, ARABIC_A, **LIMITS)


def test_a_latin_answer_to_a_latin_question_is_never_wrong():
    """French, Spanish and English share an alphabet; only the translate step can tell them apart."""
    assert not wrong_script("¿Qué es SAMA?", ENGLISH_A, **LIMITS)


def test_cjk_in_an_answer_is_wrong_unless_the_question_had_cjk():
    assert wrong_script("What is SAMA?", CJK_A, **LIMITS)
    assert not wrong_script("这是什么", CJK_A, **LIMITS)


def test_a_short_answer_decides_nothing():
    assert not wrong_script(ARABIC_Q, "Yes.", **LIMITS)


def test_the_thresholds_are_the_callers_to_set():
    assert not wrong_script(ARABIC_Q, "SAMA regulates banks", **{**LIMITS, "answer_min_letters": 40})
    assert wrong_script(ARABIC_Q, "SAMA regulates banks", **LIMITS)


def test_only_a_non_latin_script_proves_the_answer_is_already_in_the_query_language():
    assert answered_in_query_script(ARABIC_Q, ARABIC_A, **LIMITS)
    assert answered_in_query_script("Что такое SAMA?", "SAMA это рамочная программа для всех банков королевства.", **LIMITS)
    assert not answered_in_query_script("What is SAMA?", ENGLISH_A, **LIMITS), "Latin proves nothing"
    assert not answered_in_query_script(ARABIC_Q, ENGLISH_A, **LIMITS)
