"""Redis implementation of MessageQueueService.

A sorted set used as a priority queue: ZADD to produce, BZPOPMIN to consume,
with a retry count carried in each message and a dead-letter queue for tasks
that exhaust it.

BZPOPMIN removes a task the moment it is taken, so a worker that dies mid-task
would lose it. Each taken task therefore also gets a lease: a member of a second
sorted set (`<queue>:inflight`) scored by when the lease runs out, removed once
the task has finished or been re-queued / dead-lettered. Every worker reaps
expired leases - at start and every `reap_interval_seconds` - putting the task
back through the same retry / dead-letter accounting as a failure, so a task that
keeps killing its worker ends in the DLQ instead of looping.

Leases are not renewed. Instead a worker abandons a task (a counted failure) after
`task_timeout(lease_seconds)`, a little less than the lease, so a live worker gives
its task up before anyone else's reaper could take it, and a hung task cannot block
the queue. The reaper runs inside the same loop as the consumer, so it is not an
independent watchdog: with one replica a hung task is bounded by that timeout, not
by the reaper, and a busy worker reaps only between tasks.

Residual windows, accepted rather than closed with Lua scripts (which some managed
Redis ACLs forbid): a crash in the one round trip between BZPOPMIN and the lease
write loses the task; a crash between a reaper's ZREM claim and its re-queue loses
it too; a crash between a re-queue and the lease release runs the task twice.
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any

import redis.exceptions as redis_exceptions
from redis.asyncio import Redis

from app.logic.queue_codec import (
    DEAD_LETTER,
    RETRY,
    SCORE_PADDING,
    build_envelope,
    dead_letter_envelope,
    error_text,
    failure_action,
    lease_entry,
    lease_expired_error,
    lease_expiry,
    lease_is_stale,
    lease_key,
    masked_url,
    new_metadata,
    operation_id_of,
    parse_envelope,
    parse_lease_entry,
    queue_score,
    reap_due,
    requeue_failure_error,
    retry_count_of,
    retry_envelope,
    task_timeout,
    task_timeout_error,
    unparseable_entry,
    url_has_embedded_password,
)
from app.services.queue.queue_service import (
    DEFAULT_PRIORITY,
    DeadLetterFuncType,
    MessageQueueService,
    ProcessFuncType,
)

logger = logging.getLogger(__name__)


class RedisQueueService(MessageQueueService):
    """
    A Redis sorted set as a priority message queue (ZADD/BZPOPMIN - see the module
    docstring) with Dead Letter Queue (DLQ) support and a retry mechanism. Talks to
    a single standalone Redis instance, not a Redis Cluster deployment.

    Supports graceful shutdown and automatic resource management via 'async with'.
    """
    def __init__(
        self,
        queue_name: str,
        redis_url: str,  # standalone Redis (redis:// or rediss:// for TLS - both work via redis-py's from_url)
        max_retries: int,  # attempts a message gets before it is moved to the DLQ; only used with a dlq_name
        lease_seconds: float,  # how long a taken task stays leased to its worker before it may be taken back
        reap_interval_seconds: float,  # how often run_worker looks for expired leases
        health_check_interval_seconds: int,  # how often an idle connection is PINGed to notice that it has died
        socket_timeout: float | None = None,  # per-command socket timeout; must exceed the BZPOPMIN timeout. None: none
        redis_password: str | None = None,
        dlq_name: str | None = None,  # without one, failed messages are not retried or moved
    ):
        if not queue_name:
            raise ValueError("queue_name cannot be empty.")
        if not redis_url:
            raise ValueError("redis_url cannot be empty.")
        if dlq_name is not None and max_retries < 0:
            raise ValueError("max_retries must be non-negative when dlq_name is specified.")
        if dlq_name == queue_name:
            raise ValueError("dlq_name cannot be the same as queue_name.")
        if lease_seconds <= 0 or reap_interval_seconds <= 0:
            raise ValueError("lease_seconds and reap_interval_seconds must be positive.")

        self.queue_name = queue_name
        self.redis_url = redis_url
        self.redis_password = redis_password
        self.dlq_name = dlq_name
        self.max_retries = max_retries
        self.lease_seconds = lease_seconds
        self.reap_interval_seconds = reap_interval_seconds
        self.socket_timeout = socket_timeout
        self.health_check_interval_seconds = health_check_interval_seconds
        self._lease_key = lease_key(queue_name)

        self._client: Redis | None = None
        self._shutdown_event = asyncio.Event()
        self._is_initialized = False
        self._last_reap: float | None = None  # time.monotonic() of the last look for expired leases
        self._auth_failure_reported = False  # so one bad password is one ERROR, not one per retry

        # redis-py's Redis.from_url layers a password embedded in the URL OVER an explicit
        # `password=` kwarg, so if redis_url already has one the redis_password argument is
        # silently ignored, not merged with it. That is invisible unless flagged: an
        # operator rotating the REDIS_PASSWORD secret without touching an old URL-embedded
        # password would keep authenticating with the stale one.
        if self.redis_password and url_has_embedded_password(self.redis_url):
            logger.warning(
                "Both REDIS_PASSWORD and a password embedded in REDIS_URL are set for queue '%s' - the "
                "URL's password wins; REDIS_PASSWORD is ignored. Use exactly one (REDIS_PASSWORD with a "
                "passwordless URL is the documented shape - see openshift/01-secrets.yaml).",
                self.queue_name,
            )

    async def initialize(self):
        """Opens the Redis client and verifies it. Must be called before use if not using 'async with'."""
        if self._is_initialized:
            logger.warning("Redis client for queue '%s' already initialized.", self.queue_name)
            return
        if self._client:
            logger.warning("Redis client instance exists but service not marked initialized. Re-initializing.")
            await self.close()

        self._shutdown_event.clear()
        try:
            # A black-holed connection (a dropped node, a firewall that swallows packets) would
            # otherwise block BZPOPMIN forever: the timeout makes it raise, and the keepalive PING
            # notices a dead idle connection before the next command is sent on it.
            self._client = Redis.from_url(
                url=self.redis_url, password=self.redis_password, decode_responses=True,
                socket_timeout=self.socket_timeout, health_check_interval=self.health_check_interval_seconds,
            )
            await self._client.ping()
            self._is_initialized = True
            self._auth_failure_reported = False
            logger.info("Redis client ready for queue '%s' via %s", self.queue_name, masked_url(self.redis_url))
        except redis_exceptions.AuthenticationError as e:
            self._report_auth_failure(e)
            self._client = None
            self._is_initialized = False
            raise
        except Exception as e:
            logger.error("Could not connect to Redis for queue '%s': %s.", self.queue_name, e)
            self._client = None
            self._is_initialized = False
            raise

    def _report_auth_failure(self, error: Exception, retry_delay: float | None = None) -> None:
        """Logs the one clear ERROR for a rejected Redis password, once until a connection succeeds again.

        AuthenticationError subclasses ConnectionError, so it must be told apart before the generic
        connection handling: retrying does not help until the operator fixes the credential.
        """
        if self._auth_failure_reported:
            return
        self._auth_failure_reported = True
        logger.error(
            "Redis rejected the credentials for queue '%s' (%s): fix REDIS_URL / REDIS_PASSWORD "
            "(a password embedded in REDIS_URL overrides REDIS_PASSWORD).%s",
            self.queue_name, error,
            f" Retrying every {retry_delay}s; it recovers once they are correct." if retry_delay is not None else "",
        )

    def _ensure_initialized(self):
        if not self._is_initialized or not self._client:
            raise RuntimeError(
                f"Redis client for queue '{self.queue_name}' not initialized. Call initialize() or use 'async with'."
            )

    async def _send_internal(self, queue: str, wrapped_message_str: str, priority: float = DEFAULT_PRIORITY, *, score: float | None = None):
        """ZADDs a message string to a sorted set (queue).

        With `score` the message goes in at exactly that score. A message put back after being
        popped must pass the score BZPOPMIN returned: it already is priority * padding + send time,
        and treating it as a priority would multiply it by the padding again and sink the message
        behind every fresh one. Otherwise the score is made from `priority` and the current time.
        """
        self._ensure_initialized()
        if score is None:
            score = queue_score(priority, int(time.time() * 1_000_000), SCORE_PADDING)
        await self._client.zadd(queue, {wrapped_message_str: score})

    async def send_message(
        self,
        payload: dict[str, Any],
        priority: float = DEFAULT_PRIORITY,
        operation_id: str | None = None,
    ):
        """
        Sends a payload to the queue, wrapped with metadata for retry/DLQ handling. Lower
        priority values are consumed first; a UUID is generated when no operation_id is given.

        Raises RuntimeError if the client has not been initialized, TypeError if 'payload'
        is not a dictionary, redis.exceptions.RedisError on a Redis failure.
        """
        self._ensure_initialized()
        if not isinstance(payload, dict):
            raise TypeError(f"Message payload must be a dictionary (received {type(payload)}).")

        metadata = new_metadata(self.queue_name, operation_id)
        await self._send_internal(self.queue_name, json.dumps(build_envelope(payload, metadata)), priority)
        logger.debug("Queued task %s to '%s' with priority %s", metadata["operation_id"], self.queue_name, priority)

    async def count_in_window(self, key: str, window_seconds: int) -> int:
        """How many times `key` has been counted in the current fixed window, this call included.
        Shared by every replica, which is what an in-memory counter cannot be (used by the chat rate limit)."""
        self._ensure_initialized()
        bucket = f"ratelimit:{key}:{int(time.time() // window_seconds)}"
        count = await self._client.incr(bucket)
        if count == 1:
            await self._client.expire(bucket, window_seconds * 2)
        return int(count)

    # --- the worker loop ---

    async def run_worker(
        self,
        process_func: ProcessFuncType,
        brpop_timeout: int,
        error_retry_delay: int,
        on_dead_letter: DeadLetterFuncType | None = None,
    ):
        """
        Listens for messages and processes them until closed. `process_func` is
        `async def my_processor(payload, operation_id)` and MUST raise if processing
        fails. A failed task is re-queued up to max_retries times (after
        `error_retry_delay`) and then moved to the DLQ, if one is configured.
        `on_dead_letter` is `async def(payload, operation_id, error_message)`, awaited
        once when a task exhausts its retries, is dropped, or can no longer be re-queued.
        A Redis error is retried after `error_retry_delay`, reconnecting first; so is a
        rejected password, which is logged once as a clear error (never crashes, never spins).
        Tasks whose worker died are taken back from their expired lease (see the module docstring).
        """
        self._ensure_initialized()
        logger.info(
            "Worker started on queue '%s' (BZPOPMIN timeout %ss, %s)", self.queue_name, brpop_timeout,
            f"DLQ '{self.dlq_name}', max retries {self.max_retries}" if self.dlq_name else "no DLQ",
        )
        self._last_reap = None  # look for expired leases as soon as the loop is up
        while not self._shutdown_event.is_set():
            try:
                if not self._is_initialized:
                    await self.initialize()
                now = time.monotonic()
                if reap_due(now, self._last_reap, self.reap_interval_seconds):
                    self._last_reap = now
                    await self._reap_expired_leases(on_dead_letter)
                await self._consume_one(process_func, brpop_timeout, error_retry_delay, on_dead_letter)
            except redis_exceptions.AuthenticationError as e:
                # Before RedisError / ConnectionError, which it subclasses.
                self._report_auth_failure(e, error_retry_delay)
                await self._back_off(error_retry_delay)
            except redis_exceptions.RedisError as e:
                logger.error("Redis error in worker loop for queue '%s': %s. Retrying in %ss...", self.queue_name, e, error_retry_delay)
                await self._back_off(error_retry_delay)
            except Exception:
                logger.exception("Critical unexpected error in worker loop for queue '%s'. Retrying in %ss...", self.queue_name, error_retry_delay)
                await self._back_off(error_retry_delay)
        logger.info("Worker loop for queue '%s' has stopped.", self.queue_name)

    async def _back_off(self, delay: int) -> None:
        """Waits, then forces a reconnect on the next pass."""
        await asyncio.sleep(delay)
        self._is_initialized = False

    async def _consume_one(self, process_func, brpop_timeout: int, error_retry_delay: int, on_dead_letter) -> None:
        """Pops the lowest-scored message (highest priority), leases it while it is handled, and handles it."""
        popped = await self._client.bzpopmin(self.queue_name, timeout=brpop_timeout)
        if popped is None:
            return
        _, raw_message, popped_score = popped
        score = float(popped_score)  # what it was queued at: re-queued and dead-lettered with the same one
        lease = lease_entry(raw_message, score, uuid.uuid4().hex)
        await self._take_lease(lease, raw_message, score)
        await self._handle_message(raw_message, score, lease, process_func, error_retry_delay, on_dead_letter)
        # Only once the task is done - or safely re-queued / dead-lettered. Anything that
        # raises before this leaves the lease for the reaper, which is the point of it.
        await self._release_lease(lease)

    async def _take_lease(self, lease: str, raw_message: str, score: float) -> None:
        """Records that this worker holds the message it just popped. The pop and this write are two
        commands, so a crash between them (about one round trip) still loses the task; a failed write
        puts the message straight back instead."""
        try:
            await self._client.zadd(self._lease_key, {lease: lease_expiry(time.time(), self.lease_seconds)})
        except Exception:
            try:
                await self._send_internal(self.queue_name, raw_message, score=score)
            except Exception as requeue_error:
                logger.error("CRITICAL: Could not lease or re-queue a popped message from '%s': %s. Message might be lost.", self.queue_name, requeue_error)
            raise

    async def _release_lease(self, lease: str) -> None:
        """Gives up the lease. Best-effort: if this fails the lease expires and the task is taken back
        and run a second time. That must not fail a task that already finished, and it is only safe
        because handlers are re-runnable: the kb_worker's add_file_handler cleans up after itself
        (it deletes the knowledge-base document a failed or superseded attempt indexed, and skips a
        file that was deleted), so a second run leaves one reachable document, not two."""
        try:
            await self._client.zrem(self._lease_key, lease)
        except Exception as e:
            logger.error("Could not release the lease on a handled task in '%s': %s. It will be re-run when the lease expires.", self._lease_key, e)

    async def _handle_message(self, raw_message, score: float, lease: str, process_func, error_retry_delay: int, on_dead_letter) -> None:
        try:
            payload, metadata = parse_envelope(raw_message)
        except (ValueError, TypeError) as parse_error:
            await self._reject_unparseable(raw_message, parse_error, score)
            return
        operation_id = operation_id_of(metadata)
        if payload is None:
            logger.warning("Received task %s with null original_payload. Skipping processing.", operation_id)
            return
        time_limit = task_timeout(self.lease_seconds)
        try:
            async with asyncio.timeout(time_limit) as deadline:
                await process_func(payload=payload, operation_id=operation_id)
        except asyncio.CancelledError:
            # Shutdown (a rollout) interrupting the task: hand it back at once, uncounted, rather
            # than leave it for the lease to expire. If that fails the lease still covers it.
            try:
                await self._send_internal(self.queue_name, raw_message, score=score)
                await self._release_lease(lease)
            except Exception as e:
                logger.error("Could not hand task %s back on shutdown: %s. It will be recovered when its lease expires.", operation_id, e)
            raise
        except Exception as processing_error:
            if deadline.expired():
                # Gave up on it before its lease could run out: an ordinary counted failure. The
                # task's own cleanup ran when the timeout cancelled it.
                logger.warning("Task %s did not finish within %ss. Abandoning it.", operation_id, time_limit)
                error = task_timeout_error(time_limit)
            else:
                logger.warning("Processing failed for task %s: %s", operation_id, processing_error, exc_info=True)
                error = error_text(processing_error)
            await self._handle_failure(payload, metadata, score, error, error_retry_delay, on_dead_letter)

    # --- recovering tasks whose worker died ---

    async def _reap_expired_leases(self, on_dead_letter, now: float | None = None) -> int:
        """Takes back every task whose lease has expired, and returns how many this call took.

        Safe to run in every replica at once: ZREM of the lease is the claim, and only the caller
        it returns 1 to goes on to re-queue the task. A Redis error propagates to the worker loop.
        """
        now = time.time() if now is None else now
        leases = await self._client.zrange(self._lease_key, 0, -1, withscores=True)
        taken = 0
        for entry, expiry in leases:
            if not lease_is_stale(float(expiry), now):
                continue
            if await self._client.zrem(self._lease_key, entry) != 1:
                continue  # another worker claimed it first
            taken += 1
            try:
                await self._recover(entry, on_dead_letter)
            except Exception:
                logger.exception("Could not recover an expired lease in '%s'.", self._lease_key)
        return taken

    async def _recover(self, entry: str, on_dead_letter) -> None:
        """Re-queues (or dead-letters) a task taken from an expired lease, exactly like a failed attempt:
        it counts a retry, so a task that keeps killing its worker ends in the DLQ."""
        try:
            raw_message, score = parse_lease_entry(entry)
            payload, metadata = parse_envelope(raw_message)
        except (ValueError, TypeError) as parse_error:
            await self._reject_unparseable(entry, parse_error, None)
            return
        operation_id = operation_id_of(metadata)
        if payload is None:
            logger.warning("Expired lease on task %s has a null original_payload. Dropping it.", operation_id)
            return
        logger.warning(
            "Task %s was leased for more than %ss and never finished (its worker died or stalled). Taking it back.",
            operation_id, self.lease_seconds,
        )
        await self._handle_failure(
            payload, metadata, score, lease_expired_error(self.lease_seconds), 0, on_dead_letter,
        )

    async def _reject_unparseable(self, raw_message, parse_error: Exception, score: float | None) -> None:
        """Keeps a message that is not a well-formed envelope in the DLQ. `score` is what it was queued
        at; None (a lease entry, which has none to give) files it as a fresh default-priority message."""
        logger.error("Failed to parse a message from queue '%s': %s. Snippet: '%.100s'", self.queue_name, parse_error, str(raw_message))
        if not self.dlq_name:
            return
        logger.warning("Moving unparseable message to DLQ '%s'", self.dlq_name)
        try:
            await self._send_internal(
                self.dlq_name, unparseable_entry(raw_message, parse_error, DEFAULT_PRIORITY if score is None else score), score=score,
            )
        except Exception as dlq_error:
            logger.error("Failed to send unparseable message to DLQ '%s': %s", self.dlq_name, dlq_error)

    async def _handle_failure(self, payload, metadata, score, error, error_retry_delay, on_dead_letter) -> None:
        operation_id = operation_id_of(metadata)
        action = failure_action(bool(self.dlq_name), retry_count_of(metadata), self.max_retries)
        if action == RETRY:
            await self._retry(payload, metadata, score, error, error_retry_delay, on_dead_letter)
        elif action == DEAD_LETTER:
            await self._dead_letter(payload, metadata, score, error, on_dead_letter)
        else:
            logger.warning("Task %s failed, DLQ not configured. Message dropped.", operation_id)
            await self._notify_dead_letter(on_dead_letter, payload, operation_id, error)

    async def _retry(self, payload, metadata, score, error, error_retry_delay, on_dead_letter) -> None:
        operation_id = operation_id_of(metadata)
        logger.info(
            "Re-queuing task %s for retry %d/%d to queue '%s'",
            operation_id, retry_count_of(metadata) + 1, self.max_retries, self.queue_name,
        )
        # Wait first: a task that fails because a downstream dependency (e.g. the compute
        # service) is struggling would otherwise be re-popped and retried against it
        # immediately, up to max_retries times in rapid succession.
        await asyncio.sleep(error_retry_delay)
        try:
            # At the score it was popped with: a retry keeps its place in the order instead of
            # joining the back of the queue.
            await self._send_internal(self.queue_name, retry_envelope(payload, metadata, error), score=score)
        except Exception as requeue_error:
            # The message was popped by BZPOPMIN and putting it back failed, so no message is
            # left to retry. Without on_dead_letter here nothing would ever update the task's
            # row (e.g. a file record), which would stay "processing" forever.
            logger.error("CRITICAL: Failed to re-queue task %s for retry! Error: %s. Message might be lost.", operation_id, requeue_error)
            await self._notify_dead_letter(
                on_dead_letter, payload, operation_id, requeue_failure_error(error, requeue_error),
            )

    async def _dead_letter(self, payload, metadata, score, error, on_dead_letter) -> None:
        operation_id = operation_id_of(metadata)
        logger.warning("Max retries (%d) reached for task %s. Moving to DLQ '%s'.", self.max_retries, operation_id, self.dlq_name)
        try:
            await self._send_internal(self.dlq_name, dead_letter_envelope(payload, metadata, error), score=score)
        except Exception as dlq_error:
            logger.error("CRITICAL: Failed to move task %s to DLQ '%s'! Error: %s. Message might be lost.", operation_id, self.dlq_name, dlq_error)
        await self._notify_dead_letter(on_dead_letter, payload, operation_id, error)

    @staticmethod
    async def _notify_dead_letter(on_dead_letter, payload, operation_id: str, error_message: str) -> None:
        """Awaits the on_dead_letter callback, if there is one. A failing callback is logged, not raised - it must not crash the worker."""
        if on_dead_letter is None:
            return
        try:
            await on_dead_letter(payload=payload, operation_id=operation_id, error_message=error_message)
        except Exception as cb_error:
            logger.error("on_dead_letter callback failed for task %s: %s", operation_id, cb_error, exc_info=True)

    async def close(self):
        """Closes the Redis client and stops the worker loop."""
        if self._client:
            try:
                await self._client.aclose()
            except Exception as e:
                logger.error("Error closing the Redis connection for queue '%s': %s", self.queue_name, e)
            finally:
                self._client = None
                self._is_initialized = False
        self._shutdown_event.set()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
