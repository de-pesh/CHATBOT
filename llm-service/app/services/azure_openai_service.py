"""
Concrete implementation of the BaseLLMService for Azure OpenAI.
"""

import logging
from typing import Any, Dict, Optional

from openai import AsyncAzureOpenAI, APIError, APITimeoutError

from app.models.error_models import LLMInteractionError
from app.services.llm_service import BaseLLMService
from app.utils.general_utils import clean_and_load_json_string, sanatize_llm_response_util

logger = logging.getLogger(__name__)


class AzureOpenAIService(BaseLLMService):
    """
    A concrete implementation of the BaseLLMService for Azure OpenAI.
    """
    def __init__(
            self,
            api_key: str,
            azure_endpoint: str,
            api_version: str,
            model_name: str,
            temperature: float = 0.5,
            request_timeout: int = 120
    ):
        if not api_key:
            raise ValueError("Azure OpenAI API key must be a non-empty string.")
        if not azure_endpoint:
            raise ValueError("Azure OpenAI endpoint must be a non-empty string.")
        if not model_name:
            raise ValueError("model_name (deployment name) cannot be empty.")

        self.model_name = model_name
        self.temperature = temperature
        self._client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            timeout=request_timeout,
        )

        logger.info(f"AzureOpenAIService initialized for deployment '{self.model_name}' at endpoint '{azure_endpoint}'")

    async def generate_response(
            self,
            prompt: str,
            model_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate a response from the Azure OpenAI LLM based on the provided prompt.
        """
        if not prompt:
            raise ValueError("Prompt cannot be empty.")

        params = model_params or {}
        model_name = params.get("model_name", self.model_name)
        temperature = params.get("temperature", self.temperature)

        logger.info(f"Sending request to Azure OpenAI deployment '{model_name}'...")
        try:
            kwargs: Dict[str, Any] = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
            # Reasoning-family deployments (e.g. gpt-5-mini) reject a non-default temperature.
            if temperature is not None:
                kwargs["temperature"] = temperature

            try:
                completion = await self._client.chat.completions.create(**kwargs)
            except APIError as e:
                if "temperature" in str(e).lower():
                    kwargs.pop("temperature", None)
                    completion = await self._client.chat.completions.create(**kwargs)
                else:
                    raise

            message_content = completion.choices[0].message.content
            if not message_content or not isinstance(message_content, str):
                logger.error(f"Could not find valid message content in Azure OpenAI response: {completion}")
                raise ValueError("LLM response missing expected message content string.")

            sanitized_string = sanatize_llm_response_util(message_content)
            json_data = clean_and_load_json_string(sanitized_string)

            logger.info(f"Successfully parsed JSON response from Azure OpenAI deployment '{model_name}'.")
            return json_data

        except APITimeoutError as e:
            logger.error(f"Timeout interacting with Azure OpenAI deployment '{model_name}'.", exc_info=True)
            raise LLMInteractionError(f"Timeout during Azure OpenAI request: {e}") from e
        except APIError as e:
            logger.error(f"Azure OpenAI API error: {e}", exc_info=True)
            raise LLMInteractionError(f"Azure OpenAI API request failed: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred during Azure OpenAI interaction: {e}", exc_info=True)
            raise LLMInteractionError(f"An unexpected error occurred: {e}") from e
