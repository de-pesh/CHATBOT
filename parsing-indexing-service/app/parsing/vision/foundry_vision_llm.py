"""Azure AI Foundry as the vision backend (VISION_LLM_TYPE=foundry).

One of the vendor modules behind parsing/vision/vision_llm_factory.py; see that file's
docstring for the three functions every one of them exposes.

Foundry serves models that are not OpenAI's - Llama, Mistral, Phi - through
Azure's inference endpoint, which is a different API from Azure OpenAI and a
different package (langchain_azure_ai). That import stays inside build(), so an
install on any other backend does not need it.
"""
from app.config import Settings
from langchain_core.language_models.chat_models import BaseChatModel


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work.

    AZURE_AI_FOUNDRY_API_VERSION is deliberately absent: it has a non-None
    default, so it is never missing.
    """
    return ("AZURE_AI_FOUNDRY_ENDPOINT", "AZURE_AI_FOUNDRY_API_KEY", "AZURE_AI_FOUNDRY_MODEL")


def model_name(settings: Settings) -> str:
    """What the startup log calls the model."""
    return settings.AZURE_AI_FOUNDRY_MODEL or "?"


def build(settings: Settings) -> BaseChatModel:
    """The vision-capable chat client."""
    from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel

    return AzureAIChatCompletionsModel(
        endpoint=settings.AZURE_AI_FOUNDRY_ENDPOINT,
        credential=settings.AZURE_AI_FOUNDRY_API_KEY.get_secret_value(),
        model=settings.AZURE_AI_FOUNDRY_MODEL,
        api_version=settings.AZURE_AI_FOUNDRY_API_VERSION,
        temperature=settings.VISION_TEMPERATURE,
    )
