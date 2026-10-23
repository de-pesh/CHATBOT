"""The chat turn - rephrase, retrieve, rerank, answer, translate - and the session/history bookkeeping the chat router calls.

`process_chat_stream` is the single implementation of the pipeline: it reads as
the sequence of steps, each of which is a method of `ChatTurn`
(controllers/chat_steps.py) and each decision between them a pure function in
`app/logic`. It yields progress events as it goes so the HTTP layer can stream
the answer to the browser; `process_chat` returns its final result, so the
streaming and non-streaming endpoints can never drift apart.

`ChatSessionController` holds the session/history CRUD that POST /chat and
POST /chat/stream both need - open a session if there is not one, insert the
prompt, then update that row with either the answer or the error.
"""
import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import aclosing

from app.config import Settings
from app.controllers.chat_steps import ChatTurn
from app.logic.chat_events import (
    STAGE_FINALISING,
    STAGE_GENERATING,
    STAGE_REPHRASING,
    STAGE_RERANKING,
    STAGE_RETRIEVING,
    delta_event,
    sources_event,
    stage_event,
)
from app.logic.chat_records import (
    history_error_params,
    history_success_params,
    inserted_id,
    new_history_params,
    new_session_params,
    owned_history_params,
    owned_session_params,
)
from app.models.auth import CurrentUser
from app.logic.grounding import cited_passage_texts
from app.logic.prompts import build_sources
from app.logic.user_context import recent_questions_of, recent_questions_params
from app.models.chat import ChatResult
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.llm.chat_llm import ChatLLM
from app.services.vector.search_runtime import SearchRuntime
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class ChatSessionController:
    """Session/history bookkeeping shared by POST /chat and POST /chat/stream."""

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    async def get_owned_session(self, session_id: int, user_id: str) -> dict | None:
        """The session row if it exists, is active, and belongs to user_id - else None.

        Folding "doesn't exist" and "exists but isn't yours" into the same None
        (and therefore the same 404 at the call site) avoids confirming to a
        caller that a session id they don't own exists at all.
        """
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_CHATBOT_SESSIONS, owned_session_params(session_id, user_id),
        )
        rows = results[0] if results else []
        return rows[0] if rows else None

    async def ensure_session(
        self, conversation_id: int | None, user_id: str, prompt: str,
        current_user: "CurrentUser | None" = None,
    ) -> int:
        """Returns the conversation to log against, creating one if needed.

        An explicit conversation_id must already belong to user_id - otherwise a
        caller could read or append to someone else's history just by passing a
        guessed or incremented id.

        `current_user` is forwarded to new_session_params so entity/tenant context
        is stored on the row when TENANT_CONTEXT_ENABLED is on.
        """
        if conversation_id:
            if await self.get_owned_session(conversation_id, user_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Conversation {conversation_id} not found.",
                )
            return conversation_id

        params = new_session_params(
            prompt, user_id,
            entity_id=getattr(current_user, "entity_id", None),
            tenant_id=getattr(current_user, "tenant_id", None),
            user_id_guid=getattr(current_user, "user_id", None),
            user_type=getattr(current_user, "user_type", None),
            is_admin=getattr(current_user, "is_admin", False),
        ) if current_user is not None else new_session_params(prompt, user_id)
        result = await self.db_service.call_procedure(
            StoredProcedures.UPSERT_CHATBOT_SESSION, params
        )
        if not result or not result[0]:
            raise HTTPException(status_code=500, detail="Failed to create a new chat session.")
        return inserted_id(result)

    async def insert_prompt(self, conversation_id: int, prompt: str, user_id: str) -> int:
        """Logs the incoming question and returns the new history row id."""
        result = await self.db_service.call_procedure(
            StoredProcedures.UPSERT_CHATBOT_HISTORY, new_history_params(conversation_id, prompt, user_id),
        )
        return inserted_id(result)

    async def get_owned_history(self, history_id: int, user_id: str) -> dict | None:
        """The history row (one chat turn) if it exists and belongs to user_id - else None.

        Same "doesn't exist" / "isn't yours" fold as get_owned_session, and for the same
        reason: POST /chat/history/{id}/feedback must not let a caller rate, or discover
        anything about, a turn that is not theirs.
        """
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_CHATBOT_HISTORY, owned_history_params(history_id, user_id),
        )
        rows = results[0] if results else []
        return rows[0] if rows else None

    async def record_success(
        self, history_id: int, conversation_id: int, response: str, user_id: str, rag_collections: str | None,
    ) -> None:
        await self.db_service.call_procedure(
            StoredProcedures.UPSERT_CHATBOT_HISTORY,
            history_success_params(history_id, conversation_id, response, user_id, rag_collections),
        )

    async def record_error(
        self, history_id: int | None, conversation_id: int | None, error_message: str, user_id: str,
        rag_collections: str | None,
    ) -> None:
        """Best-effort: a failure to log the failure must not mask the original one."""
        if not (history_id and conversation_id):
            return
        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_CHATBOT_HISTORY,
                history_error_params(history_id, conversation_id, error_message, user_id, rag_collections),
            )
        except Exception as db_err:
            logger.critical(f"Failed to log error to database for history_id {history_id}: {db_err}")


async def recent_questions(
    db_service: DatabaseService, settings: Settings, email: str,
) -> list[str]:
    """The caller's newest questions across all their conversations - part of what POST /chat[/stream]
    tells the model about who is asking (app.logic.user_context.user_context). [] when the feature is
    off (USER_CONTEXT_RECENT_QUESTIONS) or the read fails; a chat turn never fails because
    personalization could not be read."""
    count = settings.USER_CONTEXT_RECENT_QUESTIONS
    if count <= 0:
        return []
    try:
        result = await db_service.call_procedure(
            StoredProcedures.FILTER_CHATBOT_HISTORY, recent_questions_params(email, count),
        )
        return recent_questions_of(result, settings.USER_CONTEXT_QUESTION_CHARS)
    except Exception:
        logger.warning("Could not read the user's recent questions; leaving them out.", exc_info=True)
        return []


def _verdict(intent_task: asyncio.Task | None) -> tuple | None:
    """The intent check's (intent, reply) if it has finished and says to skip the search, else None."""
    return intent_task.result() if intent_task is not None and intent_task.done() else None


async def process_chat_stream(
        settings: Settings,
        chat_history: list,
        query,
        chat_llm: ChatLLM,
        search: SearchRuntime,
        catalog: Awaitable[tuple] | None = None,
        local_hour: int | None = None,
        user_context: dict | None = None,
    ) -> AsyncIterator[dict]:
    """Runs a chat turn, yielding progress events as the work happens.

    Yields dicts of one of these shapes (built in app/logic/chat_events.py):
      {"type": "stage",  "stage": "rephrasing" | "retrieving" | "reranking" | "generating" | "finalising"}
      {"type": "delta",  "text": "..."}         one chunk of the answer as it is generated
      {"type": "sources", "sources": [...]}     the numbered passages, before generation starts
      {"type": "result", "result": ChatResult}  emitted exactly once, last

    The answer is streamed as it comes off the model, but two steps still run
    afterwards: the language check may replace the whole answer with a
    translation, and the caller linkifies citations. So the text assembled from
    the deltas is a live preview and `result.response` is authoritative.

    `catalog` is an awaitable of (catalog text, document names) - see
    KbCatalog.render. With KB_OVERVIEW_ENABLED off it goes into no prompt at all;
    its names are used only to check the intent step's reply. `local_hour` is the
    user's local hour (0-23); a greeting reply opens with the matching "Good Morning/...".
    `user_context` (user_context.user_context) is who is asking - name, preferences, recent
    questions - and is shown to the model in the answer and greeting prompts; None for no one.
    """
    turn = ChatTurn(settings, chat_llm, chat_history, query, catalog, local_hour, user_context, search)

    # A bare "hi"/"hey"/"hello" is answered here, before anything else runs: no model call, so
    # it is instant and unaffected if the intent-classification call below is ever unavailable
    # (see ChatTurn.simple_greeting). Gated the same way that call is (INTENT_ROUTING_ENABLED):
    # off means every message goes to the full pipeline, this bare-greeting shortcut included.
    greeting = turn.simple_greeting() if turn.offers_intent_check() else None
    if greeting is not None:
        for event in turn.diversion(greeting):
            yield event
        return

    yield stage_event(STAGE_REPHRASING)
    # The rewrite and the intent check start together. A greeting is settled by the check
    # alone and does not wait for a rewrite it does not need; a real question carries on as
    # soon as the rewrite is in, and the check finishes in the background of the search and
    # rerank. Nothing is generated until it has answered (the gate below), so a question is
    # never diverted late and a greeting is never answered from the knowledge base.
    rewrite_task = asyncio.create_task(turn.rewrite())
    intent_task = asyncio.create_task(turn.check_intent()) if turn.offers_intent_check() else None
    search_task = None

    try:
        if intent_task is not None:
            await asyncio.wait({intent_task, rewrite_task}, return_when=asyncio.FIRST_COMPLETED)
            if diverted := _verdict(intent_task):
                for event in turn.diversion(diverted):
                    yield event
                return
        questions, language, standalone = await rewrite_task
        turn.timer.lap("rephrase_ms")

        yield stage_event(STAGE_RETRIEVING)
        # The search runs beside the intent check rather than ahead of it: when the rewrite wins
        # the race, a greeting must not sit through a search it does not need (seconds, on a cold
        # index) just because that search had already started.
        search_task = asyncio.create_task(turn.retrieve(questions))
        if intent_task is not None:
            await asyncio.wait({search_task, intent_task}, return_when=asyncio.FIRST_COMPLETED)
            if diverted := _verdict(intent_task):
                for event in turn.diversion(diverted):
                    yield event
                return
        hits = await search_task

        if diverted := _verdict(intent_task):  # already small talk: skip the rerank call
            for event in turn.diversion(diverted):
                yield event
            return

        yield stage_event(STAGE_RERANKING)
        passages = await turn.rerank(hits)

        if intent_task is not None:
            # The gate: it has had the whole rewrite, search and rerank to finish, so this
            # normally costs nothing; intent_wait_ms shows when it did.
            with turn.timer.gate("intent_wait_ms"):
                diverted = await intent_task
            if diverted:
                for event in turn.diversion(diverted):
                    yield event
                return
        if not passages and settings.NO_EVIDENCE_GATE_ENABLED:
            # The knowledge base was searched and has nothing usable: say so, rather than asking the
            # model to write an answer from no passages.
            reply = await turn.no_evidence_reply(language)
            for event in turn.no_evidence(reply):
                yield event
            return
        catalog_names = await turn.catalog_names()
    finally:
        # Reached on the diverted returns too, and if the client hangs up mid-turn.
        for task in (rewrite_task, intent_task, search_task):
            if task is not None and not task.done():
                task.cancel()

    # Announced before generation starts, so the caller can get the cited documents'
    # download links ready while the answer is still being written.
    sources = build_sources(passages, show_versions=turn.version_scope.wants_older_versions)
    yield sources_event(sources)
    yield stage_event(STAGE_GENERATING)
    messages = turn.answer_messages(passages, catalog_names, language, standalone)
    tokens = []
    async for token in turn.stream_answer(messages):
        tokens.append(token)
        yield delta_event(token)
    response = await turn.in_query_script(messages, language, "".join(tokens))
    turn.timer.lap("generate_ms")

    # For the grounding check, which runs after the answer is sent (app/controllers/chat_grounding.py)
    # and so needs the passages' own text, not just the citation labels `sources` carries.
    cited_passages = cited_passage_texts(passages)

    if not turn.needs_translation(language, response):
        yield turn.finished(response, sources, cited_passages)
        return
    yield stage_event(STAGE_FINALISING)
    yield turn.finished(await turn.translate(response, language), sources, cited_passages)


async def process_chat(
        settings: Settings,
        chat_history: list,
        query: str,
        chat_llm: ChatLLM,
        search: SearchRuntime,
        catalog: Awaitable[tuple] | None = None,
        local_hour: int | None = None,
        user_context: dict | None = None,
    ) -> ChatResult:
    """Runs a chat turn to its end and returns the result, discarding the progress
    events, so callers that cannot stream get exactly the same answer as those that can."""
    async with aclosing(process_chat_stream(
        settings=settings,
        chat_history=chat_history,
        query=query,
        chat_llm=chat_llm,
        search=search,
        catalog=catalog,
        local_hour=local_hour,
        user_context=user_context,
    )) as events:
        async for event in events:
            if event["type"] == "result":
                return event["result"]
    raise RuntimeError("Chat pipeline produced no result.")
