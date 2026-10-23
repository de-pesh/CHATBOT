"""Celery task handlers for knowledge-base ingestion.

Each Celery worker process shares one event loop on a background thread. All
services (DB pool, storage, embedding, vision) are created once per process and
reused across tasks, so N concurrent tasks do not re-open N connection pools.
"""
import asyncio
import logging
import threading

import httpx
from redis.asyncio import Redis

from app.celery_app import (
    TASK_INGEST,
    broker_url,
    celery_app,
)
from app.config import Settings, get_settings
from app.worker.worker import handle_dead_letter, process_message
from app.logic.queue_codec import task_timeout

logger = logging.getLogger(__name__)

CATALOG_REFRESH_KEY = "kb:catalog_refresh_pending"

# OpenAI SDK error class names that indicate a permanent misconfiguration.
# Retrying cannot fix a missing deployment or a bad API key.
_PERMANENT_OPENAI_ERRORS = frozenset({
    "NotFoundError",          # deployment doesn't exist (DeploymentNotFound)
    "AuthenticationError",    # bad API key
    "PermissionDeniedError",  # not authorized for this resource
})


def _is_permanent_error(exc: Exception) -> bool:
    """True when retrying cannot possibly fix this error."""
    cls = type(exc)
    return cls.__module__.startswith("openai") and cls.__name__ in _PERMANENT_OPENAI_ERRORS


class Runtime:
    """Everything one worker process shares: event loop thread + service clients."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name="kb-event-loop", daemon=True)
        self._thread.start()
        self._db = None
        self._storage = None
        self._embedding = None
        self._vision = None
        self._redis: Redis | None = None
        self._init_lock: asyncio.Lock | None = None

    def run(self, coro, timeout: float | None = None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def _lock(self) -> asyncio.Lock:
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        return self._init_lock

    async def db(self):
        async with self._lock():
            if self._db is None:
                from app.services.db.database_factory import create_database_service
                service = create_database_service(self.settings)
                await service.initialize()
                self._db = service
        return self._db

    async def storage(self):
        async with self._lock():
            if self._storage is None:
                from app.services.storage.storage_factory import create_storage_service
                service = create_storage_service(self.settings)
                await service.initialize()
                self._storage = service
        return self._storage

    async def embedding(self):
        async with self._lock():
            if self._embedding is None:
                from app.services.embedding.embedding_factory import create_embedding_service
                self._embedding = create_embedding_service(self.settings)
        return self._embedding

    async def vision(self):
        async with self._lock():
            if self._vision is None:
                from app.parsing.vision.service import VisionService
                from app.parsing.vision.vision_llm_factory import create_vision_llm_client
                self._vision = VisionService(llm=create_vision_llm_client(self.settings))
        return self._vision

    async def redis(self) -> Redis:
        async with self._lock():
            if self._redis is None:
                self._redis = Redis.from_url(broker_url(self.settings), decode_responses=True)
        return self._redis


_runtime: Runtime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> Runtime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = Runtime(get_settings())
        return _runtime


class CatalogRefreshNotifier:
    """After a successful ingest, notifies main-service to refresh catalog descriptions.

    Uses a short-lived Redis key to deduplicate bursts: the first file in a burst
    sets the key and fires the HTTP call; subsequent files skip it until the key
    expires and the next burst can start a new one.
    """

    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    async def refresh(self) -> None:
        main_url = (self.runtime.settings.MAIN_SERVICE_URL or "").rstrip("/")
        if not main_url:
            return
        delay = self.runtime.settings.CELERY_CATALOG_REFRESH_DELAY_SECONDS
        try:
            redis = await self.runtime.redis()
            if not await redis.set(CATALOG_REFRESH_KEY, "1", nx=True, ex=max(1, int(delay))):
                return
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{main_url}/internal/catalog/refresh")
        except Exception:
            logger.warning("Could not trigger catalog refresh on main-service.", exc_info=True)


async def ingest_async(runtime: Runtime, payload: dict, operation_id: str) -> None:
    db = await runtime.db()
    storage = await runtime.storage()
    embedding = await runtime.embedding()
    vision = await runtime.vision()
    catalog = CatalogRefreshNotifier(runtime)
    await asyncio.wait_for(
        process_message(
            payload, operation_id,
            settings=runtime.settings,
            db_service=db,
            storage=storage,
            embedding=embedding,
            vision=vision,
            catalog=catalog,
        ),
        timeout=task_timeout(runtime.settings.WORKER_LEASE_SECONDS),
    )


async def mark_failed_async(runtime: Runtime, payload: dict, operation_id: str, error: str) -> None:
    await handle_dead_letter(
        payload, operation_id, error,
        db_service=await runtime.db(),
        settings=runtime.settings,
    )


_settings = get_settings()


@celery_app.task(bind=True, name=TASK_INGEST, max_retries=_settings.CHATBOT_QUEUE_MAX_RETRIES)
def ingest(self, payload: dict):
    runtime = get_runtime()
    operation_id = self.request.id or "unknown"
    try:
        runtime.run(ingest_async(runtime, payload, operation_id))
    except Exception as exc:
        permanent = _is_permanent_error(exc) or self.request.retries >= self.max_retries
        if permanent:
            if _is_permanent_error(exc):
                logger.error(
                    "Ingest %s permanently failed (unretryable %s): %s",
                    operation_id, type(exc).__name__, exc,
                )
            else:
                logger.error(
                    "Ingest %s failed for good after %d retries: %s",
                    operation_id, self.request.retries, exc,
                )
            try:
                runtime.run(mark_failed_async(runtime, payload, operation_id, f"{type(exc).__name__}: {exc}"))
            except Exception:
                logger.exception("Could not record the failure of ingest %s.", operation_id)
            raise
        logger.warning(
            "Ingest %s failed (%s); retry %d of %d.",
            operation_id, exc, self.request.retries + 1, self.max_retries,
        )
        raise self.retry(exc=exc, countdown=runtime.settings.WORKER_ERROR_RETRY_DELAY) from exc
