from functools import lru_cache
from typing import Optional
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BLOB_CONNECTION_STRING: SecretStr
    BLOB_CONTAINER_NAME: str
    MONGO_CONNECTION_STRING: SecretStr
    MONGO_DATABASE_NAME: str
    EMBEDDING_MODEL_PATH: Optional[str] = None
    GRPC_PORT: int = 8000

    # Vision model, used by ParseFile for images and scanned pages only.
    # Everything else extracts without it, so the defaults let the service
    # start and parse text documents even with no Ollama endpoint configured.
    OLLAMA_MODEL: str = "llama3.2-vision:11b"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_API_KEY: SecretStr = SecretStr("local-dev-unset")
    OLLAMA_TEMPERATURE: float = 0.1

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
