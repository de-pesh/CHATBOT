import asyncio
import json
import logging
import signal
from functools import partial
import sys
from typing import Dict, Any, Optional

import aiohttp

# Import necessary components
from app.config import Settings, get_settings
# *** Use the UPDATED RedisClusterService ***
from app.services.redis_service import RedisClusterService
from app.models.error_models import SessionNotFoundException
import redis.exceptions as redis_exceptions
import grpc
from app import parsing_service_pb2
from app import parsing_service_pb2_grpc

# Removed get_uuid and stringify_json as they are less needed now
# from app.utils.general_utils import get_uuid, stringify_json


# --- Settings Loading ---
try:
    settings: Settings = get_settings()
except Exception as e:
    # Use logging configured below if possible, otherwise print
    print(f"FATAL: Unexpected error obtaining settings: {e}")
    # logging.exception(...) won't work until basicConfig is called
    sys.exit(1)

# --- Basic Setup ---
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s', # Added logger name
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Assign logger name for clarity
logger = logging.getLogger("ParserWorker")


# --- Log Loaded Settings (Mask sensitive info) ---
logger.info("--- Configuration Loaded ---")
logger.info(f"LOG_LEVEL: {settings.LOG_LEVEL}")
# Mask credentials if present in URL
url_to_log = settings.REDIS_URL
if "@" in url_to_log:
    url_parts = url_to_log.split('@')
    proto_user = url_parts[0].split(':')
    url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"
logger.info(f"REDIS_URL (Discovery Node): {url_to_log}")
logger.info(f"REDIS_PASSWORD provided: {settings.REDIS_PASSWORD}") # Avoid logging the secret itself
logger.info(f"PARSER_QUEUE_NAME: {settings.PARSER_QUEUE_NAME}")
# --- Log DLQ/Retry Settings ---
logger.info(f"PARSER_QUEUE_DLQ_NAME: {settings.PARSER_QUEUE_DLQ_NAME}")
logger.info(f"PARSER_QUEUE_MAX_RETRIES: {settings.PARSER_QUEUE_MAX_RETRIES}")
# --- End DLQ/Retry Settings ---
logger.info(f"INDEX_QUEUE_NAME: {settings.INDEX_QUEUE_NAME}")
logger.info(f"WORKER_BRPOP_TIMEOUT: {settings.WORKER_BRPOP_TIMEOUT}")
logger.info(f"WORKER_ERROR_RETRY_DELAY: {settings.WORKER_ERROR_RETRY_DELAY}")
logger.info(f"DATABASE_SERVICE_URL: {settings.DATABASE_SERVICE_URL.get_secret_value()}") # Log URL, assumes it's not secret itself
logger.info(f"SESSION_EXPIRY_TIME: {settings.SESSION_EXPIRY_TIME}")
logger.info(f"RPC_SERVER: {settings.RPC_SERVER}")
logger.info("--------------------------")

# --- Initialize Services ---
logging.getLogger('azure').setLevel(logging.WARNING) # Quieten Azure SDK logs

# --- gRPC Client Setup ---
# This is the asynchronous gRPC client setup for the parsing service.
async def run(file_path):
    # 1. Use the asynchronous channel from grpc.aio
    async with grpc.aio.insecure_channel(f"{settings.RPC_SERVER}") as channel:
        stub = parsing_service_pb2_grpc.ParsingServiceStub(channel)
        
        # 2. Use 'await' to make the non-blocking RPC call
        response = await stub.ParseFile(parsing_service_pb2.ParseFileRequest(file_path=file_path))
        
        logging.info(f"File parsed successfully, response: {response}")
        
        # 3. Return the result. It's good practice to do this inside the
        # 'async with' block, as the response is tied to this context.
        return response.blob_path

# --- Specific Message Processing Logic ---
# This function now only needs to perform the core task and raise exceptions on failure.
# The RedisClusterService.run_worker will handle catching these exceptions for retry/DLQ.
async def process_message(payload: Dict[str, Any], operation_id: str):
    """
    Process parser queue messages. Raises exceptions on failure.

    Args:
        payload: The dictionary extracted from the JSON message's 'original_payload' field.
        operation_id: The string extracted from the JSON message's 'metadata.operation_id' field.
                      (This comes from the *wrapped* message structure now)
    """
    logger.info(f"[Processor] Task {operation_id}: Processing payload.")
    # Log full payload only at DEBUG level
    logger.debug(f"[Processor] Payload for {operation_id}: {payload}")

    # --- Extract required fields ---
    file_path = payload.get("file_path") # Original path of the file to parse
    analysis_id = payload.get("analysis_id") # Session/analysis identifier
    file_id = payload.get("file_id") # Specific ID for this file within the analysis
    control_label = payload.get("control_label") # Control label for this file within the analysis
    standard_id = payload.get("standard_id")
    version_id = payload.get("version_id","")

    # --- Validate required fields ---
    # If validation fails, we raise ValueError. run_worker will catch it.
    # Whether this should trigger retries depends on if it's a transient issue.
    # For missing essential data, it's likely permanent, so retries might not help.
    # However, the current retry logic *will* attempt retries before DLQing.
    # If you want to immediately DLQ for certain errors, run_worker would need modification.
    if not file_path:
        raise ValueError(f"Task {operation_id}: Missing 'file_path' in payload.")
    if not analysis_id:
        raise ValueError(f"Task {operation_id}: Missing 'analysis_id' in payload.")
    if not file_id:
        raise ValueError(f"Task {operation_id}: Missing 'file_id' in payload.")
    if not control_label:
        raise ValueError(f"Task {operation_id}: Missing 'control_label' in payload.")
    if not standard_id:
        raise ValueError(f"Task {operation_id}: Missing 'standard_id' in payload.")
    if not version_id:
        raise ValueError(f"Task {operation_id}: Missing 'version_id' in payload.")

    blob_path = None # Initialize blob_path

    # The try...except block remains to handle specific error types if needed,
    # but the primary goal now is to let exceptions propagate upwards to run_worker.
    try:
        # --- Step 1: Parse the document and upload text to Azure Blob ---
        logger.info(f"[Processor] Task {operation_id}: Starting parsing for file '{file_path}'.")
        # Call the RPC service to parse the file
        blob_path = await run(file_path)

        # parsing_handler should raise an exception on failure. Check if it does.
        if not blob_path:
             # If parsing_handler might return None/empty on failure instead of raising:
             raise ValueError(f"Parsing handler failed or returned empty blob path for task {operation_id}, file {file_path}")


        # --- Step 2: Update the main service via API ---
        update_payload = {
            "parsed_file_path": blob_path # URL/path of the *parsed text* blob
        }
        # Construct URL carefully
        base_url = settings.DATABASE_SERVICE_URL.get_secret_value().rstrip('/')
        update_analysis_url = f"{base_url}/analysis/{analysis_id}/controls/{control_label}/files/{file_id}/parsed-path"

        logger.info(f"[Processor] Task {operation_id}: Sending update to main service at {update_analysis_url} for file {file_id}.")

        # Use a single session for potentially multiple calls if needed later
        async with aiohttp.ClientSession() as http_session:
            async with http_session.patch(update_analysis_url, json=update_payload) as response:
                # raise_for_status() will raise ClientResponseError for 4xx/5xx,
                # which will be caught by run_worker for retry/DLQ.
                response.raise_for_status()
                logger.info(f"[Processor] Task {operation_id}: Main service successfully updated (HTTP {response.status}) for file {file_id}.")


        # --- Step 3: Update Redis session and send message to Index Queue ---
        # Create a *separate* Redis service instance specifically for interacting
        # with the index queue and session data *within this task processing*.
        # This instance is short-lived for this specific task.
        # It does NOT use the DLQ/retry settings of the main parser queue worker.
        async with RedisClusterService(
            queue_name=settings.INDEX_QUEUE_NAME, # Target queue for the *next* step
            redis_url=settings.REDIS_URL,
            redis_password=settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None,
            decode_responses=True
        ) as index_queue_redis_service:
            # Get current session data - raises SessionNotFoundException if not found
            request_data = await index_queue_redis_service.get_user_session(analysis_id)
            logger.debug(f"[Processor] Task {operation_id}: Retrieved session data for analysis_id {analysis_id}.")

            # Increment parsed count safely
            current_parsed = request_data.get("parsed_files", 0)
            total_files = request_data.get("total_files", 0) # Assume total_files exists
            updated_parsed_count = current_parsed + 1
            request_data["parsed_files"] = updated_parsed_count

            # Update the session in Redis - raises RedisError on failure
            await index_queue_redis_service.add_or_update_session(
                request_data,
                int(settings.SESSION_EXPIRY_TIME)
            )
            logger.info(f"[Processor] Task {operation_id}: Updated session {analysis_id}, parsed count: {updated_parsed_count}/{total_files}")

            # Prepare payload for the indexer queue
            # The RedisClusterService.send_message now expects a dict payload
            index_queue_payload = {
                "text_file_path": blob_path,
                "analysis_id": analysis_id,
                "file_id": file_id,
                "control_label": control_label,
                "standard_id": standard_id,
                "version_id": version_id
            }

            # Send message to the indexer queue using the correct method signature
            # Let the service generate the operation_id for the indexing task
            await index_queue_redis_service.send_message(payload=index_queue_payload)
            logger.info(f"[Processor] Task {operation_id}: Message sent to index queue '{settings.INDEX_QUEUE_NAME}' for blob '{blob_path}'.")

        # If all steps succeed without raising an exception:
        logger.info(f"[Processor] Successfully completed all steps for task {operation_id}")

    # Specific exceptions can be caught here for logging if desired,
    # but they MUST be re-raised or allowed to propagate for run_worker to handle retries.
    except aiohttp.ClientResponseError as e:
        logger.error(f"[Processor] Task {operation_id}: HTTP error calling main service API: {e.status} {e.message}. URL: {e.request_info.url}")
        raise # Re-raise for run_worker retry/DLQ handling
    except SessionNotFoundException as e:
        logger.error(f"[Processor] Task {operation_id}: Session not found for analysis_id '{analysis_id}'. Cannot update status or trigger indexing.")
        # This might be a permanent failure, but let run_worker handle retry/DLQ for consistency
        raise # Re-raise
    except redis_exceptions.RedisError as e:
        logger.error(f"[Processor] Task {operation_id}: Redis Cluster error interacting with session or index queue: {e}")
        raise # Re-raise
    except ValueError as e: # Catch specific validation/parsing errors
        logger.error(f"[Processor] Task {operation_id}: Data or parsing error: {e}")
        raise # Re-raise
    except Exception as e:
        # Catch any other unexpected error during processing
        logger.exception(f"[Processor] Task {operation_id}: Unexpected error processing file '{file_path}': {e}")
        raise # Re-raise any other exception to trigger retry/DLQ

    # No need for a final "Finished handling task" log here, success is logged above,
    # and run_worker logs successful processing completion.


# --- Graceful Shutdown Handling ---
async def shutdown(
        sig: Optional[signal.Signals],
        redis_service: RedisClusterService # Keep type hint
):
    """Gracefully shutdown the service."""
    signal_name = sig.name if sig else "Manual/Exception"
    logger.warning(f"Received exit signal {signal_name}... Shutting down.")

    # 1. Signal the worker to stop its loop
    logger.info("Signaling Redis worker to stop...")
    if redis_service:
        await redis_service.stop_worker()
    else:
        logger.warning("Redis service instance not available during shutdown signal.")

    # 2. Cancel other running asyncio tasks (like ongoing HTTP requests, parsing)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        logger.info(f"Cancelling {len(tasks)} outstanding tasks...")
        for task in tasks:
            task.cancel()
        # Give tasks a moment to cancel, log results
        results = await asyncio.gather(*tasks, return_exceptions=True)
        cancelled_count = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
        error_count = sum(1 for r in results if isinstance(r, Exception))
        logger.info(f"Outstanding tasks cancellation results: {len(results)} total, {cancelled_count} cancelled, {error_count} errored.")

    # Redis connection is closed automatically by the 'async with' block exiting
    # or explicitly if shutdown is called due to an error before 'async with' finishes.
    if redis_service and redis_service._client: # Check if client exists (might be None if init failed)
         logger.info("Redis connection will be closed by context manager or explicit call.")


    logger.info("Shutdown sequence complete.")


# --- Main Service Runner ---
async def run_service():
    """Initializes the service and runs the worker indefinitely."""
    loop = asyncio.get_running_loop()
    parser_queue_service_instance = None # Define outside try/async with

    try:
        # *** Instantiate RedisClusterService with DLQ/Retry settings ***
        async with RedisClusterService(
            queue_name=settings.PARSER_QUEUE_NAME,
            redis_url=settings.REDIS_URL,
            redis_password=settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None,
            decode_responses=True,
            dlq_name=settings.PARSER_QUEUE_DLQ_NAME,
            max_retries=settings.PARSER_QUEUE_MAX_RETRIES
        ) as parser_queue_service:
            parser_queue_service_instance = parser_queue_service # Assign for potential use in exception handlers/shutdown

            # Setup signal handlers (non-Windows)
            shutdown_handler_partial = None
            if sys.platform != "win32":
                logger.info("Setting up signal handlers for SIGTERM and SIGINT.")
                # Pass the created instance to the shutdown handler
                shutdown_handler_partial = partial(shutdown, redis_service=parser_queue_service_instance)
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(
                        sig,
                        lambda s=sig: asyncio.create_task(shutdown_handler_partial(sig=s)) # Use the instance here
                    )
            else:
                logger.warning("Signal handlers not supported on Windows. Use Ctrl+C for manual shutdown.")

            logger.info("Redis Cluster Queue Service (Parser) worker starting...")
            logger.info(f"Watching queue: {settings.PARSER_QUEUE_NAME}")
            if settings.PARSER_QUEUE_DLQ_NAME:
                logger.info(f"DLQ Enabled: Queue='{settings.PARSER_QUEUE_DLQ_NAME}', Max Retries={settings.PARSER_QUEUE_MAX_RETRIES}")
            else:
                logger.info("DLQ Disabled for parser queue.")

            # Log redis URL again here if needed, masking creds
            url_to_log = settings.REDIS_URL
            if "@" in url_to_log:
                url_parts = url_to_log.split('@')
                proto_user = url_parts[0].split(':')
                url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"
            logger.info(f"Connected to Redis Cluster via: {url_to_log}")

            # Run the worker loop - it now handles retries/DLQ internally
            await parser_queue_service.run_worker(
                process_func=process_message, # The function that raises errors on failure
                brpop_timeout=settings.WORKER_BRPOP_TIMEOUT,
                error_retry_delay=settings.WORKER_ERROR_RETRY_DELAY # For Redis connection errors
            )

            logger.info("Parser worker loop finished normally (likely due to shutdown signal).")

    # --- Exception Handling ---
    # Catch specific fatal errors during startup/connection
    except redis_exceptions.AuthenticationError:
        logger.error(f"FATAL: Redis Cluster authentication failed. Check credentials for {url_to_log}.")
        # No service instance likely exists here, so shutdown can't do much with it.
    except redis_exceptions.RedisError as e:
        logger.error(f"FATAL: Could not establish initial Redis Cluster connection or runtime Redis error: {e}")
        # No service instance likely exists here.
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, initiating shutdown...")
        # Call shutdown if the service instance was successfully created
        if parser_queue_service_instance:
            await shutdown(None, parser_queue_service_instance)
        else:
            logger.warning("Service instance not created before KeyboardInterrupt.")

    except asyncio.CancelledError:
        logger.warning("Main service task cancelled. Performing shutdown.")
        # Call shutdown if the service instance exists
        if parser_queue_service_instance:
            await shutdown(None, parser_queue_service_instance)

    except Exception as e:
        logger.exception(f"FATAL: An unexpected error occurred during service startup or runtime: {e}")
        # Attempt graceful shutdown if the service instance was created
        if parser_queue_service_instance:
            logger.info("Attempting graceful shutdown after unexpected error...")
            await shutdown(None, parser_queue_service_instance)
        else:
             logger.warning("Service instance not created before unexpected error.")

    finally:
        # Final cleanup check, although 'async with' and shutdown should handle most cases
        logger.info("Ensuring external resources are closed in finally block...")
        # Redis connection is managed by the 'async with' block or shutdown handler

        logger.info("Exiting run_service function.")


# --- Script Entry Point ---
if __name__ == "__main__":
    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        # This might catch a KeyboardInterrupt that happens *before* the run_service's try block
        logger.info("KeyboardInterrupt caught directly in __main__, application stopping.")
    except Exception as main_err:
        # Catch errors during asyncio.run() itself, unlikely but possible
        logger.exception(f"FATAL error during asyncio.run execution: {main_err}")
    finally:
        # This log confirms the process itself is exiting
        logger.info("Parser worker process exiting.") # Use logging module directly here
