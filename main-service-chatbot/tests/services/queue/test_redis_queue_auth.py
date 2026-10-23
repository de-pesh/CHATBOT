"""A rejected Redis password: one clear error, a back-off, no crash, and recovery once it is fixed.

redis.exceptions.AuthenticationError subclasses ConnectionError, so it used to be swallowed by the
generic connection handling and showed up as endless "connection error" noise.
"""
import asyncio
import json
import logging

import pytest
import redis.exceptions as redis_exceptions
from app.logic.queue_codec import build_envelope, new_metadata
from app.services.queue import redis_queue_service as queue_module
from app.services.queue.redis_queue_service import RedisQueueService
from fake_redis import FakeRedis

LOGGER = "app.services.queue.redis_queue_service"


def _service():
    service = RedisQueueService(
        queue_name="q", redis_url="redis://x", dlq_name="dlq", max_retries=2, lease_seconds=900, reap_interval_seconds=60,
        health_check_interval_seconds=30,
    )
    service._is_initialized = True
    return service


class _Captured:
    def __init__(self):
        self.records = []


@pytest.fixture
def logs():
    """Records this module's log lines itself: an earlier test that loads the app's logging config
    (dictConfig) disables loggers that already exist, which would blind pytest's own caplog."""
    logger = logging.getLogger(LOGGER)
    captured = _Captured()

    class Handler(logging.Handler):
        def emit(self, record):
            captured.records.append(record)

    handler = Handler(logging.DEBUG)
    was_disabled, was_level = logger.disabled, logger.level
    logger.disabled, logger.level = False, logging.DEBUG
    logger.addHandler(handler)
    yield captured
    logger.removeHandler(handler)
    logger.disabled, logger.level = was_disabled, was_level


def _errors(logs):
    return [r for r in logs.records if r.levelno >= logging.ERROR]


def _message(payload):
    return ("q", json.dumps(build_envelope(payload, new_metadata("q", "op-1"))), 1.0)


class Recorder:
    def __init__(self):
        self.processed = []

    async def process(self, payload, operation_id):
        self.processed.append(payload)


@pytest.mark.asyncio
async def test_a_rejected_password_in_the_worker_loop_logs_one_clear_error_and_backs_off(logs, monkeypatch):
    service = _service()
    wrong = redis_exceptions.AuthenticationError("invalid username-password pair")
    service._client = FakeRedis(service, [wrong, wrong, wrong, _message({"n": 1})])
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    reconnects = []

    async def initialize():
        reconnects.append(1)
        service._is_initialized = True

    service.initialize = initialize
    recorder = Recorder()

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=7)

    errors = _errors(logs)
    assert len(errors) == 1  # three failures, one ERROR - not one per retry
    assert "REDIS_URL" in errors[0].getMessage() and "REDIS_PASSWORD" in errors[0].getMessage()
    assert [delay for delay in sleeps if delay] == [7, 7, 7]  # (the fake's own zero-second yields aside)  # backed off at the normal error delay each time; never spun
    assert len(reconnects) == 3  # and reconnected each time
    assert recorder.processed == [{"n": 1}]  # recovered once the password worked


@pytest.mark.asyncio
async def test_an_ordinary_connection_error_is_not_reported_as_a_credentials_problem(logs):
    service = _service()
    service._client = FakeRedis(service, [redis_exceptions.ConnectionError("lost"), _message({"n": 1})])

    async def initialize():
        service._is_initialized = True

    service.initialize = initialize
    recorder = Recorder()

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=0)

    (error,) = _errors(logs)
    assert "Redis error in worker loop" in error.getMessage()
    assert "REDIS_PASSWORD" not in error.getMessage()
    assert recorder.processed == [{"n": 1}]


@pytest.mark.asyncio
async def test_a_second_outage_after_recovery_is_reported_again(logs):
    service = _service()
    wrong = redis_exceptions.AuthenticationError("bad")
    service._client = FakeRedis(service, [wrong, _message({"n": 1}), wrong])

    async def initialize():
        service._is_initialized = True
        service._auth_failure_reported = False  # what a successful initialize() does

    service.initialize = initialize

    await service.run_worker(Recorder().process, brpop_timeout=1, error_retry_delay=0)

    assert len(_errors(logs)) == 2


class _RejectingClient:
    def __init__(self, error):
        self.error = error

    async def ping(self):
        raise self.error

    async def aclose(self):
        pass


def _service_to_open():
    service = RedisQueueService(
        queue_name="q", redis_url="redis://x", max_retries=0, lease_seconds=900, reap_interval_seconds=60,
        health_check_interval_seconds=30,
    )
    return service


@pytest.mark.asyncio
async def test_opening_the_queue_with_a_rejected_password_names_the_setting_to_fix(logs, monkeypatch):
    service = _service_to_open()
    monkeypatch.setattr(queue_module.Redis, "from_url", lambda **kwargs: _RejectingClient(redis_exceptions.AuthenticationError("WRONGPASS")))

    with pytest.raises(redis_exceptions.AuthenticationError):
        await service.initialize()

    (error,) = _errors(logs)
    assert "REDIS_URL" in error.getMessage() and "REDIS_PASSWORD" in error.getMessage()
    assert service._client is None and not service._is_initialized


@pytest.mark.asyncio
async def test_opening_the_queue_with_an_unreachable_redis_is_a_plain_connection_error(logs, monkeypatch):
    service = _service_to_open()
    monkeypatch.setattr(queue_module.Redis, "from_url", lambda **kwargs: _RejectingClient(redis_exceptions.ConnectionError("refused")))

    with pytest.raises(redis_exceptions.ConnectionError):
        await service.initialize()

    (error,) = _errors(logs)
    assert "Could not connect to Redis" in error.getMessage() and "REDIS_PASSWORD" not in error.getMessage()


@pytest.mark.asyncio
async def test_a_worker_whose_reconnect_is_rejected_keeps_retrying_and_reports_once(logs, monkeypatch):
    """The real initialize() failing on auth, inside the loop: one ERROR however many times it is retried."""
    service = _service_to_open()
    service._is_initialized = False
    attempts = []

    class Client(_RejectingClient):
        async def ping(self):
            attempts.append(1)
            if len(attempts) == 4:
                service._shutdown_event.set()
            raise redis_exceptions.AuthenticationError("WRONGPASS")

    monkeypatch.setattr(queue_module.Redis, "from_url", lambda **kwargs: Client(None))
    service._is_initialized = True  # run_worker's precondition; the first failure below resets it
    service._client = FakeRedis(service, [redis_exceptions.ConnectionError("dropped")])

    await service.run_worker(Recorder().process, brpop_timeout=1, error_retry_delay=0)

    assert len(attempts) == 4
    auth_errors = [r for r in _errors(logs) if "REDIS_PASSWORD" in r.getMessage()]
    assert len(auth_errors) == 1
