"""QUEUE_TYPE=celery: the app's settings, the producer, and what a task does when an ingest fails."""
import asyncio
from types import SimpleNamespace

import pytest
from app.celery_app import (
    broker_url,
    build_celery_app,
    celery_queue_name,
)
from app.config import Settings
from app.kb_worker import celery_tasks
from app.services.queue.celery_queue_service import CeleryQueueService
from app.services.queue.queue_factory import create_queue_service
from celery.exceptions import Retry


def _settings(**overrides):
    base = dict(
        _env_file=None, REDIS_URL="redis://redis:6379/0", CHATBOT_QUEUE_NAME="chatbot_queue",
        DB_CONNECTION_STRING="x", SECRET_KEY="x", QUEUE_TYPE="celery",
    )
    return Settings(**{**base, **overrides})


def test_the_celery_queue_never_shares_a_key_with_the_redis_queue():
    assert celery_queue_name(_settings()) == "chatbot_queue_celery"
    assert celery_queue_name(_settings(CELERY_QUEUE_NAME="ingest")) == "ingest"


def test_the_redis_password_is_put_into_the_broker_url_unless_it_already_has_one():
    assert broker_url(_settings()) == "redis://redis:6379/0"
    assert broker_url(_settings(REDIS_PASSWORD="s3cret")) == "redis://:s3cret@redis:6379/0"
    assert broker_url(_settings(REDIS_URL="redis://:inurl@redis:6379/0", REDIS_PASSWORD="other")) == "redis://:inurl@redis:6379/0"


def test_the_worker_is_a_thread_pool_sized_by_the_setting_and_acknowledges_late():
    conf = build_celery_app(_settings(CELERY_CONCURRENCY=6, WORKER_LEASE_SECONDS=900, CELERY_VISIBILITY_MARGIN_SECONDS=120)).conf
    assert conf.worker_pool == "threads" and conf.worker_concurrency == 6
    assert conf.task_acks_late is True and conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_default_queue == "chatbot_queue_celery"
    # a task is not handed to another worker while it may still be running
    assert conf.broker_transport_options["visibility_timeout"] == 900 + 120


def test_the_factory_builds_the_celery_producer():
    assert isinstance(create_queue_service(_settings()), CeleryQueueService)


def test_the_worker_consumes_nothing_in_the_api_process():
    with pytest.raises(NotImplementedError):
        asyncio.run(CeleryQueueService(_settings()).run_worker(None, 1, 1))


def test_send_message_publishes_an_ingest_task_on_the_celery_queue():
    sent = []
    service = CeleryQueueService(_settings())
    service._celery = SimpleNamespace(send_task=lambda name, **kwargs: sent.append((name, kwargs)))
    service._queue = "chatbot_queue_celery"

    asyncio.run(service.send_message({"file_id": 7}, operation_id="op-1"))

    assert sent == [("kb.ingest", {"args": [{"file_id": 7}], "task_id": "op-1", "queue": "chatbot_queue_celery"})]
    with pytest.raises(TypeError):
        asyncio.run(service.send_message("not a dict"))


def test_send_message_before_initialize_is_an_error():
    with pytest.raises(RuntimeError):
        asyncio.run(CeleryQueueService(_settings()).send_message({"file_id": 1}))


# ---- the ingest task's retry and failure handling

class FakeRuntime:
    settings = SimpleNamespace(WORKER_ERROR_RETRY_DELAY=5)

    def run(self, coro, timeout=None):
        return asyncio.run(coro)


@pytest.fixture
def worker(monkeypatch):
    calls = {"failed": []}

    async def failing_ingest(runtime, payload, operation_id):
        raise RuntimeError("compute service down")

    async def mark_failed(runtime, payload, operation_id, error):
        calls["failed"].append((payload["file_id"], error))

    monkeypatch.setattr(celery_tasks, "get_runtime", lambda: FakeRuntime())
    monkeypatch.setattr(celery_tasks, "ingest_async", failing_ingest)
    monkeypatch.setattr(celery_tasks, "mark_failed_async", mark_failed)
    return calls


def _run_ingest(retries):
    celery_tasks.ingest.push_request(id="op-1", retries=retries, called_directly=False, is_eager=True)
    try:
        return celery_tasks.ingest.run({"file_id": 7})
    finally:
        celery_tasks.ingest.pop_request()


def test_a_failed_ingest_is_retried_while_retries_remain(worker):
    with pytest.raises(Retry):
        _run_ingest(retries=0)
    assert worker["failed"] == []  # not failed for good yet


def test_an_ingest_that_has_spent_its_retries_marks_the_file_failed_and_fails(worker):
    with pytest.raises(RuntimeError, match="compute service down"):
        _run_ingest(retries=celery_tasks.ingest.max_retries)
    assert worker["failed"] == [(7, "RuntimeError: compute service down")]


def test_a_successful_ingest_marks_nothing(monkeypatch):
    marked = []

    async def ok(runtime, payload, operation_id):
        return None

    async def mark_failed(*args):
        marked.append(args)

    monkeypatch.setattr(celery_tasks, "get_runtime", lambda: FakeRuntime())
    monkeypatch.setattr(celery_tasks, "ingest_async", ok)
    monkeypatch.setattr(celery_tasks, "mark_failed_async", mark_failed)
    _run_ingest(retries=0)
    assert marked == []


# ---- the catalog refresh is scheduled once per burst

class FakeRedis:
    def __init__(self):
        self.keys = set()

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def test_only_the_first_finished_file_of_a_burst_schedules_a_refresh(monkeypatch):
    scheduled = []
    redis = FakeRedis()

    class Runtime:
        settings = SimpleNamespace(CELERY_CATALOG_REFRESH_DELAY_SECONDS=30.0)

        async def redis(self):
            return redis

    monkeypatch.setattr(celery_tasks.celery_app, "send_task", lambda name, **kw: scheduled.append((name, kw)))

    async def burst():
        refresh = celery_tasks.ScheduledCatalogRefresh(Runtime())
        for _ in range(5):
            await refresh.refresh()

    asyncio.run(burst())

    assert scheduled == [("kb.refresh_catalog", {"countdown": 30.0})]
