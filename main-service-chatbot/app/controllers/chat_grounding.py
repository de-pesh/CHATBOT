"""Checks whether an answer's citations are actually supported by what they cite, and records
the verdict on the chat turn's history row.

Called from app/routers/chat.py as a fire-and-forget background task, the same way the answer
itself is saved to history - after the answer has already reached the browser, never before or
during. A failure, a slow LLM call, or GROUNDING_CHECK_ENABLED being off (the default) all just
mean the turn goes unrated; none of them can affect the chat turn the user actually saw.
"""
import logging

from app.config import Settings
from app.controllers.chat_steps import helper_json
from app.logic.grounding import (
    evaluate_answer_grounding,
    grounding_update_params,
    grounding_verdict_of,
    usable_grounding,
)
from app.logic.prompts import fetch_grounding_prompt
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.llm.chat_llm import ChatLLM

logger = logging.getLogger(__name__)

GROUNDING_CHECK_ACTOR = "grounding-check"


async def check_grounding(
    db_service: DatabaseService, settings: Settings, chat_llm: ChatLLM,
    history_id: int | None, response: str, passages: dict[int, str],
) -> None:
    """Best-effort: skipped when the feature is off, when nothing was retrieved (a greeting or
    catalog-overview reply diverted before any search ran - see chat_controller.process_chat_stream),
    or when history_id was never obtained (the history insert itself failed)."""
    if not settings.GROUNDING_CHECK_ENABLED or not passages or not history_id:
        return

    verdict, detail = None, None
    try:
        messages = fetch_grounding_prompt(response, passages, settings.GROUNDING_PASSAGE_CHARS)
        data = await helper_json(chat_llm, messages, usable_grounding, "grounding")
        if data is not None:
            verdict, detail = grounding_verdict_of(data)
    except Exception:
        logger.warning("LLM grounding check failed for turn %s; falling back to local check.", history_id, exc_info=True)

    if verdict is None:
        # Fast local lexical fallback: word-overlap between the answer and the cited passages.
        data = evaluate_answer_grounding(response, passages)
        if usable_grounding(data):
            verdict, detail = grounding_verdict_of(data)

    if verdict is None:
        logger.warning("Grounding check produced no verdict for turn %s.", history_id)
        return

    try:
        await db_service.call_procedure(
            StoredProcedures.UPDATE_CHATBOT_HISTORY_GROUNDING,
            grounding_update_params(history_id, verdict, detail, GROUNDING_CHECK_ACTOR),
        )
    except Exception:
        logger.warning("Could not save grounding verdict for turn %s.", history_id, exc_info=True)
