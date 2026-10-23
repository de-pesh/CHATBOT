"""The /chat endpoints over HTTP, with the pipeline, database and storage stubbed.

Pins the wire contract the browser depends on: the SSE event sequence and
payloads of POST /chat/stream, the error mapping of POST /chat, and the shape
and scoping of the history and session listings.
"""
import json
from datetime import datetime

import pytest
from app.config import Settings, get_settings
from app.dependencies import (
    get_current_user,
    get_db_service,
    get_file_storage_service,
    get_kb_catalog,
)
from app.main import app
from app.models.auth import CurrentUser
from app.models.chat import ChatResult
from app.routers import chat as chat_router
from app.services.db.stored_procedures import StoredProcedures
from app.services.llm.chat_llm import get_chat_llm
from app.services.vector.search_runtime import get_search_runtime
from fastapi import HTTPException
from fastapi.testclient import TestClient

NOW = datetime(2026, 1, 1).isoformat()


class FakeDB:
    def __init__(self, sessions=None, history=None, feedback=None):
        self.calls = []
        self.sessions = sessions if sessions is not None else [{"id": 5}]
        self.history = history
        self.feedback = feedback if feedback is not None else []

    async def call_procedure(self, name, params=None):
        self.calls.append((name, params))
        if name == StoredProcedures.FILTER_CHATBOT_SESSIONS:
            return [self.sessions, [{"total_records": len(self.sessions)}]]
        if name == StoredProcedures.UPSERT_CHATBOT_SESSION:
            return [[{"id": 5}]]
        if name == StoredProcedures.UPSERT_CHATBOT_HISTORY:
            return [[{"id": 9}]]
        if name == StoredProcedures.FILTER_CHATBOT_HISTORY:
            return self.history
        if name == StoredProcedures.UPDATE_USER_PROFILE:
            return [[USER_ROW]]
        if name == StoredProcedures.FILTER_CHATBOT_FEEDBACK:
            return [self.feedback]
        if name == StoredProcedures.UPSERT_CHATBOT_FEEDBACK:
            return [[{"id": self.feedback[0]["id"] if self.feedback else 21}]]
        return []

    def called(self, name):
        return [p for n, p in self.calls if n == name]


@pytest.fixture
def client(monkeypatch):
    """DISABLE_AUTH=True: every call is the shared dev identity, so owner_id is always None -
    see real_auth_client for the tests that need a real, distinct account."""
    settings = Settings(
        _env_file=None, REDIS_URL="redis://localhost:6379/0", CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="x", SECRET_KEY="x", DISABLE_AUTH=True,
    )
    db = FakeDB()
    app.dependency_overrides.update({
        get_settings: lambda: settings,
        get_db_service: lambda: db,
        get_file_storage_service: lambda: None,
        get_kb_catalog: lambda: None,
        get_chat_llm: lambda: object(),
        get_search_runtime: lambda: object(),
    })

    async def no_prefetch(sources, storage, db_service):
        return {}

    async def plain_linkify(response, sources, storage, db_service, settings, prefetched=None):
        return response + " (linked)"

    monkeypatch.setattr(chat_router, "prefetch_citation_urls", no_prefetch)
    monkeypatch.setattr(chat_router, "linkify_citations", plain_linkify)
    test_client = TestClient(app)
    test_client.db = db
    yield test_client
    app.dependency_overrides.clear()


# The row app.dependencies.get_current_user would build from dbo.users - id 3, display_name and
# preferences already on it (no separate "chat user" table or upsert to fetch them from any more).
CALLER = CurrentUser(
    id=3, first_name="Dee", last_name="P", email="dee@x.com", hashed_password="", role="user",
    display_name="Deepesh", preferences={"role": "Compliance officer"},
    created_on=NOW, created_by="system",
)
USER_ROW = {
    "id": 3, "first_name": "Dee", "last_name": "P", "email": "dee@x.com", "hashed_password": "",
    "role": "user", "display_name": "Deepesh", "preferences": '{"role": "Compliance officer"}',
    "is_active": True, "created_by": "system", "created_on": NOW, "updated_by": None, "updated_on": None,
}


@pytest.fixture
def real_auth_client(client):
    """Same as `client`, but a real, distinct logged-in caller (DISABLE_AUTH=False) instead of the
    shared dev identity - for the tests that check a row is actually linked/scoped by account id."""
    settings = Settings(
        _env_file=None, REDIS_URL="redis://localhost:6379/0", CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="x", SECRET_KEY="x", DISABLE_AUTH=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: CALLER
    return client


def _events(response):
    """[(event name, parsed data)] of an SSE body."""
    out = []
    for frame in response.text.strip().split("\n\n"):
        lines = frame.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ")
        out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


def _pipeline(events, fail=None):
    async def fake(settings, chat_history, query, chat_llm, search=None, catalog=None, local_hour=None, user_context=None):
        for event in events:
            yield event
        if fail:
            raise fail
    return fake


RESULT = ChatResult(response="The answer [1].", collections=["c1", "c2"], sources=[{"n": 1, "file_name": "a.pdf", "page": 1}],
                    passages={1: "some passage text"}, timings={"rephrase_ms": 3})
PIPELINE_EVENTS = [
    {"type": "stage", "stage": "rephrasing"},
    {"type": "stage", "stage": "generating"},
    {"type": "sources", "sources": RESULT.sources},
    {"type": "delta", "text": "The "},
    {"type": "delta", "text": "answer [1]."},
    {"type": "result", "result": RESULT},
]
PAYLOAD = {"chat": [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"},
                    {"role": "user", "content": "what is X?"}], "local_hour": 9}


# ------------------------------------------------------------------ POST /chat/stream

def test_the_stream_sends_meta_then_progress_then_done(client, monkeypatch):
    monkeypatch.setattr(chat_router, "process_chat_stream", _pipeline(PIPELINE_EVENTS))

    response = client.post("/chat/stream", json=PAYLOAD)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    events = _events(response)
    assert [e[0] for e in events] == ["meta", "stage", "stage", "delta", "delta", "done"]
    assert events[0][1] == {"conversation_id": 5}
    assert events[1][1] == {"stage": "rephrasing"}
    assert events[3][1] == {"text": "The "}
    done = events[-1][1]
    assert done["response"] == "The answer [1]. (linked)"
    assert done["conversation_id"] == 5
    assert done["history_id"] == 9
    assert done["collections"] == ["c1", "c2"]
    assert done["timings"]["rephrase_ms"] == 3
    assert {"prep_ms", "linkify_ms", "total_ms"} <= set(done["timings"])
    assert "save_ms" not in done["timings"], "the save happens after the answer is sent"
    assert client.db.called(StoredProcedures.UPSERT_CHATBOT_SESSION)[0] == [None, "what is X?", True, "dev@local.dev", None]
    assert client.db.called(StoredProcedures.UPSERT_CHATBOT_HISTORY)[0][1:3] == [5, "what is X?"]


def test_the_stream_reports_a_failure_as_an_error_event_and_logs_it(client, monkeypatch):
    monkeypatch.setattr(chat_router, "process_chat_stream", _pipeline(PIPELINE_EVENTS[:4], fail=RuntimeError("model down")))

    events = _events(client.post("/chat/stream", json=PAYLOAD))

    assert [e[0] for e in events] == ["meta", "stage", "stage", "delta", "error"]
    assert events[-1][1] == {"detail": "Internal server error: model down", "conversation_id": 5}
    error_rows = [p for p in client.db.called(StoredProcedures.UPSERT_CHATBOT_HISTORY) if p[5] == "model down"]
    assert error_rows and error_rows[0][:2] == [9, 5]


def test_the_stream_passes_an_http_error_detail_through(client, monkeypatch):
    monkeypatch.setattr(chat_router, "process_chat_stream", _pipeline([], fail=HTTPException(503, "busy")))

    events = _events(client.post("/chat/stream", json=PAYLOAD))

    assert events[-1] == ("error", {"detail": "busy", "conversation_id": 5})


def test_the_stream_with_no_result_is_an_error(client, monkeypatch):
    monkeypatch.setattr(chat_router, "process_chat_stream", _pipeline(PIPELINE_EVENTS[:2]))

    events = _events(client.post("/chat/stream", json=PAYLOAD))

    assert events[-1][0] == "error" and "no result" in events[-1][1]["detail"]


def test_a_conversation_the_caller_does_not_own_is_a_404_before_streaming(client, monkeypatch):
    client.db.sessions = []
    monkeypatch.setattr(chat_router, "process_chat_stream", _pipeline(PIPELINE_EVENTS))

    response = client.post("/chat/stream", json={**PAYLOAD, "conversation_id": 77})

    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation 77 not found."


# ------------------------------------------------------------------ POST /chat

def _blocking(result=RESULT, fail=None):
    async def fake(settings, chat_history, query, chat_llm, search=None, catalog=None, local_hour=None, user_context=None):
        if fail:
            raise fail
        return result
    return fake


def test_a_chat_reply_carries_the_linked_answer_and_timings(client, monkeypatch):
    monkeypatch.setattr(chat_router, "process_chat", _blocking())

    response = client.post("/chat", json=PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "The answer [1]. (linked)" and body["conversation_id"] == 5
    assert body["history_id"] == 9
    assert {"prep_ms", "rephrase_ms", "linkify_ms", "save_ms", "total_ms"} <= set(body["timings"])
    saved = client.db.called(StoredProcedures.UPSERT_CHATBOT_HISTORY)[-1]
    assert saved[:2] == [9, 5] and saved[3] == "The answer [1]. (linked)" and saved[-1] == "c1, c2"


@pytest.mark.parametrize("error,status,detail", [
    (RuntimeError("boom"), 500, "Internal server error: boom"),
    (ValueError("bad"), 422, "Value error during processing: bad"),
    (HTTPException(418, "teapot"), 418, "teapot"),
])
def test_a_chat_failure_maps_to_the_matching_http_error_and_is_logged(client, monkeypatch, error, status, detail):
    monkeypatch.setattr(chat_router, "process_chat", _blocking(fail=error))

    response = client.post("/chat", json=PAYLOAD)

    assert response.status_code == status and response.json()["detail"] == detail
    assert [p for p in client.db.called(StoredProcedures.UPSERT_CHATBOT_HISTORY) if p[5] == str(error)]


# ------------------------------------------------------------------ listings

def _history_row(i):
    return {"id": i, "conversation_id": 5, "prompt": "p", "response": "r", "error": None,
            "is_active": True, "created_on": NOW, "created_by": "dev@local.dev",
            "updated_on": None, "updated_by": None, "rag_collections": None}


def test_a_grounding_verdict_is_included_in_the_history_row(client):
    row = {**_history_row(1), "grounding_verdict": "partial", "grounding_detail": "citation 1 is unrelated"}
    client.db.history = [[row], [{"total_records": 1}]]

    body = client.get("/chat/history").json()

    assert body["data"][0]["grounding_verdict"] == "partial"
    assert body["data"][0]["grounding_detail"] == "citation 1 is unrelated"


def test_a_history_row_with_no_grounding_check_yet_has_none(client):
    client.db.history = [[_history_row(1)], [{"total_records": 1}]]

    body = client.get("/chat/history").json()

    assert body["data"][0]["grounding_verdict"] is None
    assert body["data"][0]["grounding_detail"] is None


def test_history_is_scoped_to_the_caller_and_paginated(client):
    client.db.history = [[_history_row(1), _history_row(2)], [{"total_records": 12}]]

    response = client.get("/chat/history", params={"conversation_id": 5, "page_number": 2, "page_size": 2})

    assert response.status_code == 200
    body = response.json()
    assert (body["total_records"], body["page_number"], body["page_size"], len(body["data"])) == (12, 2, 2, 2)
    assert client.db.called(StoredProcedures.FILTER_CHATBOT_HISTORY)[0] == [None, 5, None, None, None, "dev@local.dev", None, 2, 2, None]


def test_history_without_a_totals_set_is_a_500(client):
    client.db.history = [[_history_row(1)]]

    assert client.get("/chat/history").status_code == 500


def test_history_with_no_totals_row_reports_zero(client):
    client.db.history = [[], []]

    assert client.get("/chat/history").json()["total_records"] == 0


@pytest.mark.parametrize("query", ["page_size=0", "page_size=101", "page_number=0"])
def test_paging_limits_are_enforced(client, query):
    assert client.get(f"/chat/history?{query}").status_code == 422
    assert client.get(f"/chat/session?{query}").status_code == 422


def test_sessions_are_listed_for_the_caller_only(client):
    client.db.sessions = [{"id": 5, "name": "n", "is_active": True, "created_on": NOW, "created_by": "dev@local.dev",
                           "updated_on": None, "updated_by": None}]

    response = client.get("/chat/session")

    assert response.json()["total_records"] == 1 and response.json()["page_size"] == 10
    assert client.db.called(StoredProcedures.FILTER_CHATBOT_SESSIONS)[0] == [None, None, "dev@local.dev", True, 1, 10, None]


def test_renaming_and_deleting_a_session_need_ownership(client):
    client.db.sessions = []
    assert client.patch("/chat/session/5", json={"name": "x"}).status_code == 404
    assert client.delete("/chat/session/5").status_code == 404

    client.db.sessions = [{"id": 5}]
    assert client.patch("/chat/session/5", json={"name": "x"}).json() == {"message": "Session 5 updated successfully."}
    assert client.delete("/chat/session/5").status_code == 204
    assert client.db.called(StoredProcedures.UPSERT_CHATBOT_SESSION)[-1] == [5, "x", None, "dev@local.dev", None]
    assert client.db.called(StoredProcedures.DELETE_CHATBOT_SESSION)[-1] == [5, "dev@local.dev"]


# ------------------------------------------------------------------ personalization (app/logic/user_context.py)
#
# display_name and preferences come straight off the CurrentUser get_current_user already
# fetched - no extra database read needed for them any more. recent_questions is off by
# default (Settings.USER_CONTEXT_RECENT_QUESTIONS = 0), so the tests below that don't set it
# make no FILTER_CHATBOT_HISTORY call at all for personalization.

def _capturing(seen):
    async def fake(settings, chat_history, query, chat_llm, search=None, catalog=None, local_hour=None, user_context=None):
        seen.append(user_context)
        yield {"type": "result", "result": RESULT}
    return fake


@pytest.mark.parametrize("path", ["/chat/stream", "/chat"])
def test_a_chat_turn_tells_the_pipeline_who_is_asking_with_no_extra_database_read(real_auth_client, monkeypatch, path):
    seen = []
    if path == "/chat":
        async def blocking(**kwargs):
            seen.append(kwargs["user_context"])
            return RESULT
        monkeypatch.setattr(chat_router, "process_chat", blocking)
    else:
        monkeypatch.setattr(chat_router, "process_chat_stream", _capturing(seen))

    assert real_auth_client.post(path, json=PAYLOAD).status_code == 200

    assert seen == [{"name": "Deepesh", "preferences": {"role": "Compliance officer"}, "recent_questions": []}]
    assert not real_auth_client.db.called(StoredProcedures.FILTER_CHATBOT_HISTORY)


def test_the_dev_identity_has_no_profile_to_tell_the_pipeline_about(client, monkeypatch):
    seen = []
    monkeypatch.setattr(chat_router, "process_chat_stream", _capturing(seen))

    assert client.post("/chat/stream", json=PAYLOAD).status_code == 200

    assert seen == [None]


def test_recent_questions_are_included_when_enabled(real_auth_client, monkeypatch):
    settings = Settings(
        _env_file=None, REDIS_URL="redis://localhost:6379/0", CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="x", SECRET_KEY="x", DISABLE_AUTH=False, USER_CONTEXT_RECENT_QUESTIONS=2,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    real_auth_client.db.history = [[{"prompt": "what is SAMA?"}, {"prompt": "and the IA regulation?"}], [{"total_records": 2}]]
    seen = []
    monkeypatch.setattr(chat_router, "process_chat_stream", _capturing(seen))

    assert real_auth_client.post("/chat/stream", json=PAYLOAD).status_code == 200

    assert seen == [{"name": "Deepesh", "preferences": {"role": "Compliance officer"},
                     "recent_questions": ["what is SAMA?", "and the IA regulation?"]}]
    assert real_auth_client.db.called(StoredProcedures.FILTER_CHATBOT_HISTORY)[0] == [
        None, None, None, None, None, "dee@x.com", True, 1, 2, 3,
    ]


# ------------------------------------------------------------------ GET / PATCH /chat/me

def test_my_profile_is_read(real_auth_client):
    response = real_auth_client.get("/chat/me")

    assert response.status_code == 200
    body = response.json()
    assert body == {"id": 3, "email": "dee@x.com", "display_name": "Deepesh", "preferences": {"role": "Compliance officer"}}
    assert "hashed_password" not in body and "role" not in body


def test_my_profile_is_a_503_for_the_dev_identity(client):
    assert client.get("/chat/me").status_code == 503
    assert client.patch("/chat/me", json={"display_name": "x"}).status_code == 503


def test_updating_my_profile_saves_only_what_was_sent(real_auth_client):
    response = real_auth_client.patch("/chat/me", json={"display_name": "Dee", "preferences": {"role": "Auditor", "n": 3}})

    assert response.status_code == 200
    assert real_auth_client.db.called(StoredProcedures.UPDATE_USER_PROFILE) == [
        ["dee@x.com", "Dee", '{"role": "Auditor", "n": "3"}'],
    ]
    real_auth_client.patch("/chat/me", json={"display_name": ""})
    assert real_auth_client.db.called(StoredProcedures.UPDATE_USER_PROFILE)[-1] == ["dee@x.com", "", None]


@pytest.mark.parametrize("body", [
    {},
    {"display_name": None},
    {"display_name": "x" * 256},
    {"preferences": {f"k{i}": "v" for i in range(21)}},
    {"preferences": {"k": "v" * 201}},
    {"preferences": {"k" * 51: "v"}},
    {"preferences": {"nested": {"a": 1}}},
])
def test_a_profile_update_that_says_nothing_or_is_too_big_is_refused(real_auth_client, body):
    assert real_auth_client.patch("/chat/me", json=body).status_code == 422
    assert not real_auth_client.db.called(StoredProcedures.UPDATE_USER_PROFILE)


def test_an_update_that_fails_is_a_500_without_the_reason(real_auth_client):
    original = real_auth_client.db.call_procedure

    async def failing(name, params=None):
        if name == StoredProcedures.UPDATE_USER_PROFILE:
            raise RuntimeError("secret sql detail")
        return await original(name, params)

    real_auth_client.db.call_procedure = failing

    response = real_auth_client.patch("/chat/me", json={"display_name": "Dee"})

    assert response.status_code == 500
    assert "secret" not in response.text


# ------------------------------------------------------------------ conversations are linked to the caller's account

def test_a_new_conversation_and_its_prompt_are_linked_to_the_account(real_auth_client, monkeypatch):
    monkeypatch.setattr(chat_router, "process_chat_stream", _pipeline(PIPELINE_EVENTS))

    assert real_auth_client.post("/chat/stream", json=PAYLOAD).status_code == 200

    assert real_auth_client.db.called(StoredProcedures.UPSERT_CHATBOT_SESSION)[0] == [None, "what is X?", True, "dee@x.com", 3]
    prompt_insert = real_auth_client.db.called(StoredProcedures.UPSERT_CHATBOT_HISTORY)[0]
    assert prompt_insert[1:3] == [5, "what is X?"] and len(prompt_insert) == 11 and prompt_insert[-1] == 3


def test_the_blocking_chat_links_them_too(real_auth_client, monkeypatch):
    async def blocking(**kwargs):
        return RESULT

    monkeypatch.setattr(chat_router, "process_chat", blocking)

    assert real_auth_client.post("/chat", json=PAYLOAD).status_code == 200
    assert real_auth_client.db.called(StoredProcedures.UPSERT_CHATBOT_SESSION)[0][-1] == 3
    assert real_auth_client.db.called(StoredProcedures.UPSERT_CHATBOT_HISTORY)[0][-1] == 3


def test_an_existing_conversation_is_looked_up_as_the_accounts_own(real_auth_client, monkeypatch):
    monkeypatch.setattr(chat_router, "process_chat_stream", _pipeline(PIPELINE_EVENTS))

    real_auth_client.post("/chat/stream", json={**PAYLOAD, "conversation_id": 5})

    assert real_auth_client.db.called(StoredProcedures.FILTER_CHATBOT_SESSIONS)[0] == [5, None, "dee@x.com", True, 1, 1, 3]


def test_listings_renames_and_deletes_are_scoped_to_the_account(real_auth_client):
    real_auth_client.db.history = [[_history_row(1)], [{"total_records": 1}]]

    real_auth_client.get("/chat/history")
    real_auth_client.get("/chat/session")
    real_auth_client.patch("/chat/session/5", json={"name": "x"})
    real_auth_client.delete("/chat/session/5")

    assert real_auth_client.db.called(StoredProcedures.FILTER_CHATBOT_HISTORY)[0][-1] == 3
    listed, owned_for_rename, owned_for_delete = real_auth_client.db.called(StoredProcedures.FILTER_CHATBOT_SESSIONS)
    assert listed[-1] == owned_for_rename[-1] == owned_for_delete[-1] == 3
    assert real_auth_client.db.called(StoredProcedures.UPSERT_CHATBOT_SESSION)[-1] == [5, "x", None, "dee@x.com", 3]


# ------------------------------------------------------------------ POST /chat/history/{id}/feedback

def test_feedback_is_recorded_for_an_owned_turn(client):
    client.db.history = [[_history_row(9)], [{"total_records": 1}]]

    response = client.post("/chat/history/9/feedback", json={"rating": "positive"})

    assert response.status_code == 200
    assert response.json() == {"message": "Feedback recorded."}
    assert client.db.called(StoredProcedures.UPSERT_CHATBOT_FEEDBACK)[0] == [None, 9, "positive", None, "dev@local.dev", None]


def test_a_negative_rating_can_carry_a_comment(client):
    client.db.history = [[_history_row(9)], [{"total_records": 1}]]

    response = client.post("/chat/history/9/feedback", json={"rating": "negative", "comment": "wrong page cited"})

    assert response.status_code == 200
    assert client.db.called(StoredProcedures.UPSERT_CHATBOT_FEEDBACK)[0] == [
        None, 9, "negative", "wrong page cited", "dev@local.dev", None,
    ]


def test_resubmitting_feedback_updates_the_existing_row_instead_of_adding_one(client):
    client.db.history = [[_history_row(9)], [{"total_records": 1}]]
    client.db.feedback = [{
        "id": 41, "history_id": 9, "rating": "negative", "comment": "old",
        "created_by": "dev@local.dev", "created_on": NOW, "updated_by": None, "updated_on": None,
    }]

    response = client.post("/chat/history/9/feedback", json={"rating": "positive"})

    assert response.status_code == 200
    assert client.db.called(StoredProcedures.UPSERT_CHATBOT_FEEDBACK)[0] == [41, 9, "positive", None, "dev@local.dev", None]


def test_feedback_on_a_turn_the_caller_does_not_own_is_a_404(client):
    client.db.history = [[], [{"total_records": 0}]]

    response = client.post("/chat/history/9/feedback", json={"rating": "positive"})

    assert response.status_code == 404
    assert not client.db.called(StoredProcedures.UPSERT_CHATBOT_FEEDBACK)


def test_an_invalid_rating_is_a_422(client):
    client.db.history = [[_history_row(9)], [{"total_records": 1}]]

    assert client.post("/chat/history/9/feedback", json={"rating": "meh"}).status_code == 422
    assert not client.db.called(StoredProcedures.UPSERT_CHATBOT_FEEDBACK)


def test_feedback_is_scoped_to_the_account(real_auth_client):
    real_auth_client.db.history = [[_history_row(9)], [{"total_records": 1}]]

    real_auth_client.post("/chat/history/9/feedback", json={"rating": "positive"})

    assert real_auth_client.db.called(StoredProcedures.FILTER_CHATBOT_HISTORY)[0] == [
        9, None, None, None, None, "dee@x.com", None, 1, 1, 3,
    ]
    assert real_auth_client.db.called(StoredProcedures.UPSERT_CHATBOT_FEEDBACK)[0] == [None, 9, "positive", None, "dee@x.com", 3]


# ------------------------------------------------------------------ the grounding check is scheduled after the answer

def test_the_stream_schedules_grounding_with_the_raw_answer_and_passages(client, monkeypatch):
    """result.response (with its [n] markers), not the linkified answer - the grounding check
    maps a claim back to its passage by that number (app/logic/grounding.py)."""
    monkeypatch.setattr(chat_router, "process_chat_stream", _pipeline(PIPELINE_EVENTS))
    seen = []

    async def fake_check(db_service, settings, chat_llm, history_id, response, passages):
        seen.append((history_id, response, passages))

    monkeypatch.setattr(chat_router, "check_grounding", fake_check)

    response = client.post("/chat/stream", json=PAYLOAD)

    assert response.status_code == 200
    assert seen == [(9, "The answer [1].", {1: "some passage text"})]


def test_the_blocking_chat_schedules_grounding_too(client, monkeypatch):
    monkeypatch.setattr(chat_router, "process_chat", _blocking())
    seen = []

    async def fake_check(db_service, settings, chat_llm, history_id, response, passages):
        seen.append((history_id, response, passages))

    monkeypatch.setattr(chat_router, "check_grounding", fake_check)

    response = client.post("/chat", json=PAYLOAD)

    assert response.status_code == 200
    assert seen == [(9, "The answer [1].", {1: "some passage text"})]
