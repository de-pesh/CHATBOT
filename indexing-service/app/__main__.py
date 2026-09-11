import asyncio
import logging
import signal
from functools import partial
import sys
from typing import Dict, Any, Optional

import aiohttp
import redis.exceptions as redis_exceptions
import grpc
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct
from app import indexing_service_pb2
from app import indexing_service_pb2_grpc
from app.config import Settings, get_settings
from app.models.error_models import SessionNotFoundException
from app.services.redis_service import RedisClusterService
from app import myrpc_pb2
from app import myrpc_pb2_grpc


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
logger = logging.getLogger("IndexWorker")


# --- Log Loaded Settings (Mask sensitive info) ---
logger.info("--- Configuration Loaded ---")
logger.info(f"LOG_LEVEL: {settings.LOG_LEVEL}")
url_to_log = settings.REDIS_URL
if "@" in url_to_log:
    url_parts = url_to_log.split('@')
    proto_user = url_parts[0].split(':')
    url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"
logger.info(f"REDIS_URL (Discovery Node): {url_to_log}")
logger.info(f"REDIS_PASSWORD provided: {settings.REDIS_PASSWORD}")
logger.info(f"INDEX_QUEUE_NAME: {settings.INDEX_QUEUE_NAME}")
logger.info(f"INDEX_QUEUE_DLQ_NAME: {settings.INDEX_QUEUE_DLQ_NAME}")
logger.info(f"INDEX_QUEUE_MAX_RETRIES: {settings.INDEX_QUEUE_MAX_RETRIES}")
logger.info(f"ANALYSIS_QUEUE_NAME: {settings.ANALYSIS_QUEUE_NAME}")
logger.info(f"WORKER_BRPOP_TIMEOUT: {settings.WORKER_BRPOP_TIMEOUT}")
logger.info(f"WORKER_ERROR_RETRY_DELAY: {settings.WORKER_ERROR_RETRY_DELAY}")
logger.info(f"SESSION_EXPIRY_TIME: {settings.SESSION_EXPIRY_TIME}")
logger.info(f"RPC_SERVER: {settings.RPC_SERVER}")
db_service_url_to_log = getattr(settings, 'DATABASE_SERVICE_URL', 'Not Configured')
logger.info(f"DATABASE_SERVICE_URL for error logging: {db_service_url_to_log}")
logger.info("--------------------------")

# --- Initialize Services ---
logging.getLogger('azure').setLevel(logging.WARNING)
logging.getLogger('motor').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)

async def testrpc():
    async with grpc.aio.insecure_channel(f"{'localhost:50051'}") as channel:
        stub = myrpc_pb2_grpc.TestStub(channel)
        request = myrpc_pb2.myargument(argumentvariable1="Hello from Index Worker")
        try:
            response = await stub.MyFunction(request)
            logger.info(f"TestRPC Response: {response.message}")
        except grpc.aio.AioRpcError as e:
            logger.error(f"RPC error during TestRPC: {e.code()} - {e.details()}")

async def run(
        analysis_id:str,
        file_path:str,
        metadata:dict,
        is_policy:bool = False
        ):  
    print("TESTING MY SERVER....\nRESPONSE:")
    print(testrpc())
    print("TESTING MY SERVER....DONE")
    async with grpc.aio.insecure_channel(f"{settings.RPC_SERVER}") as channel:
        stub = indexing_service_pb2_grpc.VectorServiceStub(channel)
        logger.debug(f"--- 1. INDEXING: {file_path} into collection '{analysis_id}' ---")
        metadata_struct = indexing_service_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
        ParseDict(metadata, metadata_struct)
        index_request = indexing_service_pb2.IndexRequest(
            collection_name=analysis_id,
            file_path=file_path,
            metadata=metadata_struct,
            is_policy=is_policy
        )
        try:
            index_response = await stub.Index(index_request)
            if index_response.success and index_response.file_id:
                source_file_id = index_response.file_id
                logger.debug(f"Success: Document indexed with file_id: {source_file_id}\n")
            else:
                error_message = (
                    f"Indexing failed for '{file_path}' into collection "
                    f"'{analysis_id}': {index_response.message}"
                )
                logger.error(error_message)
                raise RuntimeError(error_message)
        except grpc.aio.AioRpcError as e:
            logger.error(
                f"RPC error during Index for '{file_path}': {e.code()} - {e.details()}"
            )
            raise



# --- Specific Message Processing Logic ---
async def process_message(payload: Dict[str, Any], operation_id: str):
    logger.info(f"[Processor] Task {operation_id}: Processing payload.")
    logger.debug(f"[Processor] Payload for {operation_id}: {payload}")

    file_path = payload.get("text_file_path")
    analysis_id = payload.get("analysis_id")
    standard_id = payload.get("standard_id")
    control_label = payload.get("control_label")
    version_id = payload.get("version_id")

    if not file_path:
        raise ValueError(f"Task {operation_id}: Missing 'text_file_path' in payload.")
    if not analysis_id:
        raise ValueError(f"Task {operation_id}: Missing 'analysis_id' in payload.")
    if not standard_id:
        raise ValueError(f"Task {operation_id}: Missing 'standard_id' in payload.")
    if not version_id:
        raise ValueError(f"Task {operation_id}: Missing 'version_id' in payload.")
    if not control_label:
        raise ValueError(f"Task {operation_id}: Missing 'control_label' in payload.")

    try:
        logger.info(f"[Processor] Task {operation_id}: Starting indexing for text blob '{file_path}'.")
        metadata = {"control_label": control_label}
        await run(
            analysis_id=analysis_id,
            file_path=file_path,
            metadata=metadata,
            is_policy=False
        )
        logger.info(f"[Processor] Task {operation_id}: Indexing complete for text blob '{file_path}'.")

        async with RedisClusterService(
            queue_name=settings.ANALYSIS_QUEUE_NAME,
            redis_url=settings.REDIS_URL,
            redis_password=settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None,
            decode_responses=True
            # No DLQ/Retry/DBLog for this specific internal Redis use
        ) as session_redis_service:
            request_data = await session_redis_service.get_user_session(analysis_id)
            logger.debug(f"[Processor] Task {operation_id}: Retrieved session data for analysis_id {analysis_id}.")

            current_indexed = request_data.get("indexed_files", 0)
            total_files = request_data.get("total_files", 0)
            updated_indexed_count = current_indexed + 1
            request_data["indexed_files"] = updated_indexed_count

            await session_redis_service.add_or_update_session(
                request_data,
                int(settings.SESSION_EXPIRY_TIME)
            )
            logger.info(f"[Processor] Task {operation_id}: Updated session {analysis_id}, indexed count: {updated_indexed_count}/{total_files}")

            if total_files > 0 and updated_indexed_count == total_files:
                logger.info(f"[Processor] Task {operation_id}: All {total_files} files indexed for analysis {analysis_id}. Sending message to '{settings.ANALYSIS_QUEUE_NAME}'.")
                analysis_queue_payload = {
                    "analysis_id": analysis_id,
                    "standard_id": standard_id,
                    "version_id": version_id,
                    "label": "Criteria"
                }
                await session_redis_service.send_message(payload=analysis_queue_payload, priority=3) # Lower is higher prio
                logger.info(f"[Processor] Task {operation_id}: Message sent to analysis queue '{settings.ANALYSIS_QUEUE_NAME}' for analysis_id '{analysis_id}'.")
            elif total_files <= 0:
                logger.warning(f"[Processor] Task {operation_id}: Session {analysis_id} has 'total_files' <= 0. Cannot determine completion status.")
            else:
                 logger.info(f"[Processor] Task {operation_id}: Analysis {analysis_id} not yet complete ({updated_indexed_count}/{total_files} indexed). No message sent to analysis queue.")

        logger.info(f"[Processor] Successfully completed all steps for task {operation_id}")

    except SessionNotFoundException as e:
        logger.error(f"[Processor] Task {operation_id}: Session not found for analysis_id '{analysis_id}'. Cannot update status or trigger next step.")
        raise
    except redis_exceptions.RedisError as e:
        logger.error(f"[Processor] Task {operation_id}: Redis Cluster error interacting with session or analysis queue: {e}")
        raise
    except ValueError as e:
        logger.error(f"[Processor] Task {operation_id}: Data or indexing error: {e}")
        raise
    except Exception as e:
        logger.exception(f"[Processor] Task {operation_id}: Unexpected error processing text blob '{file_path}': {e}")
        raise


# --- Graceful Shutdown Handling ---
async def shutdown(
        sig: Optional[signal.Signals],
        redis_service: Optional[RedisClusterService], # Can be None if init failed
        http_client: Optional[aiohttp.ClientSession]
):
    signal_name = sig.name if sig else "Manual/Exception"
    logger.warning(f"Received exit signal {signal_name}... Shutting down.")

    if redis_service:
        logger.info("Signaling Redis worker to stop...")
        await redis_service.stop_worker()
    else:
        logger.warning("Redis service instance not available or not initialized during shutdown signal.")

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        logger.info(f"Cancelling {len(tasks)} outstanding tasks...")
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        cancelled_count = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
        error_count = sum(1 for r in results if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError))
        logger.info(f"Outstanding tasks cancellation results: {len(results)} total, {cancelled_count} cancelled, {error_count} errored.")

    logger.info("Closing external connections (HTTP, Mongo, Azure Blob)...")

    # Close aiohttp.ClientSession
    if http_client and not http_client.closed:
        logger.info("Closing aiohttp.ClientSession...")
        try:
            await http_client.close()
            logger.info("aiohttp.ClientSession closed successfully.")
        except Exception as e:
            logger.error(f"Error closing aiohttp.ClientSession: {e}")

    # Redis connection for index_queue_service is closed by its 'async with' block
    # or explicitly if shutdown is called due to an error before 'async with' finishes.
    if redis_service and redis_service._client:
         logger.info("Redis connection (main worker) will be closed by context manager or explicit call in run_service.")

    logger.info("Shutdown sequence complete.")


# --- Main Service Runner ---
async def run_service():
    loop = asyncio.get_running_loop()
    index_queue_service_instance: Optional[RedisClusterService] = None
    http_client_session: Optional[aiohttp.ClientSession] = None # Define for broader scope

    try:
        # Create aiohttp client session
        http_client_session = aiohttp.ClientSession()
        logger.info("aiohttp.ClientSession created.")

        # Determine db_service_url (assuming 'DATABASE_SERVICE_URL' is the attribute name in settings)
        db_service_url_for_redis = getattr(settings, 'DATABASE_SERVICE_URL', None)
        if db_service_url_for_redis:
            logger.info(f"DB error logging enabled for Redis worker, target URL: {db_service_url_for_redis}")
        else:
            logger.info("DB error logging disabled for Redis worker (DATABASE_SERVICE_URL not configured).")


        async with RedisClusterService(
            queue_name=settings.INDEX_QUEUE_NAME,
            redis_url=settings.REDIS_URL,
            redis_password=settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None,
            decode_responses=True,
            dlq_name=settings.INDEX_QUEUE_DLQ_NAME,
            max_retries=settings.INDEX_QUEUE_MAX_RETRIES,
            db_service_url=db_service_url_for_redis
        ) as index_queue_service:
            index_queue_service_instance = index_queue_service

            shutdown_handler_partial = None
            if sys.platform != "win32":
                logger.info("Setting up signal handlers for SIGTERM and SIGINT.")
                shutdown_handler_partial = partial(
                    shutdown,
                    redis_service=index_queue_service_instance,
                    http_client=http_client_session # Pass http_client_session
                )
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(
                        sig,
                        lambda s=sig: asyncio.create_task(shutdown_handler_partial(sig=s))
                    )
            else:
                logger.warning("Signal handlers not supported on Windows. Use Ctrl+C for manual shutdown.")

            logger.info("Redis Cluster Queue Service (Index) worker starting...")
            logger.info(f"Watching queue: {settings.INDEX_QUEUE_NAME} (using BZPOPMIN)")
            if settings.INDEX_QUEUE_DLQ_NAME:
                logger.info(f"DLQ Enabled: Queue='{settings.INDEX_QUEUE_DLQ_NAME}', Max Retries={settings.INDEX_QUEUE_MAX_RETRIES}")
            else:
                logger.info("DLQ Disabled for index queue.")

            redis_conn_url_log = settings.REDIS_URL
            if "@" in redis_conn_url_log:
                redis_conn_url_log = f"{redis_conn_url_log.split('@')[0].split(':')[0]}:...@ {redis_conn_url_log.split('@')[-1]}"
            logger.info(f"Connected to Redis Cluster via: {redis_conn_url_log}")

            await index_queue_service.run_worker(
                process_func=process_message,
                brpop_timeout=settings.WORKER_BRPOP_TIMEOUT, # For BZPOPMIN timeout
                error_retry_delay=settings.WORKER_ERROR_RETRY_DELAY,
                http_client=http_client_session # Pass the http client session
            )
            logger.info("Index worker loop finished normally (likely due to shutdown signal).")

    except redis_exceptions.AuthenticationError:
        logger.error(f"FATAL: Redis Cluster authentication failed. Check credentials for {url_to_log}.")
        # Shutdown will be called in finally block
    except redis_exceptions.RedisError as e:
        logger.error(f"FATAL: Could not establish initial Redis Cluster connection or runtime Redis error: {e}")
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, initiating shutdown...")
        # Shutdown function will be called in finally if service instance exists
    except asyncio.CancelledError:
        logger.warning("Main service task cancelled. Performing shutdown.")
        # Shutdown function will be called in finally
    except Exception as e:
        logger.exception(f"FATAL: An unexpected error occurred during service startup or runtime: {e}")
        # Shutdown function will be called in finally
    finally:
        logger.info("--- Initiating final cleanup (finally block) ---")
        # Call shutdown explicitly here to ensure cleanup if not already called
        # or if an error occurred before signal handlers were set up
        # It's safe to call shutdown multiple times (idempotent for resource closing)
        await shutdown(
            sig=None, # No specific signal
            redis_service=index_queue_service_instance,
            http_client=http_client_session
        )
        logger.info("Exiting run_service function after final cleanup.")


# --- Script Entry Point ---
if __name__ == "__main__":
    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught directly in __main__, application stopping cleanly.")
    except Exception as main_err:
        logger.exception(f"FATAL error during asyncio.run execution: {main_err}")
    finally:
        logging.info("Index worker process exiting.")
