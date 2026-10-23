"""Azure OpenAI as the chat backend (LLM_TYPE=azure).

One of the vendor modules behind services/llm/chat_llm_factory.py; see that file's
docstring for the four functions every one of them exposes. The langchain_openai
import sits inside build() so an install running a different backend never has to
import it.
"""
import logging

import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings

logger = logging.getLogger(__name__)


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work.

    AZURE_OPENAI_API_VERSION is deliberately absent: it has a non-None default,
    so it is never missing and naming it here would only add noise to the error.
    """
    return ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT_NAME")


def endpoint(settings: Settings) -> str | None:
    """The URL model calls go to, for the connection warmer."""
    return settings.AZURE_OPENAI_ENDPOINT


def model_name(settings: Settings) -> str:
    """What the startup log calls the model - for Azure, the deployment name."""
    return settings.AZURE_OPENAI_DEPLOYMENT_NAME or "?"


def build(
    settings: Settings,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
    """The chat client. `model` overrides the configured deployment name."""
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_deployment=model or settings.AZURE_OPENAI_DEPLOYMENT_NAME,
        http_async_client=http_client,
        # gpt-5-mini and other reasoning-family deployments only accept
        # the default temperature (1) and reject an explicit override.
    )
