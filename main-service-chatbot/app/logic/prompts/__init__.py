"""Every prompt the chat pipeline sends, one module per step, and the readers of what comes back.

Every function here is a pure string builder: no model call, no I/O. Import from `app.logic.prompts`
(this package re-exports everything), or from the step's own module when reading one prompt:

    common     Shared by every prompt: how untrusted text is fenced as data, and how the conversation is trimmed.
    profile    The <user_profile> block: what the prompts may know about who is asking.
    sources    The numbered passages a prompt shows the model, and the metadata a citation is built from.
    rewrite    Step 1 of a chat turn: rewrite the question into search queries, and read the reply.
    rerank     Step 3 of a chat turn: the prompt that orders the retrieved passages.
    answer     Step 4 of a chat turn: the answer prompt, its translation, and the reminders for a second try.
    grounding  After the answer: the prompt that checks its citations against the passages.
    intent     Before the search: is this small talk or 'what do you have?', and the canned greeting.
    describe   The knowledge-base catalog: the prompt that describes one document.
"""

from app.logic.prompts.answer import (  # noqa: F401
    TRANSLATE_RETRY_REMINDER,
    fetch_final_prompt,
    format_translate_prompt,
    language_reminder,
)
from app.logic.prompts.common import (  # noqa: F401
    _TRIM_CHARS,
    ENGLISH,
    ISO_639_1_NAMES,
    _as_data,
    _dedupe_key,
    _language_of,
    _recent_history,
)
from app.logic.prompts.describe import (  # noqa: F401
    fetch_describe_prompt,
)
from app.logic.prompts.grounding import (  # noqa: F401
    fetch_grounding_prompt,
)
from app.logic.prompts.intent import (  # noqa: F401
    _GREETING_EXAMPLES,
    INTENT_CHITCHAT,
    INTENT_KB_OVERVIEW,
    INTENT_LANGUAGE_REMINDER,
    fetch_intent_prompt,
    simple_greeting_reply,
    time_of_day,
)
from app.logic.prompts.profile import (  # noqa: F401
    _ALL_TAGS,
    USER_PROFILE_RULE,
    user_profile_block,
)
from app.logic.prompts.rerank import (  # noqa: F401
    fetch_rerank_prompt,
)
from app.logic.prompts.rewrite import (  # noqa: F401
    fetch_new_query_prompt,
    parse_new_queries,
)
from app.logic.prompts.sources import (  # noqa: F401
    _format_context,
    _origin,
    _page_of,
    build_sources,
)
