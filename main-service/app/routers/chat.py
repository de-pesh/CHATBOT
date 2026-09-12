import json
import logging
from typing import Annotated, Any, AsyncIterator, Optional
from fastapi import APIRouter, status, HTTPException, Depends, Query, Path
from fastapi.responses import StreamingResponse
# Controller
from app.controllers.chat_controller import process_chat, process_chat_stream
# Models
from app.models.chat import(
    Chat,
    ChatHistoryFilter,
    PaginatedChatHistory,
    ChatSessionFilter,
    PaginatedChatSessions,
    ChatSessionUpdate
)
from app.stored_procedures import StoredProcedures
# Services
from app.services.ollama_service import OllamaService, get_ollama_client
from app.services.database_service import DatabaseService
from app.config import Settings, get_settings
from app.dependencies import get_db_service
from app.helpers.citation_helper import linkify_citations

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/chat",
    tags=["Chatbot"]
)


# --- Shared turn bookkeeping -------------------------------------------------
# POST /chat and POST /chat/stream write the same rows in the same order: open
# a session if there is not one, insert the prompt, then update that row with
# either the answer or the error. These helpers hold that sequence once.

async def _ensure_session(
    db_service: DatabaseService,
    conversation_id: Optional[int],
    user_id: str,
    prompt: str,
) -> int:
    """Returns the conversation to log against, creating one if needed."""
    if conversation_id:
        return conversation_id

    logger.info("No conversation_id provided. Creating a new chat session.")
    session_name = (prompt[:252] + '...') if len(prompt) > 255 else prompt
    session_params = [None, session_name, True, user_id]

    session_result = await db_service.call_procedure(
        StoredProcedures.UPSERT_CHATBOT_SESSION, session_params
    )
    if not session_result or not session_result[0]:
        raise HTTPException(status_code=500, detail="Failed to create a new chat session.")

    conversation_id = session_result[0][0]['id']
    logger.info(f"New session created with conversation_id: {conversation_id}")
    return conversation_id


async def _insert_prompt(
    db_service: DatabaseService,
    conversation_id: int,
    prompt: str,
    user_id: str,
) -> int:
    """Logs the incoming question and returns the new history row id."""
    logger.debug("Logging initial query to the database.")
    insert_params = [
        None,  # id is NULL for insert
        conversation_id,
        prompt,
        None, None, None, None,  # response, feedback, error, comment
        True,  # is_active
        user_id,
        None, None,  # intent, retrieved_collections (unknown until processing)
    ]
    upsert_result = await db_service.call_procedure(
        StoredProcedures.UPSERT_CHATBOT_HISTORY,
        insert_params
    )
    # The procedure returns a list containing one list with one dict: [[{'id': new_id}]]
    logger.info(f"Upsert result: {upsert_result}")
    history_id = upsert_result[0][0]['id']
    logger.info(f"Logged new prompt with history_id: {history_id}")
    return history_id


async def _record_success(
    db_service: DatabaseService,
    history_id: int,
    conversation_id: int,
    response: str,
    intent: Optional[str],
    retrieved_collections: Optional[str],
) -> None:
    logger.debug(f"Updating history record {history_id} with successful response.")
    update_params = [
        history_id,
        conversation_id,
        None,
        response,
        None, None, None, None,
        "LLM",
        intent, retrieved_collections,
    ]
    await db_service.call_procedure(StoredProcedures.UPSERT_CHATBOT_HISTORY, update_params)
    logger.info(f"Updated history_id {history_id} with successful response.")


async def _record_error(
    db_service: DatabaseService,
    history_id: Optional[int],
    conversation_id: Optional[int],
    error_message: str,
    user_id: str,
    intent: Optional[str],
    retrieved_collections: Optional[str],
) -> None:
    """Best-effort: a failure to log the failure must not mask the original one."""
    if not (history_id and conversation_id):
        return
    logger.error(f"Updating history record {history_id} with error details.")
    try:
        error_params = [
            history_id, conversation_id, None, None, None,
            error_message, None, None, user_id,
            intent, retrieved_collections,
        ]
        await db_service.call_procedure(StoredProcedures.UPSERT_CHATBOT_HISTORY, error_params)
    except Exception as db_err:
        logger.critical(f"Failed to log error to database for history_id {history_id}: {db_err}")


def _unpack_payload(payload: Chat):
    """Splits a chat payload into (history, latest query, user_id)."""
    chat_data = payload.model_dump()["chat"]
    chat_history = chat_data[:-1] if len(chat_data) > 1 else []
    query = chat_data[-1]
    logger.info("Extracted chat_history: %s", chat_history)
    logger.info("Extracted query: %s", query)
    return chat_history, query, (payload.user_id if payload.user_id else "system")


def _sse(event: str, data: Any) -> str:
    """Formats one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    path="",
    status_code=status.HTTP_200_OK,
    summary="Process a Chat Message",
    description="Processes a chat interaction by rephrasing the query, fetching relevant context, and generating a final response using the LLM.",
)
async def chat(
    settings: Annotated[Settings, Depends(get_settings)],
    payload: Chat,
    ollama_client: Annotated[OllamaService, Depends(get_ollama_client)],
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
):
    """
    Handles a chat interaction with the policy chatbot.

    Blocks until the whole answer is ready. `POST /chat/stream` runs the same
    pipeline and returns the answer as it is generated; prefer it for
    interactive clients.

    Args:
        settings (Settings): Application settings dependency.
        payload (Chat): The chat payload containing the conversation history.
        ollama_client (OllamaService): The Ollama LLM client dependency.

    Returns:
        dict: A dictionary containing the chatbot's response.

    Raises:
        HTTPException: For known errors during chat processing.
    """
    logger.info("Router: Received POST /chat request")
    chat_history, query, user_id = _unpack_payload(payload)
    extracted_query = query.get("content")
    conversation_id = payload.conversation_id
    history_id = None
    detected_intent = None
    retrieved_collections = None

    try:
        conversation_id = await _ensure_session(db_service, conversation_id, user_id, extracted_query)
        history_id = await _insert_prompt(db_service, conversation_id, extracted_query, user_id)

        result = await process_chat(
            settings=settings,
            chat_history=chat_history,
            query=query,
            ollama_client=ollama_client,
            assessment_id=payload.assessment_id,
            framework_id=payload.framework_id
        )
        detected_intent = result.intent
        retrieved_collections = ", ".join(result.collections) if result.collections else None
        response_with_citations = linkify_citations(result.response)

        await _record_success(
            db_service, history_id, conversation_id,
            response_with_citations, detected_intent, retrieved_collections,
        )

        return {
            "response": response_with_citations,
            "conversation_id": conversation_id
        }

    except Exception as e:
        logger.exception("An error occurred during chat processing.")
        error_message = str(e)
        status_code = 500
        detail = f"Internal server error: {error_message}"

        # Determine error type for proper HTTP response
        if isinstance(e, HTTPException):
            status_code = e.status_code
            detail = e.detail
        elif isinstance(e, ValueError):
            status_code = 422
            detail = f"Value error during processing: {error_message}"

        await _record_error(
            db_service, history_id, conversation_id,
            error_message, user_id, detected_intent, retrieved_collections,
        )
        raise HTTPException(status_code=status_code, detail=detail) from e


@router.post(
    path="/stream",
    summary="Process a Chat Message (streaming)",
    description=(
        "Same pipeline as POST /chat, delivered as Server-Sent Events so the answer "
        "appears as it is generated.\n\n"
        "Event sequence:\n"
        "- `meta` - `{conversation_id}`, sent first so a new session can be tracked immediately.\n"
        "- `stage` - `{stage}`: rephrasing, retrieving, generating, finalising. For progress display.\n"
        "- `delta` - `{text}`: the next chunk of the answer. Concatenate these for a live preview.\n"
        "- `done` - `{response, conversation_id, intent, collections}`. `response` is authoritative "
        "and replaces the concatenated deltas: a non-English question has its answer translated "
        "after generation, and citations are turned into links.\n"
        "- `error` - `{detail}`: the turn failed. May arrive after some deltas.\n\n"
        "The response is always HTTP 200 once streaming starts, so failures are reported "
        "as an `error` event rather than a status code."
    ),
)
async def chat_stream(
    settings: Annotated[Settings, Depends(get_settings)],
    payload: Chat,
    ollama_client: Annotated[OllamaService, Depends(get_ollama_client)],
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
):
    """Streams a chat turn to the client as Server-Sent Events.

    Session creation and the prompt insert happen before the response starts so
    that a database failure is still a real HTTP error. Everything after that
    point is reported inside the stream, because the status line is long gone
    by the time the LLM can fail.
    """
    logger.info("Router: Received POST /chat/stream request")
    chat_history, query, user_id = _unpack_payload(payload)
    extracted_query = query.get("content")

    conversation_id = await _ensure_session(db_service, payload.conversation_id, user_id, extracted_query)
    history_id = await _insert_prompt(db_service, conversation_id, extracted_query, user_id)

    async def event_stream() -> AsyncIterator[str]:
        detected_intent = None
        retrieved_collections = None
        try:
            yield _sse("meta", {"conversation_id": conversation_id})

            result = None
            async for event in process_chat_stream(
                settings=settings,
                chat_history=chat_history,
                query=query,
                ollama_client=ollama_client,
                assessment_id=payload.assessment_id,
                framework_id=payload.framework_id,
            ):
                if event["type"] == "delta":
                    yield _sse("delta", {"text": event["text"]})
                elif event["type"] == "stage":
                    yield _sse("stage", {"stage": event["stage"]})
                elif event["type"] == "result":
                    result = event["result"]

            if result is None:
                raise RuntimeError("Chat pipeline produced no result.")

            detected_intent = result.intent
            retrieved_collections = ", ".join(result.collections) if result.collections else None
            response_with_citations = linkify_citations(result.response)

            await _record_success(
                db_service, history_id, conversation_id,
                response_with_citations, detected_intent, retrieved_collections,
            )

            yield _sse("done", {
                "response": response_with_citations,
                "conversation_id": conversation_id,
                "intent": detected_intent,
                "collections": result.collections,
            })

        except Exception as e:
            logger.exception("An error occurred during streaming chat processing.")
            error_message = str(e)
            detail = e.detail if isinstance(e, HTTPException) else f"Internal server error: {error_message}"
            await _record_error(
                db_service, history_id, conversation_id,
                error_message, user_id, detected_intent, retrieved_collections,
            )
            yield _sse("error", {"detail": detail, "conversation_id": conversation_id})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Tells nginx (and the OpenShift router) not to buffer the response.
            # Without it a reverse proxy holds chunks back and the stream
            # arrives all at once, which defeats the point.
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    path="/history",
    response_model=PaginatedChatHistory,
    summary="Fetch Chat History",
    description="Fetches paginated and filtered chat history from the database.",
)
async def get_chat_history(
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    filters: ChatHistoryFilter = Depends(),
    page_number: int = Query(1, ge=1, description="Page number to retrieve."),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page.")
):
    """
    Retrieves a paginated list of chat history records based on filter criteria.

    Args:
        db_service (DatabaseService): The database service dependency.
        filters (ChatHistoryFilter): The filter parameters for the query.
        page_number (int): The page number for pagination.
        page_size (int): The number of items per page.

    Returns:
        PaginatedChatHistory: A paginated list of chat history records.
    """
    logger.info(f"Fetching chat history with filters: {filters.model_dump(exclude_none=True)}")

    # The order of parameters must match the stored procedure definition
    params = [
        filters.id,
        filters.conversation_id,
        filters.prompt,
        filters.response,
        filters.feedback,
        filters.created_by,
        filters.is_active,
        page_number,
        page_size,
    ]

    try:
        results = await db_service.call_procedure(
            StoredProcedures.FILTER_CHATBOT_HISTORY,
            params
        )

        if len(results) < 2:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Invalid response from DB.")

        chat_history_records = results[0]
        total_records = results[1][0].get('total_records', 0) if results[1] else 0

        return PaginatedChatHistory(
            total_records=total_records,
            page_number=page_number,
            page_size=page_size,
            data=chat_history_records,
        )
    except Exception as e:
        logger.exception("Unexpected error fetching chat history.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "An internal server error occurred.")


# --- Session Management Endpoints ---

@router.get(
    path="/session",
    response_model=PaginatedChatSessions,
    summary="Fetch Chat Sessions",
    description="Retrieves a paginated list of chat sessions for a user.",
)
async def get_chat_session(
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    filters: ChatSessionFilter = Depends(),
    page_number: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100)
):
    logger.info(f"Fetching chat sessions with filters: {filters.model_dump(exclude_none=True)}")
    params = [
        filters.id,
        filters.name,
        filters.created_by,
        filters.is_active,
        page_number,
        page_size
    ]
    try:
        results = await db_service.call_procedure(
            StoredProcedures.FILTER_CHATBOT_SESSIONS, params
        )
        if len(results) < 2:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Invalid response from DB.")

        session_records = results[0]
        total_records = results[1][0].get('total_records', 0) if results[1] else 0

        return PaginatedChatSessions(
            total_records=total_records,
            page_number=page_number,
            page_size=page_size,
            data=session_records,
        )
    except Exception as e:
        logger.exception("Unexpected error fetching chat sessions.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "An internal server error occurred.")


@router.patch(
    path="/session/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Update Session Name",
    description="Updates the name of a chat session for a user.",
)
async def update_chat_session(
    payload: ChatSessionUpdate,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    session_id: int = Path(..., ge=1, description="The ID of the session to update."),
):
    logger.info(f"Updating name for session_id: {session_id}")
    params = [session_id, payload.name, None, payload.user_id]
    try:
        await db_service.call_procedure(StoredProcedures.UPSERT_CHATBOT_SESSION, params)
        return {"message": f"Session {session_id} updated successfully."}
    except Exception as e:
        logger.exception(f"Failed to update session {session_id}.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update session.")


@router.delete(
    path="/session/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Chat Session",
    description="Soft deletes a chat session for a user.",
)
async def delete_chat_session(
    user_id: str,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    session_id: int = Path(..., ge=1, description="The ID of the session to delete."),
):
    logger.info(f"Soft deleting session_id: {session_id} by user: {user_id}")
    params = [session_id, user_id]
    try:
        await db_service.call_procedure(StoredProcedures.DELETE_CHATBOT_SESSION, params)
    except Exception as e:
        logger.exception(f"Failed to delete session {session_id}.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to delete session.")
