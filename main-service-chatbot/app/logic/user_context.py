"""What the chat prompts may know about the caller: their name and preferences - read straight
off dbo.users, since app.dependencies.get_current_user already fetched them - and, when enabled,
their recent questions."""
import json

# Bounds on what one user's preferences can put into a prompt. Capped both where they are
# written (PATCH /chat/me) and where they are read here, so a limit that changes later still
# applies to rows written under an older one.
MAX_PREFERENCES = 20
MAX_PREFERENCE_KEY_CHARS = 50
MAX_PREFERENCE_VALUE_CHARS = 200
MAX_DISPLAY_NAME_CHARS = 255  # dbo.users.display_name is NVARCHAR(255)


def clean_preferences(raw) -> dict[str, str]:
    """A flat {short key: short text} from whatever was stored or sent, and {} if it is not a mapping.

    Anything that is not a plain scalar is dropped rather than rendered: a nested object or a list has
    no sensible one-line form in a prompt. Length and count are capped, silently - a profile that runs
    long is trimmed, not refused, because this also runs on rows written before a limit changed.
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if len(cleaned) >= MAX_PREFERENCES:
            break
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        key, value = str(key).strip()[:MAX_PREFERENCE_KEY_CHARS], str(value).strip()[:MAX_PREFERENCE_VALUE_CHARS]
        if key and value:
            cleaned[key] = value
    return cleaned


def preferences_of(raw) -> dict[str, str]:
    """dbo.users.preferences - a JSON string, already a dict, or None - as a cleaned dict."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return clean_preferences(raw)


def update_profile_params(email: str, display_name: str | None, preferences: dict | None) -> list:
    """update_user_profile: email, display_name, preferences (JSON). None leaves a column as it is."""
    return [
        email, display_name,
        None if preferences is None else json.dumps(clean_preferences(preferences), ensure_ascii=False),
    ]


def recent_questions_params(user_id: str, count: int) -> list:
    """filter_chatbot_history: the caller's newest `count` rows, whatever conversation they are in."""
    return [None, None, None, None, user_id, True, 1, count]


def recent_questions_of(result: list, chars: int) -> list[str]:
    """The prompts in a filter_chatbot_history reply, newest first, each cut to `chars`; blanks dropped."""
    try:
        rows = result[0]
    except (IndexError, TypeError):
        return []
    questions = [" ".join(str(row.get("prompt") or "").split())[:chars] for row in rows]
    return [q for q in questions if q]


def user_context(display_name: str | None, preferences: dict | None, recent_questions: list[str] | None = None) -> dict | None:
    """What the prompts are told about the user, or None when there is nothing to tell."""
    context = {
        "name": display_name,
        "preferences": preferences or {},
        "recent_questions": list(recent_questions or []),
    }
    return context if any(context.values()) else None


async def generate_suggested_questions(
    chat_llm,
    query: str,
    response: str,
    sources: list[dict] | None = None,
    timeout: float = 5.0,
) -> list[str]:
    """Asks the LLM for 3 natural follow-up questions based on the question just answered.

    `sources` is the list of KB documents that were actually retrieved ({file_name, page, ...}).
    When provided, questions are grounded in those document names rather than inferred from the
    response text, which prevents the suggestions from drifting into conversation-introspection
    when the retrieved content happens to look like a dialogue.

    Returns [] on any failure or timeout so the chat turn is never held up.
    """
    import asyncio
    import logging
    logger = logging.getLogger(__name__)

    if not query or not response:
        return []

    # Greetings and very short inputs won't produce useful domain-specific suggestions.
    _GREETINGS = {"hi", "hello", "hey", "hiya", "howdy", "greetings", "sup", "yo"}
    if query.strip().lower().rstrip("!.,?") in _GREETINGS or len(query.strip()) < 5:
        return []

    # If no KB documents were retrieved (no-evidence turn, intent diversion, etc.) there is
    # nothing KB-grounded to ask follow-ups about.
    if sources is not None and len(sources) == 0:
        return []

    # Truncate to avoid sending huge context for a cheap helper call.
    q = query[:500]
    r = response[:1500]

    # Build a concise list of the document names that were actually retrieved.
    source_names: list[str] = []
    if sources:
        seen: set[str] = set()
        for s in sources:
            name = (s.get("file_name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                source_names.append(name)

    if source_names:
        source_block = (
            f"The answer was drawn from the following knowledge-base document(s): "
            + ", ".join(f'"{n}"' for n in source_names[:5])
            + ".\n\n"
        )
        topic_rule = (
            "Questions must be about the topics covered by those documents. "
            "Do NOT ask about this conversation, what was said earlier, or how the assistant works."
        )
    else:
        source_block = ""
        topic_rule = (
            "Questions must be about the knowledge-base content (document topics, policies, procedures, etc.). "
            "Do NOT ask about this conversation, what was said earlier, or how the assistant works."
        )

    messages = [
        {
            "role": "user",
            "content": (
                f"The assistant is a knowledge-base chatbot that answers questions from a fixed, "
                f"pre-loaded document corpus. Users cannot upload documents; they only query the KB.\n\n"
                f"{source_block}"
                f"A user asked the following question and received the answer below.\n\n"
                f"Question: {q}\n\n"
                f"Answer: {r}\n\n"
                f"Generate exactly 3 concise, specific follow-up questions the user might naturally "
                f"want to ask next, based on the content of this answer. "
                f"{topic_rule} "
                f"Output only a JSON array of 3 strings, no explanation:\n"
                f'["question 1", "question 2", "question 3"]'
            ),
        }
    ]
    try:
        raw = await asyncio.wait_for(
            chat_llm.invoke_helper(messages, "suggestions"),
            timeout=timeout,
        )
        # Parse the JSON array out of the response.
        import re
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not match:
            return []
        parsed = json.loads(match.group())
        if not isinstance(parsed, list):
            return []
        return [str(q).strip() for q in parsed if str(q).strip()][:3]
    except Exception as exc:
        logger.debug("Could not generate suggested questions: %s", exc)
        return []
