"""The queue's wire format: the envelope a task travels in, its sort score, and Redis URLs made safe to log."""
import json
import uuid
from typing import Any
from urllib.parse import urlparse

MISSING_OPERATION_ID = "MISSING_ID_IN_METADATA"

# Scales the priority above any send timestamp in microseconds (about 1.7e15 today), so priority
# always dominates and the timestamp only orders one priority. Changing it reorders what is queued.
SCORE_PADDING = 35_000_000_000_000

# What to do with a task whose processing failed.
RETRY, DEAD_LETTER, DROP = "retry", "dead_letter", "drop"


def url_has_embedded_password(url: str) -> bool:
    """True if `url` carries a non-empty password in its userinfo section."""
    return bool(urlparse(url).password)


def masked_url(url: str) -> str:
    """Redacts any embedded credentials from a Redis URL for safe logging."""
    if "@" not in url:
        return url
    url_parts = url.split('@')
    proto_user = url_parts[0].split(':')
    return f"{proto_user[0]}:...@{url_parts[-1]}"


def new_metadata(queue_name: str, operation_id: str | None = None) -> dict:
    """The retry/dead-letter bookkeeping a task starts with; a fresh operation id is minted when none is given."""
    return {
        "operation_id": operation_id or str(uuid.uuid4()),
        "retry_count": 0,
        "original_queue": queue_name,
        "last_error": None,
    }


def build_envelope(payload: dict[str, Any] | None, metadata: dict) -> dict:
    """The shape a task travels in, and what `parse_envelope` reads back."""
    return {"original_payload": payload, "metadata": metadata}


def parse_json(stringified_json: str) -> dict | None:
    """The parsed JSON, or None if it is not valid JSON."""
    try:
        return json.loads(stringified_json)
    except json.JSONDecodeError:
        return None


def operation_id_of(metadata: dict) -> str:
    return metadata.get("operation_id", MISSING_OPERATION_ID)


def parse_envelope(message: str) -> tuple[dict | None, dict]:
    """(payload, metadata) of a received message; the payload is None for a null one.

    Raises ValueError (or TypeError, for a message that is not text at all) when
    the message is not a well-formed envelope.
    """
    wrapped = parse_json(message)
    if not isinstance(wrapped, dict) or "original_payload" not in wrapped \
            or not isinstance(wrapped.get("metadata"), dict):
        raise ValueError("Invalid wrapped message structure received.")
    payload, metadata = wrapped["original_payload"], wrapped["metadata"]
    if payload is not None and not isinstance(payload, dict):
        raise ValueError(
            f"Invalid message format: 'original_payload' is not a dictionary or null for task {operation_id_of(metadata)}"
        )
    return payload, metadata


def queue_score(priority: float, timestamp_micros: int, padding: int) -> float:
    """The sorted-set score: priority dominates, and the send time keeps one priority first-in-first-out.

    Redis pops the lowest score first, so a lower priority number is consumed sooner.
    """
    return priority * padding + timestamp_micros


def retry_count_of(metadata: dict) -> int:
    return metadata.get("retry_count", 0)


def failure_action(has_dead_letter_queue: bool, retry_count: int, max_retries: int) -> str:
    """RETRY while attempts remain, then DEAD_LETTER; DROP when there is no dead-letter queue to keep it in."""
    if not has_dead_letter_queue:
        return DROP
    return RETRY if retry_count < max_retries else DEAD_LETTER


def error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def retry_envelope(payload: dict, metadata: dict, error: str) -> str:
    """The message to put back on the queue for another attempt: the same payload, one more retry counted."""
    retried = {**metadata, "retry_count": retry_count_of(metadata) + 1, "last_error": error}
    return json.dumps(build_envelope(payload, retried))


def dead_letter_envelope(payload: dict, metadata: dict, error: str) -> str:
    """The message to keep in the dead-letter queue: the same payload and retry count, with the last error."""
    return json.dumps(build_envelope(payload, {**metadata, "last_error": error}))


def unparseable_entry(raw_message, parse_error: BaseException, priority: float) -> str:
    """What the dead-letter queue keeps of a message that is not a well-formed envelope."""
    return json.dumps({"raw_message": str(raw_message), "parse_error": str(parse_error), "original_priority": priority})


def requeue_failure_error(last_error: str, requeue_error: BaseException) -> str:
    return f"Requeue failed after processing error '{last_error}': {requeue_error}"


# --- Leases: how a task taken off the queue stays recoverable while a worker holds it ---
#
# The queue pops a task off the sorted set, so a worker that dies mid-task takes it with it.
# While a worker holds a task it therefore also keeps a lease on it: an entry in a second sorted
# set, scored by the time the lease runs out. A lease still there after that time belongs to a
# worker that died, and a reaper puts its task back through the retry accounting.

def lease_key(queue_name: str) -> str:
    """The sorted set holding the leases on `queue_name`'s in-flight tasks."""
    return f"{queue_name}:inflight"


def lease_expiry(now: float, lease_seconds: float) -> float:
    """When a lease taken at `now` (epoch seconds) runs out."""
    return now + lease_seconds


def lease_is_stale(expiry: float, now: float) -> bool:
    """True once a lease has run out: its holder is presumed dead and the task may be taken back."""
    return expiry <= now


def lease_entry(raw_message: str, score: float, nonce: str) -> str:
    """The lease set's member for one take of a task: the message exactly as popped, the sorted-set
    score it was popped with (so it goes back where it was), and a `nonce` unique to this take.

    The nonce matters: a task handed back and popped again by another replica has the same message
    and score, and without it the two takes would share one member - the first worker's release
    would then delete the second worker's lease.
    """
    return json.dumps({"score": score, "message": raw_message, "nonce": nonce})


def parse_lease_entry(entry: str) -> tuple[str, float]:
    """(message, score) of a lease set member. Raises ValueError when it is not one `lease_entry` made."""
    wrapped = parse_json(entry)
    if not isinstance(wrapped, dict) or not isinstance(wrapped.get("message"), str)             or isinstance(wrapped.get("score"), bool) or not isinstance(wrapped.get("score"), (int, float)):
        raise ValueError("Invalid lease entry structure.")
    return wrapped["message"], float(wrapped["score"])


def task_timeout(lease_seconds: float) -> float:
    """How long a worker lets one task run: the lease less a margin (a tenth of it, at most 60s).

    The worker must give up on a task before its lease can run out, or another worker's reaper
    would take a task this one is still running; the margin is what the failure handling
    (the retry delay, the re-queue) needs to finish inside the lease.
    """
    return lease_seconds - min(60.0, lease_seconds / 10)


def task_timeout_error(seconds: float) -> str:
    """The error recorded on a task the worker gave up on."""
    return f"TaskTimeout: the task did not finish within {seconds:g}s and was abandoned"


def lease_expired_error(lease_seconds: float) -> str:
    """The error recorded on a task taken back from a lease that ran out."""
    return (
        f"LeaseExpired: the worker holding this task did not finish it within {lease_seconds:g}s "
        "(it crashed, was killed, or stalled)"
    )


def reap_due(now: float, last_reaped: float | None, interval_seconds: float) -> bool:
    """True when it is time to look for expired leases again: at once when none has been looked for yet."""
    return last_reaped is None or now - last_reaped >= interval_seconds
