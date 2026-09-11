"""
Concrete implementation of the BaseLLMService for Ollama.
"""

import json
import logging
import asyncio
from typing import Any, Dict, Optional
import aiohttp

from app.models.error_models import LLMInteractionError
from app.services.llm_service import BaseLLMService # Import the ABC
from app.utils.general_utils import clean_and_load_json_string, sanatize_llm_response_util

logger = logging.getLogger(__name__)


class OllamaService(BaseLLMService): # Inherit from the base class
    """
    A concrete implementation of the BaseLLMService for Ollama.
    """
    def __init__(
            self,
            ollama_uri: str,
            model_name: str,
            temperature: float = 0.5,
            top_p: float = 0.9,
            request_timeout: int = 120
    ):
        if not ollama_uri:
            raise ValueError("Ollama URI must be a non-empty string.")
        if not model_name:
            raise ValueError("model_name cannot be empty.")

        self._ollama_uri = ollama_uri
        self.model_name = model_name
        self.temperature = temperature
        self.top_p = top_p
        self._request_timeout = request_timeout

        logger.info(f"OllamaService initialized for model '{self.model_name}' at URI '{self._ollama_uri}'")

    async def generate_response(
            self,
            prompt: str,
            model_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate a response from the Ollama LLM based on the provided prompt.
        """
        if not prompt:
            raise ValueError("Prompt cannot be empty.")

        # Use provided params or fall back to the service's defaults
        params = model_params or {}
        model_name = params.get("model_name", self.model_name)
        temperature = params.get("temperature", self.temperature)
        top_p = params.get("top_p", self.top_p)

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
            }
        }

        logger.info(f"Sending request to Ollama model '{model_name}'...")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._request_timeout)) as session:
                api_endpoint = f"{self._ollama_uri.rstrip('/')}/api/chat"
                async with session.post(api_endpoint, json=payload) as response:
                    response_text = await response.text()
                    logger.debug(f"Received raw Ollama response (status {response.status}): {response_text[:500]}...")

                    if not response.ok:
                        logger.error(f"Ollama API request failed. Status: {response.status}, Response: {response_text}")
                        raise LLMInteractionError(f"Ollama API request failed with status {response.status}",
                                                  status_code=response.status, response_body=response_text)
                    
                    try:
                        ollama_response_data = json.loads(response_text)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode Ollama API response as JSON: {e}")
                        raise LLMInteractionError(f"Invalid JSON in Ollama response: {e}", response_body=response_text) from e

                    message_content = ollama_response_data.get("message", {}).get("content")
                    if not message_content or not isinstance(message_content, str):
                        logger.error(f"Could not find valid 'message.content' in Ollama response: {ollama_response_data}")
                        raise ValueError("LLM response missing expected 'message.content' string.")

                    sanitized_string = sanatize_llm_response_util(message_content)
                    json_data = clean_and_load_json_string(sanitized_string)

                    logger.info(f"Successfully parsed JSON response from Ollama model '{model_name}'.")
                    return json_data

        except asyncio.TimeoutError as e:
            logger.error(
                "Timeout interacting with Ollama API at '%s' using model '%s' after %ss.",
                f"{self._ollama_uri.rstrip('/')}/api/chat",
                model_name,
                self._request_timeout,
                exc_info=True,
            )
            raise LLMInteractionError(
                f"Timeout during Ollama request after {self._request_timeout}s",
                response_body=f"endpoint={self._ollama_uri.rstrip('/')}/api/chat; model={model_name}"
            ) from e
        except aiohttp.ClientError as e:
            logger.error(f"Network error interacting with Ollama API: {e}", exc_info=True)
            raise LLMInteractionError(f"HTTP Client error during Ollama request: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred during Ollama interaction: {e}", exc_info=True)
            raise LLMInteractionError(f"An unexpected error occurred: {e}") from e