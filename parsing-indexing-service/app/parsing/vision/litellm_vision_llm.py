"""A LiteLLM proxy as the vision backend (VISION_LLM_TYPE=litellm).

One of the vendor modules behind parsing/vision/vision_llm_factory.py; see that file's
docstring for the three functions every one of them exposes. The proxy speaks the
OpenAI API, so the client is a plain ChatOpenAI pointed at its base URL, and
whatever it routes to is the proxy's business rather than this service's.

This is also the way to reach an Ollama server behind an auth proxy - see the
note in ollama_vision_llm.py.
"""
from app.config import Settings
from langchain_core.language_models.chat_models import BaseChatModel


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work."""
    return ("LITELLM_BASE_URL", "LITELLM_API_KEY", "LITELLM_MODEL_NAME")


def model_name(settings: Settings) -> str:
    """What the startup log calls the model - here, the alias on the proxy."""
    return settings.LITELLM_MODEL_NAME or "?"


def build(settings: Settings) -> BaseChatModel:
    """The vision-capable chat client."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=settings.LITELLM_BASE_URL,
        api_key=settings.LITELLM_API_KEY.get_secret_value(),
        model=settings.LITELLM_MODEL_NAME,
        temperature=settings.VISION_TEMPERATURE,
    )
