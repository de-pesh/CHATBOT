"""Task leases and the reaper: a task taken off the queue survives its worker dying.

Redis is the in-memory fake in fake_redis.py. A "dead worker" is simulated by seeding a lease
nobody will release, which is exactly what one leaves behind.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from app.logic.queue_codec import (
    SCORE_PADDING,
    build_envelope,
    lease_entry,
    new_metadata,
    parse_lease_entry,
    queue_score,
)
from app.services.queue import redis_queue_service as queue_module
from app.services.queue.redis_queue_service import RedisQueueService
from fake_redis import LEASES, FakeRedis

DEFAULT_LEASE = 900
# What BZPOPMIN really returns: priority * padding + the send time in microseconds - not the priority.
SCORE = queue_score(100.0, 1_750_000_000_000_000, SCORE_PADDING)
FRESH_SCORE = queue_score(100.0, 1_750_000_060_000_000, SCORE_PADDING)  # a task sent a minute later


def _raw(payload=None, retry_count=0, op_id="op-1"):
    metadata = new_metadata("q", op_id)
    metadata["retry_count"] = retry_count
    return json.dumps(build_envelope({"file_id": 7} if payload is None else payload, metadata))


def _service(script=(), dlq="dlq", max_retries=2, client=None, lease_seconds=DEFAULT_LEASE, reap_interval=60):
    service = RedisQueueService(
        queue_name="q", redis_url="redis://x", dlq_name=dlq, max_retries=max_retries,
        lease_seconds=lease_seconds, reap_interval_seconds=reap_interval, health_check_interval_seconds=30,
    )
    service._client = client or FakeRedis(service, script)
    service._is_initialized = True
    return service


_nonces = iter(range(1_000_000))


def _seed_lease(client, raw, score=SCORE, expires_at=None):
    client.zsets.setdefault(LEASES, {})[lease_entry(raw, score, f"n{next(_nonces)}")] = time.time() - 5 if expires_at is None else expires_at


class Recorder:
    def __init__(self, fail_with=None, during=None):
        self.fail_with = fail_with
        self.during = during  # called while the task is being processed
        self.processed = []
        self.dead = []

    async def process(self, payload, operation_id):
        self.processed.append(operation_id)
        if self.during:
            self.during()
        if self.fail_with:
            raise self.fail_with

    async def on_dead_letter(self, payload, operation_id, error_message):
        self.dead.append((payload, operation_id, error_message))


# --- a worker leases what it takes, and gives the lease up when done ---

@pytest.mark.asyncio
async def test_a_taken_task_is_leased_while_it_is_processed_and_the_lease_is_cleared_on_completion():
    service = _service([("q", _raw(), SCORE)])
    seen = {}
    before = time.time()
    recorder = Recorder(during=lambda: seen.update(service._client.leases))

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=0)

    (entry, expiry), = seen.items()
    assert parse_lease_entry(entry) == (_raw(), SCORE)
    assert before + DEFAULT_LEASE <= expiry <= time.time() + DEFAULT_LEASE
    assert service._client.leases == {}


@pytest.mark.asyncio
async def test_the_lease_is_cleared_after_a_failed_task_is_requeued():
    service = _service([("q", _raw(), SCORE)])
    recorder = Recorder(fail_with=ValueError("boom"))

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=0)

    assert [queue for queue, _, _ in service._client.sent] == ["q"]
    assert service._client.leases == {}


@pytest.mark.asyncio
async def test_the_lease_is_cleared_after_an_unreadable_message_is_dead_lettered():
    service = _service([("q", "{not json", 3.0)])

    await service.run_worker(Recorder().process, brpop_timeout=1, error_retry_delay=0)

    assert [queue for queue, _, _ in service._client.sent] == ["dlq"]
    assert service._client.leases == {}


@pytest.mark.asyncio
async def test_a_failing_lease_write_puts_the_message_back_on_the_queue():
    service = _service([("q", _raw(), SCORE)])
    client = service._client
    client.zadd_error = lambda queue: queue == LEASES
    reconnects = []

    async def initialize():
        reconnects.append(1)
        service._is_initialized = True
        client.zadd_error = None

    service.initialize = initialize
    recorder = Recorder()

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=0)

    assert recorder.processed == []  # never run without a lease
    (queue, envelope, _), = client.sent
    assert queue == "q" and envelope["metadata"]["retry_count"] == 0  # back as it was, uncounted
    assert reconnects == [1]


@pytest.mark.asyncio
async def test_a_task_interrupted_by_shutdown_is_handed_back_uncounted_and_its_lease_cleared():
    service = _service([("q", _raw(retry_count=1), SCORE)])

    async def interrupted(payload, operation_id):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await service.run_worker(interrupted, brpop_timeout=1, error_retry_delay=0)

    (queue, envelope, _), = service._client.sent
    assert queue == "q"
    assert envelope["metadata"]["retry_count"] == 1 and envelope["metadata"]["last_error"] is None
    assert service._client.leases == {}


@pytest.mark.asyncio
async def test_a_failed_release_leaves_the_lease_to_expire_without_failing_the_task():
    service = _service([("q", _raw(), SCORE)])

    async def zrem(queue, member):
        raise RuntimeError("redis hiccup")

    service._client.zrem = zrem
    recorder = Recorder()

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=0)

    assert recorder.processed == ["op-1"]
    assert len(service._client.leases) == 1


# --- the reaper ---

@pytest.mark.asyncio
async def test_a_stale_lease_is_requeued_once_with_a_retry_counted():
    service = _service()
    _seed_lease(service._client, _raw(retry_count=0))

    assert await service._reap_expired_leases(None) == 1
    assert await service._reap_expired_leases(None) == 0  # nothing left to take

    (queue, envelope, score), = service._client.sent
    assert queue == "q"
    assert envelope["original_payload"] == {"file_id": 7}
    assert envelope["metadata"]["retry_count"] == 1
    assert envelope["metadata"]["last_error"].startswith("LeaseExpired:")
    assert score == SCORE  # exactly the score it was popped with
    assert service._client.leases == {}


@pytest.mark.asyncio
async def test_a_healthy_lease_is_left_alone():
    service = _service()
    _seed_lease(service._client, _raw(), expires_at=time.time() + 600)

    assert await service._reap_expired_leases(None) == 0

    assert service._client.sent == []
    assert len(service._client.leases) == 1


@pytest.mark.asyncio
async def test_a_lease_expires_exactly_when_its_time_is_up():
    service = _service()
    _seed_lease(service._client, _raw(), expires_at=1000.0)

    assert await service._reap_expired_leases(None, now=999.9) == 0
    assert await service._reap_expired_leases(None, now=1000.0) == 1


@pytest.mark.asyncio
async def test_concurrent_reapers_take_a_stale_lease_only_once():
    first = _service()
    second = _service(client=first._client)  # another replica's worker on the same Redis
    _seed_lease(first._client, _raw())

    taken = await asyncio.gather(first._reap_expired_leases(None), second._reap_expired_leases(None))

    assert sorted(taken) == [0, 1]
    assert len(first._client.sent) == 1


@pytest.mark.asyncio
async def test_a_task_out_of_retries_goes_to_the_dead_letter_queue_and_the_callback():
    service = _service(max_retries=2)
    _seed_lease(service._client, _raw(retry_count=2))
    recorder = Recorder()

    await service._reap_expired_leases(recorder.on_dead_letter)

    (queue, envelope, _), = service._client.sent
    assert queue == "dlq"
    assert envelope["metadata"]["retry_count"] == 2
    assert envelope["metadata"]["last_error"].startswith("LeaseExpired:")
    (payload, op_id, error), = recorder.dead
    assert payload == {"file_id": 7} and op_id == "op-1" and error.startswith("LeaseExpired:")


@pytest.mark.asyncio
async def test_a_task_that_keeps_killing_its_worker_is_dead_lettered_after_max_retries():
    service = _service(max_retries=2)
    recorder = Recorder()
    _seed_lease(service._client, _raw())
    destinations = []

    for _ in range(3):  # each round: the task is retaken, its worker dies, its lease expires
        service._client.sent.clear()
        await service._reap_expired_leases(recorder.on_dead_letter)
        (queue, envelope, score), = service._client.sent
        destinations.append((queue, envelope["metadata"]["retry_count"]))
        _seed_lease(service._client, json.dumps(envelope), score)

    assert destinations == [("q", 1), ("q", 2), ("dlq", 2)]
    assert score == SCORE  # and it kept its place all the way
    assert len(recorder.dead) == 1


@pytest.mark.asyncio
async def test_without_a_dead_letter_queue_an_expired_task_is_dropped_but_reported():
    service = _service(dlq=None)
    _seed_lease(service._client, _raw())
    recorder = Recorder()

    await service._reap_expired_leases(recorder.on_dead_letter)

    assert service._client.sent == []
    assert len(recorder.dead) == 1


@pytest.mark.asyncio
async def test_an_unreadable_lease_goes_to_the_dead_letter_queue_untouched():
    service = _service()
    service._client.zsets[LEASES] = {"not a lease": time.time() - 5}

    assert await service._reap_expired_leases(None) == 1

    (queue, entry, _), = service._client.sent
    assert queue == "dlq" and entry["raw_message"] == "not a lease"
    assert service._client.leases == {}


@pytest.mark.asyncio
async def test_a_failed_requeue_of_an_expired_task_is_reported_as_a_dead_letter():
    service = _service()
    service._client.zadd_error = lambda queue: queue == "q"
    _seed_lease(service._client, _raw())
    recorder = Recorder()

    await service._reap_expired_leases(recorder.on_dead_letter)

    (_, op_id, error), = recorder.dead
    assert op_id == "op-1" and error.startswith("Requeue failed after processing error 'LeaseExpired:")


# --- when the reaper runs ---

@pytest.mark.asyncio
async def test_a_worker_reaps_at_start_before_it_consumes():
    service = _service([("q", _raw(op_id="live"), SCORE)])
    _seed_lease(service._client, _raw(op_id="lost"))
    order = []
    real = service._reap_expired_leases

    async def spy(on_dead_letter, now=None):
        order.append("reap")
        return await real(on_dead_letter, now)

    service._reap_expired_leases = spy
    recorder = Recorder(during=lambda: order.append("process"))

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=0)

    assert order == ["reap", "process"]
    assert [e["metadata"]["operation_id"] for q, e, _ in service._client.sent if q == "q"] == ["lost"]


@pytest.mark.asyncio
async def test_the_reaper_runs_again_only_once_its_interval_has_passed(monkeypatch):
    service = _service([None, None, None, None], reap_interval=60)
    reaps = []

    async def spy(on_dead_letter, now=None):
        reaps.append(1)
        return 0

    service._reap_expired_leases = spy
    clock = iter([100.0, 110.0, 159.0, 161.0, 162.0, 500.0])
    # Only this module's clock: asyncio's own loop reads time.monotonic too.
    monkeypatch.setattr(queue_module, "time", SimpleNamespace(monotonic=lambda: next(clock), time=time.time))

    await service.run_worker(Recorder().process, brpop_timeout=1, error_retry_delay=0)

    assert len(reaps) == 2  # at 100 (start) and at 161; not at 110 or 159


def test_lease_and_reap_settings_must_be_positive():
    for kwargs in ({"lease_seconds": 0}, {"reap_interval": 0}):
        with pytest.raises(ValueError, match="positive"):
            _service(**kwargs)


# --- a task put back keeps the score it was popped with ---

@pytest.mark.asyncio
async def test_a_retried_task_keeps_its_place_ahead_of_newer_work():
    service = _service([("q", _raw(), SCORE)])
    recorder = Recorder(fail_with=ValueError("boom"))

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=0)
    await service._send_internal("q", _raw(op_id="fresh"))  # a new upload, made after the retry

    (retry_queue, _, retry_score), = [entry for entry in service._client.sent if entry[1]["metadata"]["operation_id"] == "op-1"]
    assert retry_score == SCORE  # not SCORE * padding, which sank it behind everything
    ordered = sorted(service._client.zsets["q"].items(), key=lambda item: item[1])
    assert [json.loads(member)["metadata"]["operation_id"] for member, _ in ordered] == ["op-1", "fresh"]


@pytest.mark.asyncio
async def test_a_dead_lettered_task_keeps_the_score_it_was_popped_with():
    service = _service([("q", _raw(retry_count=2), SCORE)], max_retries=2)

    await service.run_worker(Recorder(fail_with=ValueError("boom")).process, brpop_timeout=1, error_retry_delay=0)

    (queue, _, score), = service._client.sent
    assert queue == "dlq" and score == SCORE


@pytest.mark.asyncio
async def test_a_task_handed_back_on_shutdown_keeps_its_score():
    service = _service([("q", _raw(), SCORE)])

    async def interrupted(payload, operation_id):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await service.run_worker(interrupted, brpop_timeout=1, error_retry_delay=0)

    (_, _, score), = service._client.sent
    assert score == SCORE


@pytest.mark.asyncio
async def test_a_message_made_here_is_scored_from_its_priority_and_the_time():
    service = _service()
    before = int(time.time() * 1_000_000)

    await service._send_internal("q", _raw(), 5.0)

    (_, _, score), = service._client.sent
    assert 5.0 * SCORE_PADDING + before <= score <= 5.0 * SCORE_PADDING + time.time() * 1_000_000


@pytest.mark.asyncio
async def test_two_takes_of_the_same_message_hold_separate_leases():
    """A task handed back and popped by a second replica has the same message and score; releasing the
    first take's lease must not delete the second's."""
    first = _service([("q", _raw(), SCORE), ("q", _raw(), SCORE)])
    second = _service(client=first._client)
    held_together = []

    async def second_process(payload, operation_id):
        held_together.append(len(first._client.leases))

    async def first_process(payload, operation_id):
        await second._consume_one(second_process, 1, 0, None)
        held_together.append(len(first._client.leases))  # the second take is done; the first still holds its own

    await first._consume_one(first_process, 1, 0, None)

    assert held_together == [2, 1]
    assert first._client.leases == {}


# --- a task cannot outlive its lease ---

@pytest.mark.asyncio
async def test_a_hung_task_is_abandoned_as_a_counted_failure_before_its_lease_can_expire(monkeypatch):
    monkeypatch.setattr(queue_module, "task_timeout", lambda lease_seconds: 0.05)
    service = _service([("q", _raw(), SCORE)])
    cleaned_up = []

    async def hangs(payload, operation_id):
        try:
            await asyncio.sleep(60)
        finally:
            cleaned_up.append(True)  # the task's own cleanup runs: wait for it to be cancelled

    await service.run_worker(hangs, brpop_timeout=1, error_retry_delay=0)

    assert cleaned_up == [True]
    (queue, envelope, score), = service._client.sent
    assert queue == "q" and score == SCORE
    assert envelope["metadata"]["retry_count"] == 1
    assert envelope["metadata"]["last_error"].startswith("TaskTimeout:")
    assert service._client.leases == {}


@pytest.mark.asyncio
async def test_a_task_that_keeps_hanging_is_dead_lettered_and_reported(monkeypatch):
    monkeypatch.setattr(queue_module, "task_timeout", lambda lease_seconds: 0.05)
    service = _service([("q", _raw(retry_count=2), SCORE)], max_retries=2)
    recorder = Recorder()

    async def hangs(payload, operation_id):
        await asyncio.sleep(60)

    await service.run_worker(hangs, brpop_timeout=1, error_retry_delay=0, on_dead_letter=recorder.on_dead_letter)

    assert [queue for queue, _, _ in service._client.sent] == ["dlq"]
    assert recorder.dead[0][2].startswith("TaskTimeout:")


@pytest.mark.asyncio
async def test_a_timeout_raised_by_the_task_itself_is_not_mistaken_for_the_task_time_limit():
    service = _service([("q", _raw(), SCORE)])

    async def rpc_deadline(payload, operation_id):
        raise TimeoutError("rpc deadline exceeded")

    await service.run_worker(rpc_deadline, brpop_timeout=1, error_retry_delay=0)

    (_, envelope, _), = service._client.sent
    assert envelope["metadata"]["last_error"] == "TimeoutError: rpc deadline exceeded"


# --- the client is built with timeouts ---

@pytest.mark.asyncio
async def test_the_redis_client_is_built_with_a_socket_timeout_and_a_keepalive(monkeypatch):
    built = {}

    class Client:
        async def ping(self):
            pass

    def from_url(**kwargs):
        built.update(kwargs)
        return Client()

    monkeypatch.setattr(queue_module.Redis, "from_url", from_url)
    service = RedisQueueService(
        queue_name="q", redis_url="redis://x", max_retries=0, lease_seconds=900, reap_interval_seconds=60, health_check_interval_seconds=12,
        socket_timeout=15,
    )

    await service.initialize()

    assert built["socket_timeout"] == 15
    assert built["health_check_interval"] == 12


def test_the_factory_gives_the_socket_timeout_more_room_than_one_blocking_pop():
    from app.config import Settings
    from app.services.queue.queue_factory import create_queue_service
    from pydantic import SecretStr

    settings = Settings(
        _env_file=None, REDIS_URL=SecretStr("redis://x"), CHATBOT_QUEUE_NAME="q", DB_CONNECTION_STRING="x",
        SECRET_KEY="x", WORKER_BRPOP_TIMEOUT=7,
        REDIS_SOCKET_TIMEOUT_MARGIN_SECONDS=4, REDIS_HEALTH_CHECK_INTERVAL_SECONDS=21,
    )
    service = create_queue_service(settings)
    assert service.socket_timeout == 7 + 4
    assert service.health_check_interval_seconds == 21
    assert service.lease_seconds == settings.WORKER_LEASE_SECONDS
