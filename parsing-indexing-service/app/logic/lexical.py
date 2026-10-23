"""BM25 over the knowledge-base chunks - the lexical half of hybrid search.

MongoDB's $text index is not BM25: it does no stemming for the languages we set
it to ("none"), so "reporting" never matches "reported", and its scoring is a
plain tf-idf variant. This is Okapi BM25 with a tokenizer that keeps control
identifiers whole (T3.2.2, M1.2.3), stems English lightly and normalises Arabic.
"""
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property


def _combining_marks() -> str:
    """Every combining mark Unicode defines (categories Mn, Mc, Me), as regex character ranges."""
    ranges, start = [], None
    for code in range(0x300, 0x1F000):  # past this are only variation selectors and tags
        is_mark = unicodedata.category(chr(code)).startswith("M")
        if is_mark and start is None:
            start = code
        elif not is_mark and start is not None:
            ranges.append(f"{chr(start)}-{chr(code - 1)}" if code - 1 > start else chr(start))
            start = None
    return "".join(ranges)


# A letter of a word. `\w` alone leaves out combining marks - the vowel signs and
# virama of Hindi, Bengali, Tamil, Gujarati, Telugu and the rest - so "हिन्दी" came
# out as five one-letter tokens, all dropped as too short, and text in those scripts
# matched nothing. The marks are read from Python's own Unicode tables instead of a
# hand-kept list of scripts, so none is missed. Punctuation such as the danda "।"
# is not a mark and stays a separator.
_WORD_CHAR = rf"[\w{_combining_marks()}]"
# A word, or an identifier joined by . - _ / (T3.2.2, sub-control, e-mail).
_TOKEN = re.compile(rf"{_WORD_CHAR}+(?:[.\-_/]{_WORD_CHAR}+)*", re.UNICODE)
# Zero-width (non-)joiner: part of Persian and Indic spelling, never a word break.
_JOINERS = re.compile("[‌‍]")
_ARABIC_MARKS = re.compile("[ً-ٰٟـ]")  # tashkeel, dagger alef, tatweel

_STOPWORDS = frozenset(["a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by", "can", "could", "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "may", "me", "more", "most", "must", "my", "no", "not", "of", "on", "or", "our", "shall", "should", "so", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "to", "too", "under", "up", "us", "was", "we", "were", "what", "when", "where", "which", "who", "whom", "why", "will", "with", "would", "you", "your"]) | frozenset(["في", "من", "على", "إلى", "الى", "عن", "مع", "هذا", "هذه", "ذلك", "تلك", "التي", "الذي", "الذين", "هو", "هي", "هم", "ما", "ماذا", "كيف", "متى", "أين", "لماذا", "هل", "أن", "ان", "إن", "او", "أو", "و", "ثم", "كل", "بعض", "قد", "لا", "لم", "لن", "كان", "كانت", "يكون", "تكون"])

_ARABIC_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})
_ARABIC_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")


_SUFFIXES = (("ies", "y"), ("sses", "ss"), ("ations", "ate"), ("ation", "ate"), ("ments", ""), ("ment", ""),
             ("ingly", ""), ("ing", ""), ("edly", ""), ("ed", ""), ("ions", ""), ("ion", ""),
             ("ly", ""), ("es", ""), ("s", ""))


def _strip_once(word: str) -> str:
    for suffix, keep in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith(("ss", "us", "is")):
                return word
            stem = word[: len(word) - len(suffix)] + keep
            # "managing" -> "manag": drop a trailing e so "manage"/"managed" meet.
            return stem[:-1] if stem.endswith("e") and len(stem) > 4 else stem
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


def _stem_english(word: str) -> str:
    """A deliberately small stemmer: enough to fold plurals and -ing/-ed/-ion
    forms together (reporting/reported/reports -> report). It runs to a fixed
    point so "document" and "documented" reach the same stem. Over-stemming only
    costs a little precision, which the reranker recovers; under-stemming loses
    matches outright."""
    if len(word) <= 3 or not word.isascii() or not word.isalpha():
        return word
    for _ in range(3):
        stemmed = _strip_once(word)
        if stemmed == word:
            break
        word = stemmed
    return word


def _stem_arabic(word: str) -> str:
    for prefix in _ARABIC_PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            return word[len(prefix):]
    return word


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = _JOINERS.sub("", _ARABIC_MARKS.sub("", text)).translate(_ARABIC_MAP)
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        parts = re.split(r"[.\-_/]", raw)
        # An identifier is searchable whole (t3.2.2) and by its parts.
        candidates = [raw] + parts if len(parts) > 1 else parts
        for word in candidates:
            if not word or word in _STOPWORDS:
                continue
            word = _stem_arabic(word) if "؀" <= word[0] <= "ۿ" else _stem_english(word)
            if len(word) > 1 and word not in _STOPWORDS:
                tokens.append(word)
    return tokens


@dataclass(frozen=True, eq=False)
class Bm25Index:
    """An in-memory inverted index. Built once per corpus snapshot, never changed."""

    postings: dict[str, list[tuple[int, int]]]
    lengths: tuple[int, ...]
    k1: float  # term-frequency saturation (BM25_K1)
    b: float  # length normalisation (BM25_B)

    @classmethod
    def build(cls, texts: Sequence[str], k1: float, b: float) -> "Bm25Index":
        postings: dict[str, list[tuple[int, int]]] = {}
        lengths = []
        for doc_id, text in enumerate(texts):
            tokens = tokenize(text)
            lengths.append(len(tokens))
            for term, count in Counter(tokens).items():
                postings.setdefault(term, []).append((doc_id, count))
        return cls(postings, tuple(lengths), k1, b)

    @cached_property
    def avg_length(self) -> float:
        return sum(self.lengths) / len(self.lengths) if self.lengths else 0.0

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        """(document index, score), best first. Empty when nothing matches."""
        size = len(self.lengths)
        scores: dict[int, float] = {}
        for term in set(tokenize(query)):
            postings = self.postings.get(term)
            if not postings:
                continue
            idf = math.log(1.0 + (size - len(postings) + 0.5) / (len(postings) + 0.5))
            for doc_id, tf in postings:
                norm = self.k1 * (1.0 - self.b + self.b * self.lengths[doc_id] / (self.avg_length or 1.0))
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (self.k1 + 1.0) / (tf + norm)
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
