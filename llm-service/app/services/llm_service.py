"""
Module for the Abstract Base Class for Large Language Model (LLM) services.

This module defines the abstract interface that all concrete LLM service
implementations must follow.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

class BaseLLMService(ABC):
    """
    Abstract Base Class for an LLM service.

    Defines the common interface for interacting with different LLM providers.
    This allows the application to generate responses from various LLMs
    through a unified API.
    """

    @abstractmethod
    async def generate_response(
        self, 
        prompt: str, 
        model_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generates a response from the configured LLM.

        Args:
            prompt (str): The input prompt to be sent to the language model.
            model_params (Optional[Dict[str, Any]]): A dictionary of model parameters 
                                                      to override defaults for this specific call.

        Returns:
            Dict[str, Any]: A dictionary parsed from the LLM's JSON response.
        
        Raises:
            LLMInteractionError: If there's an issue communicating with the LLM API
                                 or processing its response.
        """
        pass