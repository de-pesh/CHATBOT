"""The database side of a chat, as data: the positional parameters of each stored procedure and how a paged reply is read."""

# chatbot_session.name is NVARCHAR(255); a longer prompt is cut to fit, ending in "...".
SESSION_NAME_COLUMN_CHARS = 255
_ELLIPSIS = "..."


def session_name(prompt: str) -> str:
    return prompt[:SESSION_NAME_COLUMN_CHARS - len(_ELLIPSIS)] + _ELLIPSIS if len(prompt) > SESSION_NAME_COLUMN_CHARS else prompt


def owned_session_params(session_id: int, user_id: str) -> list:
    """filter_chatbot_sessions: this one active session, if it is the caller's."""
    return [session_id, None, user_id, True, 1, 1]


def new_session_params(prompt: str, user_id: str, entity_id: str | None = None,
                       tenant_id: str | None = None, user_id_guid: str | None = None,
                       user_type: str | None = None, is_admin: bool = False) -> list:
    # upsert_chatbot_session: id, name, is_active, actor,
    #   entity_id, tenant_id, user_id, user_type, is_admin
    return [None, session_name(prompt), True, user_id,
            entity_id, tenant_id, user_id_guid, user_type, is_admin]


def rename_session_params(session_id: int, name: str, user_id: str) -> list:
    return [session_id, name, None, user_id]


def delete_session_params(session_id: int, user_id: str) -> list:
    return [session_id, user_id]


# upsert_chatbot_history: id, conversation_id, prompt, response, error, is_active, actor, rag_collections.
def new_history_params(conversation_id: int, prompt: str, user_id: str) -> list:
    return [None, conversation_id, prompt, None, None, True, user_id, None]


def history_success_params(history_id: int, conversation_id: int, response: str, user_id: str, collections: str | None) -> list:
    return [history_id, conversation_id, None, response, None, None, user_id, collections]


def history_error_params(history_id: int, conversation_id: int, error: str, user_id: str, collections: str | None) -> list:
    return [history_id, conversation_id, None, None, error, None, user_id, collections]


def convo_history_params(conversation_id, user_id: str, limit: int) -> list:
    """filter_chatbot_history: the most recent `limit` turns of a session, for context reloading."""
    return [None, conversation_id, None, None, user_id, True, 1, limit]


def owned_history_params(history_id: int, user_id: str) -> list:
    """filter_chatbot_history: this one history row (chat turn), if it is the caller's - for the
    ownership check in POST /chat/history/{id}/feedback."""
    return [history_id, None, None, None, user_id, None, 1, 1]


def history_list_params(filters, user_id: str, page_number: int, page_size: int) -> list:
    """filter_chatbot_history. Scoped to `user_id` (the authenticated caller), never to a client-supplied value."""
    return [
        filters.id, filters.conversation_id, filters.prompt, filters.response,
        user_id, filters.is_active, page_number, page_size,
    ]


def session_list_params(filters, user_id: str, page_number: int, page_size: int) -> list:
    """filter_chatbot_sessions, scoped to `user_id`."""
    return [filters.id, filters.name, user_id, filters.is_active, page_number, page_size]


def inserted_id(result: list) -> str:
    """The new row's id from an upsert: the procedure returns [[{'id': new_id}]]."""
    return result[0][0]["id"]


def page_of(results: list) -> tuple[list, int]:
    """(the page's rows, the total record count) of a filter_* procedure's two result sets."""
    if len(results) < 2:
        raise ValueError("Invalid response from DB.")
    rows, totals = results[0], results[1]
    return rows, totals[0].get("total_records", 0) if totals else 0


def rag_collections_label(collections: list[str]) -> str | None:
    """The searched collections as one rag_collections column value, or None when there were none."""
    return ", ".join(collections) if collections else None
