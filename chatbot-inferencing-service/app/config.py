"""
This module defines application configuration management using Pydantic Settings. 
It provides a `Settings` class to load environment variables for database connections, 
API endpoints, and Azure storage, along with a cached `get_settings` function to 
efficiently retrieve configuration throughout the application.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from typing import Optional


class Settings(BaseSettings):
    """
    Application configuration settings.

    This class uses environment variables to load configuration values needed 
    for database connections, LLM API access, and Azure storage operations.

    Attributes:
        MONGODB_CONNECTION_STRING (str): MongoDB connection URI.
        MONGODB_DB_NAME (str): Name of the MongoDB database.
        MONGODB_COLLECTION_NAME (str): Name of the MongoDB collection.
        LLM_API_URL (str): URL of the Large Language Model API endpoint.
        MODEL_NAME (str): Name of the LLM model to use.
        AZURE_STORAGE_ACCOUNT_NAME (str): Azure Storage account name.
        AZURE_STORAGE_ACCOUNT_KEY (str): Azure Storage account key.
        AZURE_STORAGE_CONTAINER_NAME (str): Name of the Azure Storage container.
    
    Configuration:
        model_config (SettingsConfigDict): Settings for environment file loading 
        and handling of extra fields.
    """


    MONGODB_COLLECTION_NAME: str
    LLM_API_URL: str
    MODEL_NAME: str
    TEMPERATURE: float = 0.1
    AZURE_STORAGE_ACCOUNT_NAME: str
    AZURE_STORAGE_ACCOUNT_KEY: str
    AZURE_STORAGE_CONTAINER_NAME: str
    RPC_SERVER: str = "localhost:50051"
    MONGO_CONNECTION_STRING: Optional[SecretStr] = None
    MONGO_DATABASE_NAME: Optional[str] = None
    AZURE_OPENAI_API_KEY: Optional[SecretStr] = None
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_API_VERSION: str = "2024-05-01-preview"
    AZURE_OPENAI_DEPLOYMENT_NAME: Optional[str] = None
    DATABASE_SERVICE_URL: Optional[str] = None
    MAIN_SERVICE_URL: Optional[str] = None
    DB_CONNECTION_STRING: SecretStr

    # --- Parsing (folded in from parsing-rpc-server) ---
    # Blob settings above are reused; these drive the vision model that only the
    # image and scanned-page extractors call.
    OLLAMA_MODEL: str = "llama3.2-vision:11b"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_API_KEY: SecretStr = SecretStr("local-dev-unset")
    OLLAMA_TEMPERATURE: float = 0.1

    # --- KB queue worker (absorbed from chatbot-kb-management-service) ---
    # Set false to run this process as API-only; a second replica of the same
    # image can then run worker-only, if parsing should not share the API's
    # event loop.
    KB_WORKER_ENABLED: bool = True
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_PASSWORD: Optional[SecretStr] = None
    CHATBOT_QUEUE_NAME: str = "chatbot_queue"
    CHATBOT_QUEUE_DLQ_NAME: Optional[str] = "chatbot_queue_dlq"
    CHATBOT_QUEUE_MAX_RETRIES: int = 3
    WORKER_BRPOP_TIMEOUT: int = 5
    WORKER_ERROR_RETRY_DELAY: int = 5

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings():
    """
    Retrieves a cached instance of the `Settings` class.

    This function ensures that the settings are loaded only once and cached, 
    providing efficient access to the configuration throughout the application.

    Returns:
        Settings: The instance of the `Settings` class populated with configuration data.
    """
    return Settings()
