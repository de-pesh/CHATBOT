"""
Manages the lifespan of the FastAPI application, handling startup and shutdown events.

This module defines the `lifespan` context manager, used by FastAPI
to execute code during application startup (e.g., initializing resources)
and shutdown (e.g., cleaning up resources). It ensures proper setup and teardown
of external services and clients.
"""
import logging
import aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI

from langchain_openai import AzureChatOpenAI
from langchain_ollama import ChatOllama

from app.config import get_settings, Settings
from app.models.common import AsyncDBLogHandler
from app.services.database_service import DatabaseService
from app.services.database_factory import create_database_service
from app.services.storage_service import FileStorageService
from app.services.storage_factory import create_storage_service
from app.services.queue_factory import create_queue_service

logger = logging.getLogger("app")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """"FastAPI lifespan context manager for application startup and shutdown.

    Handles initialization of settings, shared HTTP clients, Azure Blob Storage clients,
    and custom logging handlers during startup. Ensures proper cleanup on shutdown.

    Args:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None: The application runs within this context.
    """
    # Load application settings.
    try:
        settings = get_settings()
    except Exception as e:
        logger.exception("CRITICAL: Failed to load settings during application startup!")
        raise RuntimeError(f"Failed to load application settings: {e}") from e
    
    # Log loaded configuration settings for visibility.
    logger.info("--- Application Configuration ---")
    config_details = []
    for key in Settings.model_fields:
        value = getattr(settings, key)
        config_details.append(f"  - {key}: {value}")
    log_output = "Using the following settings:\n" + "\n".join(config_details)
    logger.info(log_output)
    logger.info("---------------------------------")

    logger.info("Application startup...")


    # --- Initialize Database Service ---
    # Backend chosen by DB_TYPE. Read the docstring in database_factory.py
    # before assuming a swap is just a config change: the data layer is stored
    # procedures, so a new backend needs its own port of scripts/*.sql.
    db_service: DatabaseService = create_database_service(settings)
    await db_service.initialize()
    app.state.db_service = db_service
    logger.info("Database service connected (DB_TYPE=%s).", settings.DB_TYPE)

     
    # --- Initialize File Storage Service ---
    # Backend chosen by STORAGE_TYPE: 'local' (a mounted persistent volume),
    # 'azure' or 'nextcloud'. The compute service must be pointed at the same
    # store with the same setting - only paths cross the RPC between them.
    storage_service: FileStorageService = create_storage_service(settings)
    await storage_service.initialize()
    app.state.file_storage_service = storage_service
    logger.info("File storage initialized (STORAGE_TYPE=%s).", settings.STORAGE_TYPE)

    # --- Initialize the message queue (producer side) ---
    # Backend chosen by QUEUE_TYPE. The worker below opens its own connection
    # through the same factory, so both agree on the DLQ and retry settings.
    app.state.redis_chatbot_queue = create_queue_service(settings)
    await app.state.redis_chatbot_queue.initialize()
    logger.info("Message queue initialized (QUEUE_TYPE=%s).", settings.QUEUE_TYPE)

    # Create and store a shared aiohttp ClientSession for optimized HTTP requests
    # to other internal services (e.g. chatbot-inferencing-service).
    logger.info("Creating shared aiohttp client session...")
    app.state.http_client = aiohttp.ClientSession()
    logger.info("Shared aiohttp client session created.")

    # --- LLM client for /chat (Azure OpenAI preferred, Ollama as fallback) ---
    # Held on app.state because app.services.ollama_service.get_ollama_client
    # reads it from there as a FastAPI dependency.
    app.state.ollama_client = None
    if settings.AZURE_OPENAI_API_KEY and settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_DEPLOYMENT_NAME:
        logger.info("Creating Azure OpenAI client")
        try:
            app.state.ollama_client = AzureChatOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
                api_version=settings.AZURE_OPENAI_API_VERSION,
                azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_NAME,
                # gpt-5-mini and other reasoning-family deployments only accept
                # the default temperature (1) and reject an explicit override.
            )
        except Exception:
            logger.exception("Failed to create Azure OpenAI client during startup!")
            app.state.ollama_client = None
    elif settings.MODEL_NAME and settings.LLM_API_URL:
        logger.info("Creating Ollama client")
        try:
            app.state.ollama_client = ChatOllama(
                model=settings.MODEL_NAME,
                temperature=settings.TEMPERATURE or 0.1,
                base_url=settings.LLM_API_URL.replace("/api/chat", ""),
            )
        except Exception:
            logger.exception("Failed to create Ollama client during startup!")
            app.state.ollama_client = None
    else:
        logger.warning("No LLM backend configured (Azure OpenAI or Ollama). /chat will return 503.")

    # Initialize AsyncDBLogHandler to send ERROR level logs to a database service.
    db_service_url_value = getattr(settings, 'DATABASE_SERVICE_URL', None)
    if isinstance(db_service_url_value, str) and db_service_url_value:
        logger.info(f"Initializing AsyncDBLogHandler for DB logs at: {db_service_url_value}")

        db_handler = AsyncDBLogHandler(
            app=app,
            db_service_url=db_service_url_value
        )
        db_handler.setLevel(logging.ERROR)

        db_log_fmt_str = "% (asctime)s - % (levelname)s - % (message)s"
        db_formatter = logging.Formatter(fmt=db_log_fmt_str, datefmt="%Y-%m-%d %H:%M:%S")
        db_handler.setFormatter(db_formatter)

        target_app_logger = logging.getLogger("app")
        target_app_logger.addHandler(db_handler)

        logger.info("AsyncDBLogHandler added to 'app' logger for ERROR level messages with assessment_id.")
    elif db_service_url_value:
        logger.error(f"DATABASE_SERVICE_URL is not a valid string: {db_service_url_value}. DB logging disabled.")
    else:
        logger.warning("DATABASE_SERVICE_URL not configured in settings. Database logging will be disabled.")

    # --- Knowledge-base queue worker ---
    # Was its own container (chatbot-kb-management-service), then an asyncio
    # task in the chat service. Same task, same queue, now in this process.
    if settings.KB_WORKER_ENABLED and getattr(app.state, "db_service", None) is not None:
        from app.kb_worker.worker import start_kb_worker
        start_kb_worker(app, settings=settings, db_service=app.state.db_service)
    elif not settings.KB_WORKER_ENABLED:
        logger.info("KB queue worker disabled by KB_WORKER_ENABLED.")
    else:
        logger.warning("KB queue worker not started: no database service.")

    # --- Application is now running ---
    yield
    # --- Application is shutting down ---

    logger.info("Application shutdown...")

    from app.kb_worker.worker import stop_kb_worker
    await stop_kb_worker(app)

    # Disconnect services in reverse order of initialization
    if hasattr(app.state, 'http_client') and app.state.http_client:
        await app.state.http_client.close()
        logger.info("Shared aiohttp client session closed.")

    if hasattr(app.state, 'redis_chatbot_queue') and app.state.redis_chatbot_queue:
        await app.state.redis_chatbot_queue.close()
        logger.info("Redis chatbot queue service closed.")

    if hasattr(app.state, 'file_storage_service') and app.state.file_storage_service:
        await app.state.file_storage_service.close()
        logger.info("File storage service closed.")

    if hasattr(app.state, 'db_service') and app.state.db_service:
        await app.state.db_service.close()
        logger.info("Database service disconnected.")

    logger.info("Application shutdown complete.")