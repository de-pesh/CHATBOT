import pytest
from app.logic.lexical import Bm25Index, tokenize


def build(texts):
    return Bm25Index.build(texts, k1=1.5, b=0.75)


def test_the_tokenizer_lowercases_drops_stopwords_and_keeps_identifiers_whole():
    tokens = tokenize("The Control T3.2.2 is required")
    assert "the" not in tokens and "is" not in tokens
    assert "t3.2.2" in tokens and "t3" in tokens


def test_the_stemmer_folds_inflections_together():
    assert tokenize("reporting") == tokenize("reported") == tokenize("reports") == ["report"]


def test_arabic_is_normalised_and_its_definite_article_stripped():
    assert tokenize("الكتاب") == tokenize("كتاب")
    assert tokenize("مَدرسة") == tokenize("مدرسه")


@pytest.mark.parametrize("text,words", [
    ("हिन्दी में जानकारी", 3),         # Hindi: vowel signs and virama are combining marks, not \w
    ("தமிழ் மொழி", 2),               # Tamil
    ("বাংলা ভাষা", 2),                # Bengali
    ("മലയാളം ഭാഷ", 2),                # Malayalam
])
def test_indic_words_stay_whole_instead_of_shredding_into_letters(text, words):
    tokens = tokenize(text)
    assert len(tokens) == words
    assert all(len(token) > 1 for token in tokens)


def test_the_danda_separates_hindi_sentences_and_a_joiner_does_not_split_a_word():
    assert tokenize("जानकारी।नियम") == tokenize("जानकारी नियम")
    assert tokenize("می‌خواهم") == tokenize("میخواهم")


def test_an_indic_query_matches_the_document_that_holds_its_words():
    index = build(["बैंकों के लिए साइबर सुरक्षा ढांचा", "the quarterly report covers revenue"])
    assert [doc for doc, _ in index.search("साइबर सुरक्षा", 5)] == [0]


def test_empty_and_none_text_give_no_tokens():
    assert tokenize("") == [] and tokenize(None) == []


def test_bm25_ranks_the_obvious_match_first():
    index = build([
        "the quarterly report covers revenue",
        "password rotation policy for administrators",
        "holiday calendar for the office",
    ])
    hits = index.search("password policy", limit=3)
    assert hits[0][0] == 1
    assert [doc for doc, _ in hits] == [1]  # only the matching document is returned


def test_bm25_stems_the_query_against_the_documents():
    index = build(["we reported the incident", "nothing relevant"])
    assert index.search("reporting", 5)[0][0] == 0


def test_bm25_limit_and_no_match():
    index = build(["alpha beta", "alpha gamma", "alpha delta"])
    assert len(index.search("alpha", limit=2)) == 2
    assert index.search("zeta", limit=2) == []


def test_bm25_over_an_empty_corpus_returns_nothing():
    assert build([]).search("anything", 3) == []


def test_a_rarer_term_outweighs_a_common_one():
    index = build(["common common rare", "common common common", "common other"])
    assert index.search("common rare", 3)[0][0] == 0


def test_the_index_cannot_be_modified_after_construction():
    index = build(["alpha"])
    with pytest.raises(AttributeError):
        index.k1 = 9.0
