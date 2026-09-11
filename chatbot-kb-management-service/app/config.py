from functools import lru_cache
from typing import Optional, Literal
from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Settings for the Chatbot Knowledge Base Worker.
    Loads configuration from environment variables and a .env file.
    """
    # --- Service & Logging ---
    LOG_LEVEL: str = Field("INFO", description="Minimum logging level.")

    # --- Redis (Queue) Settings ---
    REDIS_URL: str = Field(..., description="Redis connection URL for cluster discovery.")
    REDIS_PASSWORD: Optional[SecretStr] = Field(None, description="Redis password (if not in URL).")
    CHATBOT_QUEUE_NAME: str = Field("chatbot_kb_queue", description="Name of the queue to listen to.")
    CHATBOT_QUEUE_DLQ_NAME: Optional[str] = Field(None, description="Dead-letter queue for failed messages.")
    CHATBOT_QUEUE_MAX_RETRIES: int = Field(3, description="Max retries before sending a message to the DLQ.")
    WORKER_BRPOP_TIMEOUT: int = Field(5, description="Timeout in seconds for the blocking pop from Redis.")
    WORKER_ERROR_RETRY_DELAY: int = Field(5,
                                          description="Delay in seconds before retrying a failed message.")

    # --- Database Settings (Using Stored Procedures) ---
    DB_TYPE: Literal["mssql", "postgres", "oracle"] = Field("oracle", description="The type of database to connect to.")
    DB_CONNECTION_STRING: SecretStr = Field(..., description="The full database connection string.")

    # --- gRPC Service Dependencies ---
    PARSING_RPC_SERVER: str = Field(..., description="Address of the Parsing gRPC service.")
    INDEXING_RPC_SERVER: str = Field(..., description="Address of the Indexing gRPC service.")
    MONGO_COLLECTION_NAME: str = Field("chatbot_policies",
                                       description="The MongoDB collection name used by the Indexing service.")
    
    # --- Other Settings (Kept for compatibility if needed) ---
    SESSION_EXPIRY_TIME: int = Field(3600, description="Session expiry time.")


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
