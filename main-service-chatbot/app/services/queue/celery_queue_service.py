"""The producer side of QUEUE_TYPE=celery: `send_message` becomes a Celery task.

The API pods use this to hand a file to the ingest workers. It never consumes: the worker
is a separate process inside parsing-indexing-service (`celery -A app.celery_app worker`).

Priorities are not carried over: Redis priorities in Celery need extra queue configuration and nothing
here sends anything but the default.
"""
import asyncio
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from celery import Celery
from redis.asyncio import Redis

from app.config import Settings
from app.services.queue.queue_service import (
    DEFAULT_PRIORITY,
    DeadLetterFuncType,
    MessageQueueService,
    ProcessFuncType,
)

logger = logging.getLogger(__name__)

TASK_INGEST = "kb.ingest"


def _broker_url(settings: Settings) -> str:
    url = settings.REDIS_URL.get_secret_value()
    password = settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None
    parts = urlsplit(url)
    if not password or parts.password:
        return url
    host = parts.hostname or ""
    netloc = f":{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _queue_name(settings: Settings) -> str:
    return settings.CELERY_QUEUE_NAME or f"{settings.CHATBOT_QUEUE_NAME}_celery"


class CeleryQueueService(MessageQueueService):
    def __init__(self, settings: Settings):
        self.settings = settings
        self._celery: Celery | None = None
        self._redis: Redis | None = None
        self._queue: str = ""

    async def initialize(self) -> None:
        url = _broker_url(self.settings)
        self._queue = _queue_name(self.settings)
        self._celery = Celery("chatbot-sender", broker=url)
        self._celery.conf.update(
            task_default_queue=self._queue,
            task_serializer="json",
            accept_content=["json"],
            broker_connection_retry_on_startup=True,
        )
        await asyncio.to_thread(self._check_broker)
        self._redis = Redis.from_url(url, decode_responses=True)
        logger.info("Message queue: Celery over Redis, queue '%s'", self._queue)

    def _check_broker(self) -> None:
        with self._celery.connection_for_write() as connection:
            connection.ensure_connection(max_retries=3)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def send_message(
        self, payload: dict[str, Any], priority: float = DEFAULT_PRIORITY, operation_id: str | None = None,
    ) -> None:
        if self._celery is None:
            raise RuntimeError("CeleryQueueService has not been initialized.")
        if not isinstance(payload, dict):
            raise TypeError(f"Message payload must be a dictionary (received {type(payload)}).")
        task_id = operation_id or str(uuid.uuid4())
        await asyncio.to_thread(
            self._celery.send_task, TASK_INGEST, args=[payload], task_id=task_id, queue=self._queue,
        )
        logger.debug("Queued ingest task %s on '%s'", task_id, self._queue)

    async def count_in_window(self, key: str, window_seconds: int) -> int:
        """Counts `key` in the current fixed window, shared by every replica (the chat rate limit)."""
        if self._redis is None:
            raise RuntimeError("CeleryQueueService has not been initialized.")
        bucket = f"ratelimit:{key}:{int(time.time() // window_seconds)}"
        count = await self._redis.incr(bucket)
        if count == 1:
            await self._redis.expire(bucket, window_seconds * 2)
        return int(count)

    async def run_worker(
        self, process_func: ProcessFuncType, brpop_timeout: int, error_retry_delay: int,
        on_dead_letter: DeadLetterFuncType | None = None,
    ) -> None:
        raise NotImplementedError(
            "With QUEUE_TYPE=celery the worker runs in parsing-indexing-service: "
            "celery -A app.celery_app worker",
        )
