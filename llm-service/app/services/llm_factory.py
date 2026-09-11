"""
Factory for creating LLM service instances based on application settings.
"""
from app.config import Settings
from app.services.llm_service import BaseLLMService
# You can import other services here in the future
# from app.services.openai_service import OpenAIService

def get_llm_service(settings: Settings) -> BaseLLMService:
    """
    Initializes and returns the appropriate LLM service based on settings.

    This factory pattern allows for easy extension to other LLM providers.
    """
    llm_provider = settings.LLM_PROVIDER

    # if settings.LLM_PROVIDER == "OPENAI":
    #     return OpenAIService(...)

    if llm_provider == "ollama":
        from app.services.ollama_service import OllamaService
        return OllamaService(
            ollama_uri=settings.OLLAMA_URI.get_secret_value(),
            model_name=settings.MODEL_NAME
        )

    if llm_provider == "azure_openai":
        from app.services.azure_openai_service import AzureOpenAIService
        return AzureOpenAIService(
            api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT.get_secret_value(),
            api_version=settings.AZURE_OPENAI_API_VERSION,
            model_name=settings.MODEL_NAME
        )

    raise ValueError(f"Unsupported LLM provider specified in settings: {llm_provider}")