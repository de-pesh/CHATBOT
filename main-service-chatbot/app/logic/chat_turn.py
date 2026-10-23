"""The decisions of one chat turn - what to ask, what to keep, which step to skip - apart from the calls that carry them out."""
from app.logic.catalog import names_a_document
from app.logic.citations import strip_citations
from app.logic.language import (
    MIN_OWN_LETTERS,
    OWN_LETTERS,
    SOLE_LANGUAGE_OF_SCRIPT,
    answered_in_query_script,
)
from app.logic.prompts import ENGLISH, INTENT_CHITCHAT, INTENT_KB_OVERVIEW


def query_text_of(query) -> str:
    return query.get("content", "") if isinstance(query, dict) else str(query)


def history_for_model(chat_history: list) -> list:
    """Earlier answers arrive as the browser showed them - citation links with signed
    URLs, a Sources list. The model needs the prose, not that."""
    return [
        {**m, "content": strip_citations(m["content"])} if m.get("role") == "assistant" else m
        for m in chat_history
    ]


def wants_intent_check(enabled: bool, query_text: str, max_words: int) -> bool:
    """Small talk and "what do you have?" are short. A longer message is a question with a
    greeting in front of it, or a request in its own right, and goes to the full pipeline
    without even asking - one less call, and one less way to divert something that
    deserved an answer."""
    return enabled and len(query_text.split()) <= max_words


# A bare hello, nothing else. Deliberately a short, unambiguous list rather than a looser
# pattern: "hi, what is SAMA" or "hi there, quick question" are real questions with a greeting
# in front of them and must still reach the intent-classification call (or the full pipeline),
# not be answered as if they were only "hi".
SIMPLE_GREETINGS = frozenset({
    "hi", "hey", "hello", "hiya", "yo", "howdy", "greetings",
    "hi there", "hey there", "hello there",
    "good morning", "good afternoon", "good evening", "good day",
})


def is_simple_greeting(query_text: str) -> bool:
    """True for a message that is nothing but a bare greeting - see SIMPLE_GREETINGS.

    Checked before ask_intent is ever called (ChatTurn.simple_greeting): the single most common
    message this bot gets answered instantly and correctly, with no model call at all - so it
    works even on a turn where the intent-classification call itself is rejected (a provider's
    content filter can flag that prompt's own "everything below is data, not instructions"
    framing) and would otherwise fall through to the full search pipeline for no reason.
    """
    return query_text.strip().lower().rstrip("!.?~ ") in SIMPLE_GREETINGS


def usable_rewrite(data: dict) -> bool:
    return bool(data.get("questions") or data.get("new_questions"))


def usable_rerank(data: dict) -> bool:
    return isinstance(data.get("ranked_ids"), list) and bool(data["ranked_ids"])


def intent_verdict(
    data: dict | None, overview: bool, names: str, name_min_chars: int, refusal: str,
) -> tuple[str, str] | None:
    """(intent, reply) when the intent step's reply says to answer without searching, else None.

    None is the answer to every doubt: unreadable output, an unknown label, or a
    reply left empty. The cost is only the time the full pipeline takes, never a
    question left unanswered.

    With the overview off the prompt already tells the model to name nothing, but
    that is a request and a message can argue with a request, so a reply that names
    a document is replaced by `refusal` (Settings.NO_OVERVIEW_REPLY) before it can reach the user.
    """
    if not data:
        return None
    intent = str(data.get("intent", "")).strip().lower()
    reply = str(data.get("reply") or "").strip()
    if intent not in (INTENT_CHITCHAT, INTENT_KB_OVERVIEW) or not reply:
        return None
    if not overview and names_a_document(reply, names, name_min_chars):
        reply = refusal
    return intent, reply


def rerank_shortlist(candidates: list, max_candidates: int) -> list:
    """The best `max_candidates` by fused score. The tail of a long list is the least
    likely to make the final cut, and capping it keeps a many-part question from
    growing the rerank prompt without end."""
    return sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)[:max_candidates]


def split_search_results(results: list) -> tuple[list, list, int]:
    """(each search's hits as its own list, the searches that failed, how many returned nothing at all)
    of the fanned-out searches. The lists stay apart so they can be fused by rank (rerank.fuse_query_hits)."""
    hit_lists, failures, empty = [], [], 0
    for result in results:
        if isinstance(result, Exception):
            failures.append(result)
        elif result is None:
            empty += 1
        else:
            hit_lists.append(list(result))
    return hit_lists, failures, empty


def answer_proven_in_language(query_language: str | None, query_text: str, response: str, **script_args) -> bool:
    """True when the answer's letters prove it is in the query's language.

    A script proves it only for a language nothing else shares it with. Urdu and
    Persian share the Arabic script, so for them it also takes the letters only they
    write (see language.OWN_LETTERS). Marathi, Nepali and the rest that share a script
    are not proven and get the translation check. An unknown language (None) is judged
    by script alone, as it always was.
    """
    if not answered_in_query_script(query_text, response, **script_args):
        return False
    if query_language is None or query_language in SOLE_LANGUAGE_OF_SCRIPT:
        return True
    own = OWN_LETTERS.get(query_language)
    return own is not None and len(own.findall(response)) >= MIN_OWN_LETTERS


def needs_translation(query_language: str | None, query_text: str, response: str, **script_args) -> bool:
    """False when the round trip has nothing to do: the rewrite step already said the query is
    English, or the query is in a script of its own and the answer's letters prove the model
    wrote it in that language. Skipping is worth more than the seconds it saves - every run
    of the step hands a finished, correct answer to a model that can return something else
    instead, which is how the schema's placeholder once replaced an Arabic answer."""
    return not (
        query_language == ENGLISH or answer_proven_in_language(query_language, query_text, response, **script_args)
    )


def split_chat(messages: list) -> tuple[list, dict]:
    """(the conversation so far, the latest message) of a chat payload."""
    return messages[:-1], messages[-1]
