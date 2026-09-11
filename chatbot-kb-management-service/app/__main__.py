import asyncio
import logging
import signal
from functools import partial
import sys
from typing import Dict, Any, Optional

import redis.exceptions as redis_exceptions

# Import necessary components
from app.config import Settings, get_settings
from app.models.enums import PolicyActionTypeEnum
from app.controllers.chatbot_controller import(
    add_file_handler,
    delete_file_handler,
)
from app.services.redis_service import RedisQueueService
from app.services.database_service import DatabaseService
from app.services.mysql_service import MySQLService

# --- Settings Loading ---
try:
    settings: Settings = get_settings()
except Exception as e:
    print(f"FATAL: Unexpected error obtaining settings: {e}")
    sys.exit(1)

# --- Basic Setup ---
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ChatbotKBWorker")

# --- Log Loaded Settings (Mask sensitive info) ---
logger.info("--- Configuration Loaded ---")
logger.info(f"LOG_LEVEL: {settings.LOG_LEVEL}")
redis_url_to_log = settings.REDIS_URL
if "@" in redis_url_to_log:
    url_parts = redis_url_to_log.split('@')
    proto_user = url_parts[0].split(':')
    redis_url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"
logger.info(f"REDIS_URL (Discovery Node): {redis_url_to_log}")
logger.info(f"REDIS_PASSWORD provided: {settings.REDIS_PASSWORD}") # Log boolean
logger.info(f"CHATBOT_QUEUE_NAME: {settings.CHATBOT_QUEUE_NAME}")
logger.info(f"CHATBOT_QUEUE_MAX_RETRIES: {settings.CHATBOT_QUEUE_MAX_RETRIES}")
logger.info(f"WORKER_BRPOP_TIMEOUT: {settings.WORKER_BRPOP_TIMEOUT}")
logger.info(f"WORKER_ERROR_RETRY_DELAY: {settings.WORKER_ERROR_RETRY_DELAY}")
# Mask DB Service URL if it's SecretStr and contains sensitive info
db_url_log = "Not Provided"
if settings.DB_CONNECTION_STRING:
    try:
        # Attempt to get secret value if it's SecretStr, otherwise use as is
        url_val = settings.DB_CONNECTION_STRING.get_secret_value() if hasattr(settings.DB_CONNECTION_STRING, 'get_secret_value') else str(settings.DB_CONNECTION_STRING)
        # Basic masking if typical user:pass@host format
        if "@" in url_val:
            parts = url_val.split('@')
            protocol_creds = parts[0].split('//')
            if len(protocol_creds) > 1: # http://user:pass@... or similar
                db_url_log = f"{protocol_creds[0]}//***:***@{parts[-1]}"
            else: # Maybe just user@host ? Less common for URLs
                db_url_log = f"***@{parts[-1]}"
        else:
            db_url_log = url_val # Log as is if no '@'
    except Exception:
        db_url_log = "[Error accessing/masking DB URL]" # Avoid failing startup
logger.info(f"DB_SERVICE_URL: {db_url_log}")

logger.info(f"MONGO_COLLECTION_NAME: {settings.MONGO_COLLECTION_NAME}")
logger.info(f"SESSION_EXPIRY_TIME: {settings.SESSION_EXPIRY_TIME}")
# Log DATABASE_BASE_URL for Redis worker error logging
db_service_url_for_worker = getattr(settings, 'DATABASE_BASE_URL', 'Not Configured')
logger.info(f"DATABASE_BASE_URL (for worker error logging): {db_service_url_for_worker}")
logger.info("--------------------------")


# --- Specific Message Processing Logic ---
async def process_message(
    payload: Dict[str, Any],
    operation_id: str,
    db_service: DatabaseService, # Injected by partial
):
    logger.info(f"[Processor] Task {operation_id}: Processing chatbot payload.")
    logger.debug(f"[Processor] Payload for {operation_id}: {payload}")

    action = payload.get("action_type")

    if action == PolicyActionTypeEnum.CREATE:
        try:
            logger.info(f"[Processor] Task: Starting '{action}' process for file.")
            await add_file_handler(
                payload=payload,
                settings=settings,
                db_service=db_service
            )
            logger.info(f"[Processor] Successfully completed '{action}' task for provided file.")
        except ValueError as e:
            logger.error(f"[Processor] Task {operation_id}: Data or processing error: {e}")
            raise
        except Exception as e:
            logger.exception(f"[Processor] Task {operation_id}: Unexpected error processing file: {e}")
            raise

    elif action == PolicyActionTypeEnum.DELETE:
        try:
            logger.info(f"[Processor] Task: Starting '{action}' process for file.")
            await delete_file_handler(
                payload=payload,
                settings=settings,
                db_service=db_service
            )
            logger.info(f"[Processor] Successfully completed '{action}' task for provided file.")

        except ValueError as e:
            logger.error(f"[Processor] Task {operation_id}: Data or processing error: {e}")
            raise
        except Exception as e:
            logger.exception(f"[Processor] Task {operation_id}: Unexpected error deleting file: {e}")
            raise
    else:
        logger.error(f"[Processor] Task {operation_id}: Unsupported action type '{action}' in payload.")
        raise


# --- Graceful Shutdown Handling ---
async def shutdown(
        sig: Optional[signal.Signals],
        chatbot_redis_service: Optional[RedisQueueService],
        db_service: Optional[DatabaseService],
):
    """Graceful shutdown handler."""
    
    signal_name = sig.name if sig else "Manual/Exception"
    logger.warning(f"Received exit signal {signal_name}... Shutting down chatbot service.")

    if chatbot_redis_service:
        logger.info("Signaling Chatbot Redis worker to stop...")
        await chatbot_redis_service.stop_worker()
    else:
        logger.warning("Chatbot Redis service instance not available or not initialized during shutdown.")

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        logger.info(f"Cancelling {len(tasks)} outstanding tasks...")
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        cancelled_count = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
        error_count = sum(1 for r in results if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError))
        logger.info(f"Outstanding tasks cancellation results: {len(results)} total, {cancelled_count} cancelled, {error_count} errored.")

    logger.info("Closing external connections (Redis, Mongo)...")
    if db_service:
        logger.info("Closing database service connection...")
        try:
            await db_service.disconnect()
        except Exception as e:
            logger.error(f"Error closing database service: {e}")

    # Close Redis services (they have their own close methods, also managed by async with)
    if chatbot_redis_service and chatbot_redis_service._client:
        logger.info("Ensuring Chatbot Redis connection is closed...")
        try: await chatbot_redis_service.close()
        except Exception as e: logger.error(f"Error ensuring Chatbot Redis closed: {e}")

    logger.info("Chatbot service shutdown sequence complete.")


# --- Main Service Runner ---
async def run_service():
    loop = asyncio.get_running_loop()
    chatbot_queue_service_instance: Optional[RedisQueueService] = None
    db_service: Optional[DatabaseService] = None
    db_service_url_for_worker = getattr(settings, 'DATABASE_BASE_URL', None)


    try:
        # --- Initialize SQL database
        logger.info("Initializing SQL database service...")
        db_service: DatabaseService
        if settings.DB_TYPE == "oracle":
            db_service = MySQLService(settings.DB_CONNECTION_STRING.get_secret_value())
        else:
            raise ValueError(f"Unsupported database type: {settings.DB_TYPE}")
        await db_service.connect()
    
        logger.info("Initializing Redis Cluster Service for Chatbot Queue (worker)...")
        async with RedisQueueService(
            queue_name=settings.CHATBOT_QUEUE_NAME,
            redis_url=settings.REDIS_URL,
            redis_password=settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None,
            decode_responses=True,
            dlq_name=settings.CHATBOT_QUEUE_DLQ_NAME,
            max_retries=settings.CHATBOT_QUEUE_MAX_RETRIES,
            db_service_url=db_service_url_for_worker
        ) as chatbot_queue_service:
            chatbot_queue_service_instance = chatbot_queue_service
            logger.info(f"Chatbot Redis Service connected (watching queue: {settings.CHATBOT_QUEUE_NAME} using BZPOPMIN).")

            shutdown_handler_partial = None
            if sys.platform != "win32":
                logger.info("Setting up signal handlers for SIGTERM and SIGINT.")
                shutdown_handler_partial = partial(
                    shutdown,
                    chatbot_redis_service=chatbot_queue_service_instance,
                    db_service=db_service,
                )
                for sig_name in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(
                        sig_name,
                        lambda s=sig_name: asyncio.create_task(shutdown_handler_partial(sig=s))
                    )
            else:
                logger.warning("Signal handlers not supported on Windows. Use Ctrl+C for manual shutdown.")

            logger.info("Redis Cluster Queue Service (Chatbot) worker starting...")
            if settings.CHATBOT_QUEUE_DLQ_NAME:
                logger.info(f"DLQ Enabled: Queue='{settings.CHATBOT_QUEUE_DLQ_NAME}', Max Retries={settings.CHATBOT_QUEUE_MAX_RETRIES}")
            else:
                logger.info("DLQ Disabled for chatbot queue.")
            if db_service_url_for_worker:
                logger.info(f"DB Error Logging for worker: ENABLED to {db_service_url_for_worker}")
            else:
                logger.info("DB Error Logging for worker: DISABLED")

            process_message_with_deps = partial(
                process_message,
                db_service=db_service,
            )

            await chatbot_queue_service.run_worker(
                process_func=process_message_with_deps,
                brpop_timeout=settings.WORKER_BRPOP_TIMEOUT, # For BZPOPMIN
                error_retry_delay=settings.WORKER_ERROR_RETRY_DELAY,
            )
            logger.info("Chatbot worker loop finished normally.")
        logger.info("Chatbot RedisQueueService context exited.")

    except redis_exceptions.AuthenticationError:
        logger.error(f"FATAL: Redis Cluster authentication failed. Check credentials for {redis_url_to_log}.")
    except redis_exceptions.RedisError as e:
        logger.error(f"FATAL: Could not establish initial Redis Cluster connection or runtime Redis error: {e}")
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, initiating shutdown...")
    except asyncio.CancelledError:
        logger.warning("Main chatbot service task cancelled. Performing shutdown.")
    except Exception as e:
        logger.exception(f"FATAL: An unexpected error occurred during chatbot service startup or runtime: {e}")
    finally:
        logger.info("--- Initiating final cleanup (finally block) for ChatbotKBWorker ---")
        await shutdown(
            sig=None,
            chatbot_redis_service=chatbot_queue_service_instance,
            db_service=db_service
        )
        logger.info("Exiting run_service function for ChatbotKBWorker after final cleanup.")


# --- Script Entry Point ---
if __name__ == "__main__":
    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught directly in __main__ (ChatbotKBWorker), application stopping.")
    except Exception as main_err:
        logger.exception(f"FATAL error during asyncio.run (ChatbotKBWorker) execution: {main_err}")
    finally:
        logging.info("Chatbot worker process exiting.")
