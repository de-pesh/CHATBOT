"""With KB_OVERVIEW_ENABLED off, an intent reply that names a document never reaches the user."""
import json

import pytest
from app.config import Settings
from app.controllers.chat_steps import ask_intent
from app.logic.catalog import names_a_document

REFUSAL = "Please rephrase your question."
NAMES = "SAMA Cyber Framework; UAE IA Regulation"


def _settings(overview):
    return Settings(
        _env_file=None, REDIS_URL="redis://x", CHATBOT_QUEUE_NAME="q", DB_CONNECTION_STRING="x", SECRET_KEY="x",
        KB_OVERVIEW_ENABLED=overview, NO_OVERVIEW_REPLY=REFUSAL,
    )


class _Model:
    def __init__(self, intent, reply):
        self._raw = json.dumps({"intent": intent, "reply": reply})

    async def invoke_helper(self, messages, name):
        return self._raw


def test_names_a_document_is_case_insensitive_and_ignores_short_or_empty_names():
    assert names_a_document("We hold the sama cyber framework.", NAMES, 3)
    assert not names_a_document("I can answer questions from my documents.", NAMES, 3)
    assert not names_a_document("anything", "", 3)
    assert not names_a_document("ab", "ab", 3)


@pytest.mark.asyncio
async def test_overview_off_replaces_a_reply_thatnames_a_document():
    leaked = "I have the SAMA Cyber Framework and the UAE IA Regulation."
    result = await ask_intent(_Model("kb_overview", leaked), _settings(False), [], "what do you have?", "", NAMES, None)
    assert result == ("kb_overview", REFUSAL)


@pytest.mark.asyncio
async def test_overview_off_keeps_a_reply_that_names_nothing():
    reply = "Hello! Ask me anything about my documents."
    result = await ask_intent(_Model("chitchat", reply), _settings(False), [], "hi", "", NAMES, None)
    assert result == ("chitchat", reply)


@pytest.mark.asyncio
async def test_overview_on_is_not_filtered():
    reply = "I have the SAMA Cyber Framework."
    result = await ask_intent(_Model("kb_overview", reply), _settings(True), [], "what do you have?", "cat", NAMES, None)
    assert result == ("kb_overview", reply)
