"""
This module handles the core logic for processing chat interactions.
It includes rephrasing user queries, retrieving relevant context, and generating
final model responses using the LLM.

`process_chat_stream` is the single implementation of the pipeline; it yields
progress events as it goes so the HTTP layer can stream the answer to the
browser. `process_chat` simply drains it and hands back the final result, so
the streaming and non-streaming endpoints can never drift apart.
"""
import asyncio
import json
import re
import logging
from typing import AsyncIterator, Optional
from app.services.ollama_service import OllamaService
from app.helpers.prompt_helper import fetch_new_query_prompt, fetch_final_prompt, format_translate_prompt
from app.helpers.context_helper import fetch_context, fetch_assessments_by_framework

from app.config import Settings
from app.models.chat import ChatResult

logger = logging.getLogger(__name__)

# Maximum number of peer assessments (same framework) to also search for cross-framework context.
MAX_PEER_ASSESSMENTS = 5

# Both the rephrase and the translate step ask for JSON. Ollama tends to wrap it
# in ```json ... ``` fences; Azure OpenAI returns it bare.
_JSON_FENCE = r'```json\s*({.*?})\s*```'


def _parse_json_response(text: str) -> Optional[dict]:
    """Parses a model reply that is meant to be JSON, fenced or bare."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(_JSON_FENCE, text or "", re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


async def _resolve_collections(
        settings: Settings,
        assessment_id: Optional[int],
        framework_id: Optional[int],
        intent: str = "assessment",
    ) -> list:
    """Determines which vector collections to search for a chat request.

    - No assessment_id              -> the default policy knowledge base (unchanged).
    - assessment_id, intent=policy  -> the default policy knowledge base, so general
                                       framework/policy questions are answered from the
                                       policy corpus even while an assessment is open.
    - assessment_id, intent=assessment -> that assessment collection, plus (optionally)
                                       peer assessments under the same framework.
    """
    if assessment_id is None or intent == "policy":
        return [settings.MONGODB_COLLECTION_NAME]

    collections = [f"assessment_{assessment_id}"]

    main_service_url = getattr(settings, "MAIN_SERVICE_URL", None)
    if framework_id is not None and main_service_url:
        peer_ids = await fetch_assessments_by_framework(
            main_service_url=main_service_url,
            framework_id=framework_id,
            exclude_assessment_id=assessment_id,
            limit=MAX_PEER_ASSESSMENTS,
        )
        for peer_id in peer_ids:
            collection_name = f"assessment_{peer_id}"
            if collection_name not in collections:
                collections.append(collection_name)

    return collections


async def process_chat_stream(
        settings: Settings,
        chat_history: list,
        query,
        ollama_client: OllamaService,
        assessment_id: Optional[int] = None,
        framework_id: Optional[int] = None,
    ) -> AsyncIterator[dict]:
    """Runs a chat turn, yielding progress events as the work happens.

    Yields dicts of one of these shapes:
      {"type": "stage",  "stage": "rephrasing" | "retrieving" | "generating" | "finalising"}
      {"type": "delta",  "text": "..."}         one chunk of the answer as it is generated
      {"type": "result", "result": ChatResult}  emitted exactly once, last

    The answer is streamed as it comes off the model, but two steps still run
    afterwards: the language check may replace the whole answer with a
    translation, and the caller linkifies citations. So the text assembled from
    the deltas is a live preview and `result.response` is authoritative.

    Args:
        chat_history (list): Previous chat messages between user and system.
        query: The latest user query to process.

    Raises:
        Exception: For unexpected errors during context fetching or LLM calls.
    """
    yield {"type": "stage", "stage": "rephrasing"}
    rephrase_query_message = fetch_new_query_prompt(
        chat_history, query, assessment_active=assessment_id is not None
    )
    logger.info("Calling LLM to rephrase the query")
    rephrased_questions = await ollama_client.invoke_llm(rephrase_query_message)
    logger.info(f"Rephrased Questions: {rephrased_questions}")
    data = _parse_json_response(rephrased_questions)

    # Default intent: when an assessment is active, assume an assessment question
    # unless the model clearly flags it as a general policy question.
    intent = "assessment" if assessment_id is not None else "policy"
    # Unknown language means "run the translate check anyway", so a model that
    # ignores the new field behaves exactly as before.
    query_language = None
    if data is not None:
        questions_list = data.get("new_questions", [])
        query_language = str(data.get("language", "")).strip().lower() or None
        if assessment_id is not None:
            parsed_intent = str(data.get("intent", "")).strip().lower()
            if parsed_intent in ("assessment", "policy"):
                intent = parsed_intent
    else:
        questions_list = [query.get("content", "")] if isinstance(query, dict) else [query]
    logger.debug(f"Extracted Questions: {questions_list}")
    if isinstance(questions_list, str):
        questions_list = [questions_list]

    yield {"type": "stage", "stage": "retrieving"}
    collections = await _resolve_collections(settings, assessment_id, framework_id, intent)
    logger.info("Resolved intent=%s; searching collection(s): %s", intent, collections)
    search_tasks = [
        fetch_context(
            collection_name=collection_name,
            search_query=q,
            rpc_server_url=settings.RPC_SERVER,
        )
        for q in questions_list
        for collection_name in collections
    ]

    results_list = await asyncio.gather(*search_tasks, return_exceptions=True)
    all_results = []
    for result in results_list:
        if isinstance(result, Exception):
            logger.error("Context fetch failed for one query: %s", result, exc_info=result)
            continue
        if result is None:
            logger.warning("Context fetch returned no results for one query.")
            continue
        all_results.extend(result)

    assessment_mode = assessment_id is not None and intent == "assessment"
    final_messages = chat_history + fetch_final_prompt(
        all_results, question=query, assessment_mode=assessment_mode
    )
    logger.info(f"Final Messages: {final_messages}")
    logger.info("Calling LLM to fetch final response")

    yield {"type": "stage", "stage": "generating"}
    chunks = []
    async for token in ollama_client.stream_llm(final_messages):
        chunks.append(token)
        yield {"type": "delta", "text": token}
    response = "".join(chunks)

    # Language Detection & Translation Check
    query_text = query.get("content", "") if isinstance(query, dict) else str(query)
    final_response = response

    # The rephrase step above already told us the query's language. When it is
    # English there is nothing to translate, so the whole round trip is skipped
    # - it used to leave a finished answer sitting on screen for several
    # seconds while the model was asked to hand it back unchanged.
    if query_language == "english":
        logger.info("Query is English; skipping the translation round trip.")
        yield {
            "type": "result",
            "result": ChatResult(response=final_response, intent=intent, collections=collections),
        }
        return

    yield {"type": "stage", "stage": "finalising"}
    try:
        logger.info("Performing language detection and translation check")
        language_messages = format_translate_prompt(
            query_text=query_text,
            answer_text=response
        )
        language_response = await ollama_client.invoke_llm(language_messages)
        lang_data = _parse_json_response(language_response)

        if lang_data and "final_answer" in lang_data:
            final_response = lang_data["final_answer"]
            logger.info("Language check completed successfully")
    except Exception as e:
        logger.warning(f"Language check failed, using original response: {e}")

    yield {
        "type": "result",
        "result": ChatResult(response=final_response, intent=intent, collections=collections),
    }


async def process_chat(
        settings: Settings,
        chat_history:list,
        query:str,
        ollama_client:OllamaService,
        assessment_id: Optional[int] = None,
        framework_id: Optional[int] = None,
    ) -> ChatResult:
    """
    Process a chat interaction by rephrasing the query, fetching relevant context,
    and generating a final response using the LLM.

    Drains `process_chat_stream` and discards the progress events, so callers
    that cannot stream get exactly the same answer as those that can.

    Args:
        chat_history (list): A list of previous chat messages (str) between user and system.
        query (str): The latest user query to process.

    Returns:
        ChatResult: The generated response plus the retrieval routing metadata.

    Raises:
        Exception: For unexpected errors during context fetching or LLM calls.
    """
    result = None
    async for event in process_chat_stream(
        settings=settings,
        chat_history=chat_history,
        query=query,
        ollama_client=ollama_client,
        assessment_id=assessment_id,
        framework_id=framework_id,
    ):
        if event["type"] == "result":
            result = event["result"]
    if result is None:
        # Only reachable if the generator is changed to return without a result.
        raise RuntimeError("Chat pipeline produced no result.")
    return result
