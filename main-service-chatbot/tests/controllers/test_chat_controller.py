"""recent_questions: the one database read app/routers/chat.py still does for personalization
(display_name/preferences come straight off the CurrentUser get_current_user already fetched -
see app/logic/user_context.py), and that a failure there never reaches a chat turn."""
import pytest
from app.config import Settings
from app.controllers.chat_controller import recent_questions
from app.services.db.stored_procedures import StoredProcedures


class FakeDB:
    def __init__(self, fail=(), history=None):
        self.calls, self.fail, self.history = [], set(fail), history

    async def call_procedure(self, name, params=None):
        self.calls.append((name, params))
        if name in self.fail:
            raise RuntimeError(f"{name} down")
        if name == StoredProcedures.FILTER_CHATBOT_HISTORY:
            return self.history
        return []


def _settings(**overrides):
    base = dict(_env_file=None, REDIS_URL="redis://x", CHATBOT_QUEUE_NAME="q", DB_CONNECTION_STRING="x", SECRET_KEY="x")
    return Settings(**{**base, **overrides})


@pytest.mark.asyncio
async def test_off_by_default_and_costs_no_database_read():
    db = FakeDB()
    assert await recent_questions(db, _settings(), "a@b.c", 3) == []
    assert db.calls == []


@pytest.mark.asyncio
async def test_reads_the_callers_newest_prompts_when_enabled():
    db = FakeDB(history=[[{"prompt": "what is SAMA?"}, {"prompt": "and the IA regulation?"}], [{"total_records": 2}]])
    questions = await recent_questions(db, _settings(USER_CONTEXT_RECENT_QUESTIONS=2), "a@b.c", 3)
    assert questions == ["what is SAMA?", "and the IA regulation?"]
    # scoped to the account id as well as the email, so a namesake row elsewhere is not read
    assert db.calls == [(StoredProcedures.FILTER_CHATBOT_HISTORY, [None, None, None, None, None, "a@b.c", True, 1, 2, 3])]


@pytest.mark.asyncio
async def test_a_failed_read_is_an_empty_list_not_an_error():
    db = FakeDB(fail={StoredProcedures.FILTER_CHATBOT_HISTORY})
    assert await recent_questions(db, _settings(USER_CONTEXT_RECENT_QUESTIONS=3), "a@b.c", 3) == []
