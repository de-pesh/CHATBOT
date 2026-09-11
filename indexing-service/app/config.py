from functools import lru_cache
from typing import Optional
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    LOG_LEVEL: str = "INFO"
    REDIS_URL: str
    REDIS_PASSWORD: Optional[SecretStr] = None  # Optional
    INDEX_QUEUE_NAME: str
    ANALYSIS_QUEUE_NAME: str
    WORKER_BRPOP_TIMEOUT: int = 5
    WORKER_ERROR_RETRY_DELAY: int = 5
    SESSION_EXPIRY_TIME: str
    INDEX_QUEUE_DLQ_NAME: str
    INDEX_QUEUE_MAX_RETRIES: int = 3
    DATABASE_SERVICE_URL: str = "http://localhost:8000"
    RPC_SERVER: str = "indexing-rpc-server:80"

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
