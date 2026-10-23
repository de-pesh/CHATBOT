"""The queue's wire format."""
import json

import pytest
from app.logic.queue_codec import (
    DEAD_LETTER,
    DROP,
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
    parse_json,
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

PADDING = SCORE_PADDING


def test_an_envelope_round_trips_through_json():
    metadata = new_metadata("chatbot_queue", "op-1")
    payload = {"action_type": "create", "file_id": 7, "name": "Ünïcode.pdf"}
    received_payload, received_metadata = parse_envelope(json.dumps(build_envelope(payload, metadata)))
    assert received_payload == payload
    assert received_metadata == {
        "operation_id": "op-1", "retry_count": 0, "original_queue": "chatbot_queue", "last_error": None,
    }


def test_an_operation_id_is_minted_when_none_is_given():
    first, second = new_metadata("q")["operation_id"], new_metadata("q")["operation_id"]
    assert len(first) == 36 and first != second


def test_a_null_payload_parses_so_the_worker_can_skip_it():
    assert parse_envelope(json.dumps(build_envelope(None, new_metadata("q")))) [0] is None


@pytest.mark.parametrize("message", [
    "{not json",
    "[1, 2]",
    '"text"',
    json.dumps({"metadata": {}}),
    json.dumps({"original_payload": {}}),
    json.dumps({"original_payload": {}, "metadata": "nope"}),
])
def test_a_malformed_envelope_is_refused(message):
    with pytest.raises(ValueError, match="Invalid wrapped message structure received."):
        parse_envelope(message)


def test_a_payload_that_is_not_an_object_is_refused_naming_the_task():
    message = json.dumps(build_envelope("text", new_metadata("q", "op-9")))
    with pytest.raises(ValueError, match="not a dictionary or null for task op-9"):
        parse_envelope(message)


def test_a_message_that_is_not_text_is_a_type_error():
    with pytest.raises(TypeError):
        parse_envelope(None)


def test_a_missing_operation_id_gets_a_placeholder():
    assert operation_id_of({}) == "MISSING_ID_IN_METADATA"
    assert operation_id_of({"operation_id": "x"}) == "x"


def test_parse_json_returns_none_for_invalid_json_instead_of_raising():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json("{nope") is None


def test_priority_dominates_the_score_and_send_time_keeps_one_priority_in_order():
    early, late = 1_700_000_000_000_000, 1_700_000_000_000_500
    assert queue_score(1, early, PADDING) < queue_score(1, late, PADDING)
    assert queue_score(1, late, PADDING) < queue_score(2, early, PADDING)
    assert queue_score(100.0, early, PADDING) == 100.0 * PADDING + early


@pytest.mark.parametrize("url,masked", [
    ("redis://host:6379/0", "redis://host:6379/0"),
    ("redis://user:secret@host:6379/0", "redis:...@host:6379/0"),
    ("rediss://:secret@host", "rediss:...@host"),
])
def test_credentials_never_reach_the_log(url, masked):
    assert masked_url(url) == masked
    assert "secret" not in masked_url(url)


def test_only_a_non_empty_password_counts_as_embedded():
    assert url_has_embedded_password("redis://:secret@host")
    assert url_has_embedded_password("redis://user:secret@host")
    assert not url_has_embedded_password("redis://user@host")
    assert not url_has_embedded_password("redis://host:6379")


# --- failure handling ---

@pytest.mark.parametrize("has_dlq,retries,limit,action", [
    (True, 0, 3, RETRY), (True, 2, 3, RETRY), (True, 3, 3, DEAD_LETTER), (True, 5, 3, DEAD_LETTER),
    (True, 0, 0, DEAD_LETTER), (False, 0, 3, DROP), (False, 9, 3, DROP),
])
def test_a_failed_task_is_retried_until_the_limit_then_dead_lettered_or_dropped(has_dlq, retries, limit, action):
    assert failure_action(has_dlq, retries, limit) == action


def test_the_retry_count_defaults_to_zero():
    assert retry_count_of({}) == 0
    assert retry_count_of({"retry_count": 2}) == 2


def test_an_error_reads_as_its_type_and_message():
    assert error_text(ValueError("boom")) == "ValueError: boom"


def test_a_retry_counts_one_more_attempt_and_keeps_the_payload():
    metadata = new_metadata("q", "op-1")
    envelope = json.loads(retry_envelope({"file_id": 7}, metadata, "ValueError: boom"))
    assert envelope["original_payload"] == {"file_id": 7}
    assert envelope["metadata"]["retry_count"] == 1 and envelope["metadata"]["last_error"] == "ValueError: boom"
    assert metadata["retry_count"] == 0, "the original metadata is not changed"
    payload, again = parse_envelope(json.dumps(envelope))
    assert retry_count_of(again) == 1 and payload == {"file_id": 7}


def test_a_dead_letter_keeps_the_retry_count_and_records_the_error():
    metadata = {**new_metadata("q", "op-1"), "retry_count": 3}
    envelope = json.loads(dead_letter_envelope({"a": 1}, metadata, "boom"))
    assert envelope["metadata"]["retry_count"] == 3 and envelope["metadata"]["last_error"] == "boom"


def test_an_unreadable_message_is_kept_whole_for_inspection():
    entry = json.loads(unparseable_entry("{nope", ValueError("bad"), 3.0))
    assert entry == {"raw_message": "{nope", "parse_error": "bad", "original_priority": 3.0}


def test_a_failed_requeue_says_what_failed_first():
    assert requeue_failure_error("ValueError: boom", ConnectionError("down")) == (
        "Requeue failed after processing error 'ValueError: boom': down"
    )


# --- leases ---

def test_leases_live_in_a_set_named_after_the_queue():
    assert lease_key("chatbot_queue") == "chatbot_queue:inflight"


def test_a_lease_runs_out_its_length_after_it_was_taken():
    assert lease_expiry(1000.0, 900) == 1900.0


def test_a_lease_is_stale_from_the_moment_it_runs_out():
    assert not lease_is_stale(1900.0, 1899.9)
    assert lease_is_stale(1900.0, 1900.0)
    assert lease_is_stale(1900.0, 5000.0)


def test_a_lease_entry_round_trips_the_exact_message_and_its_score():
    raw = json.dumps(build_envelope({"file_id": 7, "name": "Ünï.pdf"}, new_metadata("q", "op-1")))
    score = queue_score(100.0, 1_750_000_000_000_000, PADDING)
    assert parse_lease_entry(lease_entry(raw, score, "n1")) == (raw, score)
    assert parse_lease_entry(lease_entry(raw, 100, "n1")) == (raw, 100.0)


def test_two_takes_of_the_same_message_make_different_lease_entries():
    raw = json.dumps(build_envelope({"file_id": 7}, new_metadata("q", "op-1")))
    assert lease_entry(raw, 5.0, "take-1") != lease_entry(raw, 5.0, "take-2")
    assert lease_entry(raw, 5.0, "take-1") == lease_entry(raw, 5.0, "take-1")


@pytest.mark.parametrize("entry", [
    "not json", json.dumps([1]), json.dumps({"message": "m"}), json.dumps({"priority": 1}),
    json.dumps({"message": 5, "priority": 1}), json.dumps({"message": "m", "priority": "high"}),
    json.dumps({"message": "m", "score": True}), json.dumps({"message": "m", "priority": 1}),
])
def test_a_lease_entry_that_is_not_one_is_rejected(entry):
    with pytest.raises(ValueError):
        parse_lease_entry(entry)


def test_an_expired_lease_is_recorded_as_such_with_its_length():
    error = lease_expired_error(900)
    assert error.startswith("LeaseExpired:") and "900s" in error


def test_a_task_taken_back_from_a_lease_follows_the_failure_accounting():
    """The reaper hands an expired task to failure_action: retried until the limit, then dead-lettered."""
    assert failure_action(True, 0, 3) == RETRY
    assert failure_action(True, 3, 3) == DEAD_LETTER
    assert failure_action(False, 0, 3) == DROP


def test_the_reaper_is_due_at_once_and_then_once_per_interval():
    assert reap_due(100.0, None, 60)
    assert not reap_due(159.9, 100.0, 60)
    assert reap_due(160.0, 100.0, 60)


def test_a_task_gets_the_lease_less_a_margin_of_a_tenth_up_to_a_minute():
    assert task_timeout(900) == 840  # the margin is capped at 60s
    assert task_timeout(600) == 540  # a tenth is exactly 60s here
    assert task_timeout(100) == 90  # below 600s the margin is a tenth
    assert task_timeout(30) == 27


def test_a_task_always_times_out_before_its_lease_does():
    for lease in (1, 30, 100, 900, 86_400):
        assert 0 < task_timeout(lease) < lease


def test_an_abandoned_task_is_recorded_as_such_with_its_time_limit():
    error = task_timeout_error(840)
    assert error.startswith("TaskTimeout:") and "840s" in error
