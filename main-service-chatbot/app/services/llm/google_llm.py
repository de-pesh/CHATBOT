"""Google Gemini as the chat backend (LLM_TYPE=google).

One of the vendor modules behind services/llm/chat_llm_factory.py; see that file's
docstring for the four functions every one of them exposes. Uses the Gemini API
with a plain API key through langchain-google-genai, which is the shape the
other backends here already have: one secret and one model name. Vertex AI
(service-account or ADC authentication) is a different credential model - add it
as its own vendor module rather than growing this one. The langchain_google_genai
import sits inside build() so an install running a different backend never has to
import it.
"""
import logging

import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings

logger = logging.getLogger(__name__)

# Gemini has no `reasoning_effort` argument (its equivalent is a thinking
# budget, set per model), so the binding ChatLLM applies for the helper
# and answer steps must not be made - see
# chat_llm_factory.chat_llm_accepts_reasoning_effort.
ACCEPTS_REASONING_EFFORT = False


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work."""
    return ("GOOGLE_API_KEY", "GOOGLE_MODEL_NAME")


def endpoint(settings: Settings) -> str | None:
    """None: the Google SDK owns its own connections, so there is nothing to keep warm."""
    return None


def model_name(settings: Settings) -> str:
    """What the startup log calls the model - e.g. gemini-2.5-flash."""
    return settings.GOOGLE_MODEL_NAME or "?"


def build(
    settings: Settings,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
    """The chat client. `model` overrides the configured model name.

    `http_client` is accepted for the contract and ignored: the Google SDK does
    not take a shared httpx client.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model or settings.GOOGLE_MODEL_NAME,
        google_api_key=settings.GOOGLE_API_KEY.get_secret_value(),
        temperature=settings.TEMPERATURE,
    )
