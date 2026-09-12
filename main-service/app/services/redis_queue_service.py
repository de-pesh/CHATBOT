"""Redis implementation of MessageQueueService.

A sorted set used as a priority queue: ZADD to produce, BZPOPMIN to consume,
with a retry count carried in each message and a dead-letter queue for tasks
that exhaust it.

This was the second of two RedisQueueService classes. The other lived in
services/redis_service.py, produced messages without the retry metadata this
one's consumer expects, and carried session helpers nothing called; it is gone
and everything now produces and consumes through this one class.
"""
import asyncio
import logging
from datetime import datetime
import json
import uuid
from typing import Optional, Dict, Any

import aiohttp # Ensure aiohttp is installed
from redis.asyncio import Redis
import redis.exceptions as redis_exceptions

from app.services.queue_service import MessageQueueService, ProcessFuncType



# --- helpers, previously kb_worker/utils.py and kb_worker/errors.py ---
# Both were single-purpose modules used only from this file.

def parse_json(stringified_json: str) -> dict:
    """
    Parses a JSON-formatted string into a dictionary.

    Args:
        stringified_json (str): The JSON string to parse.

    Returns:
        dict: The parsed dictionary if successful, otherwise None.
    """
    try:
        return json.loads(stringified_json)
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON for string: {stringified_json!r}")


def get_current_microsecond_timestamp():
    """
    Returns the current UTC time in microseconds since epoch.
    """
    now = datetime.now()
    return int(now.timestamp() * 1_000_000)


async def add_error_log(
        operation_id: str,
        message: str,
        http_client: aiohttp.ClientSession,
        db_service_url: str
):
    """
    Asynchronously logs an error message to an external database service for chatbot operations.

    Args:
        operation_id (str): The operation or task ID related to the error.
        message (str): The error message to log.
        http_client (aiohttp.ClientSession): The aiohttp client session to use for the HTTP request.
        db_service_url (str): The base URL of the external database logging service.

    Returns:
        None

    Prints:
        Success or failure messages for debugging purposes.
    """
    try:
        url_path = f"{db_service_url}/chatbotKB/logs"

        async with http_client.patch(url_path, json={"service": "chatbot_service", "operation_id": operation_id, "message": message}, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status >= 400:
                print(f"AsyncDBLogHandler: Failed to log to DB (status {response.status}). URL: {url_path}. Response: {await response.text()}")
            else:
                print("AsyncDBLogHandler: Successfully logged to DB.") # For debugging
    except aiohttp.ClientConnectorError as e:
        print(f"AsyncDBLogHandler: Connection error for DB logging: {e}")


class RedisQueueService(MessageQueueService):
    """
    an asynchronous service for interacting with Redis.

    Provides methods to use a Redis list as a reliable message queue (LPUSH/BRPOP)
    with Dead Letter Queue (DLQ) support and retry mechanism. Also manages
    key-value session data (SETEX/GET). All operations are routed
    to the appropriate cluster node based on the key.

    Supports graceful shutdown and automatic resource management via 'async with'.
    Includes optional integration for logging errors to an external DB service.
    """
    def __init__(
        self,
        queue_name: str,
        redis_url: str, # URL should point to one of the cluster nodes for discovery
        redis_password: Optional[str] = None, # Password can be in URL or separate
        decode_responses: bool = True, # Default to decoding responses to strings
        dlq_name: Optional[str] = None, # Name of the Dead Letter Queue (optional)
        max_retries: int = 3, # Max attempts before moving to DLQ
        db_service_url: Optional[str] = None, # URL for the external DB logging service
        padding: Optional[int] = 35_000_000_000_000, #Padding for score calculation
    ):
        """
        Initializes the service configuration for Redis Cluster.

        Args:
            queue_name: The name of the primary Redis list queue.
            redis_url: Connection URL for Redis Cluster discovery.
            redis_password: Optional password.
            decode_responses: Decode Redis responses to strings (UTF-8).
            dlq_name: Optional name for the Dead Letter Queue. If None, failed
                      messages are not retried or moved.
            max_retries: Maximum number of processing attempts before a message
                         is moved to the DLQ (if dlq_name is set). Must be >= 0.
            db_service_url: Optional base URL for an external service to log errors.
                            If provided, requires http_client in run_worker.
        """
        if not queue_name:
            raise ValueError("queue_name cannot be empty.")
        if not redis_url:
            raise ValueError("redis_url cannot be empty.")
        if dlq_name is not None and max_retries < 0:
             raise ValueError("max_retries must be non-negative when dlq_name is specified.")
        if dlq_name == queue_name:
             raise ValueError("dlq_name cannot be the same as queue_name.")


        self.APP_PREFIX = "CPX_evidence_analyser:session:"
        self.queue_name = queue_name
        self.redis_url = redis_url
        self.redis_password = redis_password
        self.decode_responses = decode_responses
        self.dlq_name = dlq_name
        self.max_retries = max_retries if dlq_name else 0 # Retries only active if DLQ is set
        self.db_service_url = db_service_url
        self.padding = padding

        self._client: Optional[Redis] = None
        self._shutdown_event = asyncio.Event()
        self._is_initialized = False

        url_to_log = self.redis_url
        if "@" in url_to_log:
            url_parts = url_to_log.split('@')
            proto_user = url_parts[0].split(':')
            url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"

        dlq_info = f", DLQ: '{self.dlq_name}', Max Retries: {self.max_retries}" if self.dlq_name else ", DLQ disabled"
        db_log_info = f", DB Error Logging: ENABLED to '{self.db_service_url}'" if self.db_service_url else ", DB Error Logging: DISABLED"
        logging.info(f"RedisQueueService configured for queue '{self.queue_name}' using discovery URL {url_to_log}{dlq_info}{db_log_info}")


    async def initialize(self):
        """
        Initializes the asynchronous Redis Cluster client connection and verifies it.
        Must be called before using other methods if not using 'async with'.
        """
        if self._is_initialized:
            logging.warning(f"Redis Cluster client for queue '{self.queue_name}' already initialized.")
            return
        if self._client:
            logging.warning("Redis Cluster client instance exists but service not marked initialized. Re-initializing.")
            await self.close()

        logging.info(f"Initializing Redis client for queue: {self.queue_name} via {self.redis_url}...")
        self._shutdown_event.clear()

        try:
            self._client = Redis.from_url(
                url=self.redis_url,
                password=self.redis_password,
                decode_responses=self.decode_responses
            )
            await self._client.ping()
            self._is_initialized = True
            logging.info(f"Redis client initialized and connection verified for queue: {self.queue_name}")

        except redis_exceptions.AuthenticationError:
            logging.error(f"Redis Cluster authentication failed for queue '{self.queue_name}'. Check credentials.")
            self._client = None; self._is_initialized = False
            raise
        except redis_exceptions.RedisError as e:
            logging.error(f"Failed to connect to Redis Cluster or PING for queue '{self.queue_name}': {e}")
            self._client = None; self._is_initialized = False
            raise
        except Exception as e:
            logging.error(f"An unexpected error during Redis Cluster client initialization for queue '{self.queue_name}': {e}")
            self._client = None; self._is_initialized = False
            raise


    def _ensure_initialized(self):
        """Internal helper to check if the client is ready."""
        if not self._is_initialized or not self._client:
            error_msg = f"Redis Cluster client for queue '{self.queue_name}' not initialized. Call initialize() or use 'async with'."
            logging.error(error_msg)
            raise RuntimeError(error_msg)


    async def _send_internal(self, queue: str, wrapped_message_str: str, priority: float = 100.0):
        """Internal helper to ZADD a message string to a specified sorted set (queue)."""
        self._ensure_initialized()
        try:
            # Using ZADD for priority queue behavior
            ts_micro = get_current_microsecond_timestamp()
            priority = priority * self.padding + ts_micro
            await self._client.zadd(queue, {wrapped_message_str: priority})
            logging.debug(f"Sent message to Redis Cluster queue '{queue}' with priority {priority}.")
        except redis_exceptions.RedisError as e:
            logging.error(f"Failed to send message to Redis Cluster queue '{queue}': {e}")
            raise


    async def send_message(
        self,
        payload: Dict[str, Any],
        priority: float = 100.0, # Lower numbers are higher priority for BZPOPMIN
        operation_id: Optional[str] = None
    ):
        """
        Sends a message (payload) to the configured Redis sorted set (queue) with priority.
        The payload is wrapped with metadata for retry/DLQ handling.

        Args:
            payload: The dictionary payload to send.
            priority: Message priority (float). Lower values indicate higher priority for BZPOPMIN.
            operation_id: Optional unique identifier for the operation. If None,
                          a UUID will be generated.

        Raises:
            RuntimeError: If the client has not been initialized.
            redis.exceptions.RedisError: If there's an error communicating with Redis Cluster.
            TypeError: If 'payload' is not a dictionary.
        """
        self._ensure_initialized()
        if not isinstance(payload, dict):
            raise TypeError(f"Message payload must be a dictionary (received {type(payload)}).")

        op_id = operation_id or str(uuid.uuid4())

        wrapped_message = {
            "original_payload": payload,
            "metadata": {
                "operation_id": op_id,
                "retry_count": 0,
                "original_queue": self.queue_name,
                "last_error": None
            }
        }

        try:
            wrapped_message_str = json.dumps(wrapped_message)
            await self._send_internal(self.queue_name, wrapped_message_str, priority)
            logging.info(f"Successfully queued task with operation_id: {op_id} to queue '{self.queue_name}' with priority {priority}")

        except TypeError as e:
             logging.error(f"Type error serializing message for queue '{self.queue_name}' (op_id: {op_id}): {e}")
             raise


    async def run_worker(
        self,
        process_func: ProcessFuncType,
        brpop_timeout: int = 5, # bzpopmin_timeout
        error_retry_delay: int = 5,
        http_client: Optional[aiohttp.ClientSession] = None # For DB logging
    ):
        """
        Runs a worker loop that listens for and processes messages from the queue.
        Uses BZPOPMIN for reliable message retrieval from a sorted set.
        Handles retries and moves messages to DLQ on repeated failures if configured.
        Optionally logs critical failures to an external DB service if configured.

        Args:
            process_func: An awaitable function: `async def my_processor(payload: Dict[str, Any], operation_id: str): ...`
                          It MUST raise an exception if processing fails.
            brpop_timeout: Timeout in seconds for BZPOPMIN.
            error_retry_delay: Seconds to wait before retrying after Redis errors.
            http_client: Optional aiohttp.ClientSession for logging errors to a DB service.
                         Required if db_service_url was set during initialization and DB logging is desired.
        """
        self._ensure_initialized()
        if self.decode_responses is False:
            logging.warning(f"Worker running on cluster queue '{self.queue_name}' with decode_responses=False. "
                            "Received messages will be bytes.")

        logging.info(f"Starting worker for queue '{self.queue_name}' with BZPOPMIN timeout {brpop_timeout}s...")
        if self.dlq_name:
            logging.info(f"DLQ enabled: '{self.dlq_name}', max retries: {self.max_retries}")
        else:
            logging.info("DLQ is disabled.")
        if self.db_service_url and not http_client:
            logging.warning(f"db_service_url ('{self.db_service_url}') is configured, but no http_client was provided to run_worker. DB error logging will be skipped.")


        while not self._shutdown_event.is_set():
            queue_message_tuple = None # (queue_name, message_str, score)
            message_str = None
            wrapped_message: Optional[Dict[str, Any]] = None
            original_payload: Optional[Dict[str, Any]] = None
            metadata: Optional[Dict[str, Any]] = None
            operation_id = "UNKNOWN_PRE_PARSE"
            message_priority = 100.0 # Default priority for re-queuing if original is lost

            try:
                if not self._is_initialized:
                    await self.initialize()
                # BZPOPMIN pops the member with the lowest score (highest priority)
                queue_message_tuple = await self._client.bzpopmin(self.queue_name, timeout=brpop_timeout)

                if queue_message_tuple is None:
                    continue

                _queue_name_received, message_str, score_str = queue_message_tuple
                message_priority = float(score_str) # Store original priority
                logging.debug(f"Received potential task from cluster queue '{self.queue_name}' with priority {message_priority}")

                try:
                    wrapped_message = parse_json(message_str)
                    if not isinstance(wrapped_message, dict) or \
                       "original_payload" not in wrapped_message or \
                       "metadata" not in wrapped_message or \
                       not isinstance(wrapped_message.get("metadata"), dict):
                        raise ValueError("Invalid wrapped message structure received.")

                    metadata = wrapped_message["metadata"]
                    original_payload = wrapped_message["original_payload"]
                    operation_id = metadata.get("operation_id", "MISSING_ID_IN_METADATA")

                    if not isinstance(original_payload, dict) and original_payload is not None:
                        raise ValueError(f"Invalid message format: 'original_payload' is not a dictionary or null for task {operation_id}")

                    if original_payload is None:
                       logging.warning(f"Received task {operation_id} with null original_payload. Skipping processing.")
                       continue

                except (ValueError, TypeError, json.JSONDecodeError) as parse_exc:
                    logging.error(f"Failed to parse received message from queue '{self.queue_name}'. Error: {parse_exc}. Message snippet: '{str(message_str)[:100]}...'")
                    if self.dlq_name:
                        logging.warning(f"Moving unparseable message to DLQ '{self.dlq_name}'")
                        try:
                            dlq_payload = json.dumps({"raw_message": str(message_str), "parse_error": str(parse_exc), "original_priority": message_priority})
                            await self._send_internal(self.dlq_name, dlq_payload, message_priority) # Send to DLQ with original priority
                        except Exception as dlq_err:
                            logging.error(f"Failed to send unparseable message to DLQ '{self.dlq_name}': {dlq_err}")
                    continue

                try:
                    logging.info(f"Processing task ID: {operation_id} (Retry {metadata.get('retry_count', 0)}, Priority: {message_priority})")
                    await process_func(payload=original_payload, operation_id=operation_id)
                    logging.info(f"Successfully processed task ID: {operation_id}")

                except Exception as proc_exc:
                    logging.warning(f"Processing failed for task ID: {operation_id}. Error: {proc_exc}", exc_info=True)

                    current_retry = metadata.get("retry_count", 0)
                    error_message_for_db = f"Processing failed for task {operation_id} after {current_retry} retries. Error: {type(proc_exc).__name__}: {str(proc_exc)}"

                    if self.dlq_name:
                        metadata["last_error"] = f"{type(proc_exc).__name__}: {str(proc_exc)}"

                        if current_retry < self.max_retries:
                            metadata["retry_count"] = current_retry + 1
                            logging.info(f"Re-queuing task {operation_id} for retry {metadata['retry_count']}/{self.max_retries} to queue '{self.queue_name}' with priority {message_priority}")
                            try:
                                updated_message_str = json.dumps(wrapped_message)
                                await self._send_internal(self.queue_name, updated_message_str, message_priority) # Re-queue with original priority
                            except Exception as requeue_err:
                                logging.error(f"CRITICAL: Failed to re-queue task {operation_id} for retry! Error: {requeue_err}. Message might be lost.")
                        else:
                            logging.warning(f"Max retries ({self.max_retries}) reached for task {operation_id}. Moving to DLQ '{self.dlq_name}'.")
                            if self.db_service_url and http_client and operation_id:
                                try:
                                    await add_error_log(
                                        operation_id=operation_id,
                                        message=error_message_for_db,
                                        http_client=http_client,
                                        db_service_url=self.db_service_url
                                    )
                                except Exception as db_log_err:
                                    logging.error(f"Failed to log to DB for task {operation_id} (max retries) before DLQ: {db_log_err}")

                            try:
                                final_message_str = json.dumps(wrapped_message)
                                await self._send_internal(self.dlq_name, final_message_str, message_priority) # Send to DLQ with original priority
                            except Exception as dlq_err:
                                logging.error(f"CRITICAL: Failed to move task {operation_id} to DLQ '{self.dlq_name}'! Error: {dlq_err}. Message might be lost.")
                    else: # DLQ not configured
                        logging.warning(f"Task {operation_id} failed, DLQ not configured. Message dropped.")
                        if self.db_service_url and http_client and operation_id:
                            try:
                                await add_error_log(
                                    operation_id=operation_id,
                                    message=error_message_for_db,
                                    http_client=http_client,
                                    db_service_url=self.db_service_url
                                )
                            except Exception as db_log_err:
                                logging.error(f"Failed to log to DB for task {operation_id} (no DLQ): {db_log_err}")

            except redis_exceptions.ConnectionError as e:
                logging.error(f"Redis Cluster connection lost in worker loop for queue '{self.queue_name}': {e}. Retrying in {error_retry_delay}s...")
                await asyncio.sleep(error_retry_delay)
                self._is_initialized = False
            except redis_exceptions.ClusterDownError as e:
                logging.error(f"Redis Cluster is reported as down for queue '{self.queue_name}': {e}. Retrying in {error_retry_delay}s...")
                await asyncio.sleep(error_retry_delay)
                self._is_initialized = False
            except redis_exceptions.AuthenticationError:
                logging.error(f"Redis Cluster Authentication Failed during BZPOPMIN on queue '{self.queue_name}'. Worker stopping.")
                self._shutdown_event.set(); break
            except redis_exceptions.RedisError as e:
                logging.error(f"Redis Cluster error during BZPOPMIN on queue '{self.queue_name}': {e}. Retrying in {error_retry_delay}s...")
                await asyncio.sleep(error_retry_delay)
                self._is_initialized = False
            except KeyboardInterrupt:
                logging.info(f"Shutdown signal (KeyboardInterrupt) received for queue '{self.queue_name}'. Exiting worker loop...")
                self._shutdown_event.set(); break
            except Exception as e:
                logging.exception(f"Critical unexpected error in cluster worker main loop for queue '{self.queue_name}' (Op ID: {operation_id}, Msg Snippet: '{str(message_str)[:50]}...'): {e}")
                logging.info(f"Waiting {error_retry_delay}s before continuing...")
                await asyncio.sleep(error_retry_delay)
                self._is_initialized = False

        logging.info(f"Cluster worker loop for queue '{self.queue_name}' has stopped.")


    async def stop_worker(self):
        """Signals the running worker loop to shut down gracefully."""
        if not self._shutdown_event.is_set():
            logging.info(f"Signaling cluster worker for queue '{self.queue_name}' to stop...")
            self._shutdown_event.set()
        else:
            logging.info(f"Cluster worker for queue '{self.queue_name}' already stopping.")


    async def close(self):
        """Closes the asynchronous Redis Cluster client connection gracefully."""
        if self._client:
            logging.info(f"Closing Redis Cluster client connection for queue: {self.queue_name}")
            try:
                await self._client.aclose()
                logging.info(f"Redis Cluster client connection closed for queue: {self.queue_name}")
            except redis_exceptions.RedisError as e:
                logging.error(f"Error encountered while closing Redis Cluster connection for queue '{self.queue_name}': {e}")
            except Exception as e:
                logging.error(f"Unexpected error during Redis Cluster client close for queue '{self.queue_name}': {e}")
            finally:
                self._client = None; self._is_initialized = False
        else:
            logging.debug(f"No active Redis Cluster client connection to close for queue '{self.queue_name}'.")
        self._shutdown_event.set()


    async def __aenter__(self):
        await self.initialize()
        return self


    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            logging.error(f"Exiting context for queue '{self.queue_name}' due to exception: {exc_val}", exc_info=(exc_type, exc_val, exc_tb))
        await self.close()
