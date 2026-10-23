"""A LiteLLM proxy as the chat backend (LLM_TYPE=litellm).

One of the vendor modules behind services/llm/chat_llm_factory.py; see that file's
docstring for the four functions every one of them exposes. The proxy speaks the
OpenAI API, so the client is a plain ChatOpenAI pointed at its base URL - which
is also why whatever the proxy routes to (Azure, Bedrock, a local model) is its
business, not this service's.
"""
import logging

import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings

logger = logging.getLogger(__name__)


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work."""
    return ("LITELLM_BASE_URL", "LITELLM_API_KEY", "LITELLM_MODEL_NAME")


def endpoint(settings: Settings) -> str | None:
    """The URL model calls go to, for the connection warmer."""
    return settings.LITELLM_BASE_URL


def model_name(settings: Settings) -> str:
    """What the startup log calls the model - here, the alias on the proxy."""
    return settings.LITELLM_MODEL_NAME or "?"


def build(
    settings: Settings,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
    """The chat client. `model` overrides the configured proxy alias."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=settings.LITELLM_BASE_URL,
        api_key=settings.LITELLM_API_KEY.get_secret_value(),
        model=model or settings.LITELLM_MODEL_NAME,
        temperature=settings.TEMPERATURE,
        http_async_client=http_client,
    )
