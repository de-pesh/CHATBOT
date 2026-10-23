"""The Celery application for knowledge-base ingestion.

Run the worker with:

    celery -A app.celery_app worker

CELERY_CONCURRENCY controls how many files are processed at once.
"""
from urllib.parse import urlsplit, urlunsplit

from celery import Celery

from app.config import Settings, get_settings

TASK_INGEST = "kb.ingest"


def celery_queue_name(settings: Settings) -> str:
    return settings.CELERY_QUEUE_NAME or f"{settings.CHATBOT_QUEUE_NAME}_celery"


def broker_url(settings: Settings) -> str:
    """REDIS_URL, with REDIS_PASSWORD injected when the URL does not carry one."""
    url = settings.REDIS_URL.get_secret_value()
    password = settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None
    parts = urlsplit(url)
    if not password or parts.password:
        return url
    host = parts.hostname or ""
    netloc = f":{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def build_celery_app(settings: Settings) -> Celery:
    app = Celery("chatbot-indexer", broker=broker_url(settings), include=["app.worker.celery_tasks"])
    app.conf.update(
        task_default_queue=celery_queue_name(settings),
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        worker_pool="threads",
        worker_concurrency=settings.CELERY_CONCURRENCY,
        worker_hijack_root_logger=False,
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            "visibility_timeout": settings.WORKER_LEASE_SECONDS + settings.CELERY_VISIBILITY_MARGIN_SECONDS
        },
    )
    return app


celery_app = build_celery_app(get_settings())
