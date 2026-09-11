import logging
from app.config import Settings
from app.services.llm_service import BaseLLMService


async def llm_handler(
    llm_service: BaseLLMService,
    query: str,
    model_params: dict
):
    """
    Handles a request to generate a response from the LLM service.

    Args:
        llm_service (BaseLLMService): An initialized instance of an LLM service.
        query (str): The input prompt or query for the LLM.
        model_params (dict): A dictionary of model parameters for the LLM.

    Returns:
        dict: The generated response from the LLM service.
    """
    return await llm_service.generate_response(query, model_params)