"""Plumbing shared by the chat routes: timing, background saves, error mapping, paging, the trace write.

Kept out of routers/chat.py so that file reads as the list of endpoints. None of this knows a URL."""
import asyncio
import logging
import time
from collections.abc import Awaitable
from typing import Any

from fastapi import HTTPException, status

from app.config import Settings
from app.controllers.chat_controller import ChatSessionController, recent_questions
from app.logic.chat_events import finish_timings
from app.logic.chat_records import page_of
from app.logic.trace import outcome_of, trace_params
from app.logic.user_context import user_context
from app.models.auth import CurrentUser
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures

logger = logging.getLogger(__name__)


def ms_since(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


async def timed(awaitable: Awaitable) -> tuple[int, Any]:
    """(milliseconds it took, its result)."""
    start = time.perf_counter()
    result = await awaitable
    return ms_since(start), result


def turn_timings(result_timings: dict, request_start: float, prep_ms: int, linkify_ms: int, save_ms: int | None = None) -> dict:
    """The pipeline's timings plus what the router spent around it, logged as one line per turn so a
    slow answer can be traced without a debugger. The browser adds network and rendering on top."""
    timings = finish_timings(result_timings, prep_ms, linkify_ms, save_ms, ms_since(request_start))
    logger.info("Chat timing: %s", " ".join(f"{k}={v}" for k, v in timings.items()))
    return timings


# Saves that outlive the response. A task nobody references can be garbage
# collected mid-run, so they are held here until they finish.
background_saves: set = set()


def save_in_background(coro) -> asyncio.Task:
    """Runs `coro` to completion without making the caller wait for it."""
    task = asyncio.create_task(coro)
    background_saves.add(task)
    task.add_done_callback(background_saves.discard)
    return task


async def settled(task: asyncio.Task) -> Any | None:
    """The task's result, or None if it failed - for error handling, where a second failure must not mask the first."""
    try:
        return await task
    except Exception:
        return None


async def save_answer(sessions: ChatSessionController, history_id, conversation_id, answer, user_id, collections) -> None:
    try:
        await sessions.record_success(history_id, conversation_id, answer, user_id, collections)
    except Exception as save_error:
        logger.exception("Saving the answer to chat history failed.")
        await sessions.record_error(history_id, conversation_id, str(save_error), user_id, collections)


async def save_trace(db_service: DatabaseService, settings: Settings, history_id, result, total_ms: int) -> None:
    """Stores what the turn did (app.logic.trace). Bookkeeping only: a failure is logged, never raised."""
    if not settings.TRACE_ENABLED or not history_id:
        return
    try:
        await db_service.call_procedure(
            StoredProcedures.UPSERT_CHATBOT_TRACE,
            trace_params(
                history_id, outcome_of(result.intent, result.no_evidence), settings, total_ms,
                result.trace.get("top_score"), result.trace,
            ),
        )
    except Exception:
        logger.warning("Could not store the trace for history %s.", history_id, exc_info=True)


def error_detail(e: Exception) -> str:
    return e.detail if isinstance(e, HTTPException) else f"Internal server error: {e}"


def http_error(e: Exception) -> HTTPException:
    """The HTTP error a failed POST /chat is reported as."""
    if isinstance(e, HTTPException):
        return HTTPException(status_code=e.status_code, detail=e.detail)
    if isinstance(e, ValueError):
        return HTTPException(status_code=422, detail=f"Value error during processing: {e}")
    return HTTPException(status_code=500, detail=error_detail(e))


async def list_page(db_service: DatabaseService, procedure: str, params: list, page_number: int, page_size: int, model, what: str):
    """One page of a filter_* procedure as its paginated response model."""
    try:
        rows, total = page_of(await db_service.call_procedure(procedure, params))
        return model(total_records=total, page_number=page_number, page_size=page_size, data=rows)
    except Exception as exc:
        logger.exception("Unexpected error fetching %s.", what)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "An internal server error occurred.") from exc


async def require_owned_session(
    db_service: DatabaseService, session_id: str, user_id: str,
) -> None:
    if await ChatSessionController(db_service).get_owned_session(session_id, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session {session_id} not found.")


async def context_for(
    current_user: CurrentUser, db_service: DatabaseService, settings: Settings,
) -> dict | None:
    """What the prompts are told about the caller (app.logic.user_context.user_context). display_name
    and preferences are already on `current_user` - get_current_user fetched them - so the only
    database read left is the caller's recent questions, and only when that feature is on."""
    questions = await recent_questions(db_service, settings, current_user.email)
    return user_context(current_user.display_name, current_user.preferences, questions)
