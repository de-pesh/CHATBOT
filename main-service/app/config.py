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
    APP_NAME: str = "CCPM Main Service"
    ENVIRONMENT: str = Field("development", description="Application environment (e.g., development, staging, production)")
    DEBUG: bool = Field(False, description="Enable debug mode (e.g., for detailed error messages)")
    LOG_LEVEL: str = Field("INFO", description="Minimum logging level (e.g., DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    DISABLE_AUTH: bool = Field(
        False,
        description="TEMPORARY: when true, bypasses JWT validation and permission checks on all endpoints. Must be false in production.",
    )
    RESULT_PENDING_TIMEOUT_MINUTES: int = Field(
        30,
        description="Timeout in minutes after which stale pending result rows are auto-resolved.",
    )

    # --- Redis Settings ---
    REDIS_URL: str = Field(..., description="Redis connection URL")
    REDIS_PASSWORD: Optional[SecretStr] = Field(None, description="Redis password (optional)")
    SESSION_EXPIRY_TIME: int = Field(3600, description="Session expiry time in seconds (default: 1 hour)")
    PARSER_QUEUE_NAME: str = Field(..., description="Name of the queue for parser tasks")
    LLM_QUEUE_NAME: str = Field(..., description="Name of the queue for llm tasks")
    CHATBOT_QUEUE_NAME: str = Field(..., description="Name of the queue for chatbot tasks")
    ASSESSMENT_KB_QUEUE_NAME: str = Field(
        "ccpm_assessment_kb_queue",
        description="Name of the queue for assessment knowledge-base snapshot tasks",
    )

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
    ALLOWED_FILE_TYPES: list[str] = Field(default_factory=lambda: ["pdf", "docx", "doc", "txt", "xlsx", "png", "jpeg", "jpg"], description="Allowed file types for upload.")

    # --- Chatbot Inferencing Service Settings ---
    CHATBOT_INFERENCING_SERVICE_URL: Optional[str] = Field(
        None,
        description="Base URL of the chatbot-inferencing-service (e.g. http://localhost:8003), used to push uploaded documents into its knowledge base via /kb.",
    )

    # --- JWT Settings ---
    SECRET_KEY: SecretStr = Field(..., description="Secret key for JWT encoding and decoding.")
    ALGORITHM: str = Field("HS256", description="Algorithm used for JWT signing.")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(30, description="Access token expiration time in minutes.")

    # Prompt Settings
    CORRESPONDING_QUESTION_PROMPT: str = Field(..., description="Prompt for corresponding question generation")
    CRITERIA_GENERATION_PROMPT: str = Field(..., description="Prompt for criteria generation")

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
