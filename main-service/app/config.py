"""Application configuration settings.

This module defines the `Settings` class using Pydantic's BaseSettings,
which loads configuration from environment variables and a .env file.
It centralizes all application-wide settings, making them easily accessible
and type-safe.
"""
from functools import lru_cache
from typing import Optional
from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    """
    Settings class for loading application configuration from environment variables.

    Environment variables are case-insensitive and loaded from a `.env` file
    (if present) as well as the system environment. This provides a robust
    and flexible way to manage application settings across different environments.
    """
    # --- Application Settings ---
    DEBUG: bool = Field(False, description="Enable debug mode (e.g., for detailed error messages)")
    DISABLE_AUTH: bool = Field(
        False,
        description="TEMPORARY: when true, bypasses JWT validation and permission checks on all endpoints. Must be false in production.",
    )
    # --- Redis Settings ---
    REDIS_URL: str = Field(..., description="Redis connection URL")
    REDIS_PASSWORD: Optional[SecretStr] = Field(None, description="Redis password (optional)")
    CHATBOT_QUEUE_NAME: str = Field(..., description="Name of the queue for chatbot tasks")

    # Database configuration
    DB_CONNECTION_STRING: SecretStr

    # File Storage Settings
    STORAGE_TYPE: Literal["azure", "nextcloud"] = Field("azure", description="File storage service to use")

    # Azure Blob Storage Settings (used if STORAGE_TYPE is 'azure') ---
    AZURE_STORAGE_ACCOUNT_URL: Optional[SecretStr] = Field(
        None, description="Azure Blob Storage account URL (e.g. https://<account>.blob.core.windows.net)"
    )
    AZURE_STORAGE_ACCOUNT_NAME: Optional[SecretStr] = Field(
        None, description="Azure Blob Storage account name"
    )
    AZURE_STORAGE_ACCOUNT_KEY: Optional[SecretStr] = Field(
        None, description="Azure Blob Storage account key"
    )
    AZURE_STORAGE_CONTAINER_NAME: Optional[SecretStr] = Field(
        None, description="Azure Blob Storage container name"
    )
    AZURE_STORAGE_SAS_EXPIRY_MINUTES: int = Field(
        30, description="Expiry time for generated SAS tokens (in minutes)"
    )

    # Nextcloud Settings (used if STORAGE_TYPE is 'nextcloud')
    NEXTCLOUD_URL: Optional[str] = Field(None, description="Nextcloud instance URL")
    NEXTCLOUD_USERNAME: Optional[SecretStr] = Field(None, description="Nextcloud username")
    NEXTCLOUD_PASSWORD: Optional[SecretStr] = Field(None, description="Nextcloud password")

    # --- File Upload Settings ---

    # --- JWT Settings ---
    SECRET_KEY: SecretStr = Field(..., description="Secret key for JWT encoding and decoding.")
    ALGORITHM: str = Field("HS256", description="Algorithm used for JWT signing.")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(30, description="Access token expiration time in minutes.")

    # --- Chat: Azure OpenAI ---
    LLM_API_URL: Optional[str] = Field(None, description="Chat completion endpoint URL")
    MODEL_NAME: Optional[str] = Field(None, description="Chat model deployment name")
    TEMPERATURE: float = Field(0.1, description="Sampling temperature for chat completions")
    AZURE_OPENAI_API_KEY: Optional[SecretStr] = Field(None, description="Azure OpenAI API key")
    AZURE_OPENAI_ENDPOINT: Optional[str] = Field(None, description="Azure OpenAI endpoint")
    AZURE_OPENAI_API_VERSION: str = Field("2024-05-01-preview", description="Azure OpenAI API version")
    AZURE_OPENAI_DEPLOYMENT_NAME: Optional[str] = Field(None, description="Azure OpenAI deployment name")

    # --- Vector store (read directly for GET /kb; writes go through the RPC) ---
    MONGO_CONNECTION_STRING: Optional[SecretStr] = Field(None, description="MongoDB connection URI")
    MONGO_DATABASE_NAME: Optional[str] = Field(None, description="MongoDB database name")
    MONGODB_COLLECTION_NAME: str = Field("chatbot_policies", description="Collection holding the indexed chunks")

    # --- Compute service (gRPC): ParseFile, Index, Search, Update, Delete ---
    RPC_SERVER: str = Field("indexing-rpc-server:8001", description="host:port of the compute service")

    # --- KB queue worker ---
    KB_WORKER_ENABLED: bool = Field(True, description="Run the chatbot_queue consumer in-process")
    CHATBOT_QUEUE_DLQ_NAME: Optional[str] = Field("chatbot_queue_dlq", description="Dead-letter queue name")
    CHATBOT_QUEUE_MAX_RETRIES: int = Field(3, description="Retries before a task is dead-lettered")
    WORKER_BRPOP_TIMEOUT: int = Field(5, description="Blocking pop timeout, seconds")
    WORKER_ERROR_RETRY_DELAY: int = Field(5, description="Delay after a worker error, seconds")
    # DATABASE_SERVICE_URL is deliberately NOT a field here. docker-compose's
    # shared infra-env sets it to http://main-service:8000, and lifespan_manager
    # would then attach AsyncDBLogHandler pointing this service's ERROR logs at
    # itself - at a path it does not serve, so each failed post logs an error
    # that gets posted again. getattr(settings, 'DATABASE_SERVICE_URL', None)
    # returns None without the field, which is what keeps DB logging off.

    # Prompt Settings
    # Pydantic model configuration
    # This nested class provides configuration for how Pydantic should load and handle settings.
    model_config = SettingsConfigDict(
        env_file=".env",  # Specifies the .env file to load environment variables from.
        extra="ignore",  # Ignores any extra environment variables not defined in the model.
        case_sensitive=False, # Treats environment variable names as case-insensitive.
    )


@lru_cache
def get_settings() -> Settings:
    """
    Retrieves the application settings with caching for optimization.

    This function uses `functools.lru_cache` to ensure that the `Settings`
    instance is created only once, improving performance by avoiding redundant
    environment variable loading.

    Returns:
        Settings: An instance of the application settings.
    """
    return Settings()
