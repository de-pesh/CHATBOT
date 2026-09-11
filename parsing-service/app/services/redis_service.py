import asyncio
import logging
import json
import uuid
from typing import Optional, Dict, Any, Callable, Awaitable

from redis.asyncio.cluster import RedisCluster
import redis.exceptions as redis_exceptions

from app.models.error_models import SessionNotFoundException
from app.utils.general_utils import parse_json

# Define a type hint for the process function
ProcessFuncType = Callable[[Dict[str, Any], str], Awaitable[None]]

class RedisClusterService:
    """
    An asynchronous service for interacting with a Redis Cluster.

    Provides methods to use a Redis list as a reliable message queue (LPUSH/BRPOP)
    with Dead Letter Queue (DLQ) support and retry mechanism. Also manages
    key-value session data (SETEX/GET). All operations are routed
    to the appropriate cluster node based on the key.

    Supports graceful shutdown and automatic resource management via 'async with'.
    """
    def __init__(
        self,
        queue_name: str,
        redis_url: str, # URL should point to one of the cluster nodes for discovery
        redis_password: Optional[str] = None, # Password can be in URL or separate
        decode_responses: bool = True, # Default to decoding responses to strings
        dlq_name: Optional[str] = None, # Name of the Dead Letter Queue (optional)
        max_retries: int = 3, # Max attempts before moving to DLQ
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
        """
        if not queue_name:
            raise ValueError("queue_name cannot be empty.")
        if not redis_url:
            raise ValueError("redis_url cannot be empty.")
        if dlq_name is not None and max_retries < 0:
             raise ValueError("max_retries must be non-negative when dlq_name is specified.")
        if dlq_name == queue_name:
             raise ValueError("dlq_name cannot be the same as queue_name.")


        self.APP_PREFIX = "CCPM_evidence_analyser:session:"
        self.queue_name = queue_name
        self.redis_url = redis_url
        self.redis_password = redis_password
        self.decode_responses = decode_responses
        self.dlq_name = dlq_name
        self.max_retries = max_retries if dlq_name else 0 # Retries only active if DLQ is set

        self._client: Optional[RedisCluster] = None
        self._shutdown_event = asyncio.Event()
        self._is_initialized = False

        # Log sensitive info carefully
        url_to_log = self.redis_url
        if "@" in url_to_log:
            url_parts = url_to_log.split('@')
            proto_user = url_parts[0].split(':')
            url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"

        dlq_info = f", DLQ: '{self.dlq_name}', Max Retries: {self.max_retries}" if self.dlq_name else ", DLQ disabled"
        logging.info(f"RedisClusterService configured for queue '{self.queue_name}' using discovery URL {url_to_log}{dlq_info}")


    async def initialize(self):
        """
        Initializes the asynchronous Redis Cluster client connection and verifies it.
        Must be called before using other methods if not using 'async with'.
        """
        if self._is_initialized:
            logging.warning(f"Redis Cluster client for queue '{self.queue_name}' already initialized.")
            return
        if self._client:
            logging.warning(f"Redis Cluster client instance exists but service not marked initialized. Re-initializing.")
            await self.close() # Clean up previous instance if any

        logging.info(f"Initializing Redis Cluster client for queue: {self.queue_name} via {self.redis_url}...")
        self._shutdown_event.clear()

        try:
            self._client = RedisCluster.from_url(
                url=self.redis_url,
                password=self.redis_password,
                decode_responses=self.decode_responses
            )
            await self._client.ping()
            self._is_initialized = True
            logging.info(f"Redis Cluster client initialized and connection verified for queue: {self.queue_name}")

        except redis_exceptions.AuthenticationError:
            logging.error(f"Redis Cluster authentication failed for queue '{self.queue_name}'. Check credentials.")
            self._client = None
            self._is_initialized = False
            raise
        except redis_exceptions.RedisError as e:
            logging.error(f"Failed to connect to Redis Cluster or PING for queue '{self.queue_name}': {e}")
            self._client = None
            self._is_initialized = False
            raise
        except Exception as e:
            logging.error(f"An unexpected error during Redis Cluster client initialization for queue '{self.queue_name}': {e}")
            self._client = None
            self._is_initialized = False
            raise


    def _ensure_initialized(self):
        """Internal helper to check if the client is ready."""
        if not self._is_initialized or not self._client:
            error_msg = f"Redis Cluster client for queue '{self.queue_name}' not initialized. Call initialize() or use 'async with'."
            logging.error(error_msg)
            raise RuntimeError(error_msg)


    async def _send_internal(self, queue: str, wrapped_message_str: str, priority: float = 100.0):
        """Internal helper to LPUSH a message string to a specified queue."""
        try:
            list_length = await self._client.zadd(queue, {wrapped_message_str: priority})
            logging.debug(f"Sent message to Redis Cluster queue '{queue}'. New length: {list_length}")
            return list_length
        except redis_exceptions.RedisError as e:
            logging.error(f"Failed to send message to Redis Cluster queue '{queue}': {e}")
            raise # Propagate the error


    async def send_message(
        self,
        payload: Dict[str, Any],
        priority: float = 100.0,
        operation_id: Optional[str] = None
    ):
        """
        Sends a message (payload) to the configured Redis list (queue).
        The payload is wrapped with metadata for retry/DLQ handling.

        Args:
            payload: The dictionary payload to send.
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

        # Wrap the payload with metadata
        wrapped_message = {
            "original_payload": payload,
            "metadata": {
                "operation_id": op_id,
                "retry_count": 0,
                "original_queue": self.queue_name,
                "last_error": None # Placeholder for error info on retries
            }
        }

        try:
            wrapped_message_str = json.dumps(wrapped_message)
            await self._send_internal(self.queue_name, wrapped_message_str, priority)
            logging.info(f"Successfully queued task with operation_id: {op_id} to queue '{self.queue_name}'")

        except TypeError as e: # Catches JSON serialization errors
             logging.error(f"Type error serializing message for queue '{self.queue_name}' (op_id: {op_id}): {e}")
             raise
        # _send_internal handles RedisError


    async def run_worker(
        self,
        process_func: ProcessFuncType,
        brpop_timeout: int = 5,
        error_retry_delay: int = 5
    ):
        """
        Runs a worker loop that listens for and processes messages from the queue.

        Handles retries and moves messages to DLQ on repeated failures if configured.
        Uses BRPOP, routed to the correct node based on queue_name.

        Args:
            process_func: An awaitable function: `async def my_processor(payload: Dict[str, Any], operation_id: str): ...`
                          It MUST raise an exception if processing fails.
            brpop_timeout: Timeout in seconds for BRPOP.
            error_retry_delay: Seconds to wait before retrying after Redis errors.
        """
        self._ensure_initialized()
        if self.decode_responses is False:
            logging.warning(f"Worker running on cluster queue '{self.queue_name}' with decode_responses=False. "
                            "Received messages will be bytes.")

        logging.info(f"Starting cluster worker for queue '{self.queue_name}' with BRPOP timeout {brpop_timeout}s...")
        if self.dlq_name:
            logging.info(f"DLQ enabled: '{self.dlq_name}', max retries: {self.max_retries}")
        else:
            logging.info("DLQ is disabled.")


        while not self._shutdown_event.is_set():
            queue_message = None
            message_str = None
            wrapped_message: Optional[Dict[str, Any]] = None
            operation_id = "UNKNOWN_PRE_PARSE"

            try:
                # BRPOP command works similarly, routed by key (self.queue_name)
                queue_message = await self._client.bzpopmin(self.queue_name, timeout=brpop_timeout)

                if queue_message is None:
                    # logging.debug(f"BRPOP timeout on cluster queue '{self.queue_name}', checking for shutdown.")
                    continue # Standard timeout, loop again

                _queue_name_received, message_str, priority = queue_message
                logging.debug(f"Received potential task from cluster queue '{self.queue_name}'")

                # 1. Parse the wrapper message
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
                       continue # Or raise error if null payload is invalid

                except (ValueError, TypeError, json.JSONDecodeError) as parse_exc:
                    logging.error(f"Failed to parse received message from queue '{self.queue_name}'. Error: {parse_exc}. Message snippet: '{str(message_str)[:100]}...'")
                    # Decide what to do with unparseable messages:
                    # Option A: Log and drop (current implicit behavior)
                    # Option B: Try sending raw string to DLQ (if configured)
                    if self.dlq_name:
                         logging.warning(f"Moving unparseable message to DLQ '{self.dlq_name}'")
                         try:
                            # Send the raw string, maybe wrap it minimally
                            dlq_payload = json.dumps({"raw_message": str(message_str), "parse_error": str(parse_exc)})
                            await self._send_internal(self.dlq_name, dlq_payload)
                         except Exception as dlq_err:
                              logging.error(f"Failed to send unparseable message to DLQ '{self.dlq_name}': {dlq_err}")
                    continue # Move to next message


                # 2. Attempt to process the original payload
                try:
                    logging.info(f"Processing task ID: {operation_id} (Retry {metadata.get('retry_count', 0)})")
                    await process_func(payload=original_payload, operation_id=operation_id)
                    logging.info(f"Successfully processed task ID: {operation_id}")

                except Exception as proc_exc:
                    logging.warning(f"Processing failed for task ID: {operation_id}. Error: {proc_exc}", exc_info=True) # Log exception info

                    if self.dlq_name:
                        current_retry = metadata.get("retry_count", 0)
                        metadata["last_error"] = f"{type(proc_exc).__name__}: {str(proc_exc)}" # Store last error

                        if current_retry < self.max_retries:
                            # Re-queue for retry
                            metadata["retry_count"] = current_retry + 1
                            logging.info(f"Re-queuing task {operation_id} for retry {metadata['retry_count']}/{self.max_retries} to queue '{self.queue_name}'")
                            try:
                                updated_message_str = json.dumps(wrapped_message)
                                await self._send_internal(self.queue_name, updated_message_str, priority)
                            except Exception as requeue_err:
                                logging.error(f"CRITICAL: Failed to re-queue task {operation_id} for retry! Error: {requeue_err}. Message might be lost.")

                        else:
                            # Max retries reached, send to DLQ
                            logging.warning(f"Max retries ({self.max_retries}) reached for task {operation_id}. Moving to DLQ '{self.dlq_name}'.")
                            try:
                                final_message_str = json.dumps(wrapped_message)
                                await self._send_internal(self.dlq_name, final_message_str)
                            except Exception as dlq_err:
                                logging.error(f"CRITICAL: Failed to move task {operation_id} to DLQ '{self.dlq_name}'! Error: {dlq_err}. Message might be lost.")
                                # Consider fallback action?

                    else:
                        # DLQ not configured, log and drop
                        logging.warning(f"Task {operation_id} failed, DLQ not configured. Message dropped.")

            # Handle Redis communication errors
            except redis_exceptions.ConnectionError as e:
                logging.error(f"Redis Cluster connection lost in worker loop for queue '{self.queue_name}': {e}. Retrying in {error_retry_delay}s...")
                await asyncio.sleep(error_retry_delay)
            except redis_exceptions.ClusterDownError as e:
                logging.error(f"Redis Cluster is reported as down for queue '{self.queue_name}': {e}. Retrying in {error_retry_delay}s...")
                await asyncio.sleep(error_retry_delay)
            except redis_exceptions.AuthenticationError:
                logging.error(f"Redis Cluster Authentication Failed during BRPOP on queue '{self.queue_name}'. Worker stopping.")
                self._shutdown_event.set()
                break
            except redis_exceptions.RedisError as e:
                logging.error(f"Redis Cluster error during BRPOP on queue '{self.queue_name}': {e}. Retrying in {error_retry_delay}s...")
                await asyncio.sleep(error_retry_delay)
            except KeyboardInterrupt:
                logging.info(f"Shutdown signal (KeyboardInterrupt) received for queue '{self.queue_name}'. Exiting worker loop...")
                self._shutdown_event.set()
                break
            except Exception as e:
                 # Catch unexpected errors in the main loop logic (outside processing)
                logging.exception(f"Critical unexpected error in cluster worker main loop for queue '{self.queue_name}' (Operation ID: {operation_id}): {e}")
                logging.info(f"Waiting {error_retry_delay}s before continuing...")
                await asyncio.sleep(error_retry_delay)

        logging.info(f"Cluster worker loop for queue '{self.queue_name}' has stopped.")


    async def stop_worker(self):
        """Signals the running worker loop to shut down gracefully."""
        if not self._shutdown_event.is_set():
            logging.info(f"Signaling cluster worker for queue '{self.queue_name}' to stop...")
            self._shutdown_event.set()
        else:
            logging.info(f"Cluster worker for queue '{self.queue_name}' already stopping.")


    async def close(self):
        """
        Closes the asynchronous Redis Cluster client connection gracefully.
        """
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
                self._client = None
                self._is_initialized = False
        else:
            logging.debug(f"No active Redis Cluster client connection to close for queue '{self.queue_name}'.")
        self._shutdown_event.set() # Ensure shutdown event is set on close


    # --- Context Manager Protocols ---
    async def __aenter__(self):
        """Initializes the cluster client when entering an 'async with' block."""
        await self.initialize()
        return self


    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Closes the cluster client when exiting an 'async with' block."""
        if exc_type:
            logging.error(f"Exiting cluster context for queue '{self.queue_name}' due to exception: {exc_val}", exc_info=(exc_type, exc_val, exc_tb))
        else:
            logging.debug(f"Exiting cluster context for queue '{self.queue_name}'.")
        await self.close()


    async def add_or_update_session(
        self,
        session: dict,
        session_expiry_time: int
    ):
        """
        Adds/updates a session in Redis Cluster using SETEX.
        The key is hashed to determine the target node.

        Args:
            session: Session data dict (must include "token").
            session_expiry_time: Expiration time in seconds.

        Raises:
            RuntimeError: If client not initialized or on unexpected errors.
            KeyError: If session dict lacks "token".
        """
        self._ensure_initialized() # Ensure client is ready

        try:
            token = session["token"]
            redis_key = f"{self.APP_PREFIX}{token}"
            logging.debug(f"Adding/updating session for cluster key: {redis_key} with expiry: {session_expiry_time}s")

            # SETEX command works the same, client routes based on redis_key
            await self._client.setex(redis_key, session_expiry_time, json.dumps(session))
            logging.info(f"Session added/updated successfully for token: {token}")

        except KeyError as e:
            logging.error(f"Missing required key 'token' in session data: {session}")
            raise RuntimeError(f"Session data missing key: {e}") from e
        except redis_exceptions.RedisError as e:
            logging.error(f"Redis Cluster error while adding/updating session for token {session.get('token', 'N/A')}: {e}")
            raise RuntimeError("Redis Cluster failure while adding/updating session.") from e
        except Exception as e:
            logging.error(f"Unexpected error while adding/updating session: {e}")
            raise RuntimeError("Unexpected failure while adding/updating session.") from e


    async def get_user_session(
        self,
        token: str
    ) -> Dict[str, Any]:
        """
        Retrieves a user session from Redis Cluster using GET.
        The key is hashed to determine the target node.

        Args:
            token: The session token.

        Returns:
            The session data as a dictionary.

        Raises:
            SessionNotFoundException: If no session exists for the token.
            RuntimeError: If client not initialized or on unexpected errors.
        """
        self._ensure_initialized() # Ensure client is ready

        redis_key = f"{self.APP_PREFIX}{token}"
        logging.debug(f"Attempting to retrieve session for cluster key: {redis_key}")

        try:
            # GET command works the same, client routes based on redis_key
            retrieved_data = await self._client.get(redis_key)

            if retrieved_data is None:
                logging.warning(f"Session not found for token: {token} (key: {redis_key})")
                raise SessionNotFoundException(token)

            session_data = json.loads(retrieved_data)
            logging.info(f"Session retrieved successfully for token: {token}")
            return session_data

        except SessionNotFoundException:
            raise # Re-raise specifically
        except redis_exceptions.RedisError as e:
            logging.error(f"Redis Cluster error while retrieving session for token {token}: {e}")
            raise RuntimeError("Redis Cluster failure while retrieving session.") from e
        except json.JSONDecodeError as e:
            logging.error(f"Failed to decode JSON for session data retrieved for token {token}: {e}. Data: '{retrieved_data}'")
            raise RuntimeError("Failed to parse session data from Redis Cluster.") from e
        except Exception as e:
            logging.error(f"Unexpected error while retrieving session for token {token}: {e}")
            raise RuntimeError("Unexpected failure while retrieving session.") from e
