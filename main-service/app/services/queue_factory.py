"""Chooses the message queue backend from configuration.

Adding a broker means a class implementing `MessageQueueService` and a branch
here. The DLQ settings are passed on every construction so producers and the
consumer agree on retry behaviour - they used not to: the app-state producer
was built without a DLQ while the worker built its own with one.
"""
import logging
from typing import Optional

from app.config import Settings
from app.services.queue_service import MessageQueueService

logger = logging.getLogger(__name__)


def create_queue_service(
    settings: Settings,
    queue_name: Optional[str] = None,
) -> MessageQueueService:
    """Builds the configured queue backend. Call `initialize()` on the result."""
    queue_type = (settings.QUEUE_TYPE or "").lower()
    name = queue_name or settings.CHATBOT_QUEUE_NAME

    if queue_type == "redis":
        from app.services.redis_queue_service import RedisQueueService

        password = settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None
        logger.info("Message queue: Redis, queue '%s'", name)
        return RedisQueueService(
            queue_name=name,
            redis_url=settings.REDIS_URL,
            redis_password=password,
            dlq_name=settings.CHATBOT_QUEUE_DLQ_NAME,
            max_retries=settings.CHATBOT_QUEUE_MAX_RETRIES,
        )

    raise ValueError(f"Unsupported QUEUE_TYPE: {settings.QUEUE_TYPE!r}. Expected: redis.")
