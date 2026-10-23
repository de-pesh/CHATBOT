"""Any OpenAI-compatible /v1 endpoint as the vision backend (VISION_LLM_TYPE=openai).

One of the vendor modules behind parsing/vision/vision_llm_factory.py; see that file's
docstring for the three functions every one of them exposes. Azure AI Foundry
serves a plain /openai/v1 API next to its Azure-specific one, so a deployment there
is reached with the ordinary ChatOpenAI client and the deployment name as the model.

Same client as litellm_vision_llm.py; what differs is the variables. LITELLM_* is
shared with main-service-chatbot's chat model, so a vision model on another
resource needs names of its own.
"""
from app.config import Settings
from langchain_core.language_models.chat_models import BaseChatModel


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work."""
    return ("VISION_OPENAI_BASE_URL", "VISION_OPENAI_API_KEY", "VISION_OPENAI_MODEL")


def model_name(settings: Settings) -> str:
    """What the startup log calls the model - the deployment name on Azure."""
    return settings.VISION_OPENAI_MODEL or "?"


def build(settings: Settings) -> BaseChatModel:
    """The vision-capable chat client."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=settings.VISION_OPENAI_BASE_URL,
        api_key=settings.VISION_OPENAI_API_KEY.get_secret_value(),
        model=settings.VISION_OPENAI_MODEL,
        temperature=settings.VISION_TEMPERATURE,
    )
