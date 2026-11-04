"""POST /chat, POST /chat/stream and the history and session endpoints: request -> controller -> response."""
import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse

from app.config import Settings, get_settings
from app.controllers.chat_citations import linkify_citations, prefetch_citation_urls
from app.controllers.chat_controller import (
    ChatSessionController,
    process_chat,
    process_chat_stream,
)
from app.controllers.chat_grounding import check_grounding
from app.dependencies import (
    CurrentUserDep,
    get_db_service,
    get_file_storage_service,
    get_kb_catalog,
)
from app.logic.chat_events import (
    browser_event,
    done_payload,
    error_payload,
)
from app.logic.chat_records import (
    delete_session_params,
    history_list_params,
    rag_collections_label,
    rename_session_params,
    session_list_params,
)
from app.logic.chat_turn import split_chat
from app.logic.versions import Mode, VersionScope
from app.logic.feedback import (
    existing_feedback_id,
    feedback_lookup_params,
    feedback_params,
)
from app.logic.http import SSE_HEADERS, sse_frame
from app.logic.user_context import generate_suggested_questions, update_profile_params
from app.models.auth import CurrentUser
from app.models.chat import (
    Chat,
    ChatFeedbackCreate,
    ChatHistoryFilter,
    ChatSessionFilter,
    ChatSessionUpdate,
    ChatUserProfile,
    ChatUserUpdate,
    PaginatedChatHistory,
    PaginatedChatSessions,
)
from app.rate_limit import chat_rate_limit
from app.routers.chat_helpers import (
    context_for,
    error_detail,
    http_error,
    list_page,
    ms_since,
    require_owned_session,
    save_answer,
    save_in_background,
    save_trace,
    settled,
    timed,
    turn_timings,
)
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.llm.chat_llm import ChatLLM, get_chat_llm
from app.services.storage.storage_service import FileStorageService
from app.services.vector.search_runtime import SearchRuntime, get_search_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chatbot"])

_settings = get_settings()


def _page_number():
    return Query(1, ge=1, description="Page number to retrieve.")


def _page_size():
    return Query(
        _settings.CHAT_PAGE_SIZE_DEFAULT, ge=1, le=_settings.CHAT_PAGE_SIZE_MAX, description="Number of records per page.",
    )




























@router.post(
    path="",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(chat_rate_limit)],
    summary="Process a Chat Message",
    description="Processes a chat interaction by rephrasing the query, fetching relevant context, and generating a final response using the LLM.",
)
async def chat(
    settings: Annotated[Settings, Depends(get_settings)],
    payload: Chat,
    current_user: CurrentUserDep,
    chat_llm: Annotated[ChatLLM, Depends(get_chat_llm)],
    search: Annotated[SearchRuntime, Depends(get_search_runtime)],
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    storage: Annotated[FileStorageService, Depends(get_file_storage_service)],
    catalog=Depends(get_kb_catalog),
):
    """Handles a chat interaction, blocking until the whole answer is ready.

    `POST /chat/stream` runs the same pipeline and returns the answer as it is
    generated; prefer it for interactive clients. The caller owns a new session
    and must own an existing `conversation_id` (404 otherwise).
    """
    request_start = time.perf_counter()
    sessions = ChatSessionController(db_service)
    chat_history, query = split_chat(payload.model_dump()["chat"])
    user_id, prompt = current_user.email, query["content"]
    conversation_id = payload.conversation_id
    history_id = collections = None

    try:
        # Two independent reads: the caller's session, and the recent questions the prompts may be told about.
        conversation_id, ctx = await asyncio.gather(
            sessions.ensure_session(conversation_id, user_id, prompt, current_user),
            context_for(current_user, db_service, settings),
        )
        history_id = await sessions.insert_prompt(conversation_id, prompt, user_id)
        prep_ms = ms_since(request_start)

        result = await process_chat(
            settings=settings, chat_history=chat_history, query=query, chat_llm=chat_llm, search=search,
            catalog=catalog.render() if catalog is not None else None, local_hour=payload.local_hour,
            user_context=ctx,
        )
        collections = rag_collections_label(result.collections)
        linkify_ms, answer = await timed(linkify_citations(result.response, result.sources, storage, db_service, settings))
        save_ms, _ = await timed(sessions.record_success(history_id, conversation_id, answer, user_id, collections))
        # Fire-and-forget, same as the streaming endpoint: never something the caller waits on.
        save_in_background(check_grounding(db_service, settings, chat_llm, history_id, result.response, result.passages))
        save_in_background(save_trace(db_service, settings, history_id, result, ms_since(request_start)))

        suggested = await generate_suggested_questions(chat_llm, prompt, result.response, result.sources)
        return {
            "response": answer,
            "conversation_id": conversation_id,
            "history_id": history_id,
            "timings": turn_timings(result.timings, request_start, prep_ms, linkify_ms, save_ms),
            "suggested_questions": suggested,
        }

    except Exception as e:
        logger.exception("An error occurred during chat processing.")
        await sessions.record_error(history_id, conversation_id, str(e), user_id, collections)
        raise http_error(e) from e


@router.post(
    path="/stream",
    dependencies=[Depends(chat_rate_limit)],
    summary="Process a Chat Message (streaming)",
    description=(
        "Same pipeline as POST /chat, delivered as Server-Sent Events so the answer "
        "appears as it is generated.\n\n"
        "Event sequence:\n"
        "- `meta` - `{conversation_id}`, sent first so a new session can be tracked immediately.\n"
        "- `stage` - `{stage}`: rephrasing, retrieving, reranking, generating, finalising. For progress display.\n"
        "- `delta` - `{text}`: the next chunk of the answer. Concatenate these for a live preview.\n"
        "- `done` - `{response, conversation_id, collections, timings}`. `timings` is milliseconds per "
        "stage, for finding out where a slow turn went. `response` is authoritative "
        "and replaces the concatenated deltas: a non-English question has its answer translated "
        "after generation, and inline `[n]` citation markers are turned into links.\n"
        "- `error` - `{detail, conversation_id}`: the turn failed. May arrive after some deltas.\n\n"
        "The response is always HTTP 200 once streaming starts, so failures are reported "
        "as an `error` event rather than a status code."
    ),
)
async def chat_stream(
    settings: Annotated[Settings, Depends(get_settings)],
    payload: Chat,
    current_user: CurrentUserDep,
    chat_llm: Annotated[ChatLLM, Depends(get_chat_llm)],
    search: Annotated[SearchRuntime, Depends(get_search_runtime)],
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    storage: Annotated[FileStorageService, Depends(get_file_storage_service)],
    catalog=Depends(get_kb_catalog),
):
    """Streams a chat turn to the client as Server-Sent Events.

    Session creation happens before the response starts so that a database
    failure - or a conversation_id that doesn't exist or isn't owned by the
    caller - is still a real HTTP error. Everything after that is reported
    inside the stream, because the status line is long gone by the time the LLM
    can fail. That includes the prompt insert, which runs alongside the turn and
    is checked once the answer is complete.
    """
    request_start = time.perf_counter()
    sessions = ChatSessionController(db_service)
    chat_history, query = split_chat(payload.model_dump()["chat"])
    user_id, prompt = current_user.email, query["content"]

    conversation_id, ctx = await asyncio.gather(
        sessions.ensure_session(payload.conversation_id, user_id, prompt, current_user),
        context_for(current_user, db_service, settings),
    )
    # Logging the question is only needed once there is an answer to attach to it, so it
    # runs alongside the turn. The session check above stays first: it is what turns a
    # foreign conversation_id into a 404.
    history_task = save_in_background(sessions.insert_prompt(conversation_id, prompt, user_id))
    prep_ms = ms_since(request_start)

    async def event_stream() -> AsyncIterator[str]:
        collections = history_id = None
        try:
            yield sse_frame("meta", {"conversation_id": conversation_id})

            result = url_prefetch = None
            async for event in process_chat_stream(
                settings=settings, chat_history=chat_history, query=query, chat_llm=chat_llm, search=search,
                catalog=catalog.render() if catalog is not None else None, local_hour=payload.local_hour,
                user_context=ctx,
            ):
                if shown := browser_event(event):
                    yield sse_frame(*shown)
                elif event["type"] == "sources":
                    # The passages are known before the answer is written, so resolve the download links now, alongside it.
                    url_prefetch = asyncio.create_task(prefetch_citation_urls(event["sources"], storage, db_service, settings))
                elif event["type"] == "result":
                    result = event["result"]
            if result is None:
                raise RuntimeError("Chat pipeline produced no result.")

            collections = rag_collections_label(result.collections)
            history_id = await history_task  # long finished by now; if the insert failed, this reports it
            linkify_ms, answer = await timed(linkify_citations(
                result.response, result.sources, storage, db_service, settings, prefetched=url_prefetch,
            ))
            # The browser should not wait for the history write. It is its own task so it
            # still finishes if the client hangs up the moment it has the answer.
            save_in_background(save_answer(sessions, history_id, conversation_id, answer, user_id, collections))
            # result.response (not `answer`): the grounding check needs the bare [n] markers to
            # map a claim back to its passage - linkify_citations has already replaced them with
            # HTML links in `answer` by this point.
            save_in_background(check_grounding(db_service, settings, chat_llm, history_id, result.response, result.passages))

            save_in_background(save_trace(db_service, settings, history_id, result, ms_since(request_start)))

            timings = turn_timings(result.timings, request_start, prep_ms, linkify_ms)
            suggested = await generate_suggested_questions(chat_llm, prompt, result.response, result.sources)
            vs = VersionScope(Mode(result.version_mode), result.version_token) if result.version_mode else None
            yield sse_frame("done", done_payload(answer, conversation_id, history_id, result.collections, timings, suggested, version_scope=vs))

        except Exception as e:
            logger.exception("An error occurred during streaming chat processing.")
            history_id = history_id or await settled(history_task)
            await sessions.record_error(history_id, conversation_id, str(e), user_id, collections)
            yield sse_frame("error", error_payload(error_detail(e), conversation_id))

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get(
    path="/me",
    response_model=ChatUserProfile,
    summary="Get My Chat Profile",
    description="The caller's chat profile - their name and preferences, which the assistant uses to fit its "
                "answers to them. Only available to a real, logged-in account (DISABLE_AUTH=False); "
                "503 otherwise.",
)
async def get_my_chat_profile(
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: CurrentUserDep,
):
    if getattr(settings, "DISABLE_AUTH", False):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The chat profile is unavailable right now.")
    return current_user


@router.patch(
    path="/me",
    response_model=ChatUserProfile,
    summary="Update My Chat Profile",
    description="Changes the caller's own name and/or preferences. Fields left out stay as they are. "
                "Preferences are written into the assistant's prompts, so keep them to plain facts about "
                "the person, such as their role or how they like answers written.",
)
async def update_my_chat_profile(
    settings: Annotated[Settings, Depends(get_settings)],
    payload: ChatUserUpdate,
    current_user: CurrentUserDep,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
):
    if getattr(settings, "DISABLE_AUTH", False):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The chat profile is unavailable right now.")
    try:
        result = await db_service.call_procedure(
            StoredProcedures.UPDATE_USER_PROFILE,
            update_profile_params(current_user.email, payload.display_name, payload.preferences),
        )
    except Exception as exc:
        logger.exception("Failed to update the chat profile.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update the chat profile.") from exc
    if not result or not result[0]:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update the chat profile.")
    return CurrentUser(**result[0][0])


@router.get(
    path="/history",
    response_model=PaginatedChatHistory,
    summary="Fetch Chat History",
    description="Fetches paginated and filtered chat history from the database.",
)
async def get_chat_history(
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    current_user: CurrentUserDep,
    filters: ChatHistoryFilter = Depends(),
    page_number: int = _page_number(),
    page_size: int = _page_size(),
):
    """The caller's own chat history, whatever `filters` ask for."""
    params = history_list_params(filters, current_user.email, page_number, page_size)
    return await list_page(
        db_service, StoredProcedures.FILTER_CHATBOT_HISTORY, params, page_number, page_size,
        PaginatedChatHistory, "chat history",
    )


@router.get(
    path="/session",
    response_model=PaginatedChatSessions,
    summary="Fetch Chat Sessions",
    description="Retrieves a paginated list of the caller's own chat sessions.",
)
async def get_chat_session(
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    current_user: CurrentUserDep,
    filters: ChatSessionFilter = Depends(),
    page_number: int = _page_number(),
    page_size: int = _page_size(),
):
    params = session_list_params(filters, current_user.email, page_number, page_size)
    return await list_page(
        db_service, StoredProcedures.FILTER_CHATBOT_SESSIONS, params, page_number, page_size,
        PaginatedChatSessions, "chat sessions",
    )


@router.patch(
    path="/session/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Update Session Name",
    description="Renames one of the caller's own chat sessions.",
)
async def update_chat_session(
    payload: ChatSessionUpdate,
    current_user: CurrentUserDep,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    session_id: str = Path(..., description="The ID of the session to update."),
):
    await require_owned_session(db_service, session_id, current_user.email)
    try:
        await db_service.call_procedure(
            StoredProcedures.UPSERT_CHATBOT_SESSION, rename_session_params(session_id, payload.name, current_user.email),
        )
        return {"message": f"Session {session_id} updated successfully."}
    except Exception as exc:
        logger.exception(f"Failed to update session {session_id}.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update session.") from exc


@router.delete(
    path="/session/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Chat Session",
    description="Soft deletes one of the caller's own chat sessions.",
)
async def delete_chat_session(
    current_user: CurrentUserDep,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    session_id: str = Path(..., description="The ID of the session to delete."),
):
    await require_owned_session(db_service, session_id, current_user.email)
    try:
        await db_service.call_procedure(
            StoredProcedures.DELETE_CHATBOT_SESSION, delete_session_params(session_id, current_user.email),
        )
    except Exception as exc:
        logger.exception(f"Failed to delete session {session_id}.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to delete session.") from exc


@router.post(
    path="/history/{history_id}/feedback",
    status_code=status.HTTP_200_OK,
    summary="Rate an Answer",
    description="Records a thumbs up/down (and an optional comment) on one of the caller's own "
                "chat turns. Submitting again for the same turn replaces the previous rating.",
)
async def submit_chat_feedback(
    payload: ChatFeedbackCreate,
    current_user: CurrentUserDep,
    db_service: Annotated[DatabaseService, Depends(get_db_service)],
    history_id: str = Path(..., description="The chat history row (answer) being rated."),
):
    sessions = ChatSessionController(db_service)
    if await sessions.get_owned_history(history_id, current_user.email) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Chat turn {history_id} not found.")
    try:
        existing = await db_service.call_procedure(
            StoredProcedures.FILTER_CHATBOT_FEEDBACK, feedback_lookup_params(history_id),
        )
        await db_service.call_procedure(
            StoredProcedures.UPSERT_CHATBOT_FEEDBACK,
            feedback_params(
                existing_feedback_id(existing), history_id, payload.rating, payload.comment,
                current_user.email,
            ),
        )
        return {"message": "Feedback recorded."}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to record feedback for chat turn {history_id}.")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to record feedback.") from exc
