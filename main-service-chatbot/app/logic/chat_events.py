"""What a chat turn reports as it goes: the pipeline's progress events, and the payloads the router sends the browser."""
from app.models.chat import ChatResult

STAGE_REPHRASING = "rephrasing"
STAGE_RETRIEVING = "retrieving"
STAGE_RERANKING = "reranking"
STAGE_GENERATING = "generating"
STAGE_FINALISING = "finalising"


def stage_event(stage: str) -> dict:
    return {"type": "stage", "stage": stage}


def delta_event(text: str) -> dict:
    """One chunk of the answer as it is generated."""
    return {"type": "delta", "text": text}


def sources_event(sources: list) -> dict:
    """The numbered passages, announced before generation starts."""
    return {"type": "sources", "sources": sources}


def result_event(
    response: str, collections: list, sources: list, timings: dict, intent: str | None = None,
    passages: dict | None = None, trace: dict | None = None, no_evidence: bool = False,
) -> dict:
    """The turn's outcome, emitted exactly once, last."""
    return {
        "type": "result",
        "result": ChatResult(
            response=response, collections=collections, sources=sources, timings=timings, intent=intent,
            passages=passages or {}, trace=trace or {}, no_evidence=no_evidence,
        ),
    }


def diversion_events(intent: str, reply: str, timings: dict, trace: dict | None = None) -> list:
    """The events for a turn the intent step answered without searching."""
    return [delta_event(reply), result_event(reply, [], [], timings, intent, trace=trace)]


def no_evidence_events(reply: str, collections: list, timings: dict, trace: dict | None = None) -> list:
    """The events for a turn the evidence gate answered: the search ran and found nothing usable."""
    return [delta_event(reply), result_event(reply, collections, [], timings, trace=trace, no_evidence=True)]


# --- What the router sends over POST /chat/stream (see its description for the sequence) ---

def finish_timings(result_timings: dict, prep_ms: int, linkify_ms: int, save_ms: int | None, total_ms: int) -> dict:
    """The pipeline's per-stage timings plus what the router spent around it.

    `save_ms` is absent when the history save is done off to the side, after the
    answer is sent. `total_ms` is server time from the request arriving to the
    answer being complete.
    """
    return {
        "prep_ms": prep_ms,
        **result_timings,
        "linkify_ms": linkify_ms,
        **({"save_ms": save_ms} if save_ms is not None else {}),
        "total_ms": total_ms,
    }


def done_payload(
    response: str, conversation_id: int, history_id: int | None, collections: list, timings: dict,
    suggested_questions: list[str] | None = None,
) -> dict:
    payload: dict = {
        "response": response, "conversation_id": conversation_id, "history_id": history_id,
        "collections": collections, "timings": timings,
    }
    if suggested_questions:
        payload["suggested_questions"] = suggested_questions
    return payload


def error_payload(detail: str, conversation_id: int) -> dict:
    return {"detail": detail, "conversation_id": conversation_id}


def browser_event(event: dict) -> tuple[str, dict] | None:
    """(SSE event name, payload) for a pipeline event the browser is shown, None for the two only the server uses."""
    if event["type"] in ("stage", "delta"):
        return event["type"], {key: value for key, value in event.items() if key != "type"}
    return None
