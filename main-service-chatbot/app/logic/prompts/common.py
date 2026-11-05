"""Shared by every prompt: how untrusted text is fenced as data, and how the conversation is trimmed."""

import re

# The language a reply names, keyed by the code it reports it with. The name is
# free text - a model asked for "the English name" sometimes writes "Anglais",
# or the language's name in its own script - and comparing free text against
# "english" is how a perfectly correct answer misses the shortcut that skips the
# translation round trip. A code is a closed set, so it can be compared. An
# unlisted code is no disaster: the reported name is used instead, exactly as
# before.
ISO_639_1_NAMES = {
    "ar": "arabic", "bn": "bengali", "de": "german", "en": "english",
    "es": "spanish", "fa": "persian", "fr": "french", "hi": "hindi",
    "id": "indonesian", "it": "italian", "ja": "japanese", "ko": "korean",
    "ml": "malayalam", "ms": "malay", "ne": "nepali", "pt": "portuguese",
    "ru": "russian", "ta": "tamil", "tl": "filipino", "tr": "turkish",
    "ur": "urdu", "zh": "chinese",
}


ENGLISH = ISO_639_1_NAMES["en"]


def _as_data(text, *tags: str) -> str:
    """Untrusted text, made safe to drop inside a <tag>...</tag> block.

    The prompts below tell the model that whatever sits inside their tags is
    data and not instructions. That promise is only worth anything if the data
    cannot close the tag around it: a message, or an uploaded document,
    containing "</context>" or "</latest_user_query>" would otherwise end the
    block early and have everything after it read as prompt. Neutralising the
    delimiters here is what makes the rule true, rather than merely stated.

    Only the block's own tags are touched, so ordinary text - including angle
    brackets, "a < b", or HTML that happens to be in a document - is unchanged.
    """
    out = str(text)
    for tag in tags:
        out = re.sub(rf"<\s*/?\s*{re.escape(tag)}\s*/?\s*>", f"[{tag}]", out, flags=re.IGNORECASE)
    return out


# Trimmed off the ends of a word before two queries are compared: sentence
# punctuation and quotation marks, in the scripts this knowledge base is asked
# about. Deliberately a list and not "every punctuation character" - stripping
# those indiscriminately turns "C++" and "C#" into "c", and answers one of two
# different questions.
_TRIM_CHARS = ".,;:!?…\"'`()[]{}<>«»“”‘’-–—،؛؟۔।。？！，"


def _dedupe_key(text: str) -> str:
    """What makes two returned queries "the same question" for deduplication.

    Case, the spacing between words and the punctuation around the ends are not
    the question - "What is X?" and "what is X" are one search, not two. Nothing
    inside the words is touched, so a real rewording stays a separate query and
    keeps its own search; only the packaging is ignored. The key is used for
    comparison only, never as the query - what gets searched is always the text
    the model actually returned.
    """
    stripped = []
    for word in str(text).lower().split():
        word = word.strip(_TRIM_CHARS)
        if word:
            stripped.append(word)
    return " ".join(stripped)


def _language_of(data: dict):
    """The query's language, lowercased, or None when none was reported.

    Prefers the reported code, which can be checked against a known set, over
    the name, which is free text. None means "not established", which the
    pipeline already reads as "run the translation check anyway".
    """
    # "en-US" is a code with a region on it; "tlh" is a three-letter code from a
    # different standard. Take the part before any separator and accept it only
    # at its full length - truncating "tlh" to "tl" would confidently report
    # Filipino.
    code = re.split(r"[^a-zA-Z]+", str(data.get("language_code") or "").strip())[0].lower()
    if len(code) == 2 and code in ISO_639_1_NAMES:
        return ISO_639_1_NAMES[code]

    name = str(data.get("language") or "").strip().lower()
    if name in ("", "unknown") or not name.replace(" ", "").replace("-", "").isalpha():
        return None
    return name


def _recent_history(chat_history, turns: int, chars: int, last_turn_chars: int | None = None) -> list:
    """The last `turns` messages, each clipped to `chars`, as role/content dicts.

    When `last_turn_chars` is given, the final two messages (the most recent
    Q&A pair) use that limit instead of `chars`. This lets follow-up references
    like "point 3" or "the second requirement" be resolved from the full
    previous answer, without inflating the prompt for every older turn.
    """
    messages = list((chat_history or [])[-turns:])
    last_pair_start = max(0, len(messages) - 2)
    recent = []
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "")
        limit = last_turn_chars if (last_turn_chars is not None and i >= last_pair_start) else chars
        recent.append({
            "role": str(message.get("role") or ""),
            "content": content[:limit],
        })
    return recent
