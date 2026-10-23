"""The events a chat turn reports, and the payloads the router sends the browser."""
from app.logic.chat_events import (
    STAGE_GENERATING,
    browser_event,
    delta_event,
    diversion_events,
    done_payload,
    error_payload,
    finish_timings,
    result_event,
    sources_event,
    stage_event,
)


def test_events_have_the_shapes_the_router_reads():
    assert stage_event(STAGE_GENERATING) == {"type": "stage", "stage": "generating"}
    assert delta_event("hi") == {"type": "delta", "text": "hi"}
    assert sources_event([{"n": 1}]) == {"type": "sources", "sources": [{"n": 1}]}


def test_a_result_event_carries_the_chat_result():
    event = result_event("answer", ["c"], [{"n": 1}], {"pipeline_ms": 5})
    result = event["result"]
    assert event["type"] == "result"
    assert (result.response, result.collections, result.sources, result.timings, result.intent) == (
        "answer", ["c"], [{"n": 1}], {"pipeline_ms": 5}, None,
    )


def test_a_diverted_turn_is_the_reply_then_a_result_with_nothing_searched():
    delta, result = diversion_events("chitchat", "Hello!", {"intent_ms": 3})
    assert delta == {"type": "delta", "text": "Hello!"}
    assert result["type"] == "result"
    assert (result["result"].response, result["result"].collections, result["result"].sources, result["result"].intent) == (
        "Hello!", [], [], "chitchat",
    )


def test_only_progress_events_are_shown_to_the_browser():
    assert browser_event({"type": "stage", "stage": "reranking"}) == ("stage", {"stage": "reranking"})
    assert browser_event({"type": "delta", "text": "x"}) == ("delta", {"text": "x"})
    assert browser_event({"type": "sources", "sources": []}) is None
    assert browser_event({"type": "result", "result": None}) is None


def test_timings_put_the_router_steps_around_the_pipeline_ones():
    timings = finish_timings({"rephrase_ms": 3}, prep_ms=1, linkify_ms=2, save_ms=4, total_ms=10)
    assert list(timings) == ["prep_ms", "rephrase_ms", "linkify_ms", "save_ms", "total_ms"]


def test_the_save_time_is_absent_when_the_save_happens_after_the_answer():
    assert "save_ms" not in finish_timings({}, 1, 2, None, 10)


def test_the_done_and_error_payloads():
    assert done_payload("a", 5, 9, ["c"], {"total_ms": 1}) == {
        "response": "a", "conversation_id": 5, "history_id": 9, "collections": ["c"], "timings": {"total_ms": 1},
    }
    assert error_payload("boom", 5) == {"detail": "boom", "conversation_id": 5}
