"""
Module for the Redis Cluster Service.

This module provides an asynchronous service for interacting with Redis,
including message queueing and session management.
"""
import asyncio
import logging
import json
from typing import Optional, Dict, Any

from redis.asyncio import Redis
import redis.exceptions as redis_exceptions

from app.models.common import SessionNotFoundException
from app.utils import (
    get_current_microsecond_timestamp,
    get_currect_timestamp,
    get_uuid,
    stringify_json,
)
logger = logging.getLogger(__name__)

class RedisQueueService: # Renamed class slightly for clarity
    """
    an asynchronous service for interacting with Redis.

    Provides methods to use a Redis list as a message queue (LPUSH/BRPOP)
    and manage key-value session data (SETEX/GET). All operations are routed
    to the appropriate cluster node based on the key.

    Supports graceful shutdown and automatic resource management via 'async with'.
    """
    # Service for interacting with a Redis Cluster.
    def __init__(
        self,
        queue_name: str,
        redis_url: str, # URL should point to one of the cluster nodes for discovery
        redis_password: Optional[str] = None, # Password can be in URL or separate
        decode_responses: bool = True, # Default to decoding responses to strings
        padding: Optional[int] = 35_000_000_000_000
    ):
        """
        Initializes the service configuration for Redis Cluster.

        Args:
            queue_name: The name of the Redis list to use as a queue.
                        This key will be hashed to determine its slot/node.
            redis_url: The connection URL for Redis Cluster discovery
                       (e.g., "redis://node1:6379" or "rediss://user:pass@node1:7000").
                       The client will query this node to find the cluster topology.
            redis_password: Optional password if not included in the redis_url.
            decode_responses: If True, Redis commands returning bytes will decode
                              them to strings using UTF-8 (recommended for text queues).
        """
        # Initializes the RedisQueueService.
        if not queue_name:
            raise ValueError("queue_name cannot be empty.")
        if not redis_url:
             raise ValueError("redis_url cannot be empty.")

        self.APP_PREFIX = "CCPM_evidence_analyser:session:"
        self.queue_name = queue_name
        self.redis_url = redis_url
        self.redis_password = redis_password
        self.decode_responses = decode_responses
        self.padding = padding

        
        self._client: Optional[Redis] = None
        self._shutdown_event = asyncio.Event()
        self._is_initialized = False # Track initialization state

        # Log sensitive info carefully
        url_to_log = self.redis_url
        if "@" in url_to_log:
            # Basic hiding, might need refinement for complex URLs
            url_parts = url_to_log.split('@')
            proto_user = url_parts[0].split(':')
            url_to_log = f"{proto_user[0]}:...@{url_parts[-1]}"
        logger.info(f"RedisQueueService configured for queue '{self.queue_name}' using discovery URL {url_to_log}")


    async def initialize(self):
        """
        Initializes the asynchronous Redis Cluster client connection and verifies it.
        Must be called before using other methods if not using 'async with'.
        """
        # Initializes the Redis Cluster client connection.
        if self._is_initialized:
            logger.warning(f"Redis Cluster client for queue '{self.queue_name}' already initialized.")
            return
        if self._client:
            logger.warning("Redis Cluster client instance exists but service not marked initialized. Re-initializing.")
            await self.close() # Clean up previous instance if any

        logger.info(f"Initializing Redis client for queue: {self.queue_name} via {self.redis_url}...")
        self._shutdown_event.clear() # Ensure shutdown event is clear on new init

        try:
            # Use Redis.from_url for initialization
            self._client = Redis.from_url(
                url=self.redis_url,
                password=self.redis_password,
                decode_responses=self.decode_responses
            )

            # Verify the connection (ping might go to a random node or all nodes)
            await self._client.ping()
            self._is_initialized = True
            logger.info(f"Redis client initialized and connection verified for queue: {self.queue_name}")

        # Use exceptions from redis.exceptions
        except redis_exceptions.AuthenticationError:
            logger.error(f"Redis Cluster authentication failed for queue '{self.queue_name}'. Check credentials.")
            self._client = None
            self._is_initialized = False
            raise # Propagate error
        except redis_exceptions.RedisError as e:
            logger.error(f"Failed to connect to Redis Cluster or PING for queue '{self.queue_name}': {e}")
            self._client = None
            self._is_initialized = False
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred during Redis Cluster client initialization for queue '{self.queue_name}': {e}")
            self._client = None
            self._is_initialized = False
            raise


    def _ensure_initialized(self):
        """Internal helper to check if the client is ready."""
        # Ensures the client is initialized.
        if not self._is_initialized or not self._client:
            error_msg = f"Redis Cluster client for queue '{self.queue_name}' not initialized. Call initialize() or use 'async with'."
            logger.error(error_msg)
            raise RuntimeError(error_msg)


    async def send_message(self, msg: dict, priority: float = 100.0):
        """
        Sends a message to the configured Redis list (queue) using LPUSH.
        The command is routed to the correct cluster node based on the queue_name key.

        Args:
            msg: The string message to send.

        Raises:
            RuntimeError: If the client has not been initialized.
            redis.exceptions.RedisError: If there's an error communicating with Redis Cluster.
            TypeError: If 'msg' is not a string (or cannot be encoded if decode_responses=False).
        """
        # Sends a message to the Redis queue.
        self._ensure_initialized()
        if not isinstance(msg, dict):
            raise TypeError(f"Message must be a dict (received {type(msg)}).")

        try:
            queue_message = {
                "original_payload": msg,
                "metadata": {
                    "operation_id": get_uuid(),
                    "created_at": get_currect_timestamp()
                }
            }
            queue_message = stringify_json(queue_message)
            # LPUSH works the same way, client handles routing based on self.queue_name
            ts_micro = get_current_microsecond_timestamp()
            priority = priority * self.padding + ts_micro
            
            try:
                list_length = await self._client.zadd(self.queue_name, {queue_message: priority})
            except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as e:
                logger.warning(f"Redis connection error in send_message: {e}. Reconnecting and retrying.")
                await self.close()
                await self.initialize()
                self._ensure_initialized()
                list_length = await self._client.zadd(self.queue_name, {queue_message: priority})

            logger.debug(f"Sent message to Redis Cluster queue '{self.queue_name}'. New length: {list_length}")
        except redis_exceptions.RedisError as e:
            logger.error(f"Failed to send message to Redis Cluster queue '{self.queue_name}': {e}")
            raise
        except TypeError as e: # Catches potential encoding errors
            logger.error(f"Type error sending message to Redis Cluster queue '{self.queue_name}': {e}")
            raise


    async def close(self):
        """
        Closes the asynchronous Redis Cluster client connection gracefully.
        """
        # Closes the Redis Cluster client connection.
        if self._client:
            logger.info(f"Closing Redis Cluster client connection for queue: {self.queue_name}")
            # Signal worker to stop if needed
            # await self.stop_worker()

            try:
                # Use aclose() for async cluster client
                await self._client.aclose()
                logger.info(f"Redis Cluster client connection closed for queue: {self.queue_name}")
            except redis_exceptions.RedisError as e:
                logger.error(f"Error encountered while closing Redis Cluster connection for queue '{self.queue_name}': {e}")
            except Exception as e:
                logger.error(f"Unexpected error during Redis Cluster client close for queue '{self.queue_name}': {e}")
            finally:
                self._client = None
                self._is_initialized = False # Mark as uninitialized
        else:
            logger.debug(f"No active Redis Cluster client connection to close for queue '{self.queue_name}'.")
        self._shutdown_event.set()


    # --- Context Manager Protocols ---
    async def __aenter__(self):
        """Initializes the cluster client when entering an 'async with' block."""
        # Enters the asynchronous context.
        await self.initialize()
        return self


    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Closes the cluster client when exiting an 'async with' block."""
        # Exits the asynchronous context.
        if exc_type:
            logger.error(f"Exiting cluster context for queue '{self.queue_name}' due to exception: {exc_val}", exc_info=(exc_type, exc_val, exc_tb))
        else:
            logger.debug(f"Exiting cluster context for queue '{self.queue_name}'.")
        await self.close()
        return False


    # --- Session Management Methods ---
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
            session_expiry_time: Expiration time in seconds

        Raises:
            RuntimeError: If client not initialized or on unexpected errors.
            KeyError: If session dict lacks "token".
        """
        # Adds or updates a session in Redis.
        self._ensure_initialized() # Ensure client is ready

        try:
            token = session["token"]
            redis_key = f"{self.APP_PREFIX}{token}"
            session_value = json.dumps(session)
            logger.debug(f"Adding/updating session for cluster key: {redis_key} with expiry: {session_expiry_time}s")

            try:
                # SETEX command works the same, client routes based on redis_key
                await self._client.setex(redis_key, session_expiry_time, session_value)
            except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as e:
                logger.warning(f"Redis connection error in add_or_update_session: {e}. Reconnecting and retrying.")
                await self.close()
                await self.initialize()
                self._ensure_initialized()
                await self._client.setex(redis_key, session_expiry_time, session_value) # Retry

            logger.info(f"Session added/updated successfully for token: {token}")

        except KeyError as e:
            logger.error(f"Missing required key 'token' in session data: {session}")
            raise RuntimeError(f"Session data missing key: {e}") from e
        except redis_exceptions.RedisError as e:
            logger.error(f"Redis Cluster error while adding/updating session for token {session.get('token', 'N/A')}: {e}")
            raise RuntimeError("Redis Cluster failure while adding/updating session.") from e
        except Exception as e:
            logger.error(f"Unexpected error while adding/updating session: {e}")
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
        # Retrieves a user session from Redis.
        self._ensure_initialized() # Ensure client is ready

        redis_key = f"{self.APP_PREFIX}{token}"
        logger.debug(f"Attempting to retrieve session for cluster key: {redis_key}")

        try:
            try:
                # GET command works the same, client routes based on redis_key
                retrieved_data = await self._client.get(redis_key)
            except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as e:
                logger.warning(f"Redis connection error in get_user_session: {e}. Reconnecting and retrying.")
                await self.close()
                await self.initialize()
                self._ensure_initialized()
                retrieved_data = await self._client.get(redis_key) # Retry

            if retrieved_data is None:
                logger.warning(f"Session not found for token: {token} (key: {redis_key})")
                raise SessionNotFoundException(token)

            # If decode_responses=True, retrieved_data is str, otherwise bytes
            # json.loads handles both str and bytes (decoded as UTF-8)
            session_data = json.loads(retrieved_data)
            logger.info(f"Session retrieved successfully for token: {token}")
            return session_data

        except SessionNotFoundException:
            raise # Re-raise specifically
        except redis_exceptions.RedisError as e:
            logger.error(f"Redis Cluster error while retrieving session for token {token}: {e}")
            raise RuntimeError("Redis Cluster failure while retrieving session.") from e
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON for session data retrieved for token {token}: {e}. Data: '{retrieved_data}'")
            # Treat corrupted data as session not found or raise a different error?
            # For now, let's treat it like a failure.
            raise RuntimeError("Failed to parse session data from Redis Cluster.") from e
        except Exception as e:
            logger.error(f"Unexpected error while retrieving session for token {token}: {e}")
            raise RuntimeError("Unexpected failure while retrieving session.") from e
