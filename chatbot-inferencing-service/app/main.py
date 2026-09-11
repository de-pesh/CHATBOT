import logging
import aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from langchain_ollama.chat_models import ChatOllama
from langchain_openai import AzureChatOpenAI
from app.services.database_service import DatabaseService
from app.services.mysql_service import MySQLService
from app.logging.logging_config import setup_logging
from app.config import get_settings
from app.controllers.error_controller import (
    generic_exception_handler,
    http_exception_handler
)
from app.models.error_models import AsyncDBLogHandler
from app.routers import chatbot, kb

setup_logging()
logger = logging.getLogger("app")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Get settings during startup ---
    try:
        settings = get_settings()
    except Exception as e:
        logger.exception("CRITICAL: Failed to load settings during application startup!")
        raise RuntimeError(f"Failed to load application settings: {e}") from e

    logger.info("Application startup...")

    # --- Create shared aiohttp ClientSession ---
    logger.info("Creating shared aiohttp client session...")
    http_client = aiohttp.ClientSession()
    app.state.http_client = http_client
    logger.info("Shared aiohttp client session created.")

    # --- Create LLM Client (Azure OpenAI preferred, Ollama as fallback) ---
    temperature = settings.TEMPERATURE if settings.TEMPERATURE else 0.1
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
                temperature=temperature,
                base_url=settings.LLM_API_URL.replace("/api/chat", ""),
            )
        except Exception:
            logger.exception("Failed to create Ollama client during startup!")
            app.state.ollama_client = None
    else:
        logger.warning("No LLM backend configured (Azure OpenAI or Ollama). LLM client will not be created.")

     # --- Initialize Database Service ---
    # One backend; see the note in main-service's lifespan_manager.
    if settings.DB_CONNECTION_STRING:
        db_service: DatabaseService = MySQLService(
            settings.DB_CONNECTION_STRING.get_secret_value()
        )
        try:
            await db_service.connect()
            app.state.db_service = db_service
            logger.info("MySQL database service connected.")
        except Exception:
            logger.exception("Failed to connect to MySQL during startup.")
            app.state.db_service = None  # Ensure state is None on failure
    else:
        logger.warning("DB_CONNECTION_STRING not configured. Database service will be disabled.")
        app.state.db_service = None

    db_service_url_value = getattr(settings, 'DATABASE_SERVICE_URL', None)
    if isinstance(db_service_url_value, str) and db_service_url_value:
        logger.info(f"Initializing AsyncDBLogHandler for DB logs at: {db_service_url_value}")
        
        db_handler = AsyncDBLogHandler(
            app=app,
            db_service_url=db_service_url_value
        )
        db_handler.setLevel(logging.ERROR) # Only send ERROR and CRITICAL to DB

        # Create a formatter for the DB logs
        db_log_fmt_str = "%(asctime)s - %(levelname)s - %(message)s" # Format for DB
        db_formatter = logging.Formatter(fmt=db_log_fmt_str, datefmt="%Y-%m-%d %H:%M:%S")
        db_handler.setFormatter(db_formatter)

        # Add this handler to your target logger (e.g., "app" logger)
        target_app_logger = logging.getLogger("app") # Get the 'app' logger instance
        target_app_logger.addHandler(db_handler)

        logger.info("AsyncDBLogHandler added to 'app' logger for ERROR level messages with analysis_id.")
    elif db_service_url_value:
        logger.error(f"DATABASE_SERVICE_URL is not a valid string: {db_service_url_value}. DB logging disabled.")
    else:
        logger.warning("DATABASE_SERVICE_URL not configured in settings. Database logging will be disabled.")

    # --- Knowledge-base queue worker ---
    # Absorbed from chatbot-kb-management-service. Uses the same in-process
    # parser as POST /kb, so ingestion has one implementation, not two.
    if app.state.db_service is not None:
        from app.kb_worker.worker import start_kb_worker
        from app.routers.kb import _parsing_blob_service, _parsing_ollama_client
        start_kb_worker(
            app,
            settings=settings,
            db_service=app.state.db_service,
            azure_blob_service=_parsing_blob_service(),
            ollama_client=_parsing_ollama_client(),
        )
    else:
        logger.warning("KB queue worker not started: no database service.")

    yield # The application runs here

    # Code to run on shutdown
    logger.info("Application shutdown...")

    # --- Disconnect Database Service ---
    from app.kb_worker.worker import stop_kb_worker
    await stop_kb_worker(app)

    if hasattr(app.state, 'db_service') and app.state.db_service:
        logger.info("Disconnecting database service...")
        try:
            await app.state.db_service.disconnect()
            logger.info("Database service disconnected.")
        except Exception as e:
            logger.exception("Error disconnecting database service.")

    # --- Close shared aiohttp ClientSession ---
    # Check if attribute exists AND if it's not None before closing
    if hasattr(app.state, 'http_client') and app.state.http_client:
        logger.info("Closing shared aiohttp client session...")
        try:
            await app.state.http_client.close()
            logger.info("Shared aiohttp client session closed.")
        except Exception as e:
            logger.exception("Error closing shared aiohttp client session.")
    else:
        logger.debug("Shared aiohttp client session not found or already None during shutdown.")

    logger.info("Application shutdown complete.")


# FastAPI application initialization with lifespan.
app = FastAPI(
    title="Policy Chatbot",
    version="1.0.0",
    lifespan=lifespan # Register the lifespan handler
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3006", "http://127.0.0.1:3006"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Exception Handlers ---
logger.info("Registering global exception handlers...")
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)
logger.info("Exception handlers registered.")

# --- API Routers ---
logger.info("Including API routers...")
app.include_router(chatbot.router)
app.include_router(kb.router)
logger.info("API routers included.")

# --- Root Endpoint ---
@app.get("/")
def root():
    """ Root endpoint that checks if the service is running. """
    logger.debug("Root endpoint '/' called.")
    return {"status": "Service is running..."}

