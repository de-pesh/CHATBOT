"""
This module handles the core logic for processing chat interactions.
It includes rephrasing user queries, retrieving relevant context, and generating
final model responses using the LLM.
"""
import asyncio
import json
import re
import logging
from typing import Optional
from app.services.ollama_service import OllamaService
from app.helpers.prompt_helper import fetch_new_query_prompt, fetch_final_prompt, format_translate_prompt
from app.helpers.context_helper import fetch_context, fetch_assessments_by_framework

from app.config import Settings
from app.models.chat_model import ChatResult

logger = logging.getLogger(__name__)

# Maximum number of peer assessments (same framework) to also search for cross-framework context.
MAX_PEER_ASSESSMENTS = 5


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
    - assessment_id, intent=assessment -> that assessment's collection, plus (optionally)
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


async def process_chat(
        settings: Settings,
        chat_history:list, 
        query:str, 
        ollama_client:OllamaService,
        assessment_id: Optional[int] = None,
        framework_id: Optional[int] = None,
    ):
    """
    Process a chat interaction by rephrasing the query, fetching relevant context, 
    and generating a final response using the LLM.

    Args:
        chat_history (list): A list of previous chat messages (str) between user and system.
        query (str): The latest user query to process.

    Returns:
        str: The generated response from the LLM based on the rephrased query and retrieved context.

    Raises:
        json.JSONDecodeError: If the rephrased questions cannot be parsed as valid JSON.
        Exception: For unexpected errors during context fetching or LLM calls.
    """
    rephrase_query_message = []
    rephrase_query_message = fetch_new_query_prompt(
        chat_history, query, assessment_active=assessment_id is not None
    )
    logger.info("Calling LLM to rephrase the query")
    rephrased_questions = await ollama_client.invoke_llm(rephrase_query_message)
    logger.info(f"Rephrased Questions: {rephrased_questions}")
    # Different LLM backends format this differently: Ollama tends to wrap JSON in
    # ```json ... ``` fences, while Azure OpenAI's json_object mode returns bare
    # JSON with no fences. Try bare JSON first, then fall back to fence-extraction.
    pattern = r'```json\s*({.*?})\s*```'
    data = None
    try:
        data = json.loads(rephrased_questions)
    except (json.JSONDecodeError, TypeError):
        match = re.search(pattern, rephrased_questions, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                data = None
    # Default intent: when an assessment is active, assume an assessment question
    # unless the model clearly flags it as a general policy question.
    intent = "assessment" if assessment_id is not None else "policy"
    if data is not None:
        questions_list = data.get("new_questions", [])
        if assessment_id is not None:
            parsed_intent = str(data.get("intent", "")).strip().lower()
            if parsed_intent in ("assessment", "policy"):
                intent = parsed_intent
    else:
        questions_list = [query.get("content", "")] if isinstance(query, dict) else [query]
    logger.debug(f"Extracted Questions: {questions_list}")
    if isinstance(questions_list, str):
        questions_list = [questions_list]
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
    final_messages = []
    assessment_mode = assessment_id is not None and intent == "assessment"
    final_messages = chat_history + fetch_final_prompt(
        all_results, question=query, assessment_mode=assessment_mode
    )
    logger.info(f"Final Messages: {final_messages}")
    logger.info("Calling LLM to fetch final response")
    response = await ollama_client.invoke_llm(final_messages)

    # Language Detection & Translation Check
    query_text = query.get("content", "") if isinstance(query, dict) else str(query)
    final_response = response

    try:
        logger.info("Performing language detection and translation check")
        language_messages = format_translate_prompt(
            query_text=query_text,
            answer_text=response
        )
        language_response = await ollama_client.invoke_llm(language_messages)

        lang_data = None
        try:
            lang_data = json.loads(language_response)
        except (json.JSONDecodeError, TypeError):
            match = re.search(pattern, language_response, re.DOTALL)
            if match:
                try:
                    lang_data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    lang_data = None

        if lang_data and "final_answer" in lang_data:
            final_response = lang_data["final_answer"]
            logger.info("Language check completed successfully")
    except Exception as e:
        logger.warning(f"Language check failed, using original response: {e}")

    return ChatResult(response=final_response, intent=intent, collections=collections)
