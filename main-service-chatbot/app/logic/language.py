import re
import unicodedata


def normalize_nfc(text: str | None) -> str:
    """Normalizes Unicode text to NFC (Normalization Form C) standard format."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text)

# The scripts a question or an answer can be written in here, by the ranges
# their letters occupy. Script is the one thing about "is this in the language
# it should be in?" that can be settled from the text itself, with no model and
# no extra call - and a question and its answer are always in the same script.
SCRIPTS = {
    "latin": re.compile("[A-Za-zÀ-ɏ]"),
    "arabic": re.compile("[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]"),
    "cyrillic": re.compile("[Ѐ-ӿ]"),
    "cjk": re.compile("[぀-ヿ㐀-䶿一-鿿가-힯]"),
    "devanagari": re.compile("[ऀ-ॿ]"),
    "bengali": re.compile("[ঀ-৿]"),
    "tamil": re.compile("[஀-௿]"),
    "malayalam": re.compile("[ഀ-ൿ]"),
}

# The languages (as prompts._language_of names them) that are the only one here
# written in their script, so the script alone shows an answer is in that
# language. Urdu and Persian are written in Arabic letters, Marathi and Nepali in
# Devanagari, Ukrainian in Cyrillic: for those the letters cannot say whether the
# model answered in the right language, so they are deliberately not listed.
SOLE_LANGUAGE_OF_SCRIPT = frozenset({"arabic", "hindi", "russian", "bengali", "tamil", "malayalam"})


# Urdu and Persian are written in Arabic letters plus a few Arabic does not have
# (Urdu's tteh, ddal, rreh, noon ghunna, yeh barree, heh goal, do-chashmee heh;
# Persian's pe, che, zhe, gaf, keheh, Farsi yeh), so an answer holding several of
# them is in that language and not in Arabic, without asking a model.
OWN_LETTERS = {
    "urdu": re.compile("[ٹڈڑںےہھ]"),
    "persian": re.compile("[پچژگکی]"),
}
MIN_OWN_LETTERS = 2  # one can be a borrowed name; a sentence in the language has many


def script_of(text: str, min_letters: int, script_share: float) -> str | None:
    """The script `text` is written in, or None when it is too short or too mixed.

    A script is only read off text that has enough letters to carry one. Every
    language borrows - an Arabic answer still writes "SAMA" and "IFRS 9" in Latin
    letters - so the script holding at least `script_share` of the letters is the
    one the text is written in; below that it is mixed enough not to say.
    """
    counts = {name: len(pattern.findall(text or "")) for name, pattern in SCRIPTS.items()}
    total = sum(counts.values())
    if total < min_letters:
        return None
    script, count = max(counts.items(), key=lambda item: item[1])
    return script if count >= total * script_share else None


def wrong_script(
    query: str, answer: str, query_min_letters: int, answer_min_letters: int, script_share: float,
) -> bool:
    """True when the answer is plainly not in the language the question was in.

    A query may be short - "ما هي ساما؟" is the whole question - while an answer
    may not be, so a one-word reply cannot decide anything.

    Nothing in the knowledge base or the users' questions is in CJK, so CJK in
    an answer is the model wandering off regardless of how short the question
    was. Otherwise it takes both scripts being readable and different - an
    English answer to an Arabic question, say. A Latin answer to a Latin
    question is never "wrong" here: French, Spanish and English share an
    alphabet, so only the translate step can tell those apart.
    """
    answer_script = script_of(answer, answer_min_letters, script_share)
    if answer_script is None:
        return False
    if answer_script == "cjk" and not SCRIPTS["cjk"].search(query or ""):
        return True
    query_script = script_of(query, query_min_letters, script_share)
    return query_script is not None and query_script != answer_script


def answered_in_query_script(
    query: str, answer: str, query_min_letters: int, answer_min_letters: int, script_share: float,
) -> bool:
    """True when the answer is certainly already in the query's language.

    Only a non-Latin script can prove it. An Arabic question answered in Arabic
    letters, or a Russian one in Cyrillic, was not left in English - there is
    nothing for the translate step to do. Latin proves nothing, because the
    context, the answer and half the languages here are all written in it.
    """
    query_script = script_of(query, query_min_letters, script_share)
    return (
        query_script is not None
        and query_script != "latin"
        and script_of(answer, answer_min_letters, script_share) == query_script
    )
