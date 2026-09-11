from functools import lru_cache
from typing import Optional
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BLOB_CONNECTION_STRING: SecretStr
    BLOB_CONTAINER_NAME: str
    DATABASE_SERVICE_URL: SecretStr = "http://localhost:8000"
    OLLAMA_MODEL: str
    OLLAMA_BASE_URL: str
    OLLAMA_API_KEY: SecretStr
    OLLAMA_TEMPERATURE: float = 0.1
    LOG_LEVEL: str = "INFO"
    EMBEDDING_MODEL_PATH: Optional[str] = None
    GRPC_PORT: int = 8000

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
