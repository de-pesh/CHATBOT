"""Chooses the message queue backend from configuration.

Adding a broker means a class implementing `MessageQueueService` and a branch
here. The DLQ settings are passed on every construction so producers and the
consumer agree on retry behaviour.
"""
import logging

from app.config import Settings
from app.services.queue.queue_service import MessageQueueService

logger = logging.getLogger(__name__)


def create_queue_service(
    settings: Settings,
    queue_name: str | None = None,
) -> MessageQueueService:
    """Builds the configured queue backend. Call `initialize()` on the result."""
    queue_type = (settings.QUEUE_TYPE or "").lower()
    name = queue_name or settings.CHATBOT_QUEUE_NAME

    if queue_type == "redis":
        from app.services.queue.redis_queue_service import RedisQueueService

        password = settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None
        logger.info("Message queue: Redis, queue '%s'", name)
        return RedisQueueService(
            queue_name=name,
            redis_url=settings.REDIS_URL.get_secret_value(),
            redis_password=password,
            dlq_name=settings.CHATBOT_QUEUE_DLQ_NAME,
            max_retries=settings.CHATBOT_QUEUE_MAX_RETRIES,
            lease_seconds=settings.WORKER_LEASE_SECONDS,
            reap_interval_seconds=settings.WORKER_REAP_INTERVAL_SECONDS,
            socket_timeout=settings.WORKER_BRPOP_TIMEOUT + settings.REDIS_SOCKET_TIMEOUT_MARGIN_SECONDS,
            health_check_interval_seconds=settings.REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
        )

    if queue_type == "celery":
        from app.services.queue.celery_queue_service import CeleryQueueService

        return CeleryQueueService(settings)

    raise ValueError(f"Unsupported QUEUE_TYPE: {settings.QUEUE_TYPE!r}. Expected: redis, celery.")
