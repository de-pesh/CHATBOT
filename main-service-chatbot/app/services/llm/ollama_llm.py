"""An Ollama server as the chat backend (LLM_TYPE=ollama).

One of the vendor modules behind services/llm/chat_llm_factory.py; see that file's
docstring for the four functions every one of them exposes.

This is the only backend with no credentials - a model name and a URL are the
whole configuration - and the only one that cannot take the shared HTTP client,
which is why endpoint() returns None. See the notes on both below.
"""
import logging

import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings

logger = logging.getLogger(__name__)


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work."""
    return ("MODEL_NAME", "LLM_API_URL")


def endpoint(settings: Settings) -> str | None:
    """None: there is nothing here worth keeping warm.

    Connection warming exists to hold TLS connections open to a remote endpoint
    across idle turns (see lifespan_manager._llm_http_client). ChatOllama takes
    no shared client, so the warmer could not reach the connections it actually
    uses - and Ollama is normally local, where the handshake it would save costs
    nothing. Returning None keeps the warmer switched off.
    """
    return None


def model_name(settings: Settings) -> str:
    """What the startup log calls the model."""
    return settings.MODEL_NAME or "?"


def build(
    settings: Settings,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
    """The chat client. `model` overrides the configured model name.

    `http_client` is accepted so every vendor module has the same signature, and
    ignored: ChatOllama has no parameter for one.
    """
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model or settings.MODEL_NAME,
        temperature=settings.TEMPERATURE,
        base_url=settings.LLM_API_URL.replace("/api/chat", ""),
    )
