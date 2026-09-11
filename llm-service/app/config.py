from functools import lru_cache
from typing import Optional, Literal
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    LOG_LEVEL: str = "INFO"
    REDIS_URL: str
    REDIS_PASSWORD: Optional[SecretStr] = None  # Optional
    LLM_QUEUE_NAME: str
    # GRAPH_QUEUE_NAME: str
    ANALYSIS_QUEUE_NAME: str
    WORKER_BRPOP_TIMEOUT: int = 5
    WORKER_ERROR_RETRY_DELAY: int = 5
    SESSION_EXPIRY_TIME: str
    LLM_QUEUE_DLQ_NAME: str
    LLM_QUEUE_MAX_RETRIES: int = 3
    DB_CONNECTION_STRING: SecretStr
    DB_TYPE: Literal["mssql", "postgres", "oracle"] = "oracle"
    LLM_PROVIDER: Literal["ollama", "azure_openai"] = "ollama"
    MODEL_NAME: str
    # -- Ollama Models Specific --
    OLLAMA_URI: Optional[SecretStr] = None
    # -- Azure OpenAI Specific --
    AZURE_OPENAI_API_KEY: Optional[SecretStr] = None
    AZURE_OPENAI_API_VERSION: Optional[str] = None # e.g., "2024-02-01"
    AZURE_OPENAI_ENDPOINT: Optional[SecretStr] = None
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings():
    """
    Retrieves the application settings with caching for optimization.

    This function loads the settings from environment variables (using the `Settings` class) 
    and caches the result to avoid reloading the configuration on each request.

    Returns:
        Settings: An instance of the Settings class containing the loaded configuration.
    """
    return Settings()
