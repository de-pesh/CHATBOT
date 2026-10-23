"""Azure OpenAI as the vision backend (VISION_LLM_TYPE=azure).

One of the vendor modules behind parsing/vision/vision_llm_factory.py; see that file's
docstring for the three functions every one of them exposes. The langchain_openai
import sits inside build() so an install running a different backend never has to
import it.
"""
from app.config import Settings
from langchain_core.language_models.chat_models import BaseChatModel


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work.

    AZURE_OPENAI_API_VERSION is deliberately absent: it has a non-None default,
    so it is never missing.
    """
    return ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT_NAME")


def model_name(settings: Settings) -> str:
    """What the startup log calls the model - for Azure, the deployment name."""
    return settings.AZURE_OPENAI_DEPLOYMENT_NAME or "?"


def build(settings: Settings) -> BaseChatModel:
    """The vision-capable chat client."""
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_NAME,
        # Some deployments (o-series/reasoning models) reject an explicit
        # temperature - leave it at the model's default, same reasoning as
        # main-service-chatbot's services/azure_llm.py.
    )
