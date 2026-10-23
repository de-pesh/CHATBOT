"""An Ollama server as the vision backend (VISION_LLM_TYPE=ollama).

One of the vendor modules behind parsing/vision/vision_llm_factory.py; see that file's
docstring for the three functions every one of them exposes.
"""
from app.config import Settings
from langchain_core.language_models.chat_models import BaseChatModel


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work.

    OLLAMA_MODEL has a default, so in practice only the base URL decides whether
    this backend is configured; it is named here anyway so an explicitly blanked
    model is reported rather than silently becoming the default.
    """
    return ("OLLAMA_BASE_URL", "OLLAMA_MODEL")


def model_name(settings: Settings) -> str:
    """What the startup log calls the model."""
    return settings.OLLAMA_MODEL or "?"


def build(settings: Settings) -> BaseChatModel:
    """The vision-capable chat client."""
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.OLLAMA_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=settings.VISION_TEMPERATURE,
        # Set explicitly: Ollama's default window is small enough to
        # truncate the prompt once the image's tokens are added, and it
        # does so silently. See VISION_NUM_CTX in config.py.
        num_ctx=settings.VISION_NUM_CTX,
        # ChatOllama has no api_key param - plain Ollama needs none. A server
        # behind an auth proxy is reached with VISION_LLM_TYPE=litellm instead.
    )
