"""ChatLLM.invoke_llm: the fallback model answers when the main one fails, and only then."""
import asyncio
from types import SimpleNamespace

import pytest
from app.services.llm.chat_llm import ChatLLM
from fastapi import HTTPException


class Model:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    async def ainvoke(self, messages):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.reply)


MESSAGES = [{"role": "user", "content": "hi"}]


def test_the_fallback_answers_when_the_main_model_fails():
    main, fallback = Model(error=RuntimeError("down")), Model("from fallback")
    assert asyncio.run(ChatLLM(main, fallback_model=fallback).invoke_llm(MESSAGES)) == "from fallback"
    assert (main.calls, fallback.calls) == (1, 1)


def test_the_fallback_is_not_touched_when_the_main_model_works():
    main, fallback = Model("from main"), Model("from fallback")
    assert asyncio.run(ChatLLM(main, fallback_model=fallback).invoke_llm(MESSAGES)) == "from main"
    assert fallback.calls == 0


def test_without_a_fallback_the_failure_propagates():
    with pytest.raises(HTTPException):
        asyncio.run(ChatLLM(Model(error=RuntimeError("down"))).invoke_llm(MESSAGES))
