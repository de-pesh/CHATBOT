"""The queue worker loop: what it does with each message it pops, and how it retries.

Redis is replaced by a scripted fake, so this pins the retry / dead-letter
behaviour (including what is sent where, at which priority) without a server.
"""
import json

import pytest
import redis.exceptions as redis_exceptions
from app.logic.queue_codec import (
    SCORE_PADDING,
    build_envelope,
    new_metadata,
    queue_score,
)
from app.services.queue.redis_queue_service import RedisQueueService
from fake_redis import FakeRedis

# What BZPOPMIN really returns: priority * padding + send time in microseconds.
SCORE = queue_score(100.0, 1_750_000_000_000_000, SCORE_PADDING)


def _message(payload, retry_count=0, op_id="op-1", score=SCORE):
    metadata = new_metadata("q", op_id)
    metadata["retry_count"] = retry_count
    return ("q", json.dumps(build_envelope(payload, metadata)), score)


def _service(script, dlq="dlq", max_retries=2, zadd_error=None):
    service = RedisQueueService(
        queue_name="q", redis_url="redis://x", dlq_name=dlq, max_retries=max_retries,
        lease_seconds=900, reap_interval_seconds=60,
        health_check_interval_seconds=30,
    )
    service._client = FakeRedis(service, script, zadd_error)
    service._is_initialized = True
    return service


class Recorder:
    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.processed = []
        self.dead = []

    async def process(self, payload, operation_id):
        self.processed.append((payload, operation_id))
        if self.fail_with:
            raise self.fail_with

    async def on_dead_letter(self, payload, operation_id, error_message):
        self.dead.append((payload, operation_id, error_message))


async def _work(service, recorder, on_dead_letter=True):
    await service.run_worker(
        recorder.process, brpop_timeout=1, error_retry_delay=0,
        on_dead_letter=recorder.on_dead_letter if on_dead_letter else None,
    )


@pytest.mark.asyncio
async def test_a_message_is_processed_with_its_payload_and_operation_id():
    service = _service([_message({"file_id": 7})])
    recorder = Recorder()

    await _work(service, recorder)

    assert recorder.processed == [({"file_id": 7}, "op-1")]
    assert service._client.sent == []
    assert recorder.dead == []


@pytest.mark.asyncio
async def test_an_empty_poll_just_polls_again():
    service = _service([None, _message({"a": 1})])
    recorder = Recorder()

    await _work(service, recorder)

    assert len(recorder.processed) == 1


@pytest.mark.asyncio
async def test_a_null_payload_is_skipped():
    service = _service([_message(None)])
    recorder = Recorder()

    await _work(service, recorder)

    assert recorder.processed == []
    assert service._client.sent == []


@pytest.mark.asyncio
async def test_a_failed_task_is_requeued_with_its_retry_count_and_error():
    service = _service([_message({"file_id": 7})])
    recorder = Recorder(fail_with=ValueError("boom"))

    await _work(service, recorder)

    (queue, envelope, score), = service._client.sent
    assert queue == "q"
    assert envelope["original_payload"] == {"file_id": 7}
    assert envelope["metadata"]["retry_count"] == 1
    assert envelope["metadata"]["last_error"] == "ValueError: boom"
    assert score == SCORE  # the popped score is passed through unchanged, not treated as a priority and scaled again
    assert recorder.dead == []


@pytest.mark.asyncio
async def test_a_task_out_of_retries_goes_to_the_dead_letter_queue_and_the_callback():
    service = _service([_message({"file_id": 7}, retry_count=2)], max_retries=2)
    recorder = Recorder(fail_with=ValueError("boom"))

    await _work(service, recorder)

    (queue, envelope, _), = service._client.sent
    assert queue == "dlq"
    assert envelope["metadata"]["retry_count"] == 2
    assert envelope["metadata"]["last_error"] == "ValueError: boom"
    assert recorder.dead == [({"file_id": 7}, "op-1", "ValueError: boom")]


@pytest.mark.asyncio
async def test_a_task_retries_until_the_limit_then_dead_letters():
    service = _service([_message({"file_id": 7}, retry_count=1)], max_retries=2)
    recorder = Recorder(fail_with=ValueError("boom"))

    await _work(service, recorder)
    assert [s[0] for s in service._client.sent] == ["q"]

    service._client.script = [("q", json.dumps(service._client.sent[0][1]), 1.0)]
    service._shutdown_event.clear()
    service._client.sent.clear()
    await _work(service, recorder)
    assert [s[0] for s in service._client.sent] == ["dlq"]
    assert len(recorder.dead) == 1


@pytest.mark.asyncio
async def test_without_a_dead_letter_queue_a_failure_is_dropped_but_reported():
    service = _service([_message({"file_id": 7})], dlq=None)
    recorder = Recorder(fail_with=ValueError("boom"))

    await _work(service, recorder)

    assert service._client.sent == []
    assert recorder.dead == [({"file_id": 7}, "op-1", "ValueError: boom")]


@pytest.mark.asyncio
async def test_a_failed_requeue_is_reported_as_a_dead_letter():
    service = _service([_message({"file_id": 7})], zadd_error=lambda queue: queue == "q")
    recorder = Recorder(fail_with=ValueError("boom"))

    await _work(service, recorder)

    (payload, op_id, message), = recorder.dead
    assert payload == {"file_id": 7} and op_id == "op-1"
    assert message.startswith("Requeue failed after processing error 'ValueError: boom': ")


@pytest.mark.asyncio
async def test_a_failed_dlq_write_still_reports_the_dead_letter():
    service = _service([_message({"file_id": 7}, retry_count=2)], max_retries=2, zadd_error=lambda queue: queue == "dlq")
    recorder = Recorder(fail_with=ValueError("boom"))

    await _work(service, recorder)

    assert recorder.dead == [({"file_id": 7}, "op-1", "ValueError: boom")]


@pytest.mark.asyncio
async def test_a_failing_dead_letter_callback_does_not_stop_the_worker():
    service = _service([_message({"n": 1}, retry_count=2), _message({"n": 2})], max_retries=2)
    recorder = Recorder(fail_with=ValueError("boom"))

    async def broken(payload, operation_id, error_message):
        raise RuntimeError("callback down")

    await service.run_worker(recorder.process, brpop_timeout=1, error_retry_delay=0, on_dead_letter=broken)

    assert len(recorder.processed) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["{not json", json.dumps([1, 2]), json.dumps({"original_payload": "text", "metadata": {}})])
async def test_an_unreadable_message_goes_to_the_dead_letter_queue_untouched(raw):
    service = _service([("q", raw, 3.0)])
    recorder = Recorder()

    await _work(service, recorder)

    (queue, entry, score), = service._client.sent
    assert queue == "dlq"
    assert entry["raw_message"] == raw and "parse_error" in entry and entry["original_priority"] == 3.0
    assert recorder.processed == [] and recorder.dead == []


@pytest.mark.asyncio
async def test_an_unreadable_message_is_dropped_when_there_is_no_dead_letter_queue():
    service = _service([("q", "{not json", 3.0)], dlq=None)
    recorder = Recorder()

    await _work(service, recorder)

    assert service._client.sent == [] and recorder.processed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    redis_exceptions.ConnectionError("lost"), redis_exceptions.RedisError("bad"), RuntimeError("odd"),
])
async def test_a_redis_or_unexpected_error_reconnects_and_carries_on(error):
    service = _service([error, _message({"n": 1})])
    recorder = Recorder()
    reconnects = []

    async def initialize():
        reconnects.append(1)
        service._is_initialized = True

    service.initialize = initialize

    await _work(service, recorder)

    assert reconnects == [1]
    assert recorder.processed == [({"n": 1}, "op-1")]


@pytest.mark.asyncio
async def test_a_reconnect_that_keeps_failing_is_retried_every_time_round_the_loop():
    """Unchanged behaviour: a failing initialize() (an auth failure, say) is caught like any Redis error and retried."""
    service = _service([redis_exceptions.ConnectionError("lost")])
    attempts = []

    async def initialize():
        attempts.append(1)
        if len(attempts) == 3:
            service._shutdown_event.set()
        raise redis_exceptions.AuthenticationError("bad password")

    service.initialize = initialize

    await service.run_worker(Recorder().process, brpop_timeout=1, error_retry_delay=0)

    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_the_worker_refuses_to_run_before_initialisation():
    service = RedisQueueService(
        queue_name="q", redis_url="redis://x", max_retries=0, lease_seconds=900, reap_interval_seconds=60,
        health_check_interval_seconds=30,
    )

    with pytest.raises(RuntimeError, match="not initialized"):
        await service.run_worker(Recorder().process, brpop_timeout=1, error_retry_delay=0)
