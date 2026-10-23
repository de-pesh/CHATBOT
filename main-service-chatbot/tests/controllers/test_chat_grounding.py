"""check_grounding: best-effort, never allowed to affect the chat turn it is rating.

The database and the model are both stubbed; nothing here reaches a real one.
"""
import json

import pytest
from app.config import Settings
from app.controllers.chat_grounding import GROUNDING_CHECK_ACTOR, check_grounding
from app.services.db.stored_procedures import StoredProcedures


class FakeDB:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def call_procedure(self, name, params=None):
        if self.fail:
            raise RuntimeError("db is down")
        self.calls.append((name, params))
        return []

    def called(self, name):
        return [p for n, p in self.calls if n == name]


class FakeLLM:
    """uses_helper/invoke_helper/invoke_llm - the surface app.controllers.chat_steps.helper_json calls."""

    def __init__(self, reply=None, uses_helper=True, fail=False):
        self.reply = reply
        self._uses_helper = uses_helper
        self.fail = fail
        self.calls = []

    def uses_helper(self, task):
        return self._uses_helper

    async def invoke_helper(self, messages, task):
        self.calls.append(("helper", task, messages))
        if self.fail:
            raise RuntimeError("model is down")
        return self.reply

    async def invoke_llm(self, messages):
        self.calls.append(("main", None, messages))
        if self.fail:
            raise RuntimeError("model is down")
        return self.reply


def _settings(**overrides):
    return Settings(
        _env_file=None, REDIS_URL="redis://localhost:6379/0", CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="x", SECRET_KEY="x", **overrides,
    )


PASSAGES = {1: "the policy requires annual review"}
GROUNDED_REPLY = json.dumps({"verdict": "grounded", "detail": ""})
PARTIAL_REPLY = json.dumps({"verdict": "partial", "detail": "citation 1 does not mention the deadline"})


@pytest.mark.asyncio
async def test_off_by_default_makes_no_call_at_all():
    db, llm = FakeDB(), FakeLLM(reply=GROUNDED_REPLY)

    await check_grounding(db, _settings(), llm, 9, "The answer [1].", PASSAGES)

    assert not llm.calls
    assert not db.calls


@pytest.mark.asyncio
async def test_enabled_with_no_passages_is_skipped():
    """A diverted turn (greeting, kb overview) never searched, so there is nothing to check it
    against - see chat_controller.process_chat_stream."""
    db, llm = FakeDB(), FakeLLM(reply=GROUNDED_REPLY)

    await check_grounding(db, _settings(GROUNDING_CHECK_ENABLED=True), llm, 9, "Hi there!", {})

    assert not llm.calls
    assert not db.calls


@pytest.mark.asyncio
async def test_enabled_with_no_history_id_is_skipped():
    db, llm = FakeDB(), FakeLLM(reply=GROUNDED_REPLY)

    await check_grounding(db, _settings(GROUNDING_CHECK_ENABLED=True), llm, None, "The answer [1].", PASSAGES)

    assert not llm.calls
    assert not db.calls


@pytest.mark.asyncio
async def test_a_grounded_verdict_is_recorded():
    db, llm = FakeDB(), FakeLLM(reply=GROUNDED_REPLY)

    await check_grounding(db, _settings(GROUNDING_CHECK_ENABLED=True), llm, 9, "The answer [1].", PASSAGES)

    assert llm.calls[0][1] == "grounding"
    assert db.called(StoredProcedures.UPDATE_CHATBOT_HISTORY_GROUNDING) == [
        [9, "grounded", None, GROUNDING_CHECK_ACTOR],
    ]


@pytest.mark.asyncio
async def test_a_partial_verdict_carries_its_detail():
    db, llm = FakeDB(), FakeLLM(reply=PARTIAL_REPLY)

    await check_grounding(db, _settings(GROUNDING_CHECK_ENABLED=True), llm, 9, "The answer [1].", PASSAGES)

    assert db.called(StoredProcedures.UPDATE_CHATBOT_HISTORY_GROUNDING) == [
        [9, "partial", "citation 1 does not mention the deadline", GROUNDING_CHECK_ACTOR],
    ]


@pytest.mark.asyncio
async def test_an_unusable_reply_is_not_recorded():
    db, llm = FakeDB(), FakeLLM(reply=json.dumps({"verdict": "maybe"}))

    await check_grounding(db, _settings(GROUNDING_CHECK_ENABLED=True), llm, 9, "The answer [1].", PASSAGES)

    assert not db.calls


@pytest.mark.asyncio
async def test_unparseable_json_is_not_recorded():
    db, llm = FakeDB(), FakeLLM(reply="not json")

    await check_grounding(db, _settings(GROUNDING_CHECK_ENABLED=True), llm, 9, "The answer [1].", PASSAGES)

    assert not db.calls


@pytest.mark.asyncio
async def test_a_model_failure_never_raises():
    db, llm = FakeDB(), FakeLLM(fail=True, uses_helper=False)

    await check_grounding(db, _settings(GROUNDING_CHECK_ENABLED=True), llm, 9, "The answer [1].", PASSAGES)

    assert not db.calls


@pytest.mark.asyncio
async def test_a_database_failure_never_raises():
    db, llm = FakeDB(fail=True), FakeLLM(reply=GROUNDED_REPLY)

    await check_grounding(db, _settings(GROUNDING_CHECK_ENABLED=True), llm, 9, "The answer [1].", PASSAGES)
    # No assertion beyond "it did not raise" - db.calls is meaningless once call_procedure itself fails.
