import asyncio
import logging
import signal
from functools import partial
import sys
from typing import Optional
from contextlib import AsyncExitStack

import aiohttp
import redis.exceptions as redis_exceptions

# Import necessary components
from app.config import Settings, get_settings
from app.controllers.flow_controller import process_message
from app.services.llm_factory import get_llm_service
from app.services.redis_service import RedisClusterService
from app.services.database_service import DatabaseService
from app.services.mysql_service import MySQLService


# --- Settings Loading ---
try:
    settings: Settings = get_settings()
except Exception as e:
    # Use print here as logging might not be configured yet
    print(f"FATAL: Unexpected error obtaining settings: {e}")
    sys.exit(1)

# --- Basic Setup ---
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Assign logger name for clarity
logger = logging.getLogger("LLMWorker")

# --- Log Loaded Settings (Mask sensitive info) ---
logger.info("--- Configuration Loaded ---")
logger.info(f"LOG_LEVEL: {settings.LOG_LEVEL}")
# Mask Redis credentials
redis_url_to_log = settings.REDIS_URL
if "@" in redis_url_to_log:
    url_parts = redis_url_to_log.split('@')
    proto_user = url_parts[0].split(':')
    redis_url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"
logger.info(f"REDIS_URL (Discovery Node): {redis_url_to_log}")
logger.info(f"REDIS_PASSWORD provided: {settings.REDIS_PASSWORD}")
logger.info(f"LLM_QUEUE_NAME: {settings.LLM_QUEUE_NAME}")
# --- Log DLQ/Retry Settings ---
logger.info(f"LLM_QUEUE_DLQ_NAME: {settings.LLM_QUEUE_DLQ_NAME}")
logger.info(f"LLM_QUEUE_MAX_RETRIES: {settings.LLM_QUEUE_MAX_RETRIES}")
# --- End DLQ/Retry Settings ---
# logger.info(f"GRAPH_QUEUE_NAME: {settings.GRAPH_QUEUE_NAME}")
logger.info(f"ANALYSIS_QUEUE_NAME: {settings.ANALYSIS_QUEUE_NAME}")
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

logger.info(f"MODEL_NAME: {settings.MODEL_NAME}")
logger.info(f"SESSION_EXPIRY_TIME: {settings.SESSION_EXPIRY_TIME}")
logger.info("--------------------------")

# Quieten noisy libraries
logging.getLogger('azure').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)


# --- Graceful Shutdown Handling ---
async def shutdown(
        sig: Optional[signal.Signals],
        listener_service: RedisClusterService, # The service running the worker loop
        stack: AsyncExitStack, # Pass the stack to close resources if needed early
        db_service: DatabaseService, # The database service to close connections
):
    """Gracefully shutdown the service."""
    signal_name = sig.name if sig else "Manual/Exception"
    logger.warning(f"Received exit signal {signal_name}... Shutting down LLM worker.")

    # 1. Signal the primary worker (listener) to stop its loop
    logger.info("Signalling LLM listener worker to stop...")
    if listener_service:
        await listener_service.stop_worker()
    else:
        logger.warning("Listener service instance not available during shutdown signal.")

    # 2. Cancel other running asyncio tasks (like ongoing llm_handler calls, HTTP requests)
    #    Give them a chance to finish cleanly if possible.
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        logger.info(f"Cancelling {len(tasks)} outstanding tasks...")
        for task in tasks:
            task.cancel()
        # Give tasks a moment to cancel, log results
        results = await asyncio.gather(*tasks, return_exceptions=True)
        cancelled_count = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
        error_count = sum(1 for r in results if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError))
        success_count = len(results) - cancelled_count - error_count
        logger.info(f"Outstanding tasks cancellation results: {len(results)} total, {success_count} finished, {cancelled_count} cancelled, {error_count} errored.")

    # Close the database connection
    logger.info("Disconnecting from database service...")
    try:
        await db_service.disconnect()
        logger.info("Database service disconnected.")
    except Exception as e:
        logger.exception("Error disconnecting from database service.")

    logger.info("Resource cleanup (Redis connections, HTTP Session) will be handled by context managers upon service exit.")

    logger.info("LLM worker shutdown sequence complete.")


# --- Main Service Runner ---
async def run_service():
    """Initializes Redis services, HTTP session, and runs the LLM queue listener."""
    loop = asyncio.get_running_loop()
    listener_service = None # Track the main service for shutdown/finally
    # Use AsyncExitStack for cleaner management of multiple async context managers
    async with AsyncExitStack() as stack:
        try:
            # --- Initialize SQL database
            logger.info("Initializing SQL database service...")
            db_service: DatabaseService
            if settings.DB_TYPE == "oracle":
                db_service = MySQLService(settings.DB_CONNECTION_STRING.get_secret_value())
            else:
                raise ValueError(f"Unsupported database type: {settings.DB_TYPE}")
            await db_service.connect()

            # --- Initialize LLM
            logger.info("Initializing LLM service...")
            llm_service = get_llm_service(settings)
            logger.info(f"LLM Service ({type(llm_service).__name__}) initialized.")

            # --- Initialize Redis Services ---
            logger.info("Initializing Redis services...")

            # # 1. Graph Queue Service (Sender) - Enters stack first
            # graph_queue_service = await stack.enter_async_context(
            #     RedisClusterService(
            #         queue_name=settings.GRAPH_QUEUE_NAME,
            #         redis_url=settings.REDIS_URL,
            #         redis_password=settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None,
            #         decode_responses=True
            #     )
            # )
            # logger.info(f"Graph Queue sender service initialized (target queue: {settings.GRAPH_QUEUE_NAME}).")

            # 2. Analysis Queue Service (Sender & Session Manager) - Enters stack second
            analysis_queue_service = await stack.enter_async_context(
                RedisClusterService(
                    queue_name=settings.ANALYSIS_QUEUE_NAME,
                    redis_url=settings.REDIS_URL,
                    redis_password=settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None,
                    decode_responses=True
                )
            )
            logger.info(f"Analysis Queue/Session service initialized (target queue: {settings.ANALYSIS_QUEUE_NAME}).")

            # 3. LLM Queue Service (Listener) - Enters stack last
            #    *** Initialize with DLQ/Retry settings ***
            llm_queue_service = await stack.enter_async_context(
                RedisClusterService(
                    queue_name=settings.LLM_QUEUE_NAME, # Queue it listens to
                    redis_url=settings.REDIS_URL,
                    redis_password=settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None,
                    decode_responses=True,
                    # *** DLQ/Retry Configuration for the LLM Worker ***
                    dlq_name=settings.LLM_QUEUE_DLQ_NAME,
                    max_retries=settings.LLM_QUEUE_MAX_RETRIES
                )
            )
            listener_service = llm_queue_service # Assign for signal handler and finally block
            logger.info(f"LLM Queue listener service initialized (watching queue: {settings.LLM_QUEUE_NAME}).")

            # --- Setup Signal Handlers ---
            shutdown_handler_partial = None
            if sys.platform != "win32":
                logger.info("Setting up signal handlers for SIGTERM and SIGINT.")
                # Pass the listener service and the stack to the shutdown handler
                shutdown_handler_partial = partial(shutdown, listener_service=listener_service, stack=stack)
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(
                        sig,
                        lambda s=sig: asyncio.create_task(shutdown_handler_partial(sig=s))
                    )
            else:
                logger.warning("Signal handlers not supported on Windows. Use Ctrl+C for manual shutdown.")

            # --- Run the Worker ---
            logger.info("LLM Queue Service worker starting...")
            logger.info(f"Watching queue: {settings.LLM_QUEUE_NAME}")
            if settings.LLM_QUEUE_DLQ_NAME:
                logger.info(f"DLQ Enabled: Queue='{settings.LLM_QUEUE_DLQ_NAME}', Max Retries={settings.LLM_QUEUE_MAX_RETRIES}")
            else:
                logger.info("DLQ Disabled for LLM queue.")

            # Log redis connection details once
            url_to_log = settings.REDIS_URL # Use the base URL logged earlier
            if "@" in url_to_log:
                url_parts = url_to_log.split('@')
                proto_user = url_parts[0].split(':')
                url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"
            logger.info(f"Connected to Redis Cluster via: {url_to_log}")

            # Prepare the processing function with dependencies using partial
            process_func_with_deps = partial(
                process_message,
                # graph_queue_service=graph_queue_service,
                analysis_queue_service=analysis_queue_service,
                db_service=db_service,
                llm_service=llm_service,
                settings=settings
            )

            # Run the worker loop - it now handles retries/DLQ internally
            await listener_service.run_worker(
                process_func=process_func_with_deps,
                brpop_timeout=settings.WORKER_BRPOP_TIMEOUT,
                error_retry_delay=settings.WORKER_ERROR_RETRY_DELAY # For Redis connection errors
            )

            logger.info("LLM Worker loop finished normally (likely due to shutdown signal).")

        # --- Exception Handling (Aligned with Template) ---
        except redis_exceptions.AuthenticationError:
            logger.error(f"FATAL: Redis Cluster authentication failed. Check credentials for {url_to_log}.")
            # ExitStack will handle cleanup
        except redis_exceptions.RedisError as e:
            logger.error(f"FATAL: Could not establish initial Redis Cluster connection or runtime Redis error: {e}")
            # ExitStack will handle cleanup
        except aiohttp.ClientError as e:
             logger.error(f"FATAL: Could not create aiohttp client session: {e}")
             # ExitStack will handle cleanup
        except Exception as e:
            logger.exception(f"FATAL: An unexpected error occurred during service startup or runtime: {e}")
            # ExitStack handles cleanup; consider if explicit shutdown call is needed for tasks
            if listener_service: # If listener was created, attempt task cancellation via shutdown
                logger.info("Attempting graceful shutdown after unexpected error...")
                await shutdown(None, listener_service, stack) # Pass None signal

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, initiating shutdown...")
            # Shutdown handler should be triggered by signal handler setup above
            # If not (e.g., Windows Ctrl+C before loop start), ExitStack handles cleanup.
        except asyncio.CancelledError:
            logger.warning("Main LLM service task cancelled. Performing shutdown.")
            # Ensure shutdown logic runs
            if listener_service:
                await shutdown(None, listener_service, stack)

        finally:
            logger.info("Exiting run_service. Resources will be cleaned up by AsyncExitStack.")
            # The `async with AsyncExitStack()` ensures __aexit__ is called for all
            # successfully entered contexts (database ser, redis services)


# --- Script Entry Point ---
if __name__ == "__main__":
    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        # This might catch a KeyboardInterrupt that happens *before* run_service's try block
        logger.info("KeyboardInterrupt caught directly in __main__, application stopping.")
    except Exception as main_err:
        # Catch errors during asyncio.run() itself or very early setup errors
        logger.exception(f"FATAL error during asyncio.run execution or pre-loop setup: {main_err}")
    finally:
        # Final log confirms the process itself is exiting.
        logging.info("LLM worker process exiting.")