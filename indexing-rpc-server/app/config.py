"""Compute service configuration.

Each piece of infrastructure is selected by a *_TYPE setting with its
credentials beside it, so a backend swap is configuration rather than an edit
to the startup path:

    STORAGE_TYPE   local | azure       -> services/storage_factory.py
    VECTOR_STORE   mongo               -> services/vector_store_factory.py

STORAGE_TYPE must match main-service's. Only paths cross the gRPC boundary, so
the two processes have to be looking at the same store - under 'local' that is
the same mounted volume, which on OpenShift means one ReadWriteMany PVC in both
pods.
"""
from functools import lru_cache
from typing import Literal, Optional
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- File storage ---
    # 'local' is the OpenShift target: a mounted PVC shared with main-service.
    STORAGE_TYPE: Literal["local", "azure"] = "azure"

    # Used if STORAGE_TYPE is 'local'.
    LOCAL_STORAGE_PATH: Optional[str] = None

    # Used if STORAGE_TYPE is 'azure'. Optional so the service starts without
    # Azure credentials once it is running on a persistent volume.
    BLOB_CONNECTION_STRING: Optional[SecretStr] = None
    BLOB_CONTAINER_NAME: Optional[str] = None

    # --- Vector store ---
    VECTOR_STORE: Literal["mongo"] = "mongo"
    MONGO_CONNECTION_STRING: Optional[SecretStr] = None
    MONGO_DATABASE_NAME: Optional[str] = None
    # Fallback collection. Every RPC carries its own collection_name; this is
    # only the default a store is constructed with.
    VECTOR_COLLECTION_NAME: str = "chatbot_policies"

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
