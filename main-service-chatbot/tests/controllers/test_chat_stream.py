"""The chat pipeline end to end, with the model and the search stubbed.

Pins what the browser depends on: the order of the progress events, what each
carries, which steps are skipped and when, and what happens when a step fails.
Nothing here reaches a model or the compute service.
"""
import asyncio
import json

import pytest
from app.config import Settings
from app.controllers import chat_steps
from app.controllers.chat_controller import process_chat, process_chat_stream
from fastapi import HTTPException

# Where the pipeline looks up its search function; the fake is installed there.
SEARCH_OWNER = chat_steps

REWRITE_EN = json.dumps({
    "language": "English", "language_code": "en",
    "questions": [
        {"query": "what is X", "type": "original"},
        {"query": "define X", "type": "alternate"},
    ],
})
REWRITE_FR = json.dumps({
    "language": "French", "language_code": "fr",
    "questions": [{"query": "qu'est-ce que X", "type": "original"}],
})
REWRITE_AR = json.dumps({
    "language": "Arabic", "language_code": "ar",
    "questions": [{"query": "ما هي ساما؟", "type": "original"}],
})
ID_A = "aaaaaaaa-0000-0000-0000-000000000000"
ID_B = "bbbbbbbb-0000-0000-0000-000000000000"
ID_C = "cccccccc-0000-0000-0000-000000000000"
ENGLISH_ANSWER = "X is a thing according to the policy [1]."


def _hit(chunk_id, score, name="Policy.pdf", text="some passage text"):
    return {"id": chunk_id, "score": score, "metadata": {"file_name": name, "textContent": text, "page_number": 2.0}}


class FakeLLM:
    """Stands in for ChatLLM: scripted replies per helper task, a queue for the main model."""

    def __init__(self, helper=None, main=(), tokens=(), helper_tasks=None, delays=None, stream_error=None):
        self.helper = dict(helper or {})
        self.main = list(main)
        self.tokens = list(tokens)
        self.helper_tasks = set(helper_tasks or ())
        self.delays = delays or {}
        self.stream_error = stream_error
        self.calls = []

    def uses_helper(self, task):
        return task in self.helper_tasks

    async def invoke_helper(self, messages, task):
        self.calls.append(("helper", task, messages))
        await asyncio.sleep(self.delays.get(task, 0))
        reply = self.helper[task]
        if isinstance(reply, list):  # one reply per call, in order
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def invoke_llm(self, messages):
        self.calls.append(("main", None, messages))
        reply = self.main.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def stream_llm(self, messages):
        self.calls.append(("stream", None, messages))
        for token in self.tokens:
            yield token
        if self.stream_error:
            raise self.stream_error

    def called(self, kind, task=None):
        return [c for c in self.calls if c[0] == kind and (task is None or c[1] == task)]


def _settings(**overrides):
    base = dict(
        _env_file=None, REDIS_URL="redis://localhost:6379/0", CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="x", SECRET_KEY="x", INTENT_ROUTING_ENABLED=False,
        # These tests pin the pipeline itself, including a turn that finds nothing and is still answered by
        # the model; the evidence gate has its own tests at the end of this file.
        NO_EVIDENCE_GATE_ENABLED=False,
    )
    return Settings(**{**base, **overrides})


@pytest.fixture
def search(monkeypatch):
    """Replaces the in-process search; `search.hits[query]` is what that query returns (or raises)."""
    class Search:
        hits = {}
        queries = []
        scopes = []

    async def fake(runtime, collection_name, search_query, limit, version_scope=None):
        Search.queries.append((collection_name, search_query, limit))
        Search.scopes.append(version_scope)
        result = Search.hits.get(search_query, [])
        if isinstance(result, Exception):
            raise result
        return result

    Search.hits, Search.queries, Search.scopes = {}, [], []
    monkeypatch.setattr(SEARCH_OWNER, "fetch_context", fake)
    return Search


async def _run(llm, settings=None, history=(), query="what is X", catalog=None, local_hour=None, user_context=None, search_runtime=None):
    events = []
    async for event in process_chat_stream(
        settings or _settings(), list(history), query, llm, search_runtime, catalog=catalog, local_hour=local_hour,
        user_context=user_context,
    ):
        events.append(event)
    return events


def _shape(events):
    """Each event as a short comparable tuple."""
    out = []
    for e in events:
        if e["type"] == "stage":
            out.append(("stage", e["stage"]))
        elif e["type"] == "delta":
            out.append(("delta", e["text"]))
        else:
            out.append((e["type"],))
    return out


def _result(events):
    assert events[-1]["type"] == "result"
    assert sum(e["type"] == "result" for e in events) == 1, "result is emitted exactly once, last"
    return events[-1]["result"]


def _sources(events):
    return events[_shape(events).index(("sources",))]["sources"]


def _answer_prompt(llm):
    return llm.called("stream")[0][2][-1]["content"]


# ------------------------------------------------------------------ the full pipeline

@pytest.mark.asyncio
async def test_an_english_question_runs_every_stage_and_streams_the_answer(search):
    search.hits = {"what is X": [_hit(ID_A, 0.9), _hit(ID_B, 0.5)], "define X": [_hit(ID_A, 0.7), _hit(ID_C, 0.6)]}
    llm = FakeLLM(
        helper={"rewrite": REWRITE_EN, "rerank": json.dumps({"ranked_ids": [ID_C, ID_A]})},
        tokens=["X is ", "a thing [1]."],
    )

    events = await _run(llm)

    assert _shape(events) == [
        ("stage", "rephrasing"), ("stage", "retrieving"), ("stage", "reranking"),
        ("sources",), ("stage", "generating"), ("delta", "X is "), ("delta", "a thing [1]."), ("result",),
    ]
    assert [q[1] for q in search.queries] == ["what is X", "define X"]
    assert search.queries[0] == ("chatbot_policies", "what is X", 10)
    sources = _sources(events)
    assert [(s["n"], s["file_name"], s["page"]) for s in sources] == [(1, "Policy.pdf", 2), (2, "Policy.pdf", 2)]
    result = _result(events)
    assert result.response == "X is a thing [1]."
    assert result.collections == ["chatbot_policies"]
    assert result.sources == sources
    assert result.intent is None
    assert {"rephrase_ms", "retrieve_ms", "rerank_ms", "first_token_ms", "generate_ms", "pipeline_ms"} <= set(result.timings)
    assert "translate_ms" not in result.timings, "an English answer skips the translation"


@pytest.mark.asyncio
async def test_history_is_stripped_of_citation_links_before_the_model_sees_it(search):
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["ok"])
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "It is so [Policy, Pg: 2]."},
    ]

    await _run(llm, history=history)

    stream_messages = llm.called("stream")[0][2]
    assert stream_messages[0] == {"role": "user", "content": "hello"}
    assert stream_messages[1] == {"role": "assistant", "content": "It is so."}


@pytest.mark.asyncio
async def test_the_query_may_be_a_dict_with_content(search):
    llm = FakeLLM(helper={"rewrite": "not json", "rerank": "{}"}, tokens=["ok"])

    events = await _run(llm, query={"role": "user", "content": "what is X"})

    assert search.queries[0][1] == "what is X", "an unusable rewrite falls back to the query as typed"
    assert _result(events).response == "ok"


@pytest.mark.asyncio
async def test_process_chat_returns_only_the_result(search):
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["done"])

    result = await process_chat(_settings(), [], "what is X", llm, None)

    assert result.response == "done"


# ------------------------------------------------------------------ retrieval and rerank

@pytest.mark.asyncio
async def test_a_failed_search_does_not_stop_the_others(search):
    search.hits = {"what is X": RuntimeError("compute down"), "define X": [_hit(ID_A, 0.9)]}
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": json.dumps({"ranked_ids": [ID_A]})}, tokens=["ok"])

    events = await _run(llm)

    assert len(_sources(events)) == 1


@pytest.mark.asyncio
async def test_when_every_search_fails_the_turn_fails_instead_of_answering_from_no_passages(search):
    # An answer made from nothing reads as "your question is off-topic" - a false refusal for an outage.
    search.hits = {"what is X": RuntimeError("down"), "define X": RuntimeError("down")}
    llm = FakeLLM(helper={"rewrite": REWRITE_EN}, tokens=["I do not know."])

    with pytest.raises(HTTPException) as failure:
        await _run(llm)

    assert failure.value.status_code == 503 and "search is unavailable" in failure.value.detail
    assert not llm.called("stream") and not llm.called("helper", "rerank")


@pytest.mark.asyncio
async def test_searches_that_return_nothing_are_not_failures_and_still_get_an_answer(search):
    search.hits = {"what is X": [], "define X": []}  # the store answered: there is simply nothing
    llm = FakeLLM(helper={"rewrite": REWRITE_EN}, tokens=["I do not know."])

    events = await _run(llm)

    assert _sources(events) == [] and _result(events).response == "I do not know."


@pytest.mark.asyncio
async def test_a_duplicate_hit_is_kept_once_and_the_reply_orders_them(search):
    search.hits = {
        "what is X": [_hit(ID_A, 0.9, text="from a"), _hit(ID_B, 0.5, text="from b")],
        "define X": [_hit(ID_A, 0.8, text="from a")],
    }
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": json.dumps({"ranked_ids": [ID_B, ID_A]})}, tokens=["ok"])

    events = await _run(llm)

    prompt = _answer_prompt(llm)
    assert prompt.index("from b") < prompt.index("from a")
    assert prompt.count("from a") == 1
    assert len(_sources(events)) == 2


@pytest.mark.asyncio
async def test_a_failed_rerank_falls_back_to_score_order_and_top_k(search):
    hits = [_hit(f"{i:08d}-0000-0000-0000-000000000000", 1.0 - i / 100, text=f"passage {i}") for i in range(12)]
    search.hits = {"what is X": hits}
    llm = FakeLLM(helper={"rewrite": json.dumps({"questions": ["what is X"]}), "rerank": RuntimeError("boom")},
                  main=[RuntimeError("main boom")], tokens=["ok"])

    events = await _run(llm, _settings(RERANK_TOP_K=3))

    assert [s["n"] for s in _sources(events)] == [1, 2, 3]
    prompt = _answer_prompt(llm)
    assert "passage 0" in prompt and "passage 2" in prompt and "passage 3" not in prompt


@pytest.mark.asyncio
async def test_rerank_is_offered_only_the_best_candidates(search):
    hits = [_hit(f"{i:08d}-0000-0000-0000-000000000000", i / 100, text=f"passage {i}") for i in range(30)]
    search.hits = {"what is X": hits}
    llm = FakeLLM(helper={"rewrite": json.dumps({"questions": ["what is X"]}), "rerank": json.dumps({"ranked_ids": []})},
                  helper_tasks=set(), tokens=["ok"])

    await _run(llm, _settings(RERANK_MAX_CANDIDATES=5))

    rerank_prompt = llm.called("helper", "rerank")[0][2][0]["content"]
    assert rerank_prompt.count("- id: ") == 5
    assert "passage 29" in rerank_prompt and "passage 24" not in rerank_prompt


@pytest.mark.asyncio
async def test_searches_of_the_rewritten_queries_are_fused_by_rank(search):
    # C tops the alternate's search and A the original's, but B is second in both - the chunk the
    # whole question points at - so it is offered first, though no search ranked it first.
    search.hits = {
        "what is X": [_hit(ID_A, 0.09, text="passage a"), _hit(ID_B, 0.08, text="passage b")],
        "define X": [_hit(ID_C, 0.09, text="passage c"), _hit(ID_B, 0.08, text="passage b")],
    }
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["ok"])

    events = await _run(llm)

    assert [s["file_name"] for s in _sources(events)] == ["Policy.pdf"] * 3
    prompt = _answer_prompt(llm)
    assert prompt.index("passage b") < prompt.index("passage a") < prompt.index("passage c")


# ------------------------------------------------------------------ helper model escalation

@pytest.mark.asyncio
async def test_a_helper_that_fails_is_redone_on_the_main_model(search):
    llm = FakeLLM(helper={"rewrite": RuntimeError("helper down"), "rerank": "{}"}, main=[REWRITE_EN], tokens=["ok"],
                  helper_tasks={"rewrite"})

    await _run(llm)

    assert [q[1] for q in search.queries] == ["what is X", "define X"]
    assert llm.calls[1][0] == "main"


@pytest.mark.asyncio
async def test_an_unusable_helper_result_is_redone_on_the_main_model(search):
    llm = FakeLLM(helper={"rewrite": json.dumps({"language": "English"}), "rerank": "{}"}, main=[REWRITE_EN],
                  tokens=["ok"], helper_tasks={"rewrite"})

    await _run(llm)

    assert [q[1] for q in search.queries] == ["what is X", "define X"]


# ------------------------------------------------------------------ bare "hi"/"hey"/"hello" - no model call at all

@pytest.mark.asyncio
async def test_a_bare_greeting_is_answered_with_no_model_or_search_call(search):
    """The single most common message this bot gets works even if every LLM call would fail -
    there is none to fail. See app/logic/chat_turn.is_simple_greeting."""
    llm = FakeLLM()  # no scripted replies at all: any call would raise KeyError

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query="hey", local_hour=9)

    assert _shape(events) == [("delta", "Hi! I can help answer questions from your documents. What would you like to know?"), ("result",)]
    result = _result(events)
    assert (result.collections, result.sources, result.intent) == ([], [], "chitchat")
    assert llm.calls == []
    assert search.queries == []


@pytest.mark.asyncio
async def test_a_bare_greeting_uses_the_callers_name(search):
    llm = FakeLLM()

    events = await _run(
        llm, _settings(INTENT_ROUTING_ENABLED=True), query="hi", local_hour=9,
        user_context={"name": "Deepesh", "preferences": {}, "recent_questions": []},
    )

    assert _result(events).response.startswith("Hi, Deepesh!")


@pytest.mark.asyncio
async def test_a_bare_greeting_is_not_shortcut_when_intent_routing_is_off(search):
    """INTENT_ROUTING_ENABLED gates this the same way it gates the intent-classification call -
    off means every message, this one included, goes to the full pipeline."""
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["answer"])

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=False), query="hi")

    assert _result(events).response == "answer"
    assert llm.called("helper", "rewrite")


@pytest.mark.asyncio
async def test_a_greeting_with_a_real_question_is_not_a_bare_greeting(search):
    """"hi, what is X" is a question with a greeting in front of it - full pipeline, not the
    bare-greeting shortcut (see chat_turn.is_simple_greeting)."""
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}", "intent": json.dumps({"intent": "knowledge", "reply": ""})},
                  tokens=["answer"])

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query="hi, what is X")

    assert _result(events).response == "answer"


# ------------------------------------------------------------------ intent routing

@pytest.mark.asyncio
async def test_small_talk_is_answered_without_searching(search):
    chitchat = json.dumps({"intent": "chitchat", "reply": "Hello there!"})
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "intent": chitchat}, delays={"rewrite": 0.05})

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query="thanks")

    assert _shape(events) == [("stage", "rephrasing"), ("delta", "Hello there!"), ("result",)]
    result = _result(events)
    assert (result.response, result.collections, result.sources, result.intent) == ("Hello there!", [], [], "chitchat")
    assert {"intent_ms", "pipeline_ms"} <= set(result.timings)
    assert search.queries == []


@pytest.mark.asyncio
async def test_an_overview_reply_that_names_a_document_is_replaced_when_the_overview_is_off(search):
    leak = json.dumps({"intent": "kb_overview", "reply": "I hold the SAMA Framework."})

    async def catalog():
        return "cat", "SAMA Framework"

    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "intent": leak}, delays={"rewrite": 0.05})

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query="what do you have", catalog=catalog())

    assert _result(events).response == Settings.model_fields["NO_OVERVIEW_REPLY"].default


@pytest.mark.asyncio
async def test_a_slow_intent_check_still_gates_generation(search):
    search.hits = {"what is X": [_hit(ID_A, 0.9)]}
    chitchat = json.dumps({"intent": "chitchat", "reply": "Hi!"})
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": json.dumps({"ranked_ids": [ID_A]}), "intent": chitchat},
                  delays={"intent": 0.1}, tokens=["should not be generated"])

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query="thanks")

    assert _shape(events) == [
        ("stage", "rephrasing"), ("stage", "retrieving"), ("stage", "reranking"), ("delta", "Hi!"), ("result",),
    ]
    assert not llm.called("stream")
    result = _result(events)
    assert "intent_wait_ms" in result.timings and result.intent == "chitchat"


@pytest.mark.asyncio
async def test_a_knowledge_verdict_carries_on_with_the_pipeline(search):
    search.hits = {"what is X": [_hit(ID_A, 0.9)]}
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}",
                          "intent": json.dumps({"intent": "knowledge", "reply": ""})},
                  delays={"intent": 0.02}, tokens=["answer"])

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query="what is X")

    result = _result(events)
    assert result.response == "answer" and result.intent is None
    assert {"intent_ms", "intent_wait_ms"} <= set(result.timings)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    RuntimeError("down"), "not json",
    json.dumps({"intent": "weird", "reply": "x"}), json.dumps({"intent": "chitchat", "reply": ""}),
])
async def test_every_doubt_about_the_intent_means_the_full_pipeline(search, reply):
    search.hits = {"what is X": [_hit(ID_A, 0.9)]}
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}", "intent": reply}, tokens=["answer"])

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query="thanks")

    assert _result(events).response == "answer"


@pytest.mark.asyncio
async def test_a_greeting_does_not_wait_for_a_search_that_started_before_the_intent_check_finished(search, monkeypatch):
    # The rewrite is quick, so the search is under way when the intent check says "small talk".
    # The greeting must go out then, not after the (here: very slow) search.
    async def slow_search(runtime, collection_name, search_query, limit, version_scope=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(SEARCH_OWNER, "fetch_context", slow_search)
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "intent": json.dumps({"intent": "chitchat", "reply": "Hi there!"})},
                  delays={"intent": 0.1})

    events = await asyncio.wait_for(_run(llm, _settings(INTENT_ROUTING_ENABLED=True), query="thanks"), timeout=5)

    assert _shape(events) == [("stage", "rephrasing"), ("stage", "retrieving"), ("delta", "Hi there!"), ("result",)]
    assert _result(events).intent == "chitchat"
    assert not llm.called("stream")


ARABIC_GREETING = "مرحبا بك"
ARABIC_HELLO_REPLY = "مرحبا، صباح الخير! يمكنني الإجابة عن أسئلتك من المستندات."


def _chitchat(reply):
    return json.dumps({"intent": "chitchat", "language": "x", "reply": reply})


@pytest.mark.asyncio
async def test_a_greeting_in_the_wrong_language_is_asked_for_again_once(search):
    llm = FakeLLM(helper={"rewrite": REWRITE_AR, "intent": [_chitchat("Hello! How can I help you today?"),
                                                            _chitchat(ARABIC_HELLO_REPLY)]},
                  delays={"rewrite": 0.05})

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query=ARABIC_GREETING)

    assert _result(events).response == ARABIC_HELLO_REPLY
    first, second = (call[2][-1]["content"] for call in llm.called("helper", "intent"))
    assert "IMPORTANT" not in first and "not in the language of the latest user message" in second
    assert search.queries == []


@pytest.mark.asyncio
async def test_a_greeting_in_the_wrong_language_twice_goes_to_the_full_pipeline(search):
    search.hits = {"ما هي ساما؟": [_hit(ID_A, 0.9)]}
    english = _chitchat("Hello! How can I help you today?")
    arabic = "ساما هي البنك المركزي في المملكة العربية السعودية."
    llm = FakeLLM(helper={"rewrite": REWRITE_AR, "rerank": "{}", "intent": [english, english]}, tokens=[arabic])

    events = await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query=ARABIC_GREETING)

    result = _result(events)
    assert result.response == arabic and result.intent is None
    assert len(llm.called("helper", "intent")) == 2


@pytest.mark.asyncio
async def test_a_greeting_in_the_right_language_is_not_asked_for_twice(search):
    llm = FakeLLM(helper={"rewrite": REWRITE_AR, "intent": [_chitchat(ARABIC_HELLO_REPLY)]},
                  delays={"rewrite": 0.05})

    await _run(llm, _settings(INTENT_ROUTING_ENABLED=True), query=ARABIC_GREETING)

    assert len(llm.called("helper", "intent")) == 1


@pytest.mark.asyncio
async def test_a_message_longer_than_the_word_limit_is_never_offered_to_the_intent_step(search):
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["answer"])

    await _run(llm, _settings(INTENT_ROUTING_ENABLED=True, INTENT_MAX_WORDS=3), query="one two three four five")

    assert not llm.called("helper", "intent")


@pytest.mark.asyncio
async def test_a_failing_catalog_read_does_not_fail_the_turn(search):
    async def catalog():
        raise RuntimeError("storage down")

    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["answer"])

    events = await _run(llm, _settings(KB_OVERVIEW_ENABLED=True), catalog=catalog())

    assert _result(events).response == "answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("overview", [True, False])
async def test_the_catalog_reaches_the_rewrite_only_with_the_overview_on(search, overview):
    async def catalog():
        return "CATALOG-TEXT", "Doc One"

    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["answer"])

    await _run(llm, _settings(KB_OVERVIEW_ENABLED=overview), catalog=catalog())

    rewrite_prompt = llm.called("helper", "rewrite")[0][2][0]["content"]
    assert ("CATALOG-TEXT" in rewrite_prompt) is overview


# ------------------------------------------------------------------ language handling

@pytest.mark.asyncio
async def test_an_answer_in_the_wrong_script_is_regenerated_once(search):
    search.hits = {"ما هي ساما؟": [_hit(ID_A, 0.9)]}
    arabic = "ساما هي البنك المركزي في المملكة العربية السعودية."
    llm = FakeLLM(helper={"rewrite": REWRITE_AR, "rerank": "{}"}, main=[arabic],
                  tokens=["SAMA is the central bank of the kingdom."])

    events = await _run(llm, query="ما هي ساما؟")

    assert _result(events).response == arabic
    assert ("stage", "finalising") not in _shape(events), "already in the query's script: no translation"
    reminder = llm.called("main")[0][2][-1]["content"]
    assert reminder.endswith("\n\nIMPORTANT: write the entire answer in Arabic. Do not use any other language.")


@pytest.mark.asyncio
async def test_a_wrong_script_retry_that_is_also_wrong_keeps_the_streamed_answer(search):
    search.hits = {"ما هي ساما؟": [_hit(ID_A, 0.9)]}
    streamed = "SAMA is the central bank of the kingdom."
    llm = FakeLLM(helper={"rewrite": REWRITE_AR, "rerank": "{}", "translate": json.dumps({"final_answer": "x"})},
                  main=["Still English words here, definitely."], tokens=[streamed])

    events = await _run(llm, query="ما هي ساما؟")

    assert _result(events).response == streamed


@pytest.mark.asyncio
async def test_a_translated_answer_replaces_the_streamed_one(search):
    search.hits = {"qu'est-ce que X": [_hit(ID_A, 0.9)]}
    translated = json.dumps({"final_answer": "X est une chose selon la politique [1]."})
    llm = FakeLLM(helper={"rewrite": REWRITE_FR, "rerank": "{}", "translate": translated}, tokens=[ENGLISH_ANSWER])

    events = await _run(llm, query="qu'est-ce que X")

    assert _shape(events)[-3:] == [("delta", ENGLISH_ANSWER), ("stage", "finalising"), ("result",)]
    result = _result(events)
    assert result.response == "X est une chose selon la politique [1]."
    assert "translate_ms" in result.timings


@pytest.mark.asyncio
async def test_an_unusable_translation_is_retried_on_the_main_model_then_the_answer_stands(search):
    search.hits = {"qu'est-ce que X": [_hit(ID_A, 0.9)]}
    llm = FakeLLM(helper={"rewrite": REWRITE_FR, "rerank": "{}", "translate": json.dumps({"final_answer": "no"})},
                  main=[json.dumps({"final_answer": "also no"}), json.dumps({"final_answer": "still no"})],
                  helper_tasks={"translate"}, tokens=[ENGLISH_ANSWER])

    events = await _run(llm, query="qu'est-ce que X")

    assert _result(events).response == ENGLISH_ANSWER
    calls = llm.called("main")
    assert len(calls) == 2, "one redo on the main model after the helper, one with the rule spelled out"
    assert "IMPORTANT: reply with the JSON object and nothing else." in calls[1][2][-1]["content"]


@pytest.mark.asyncio
async def test_a_failed_translation_step_keeps_the_answer(search):
    search.hits = {"qu'est-ce que X": [_hit(ID_A, 0.9)]}
    llm = FakeLLM(helper={"rewrite": REWRITE_FR, "rerank": "{}", "translate": RuntimeError("down")},
                  main=[RuntimeError("down too"), RuntimeError("down too")], helper_tasks={"translate"},
                  tokens=[ENGLISH_ANSWER])

    events = await _run(llm, query="qu'est-ce que X")

    assert _result(events).response == ENGLISH_ANSWER


@pytest.mark.asyncio
async def test_an_english_query_never_calls_the_translate_step(search):
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["answer"])

    await _run(llm)

    assert not llm.called("helper", "translate")


# ------------------------------------------------------------------ failure

@pytest.mark.asyncio
async def test_a_generation_failure_propagates_after_the_events_already_sent(search):
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}"}, tokens=["partial"], stream_error=RuntimeError("stream died"))
    seen = []

    with pytest.raises(RuntimeError, match="stream died"):
        async for event in process_chat_stream(_settings(), [], "what is X", llm, None):
            seen.append(event)

    assert ("delta", "partial") in _shape(seen)


@pytest.mark.asyncio
async def test_hanging_up_mid_turn_cancels_the_background_checks(search):
    llm = FakeLLM(helper={"rewrite": REWRITE_EN, "rerank": "{}", "intent": json.dumps({"intent": "knowledge"})},
                  delays={"intent": 0.3}, tokens=["a", "b"])
    stream = process_chat_stream(_settings(INTENT_ROUTING_ENABLED=True), [], "hi", llm, None)
    await stream.__anext__()
    await stream.aclose()
    await asyncio.sleep(0)
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]


# ------------------------------------------------------------------ the evidence gate

@pytest.mark.asyncio
async def test_a_turn_with_no_hits_is_answered_by_the_gate_without_calling_the_model(search):
    search.hits = {"what is X": [], "define X": []}
    llm = FakeLLM(helper={"rewrite": REWRITE_EN}, tokens=["should never be written"])
    settings = _settings(NO_EVIDENCE_GATE_ENABLED=True, NO_EVIDENCE_REPLY="Nothing found.")

    events = await _run(llm, settings)

    result = _result(events)
    assert result.response == "Nothing found." and result.no_evidence is True
    assert llm.called("stream") == [] and llm.called("main") == []
    assert [e["text"] for e in events if e["type"] == "delta"] == ["Nothing found."]
    assert result.trace["queries"] == ["what is X", "define X"]


@pytest.mark.asyncio
async def test_hits_below_min_relevance_score_reach_the_gate(search):
    search.hits = {"what is X": [_hit(ID_A, 0.2)], "define X": [_hit(ID_B, 0.1)]}
    llm = FakeLLM(helper={"rewrite": REWRITE_EN}, tokens=["should never be written"])
    settings = _settings(NO_EVIDENCE_GATE_ENABLED=True, MIN_RELEVANCE_SCORE=0.5)

    result = _result(await _run(llm, settings))

    assert result.no_evidence is True
    assert result.trace["retrieved"] == [] and result.trace["top_score"] == 0.2  # how close it came is still traced


@pytest.mark.asyncio
async def test_the_gate_lets_a_turn_with_usable_hits_through_and_traces_it(search):
    search.hits = {"what is X": [_hit(ID_A, 0.9)], "define X": [_hit(ID_A, 0.8)]}
    llm = FakeLLM(helper={"rewrite": REWRITE_EN}, tokens=[ENGLISH_ANSWER])

    result = _result(await _run(llm, _settings(NO_EVIDENCE_GATE_ENABLED=True, MIN_RELEVANCE_SCORE=0.5)))

    assert result.no_evidence is False and result.response == ENGLISH_ANSWER
    assert [h["id"] for h in result.trace["reranked"]] == [ID_A]


# ------------------------------------------------------------------ document versions

@pytest.mark.asyncio
async def test_a_comparison_question_searches_every_version_and_tells_the_model_to_keep_them_apart(search):
    older = _hit(ID_A, 0.9)
    older["metadata"].update(version_no=2, is_latest=False)
    search.hits = {"compare X with the previous version": [older]}
    rewrite = json.dumps({"language": "English", "language_code": "en", "questions": [
        {"query": "compare X with the previous version", "type": "original"}]})
    llm = FakeLLM(helper={"rewrite": rewrite}, tokens=[ENGLISH_ANSWER])

    events = await _run(llm, query="Compare X with the previous version")

    result = _result(events)
    assert search.scopes and all(scope.mode.value == "all" for scope in search.scopes)
    assert result.trace["version_scope"] == "all"
    assert "never merge two versions" in _answer_prompt(llm) and "Version: v2 (older version)" in _answer_prompt(llm)
    assert _sources(events)[0]["version"] == "v2"


@pytest.mark.asyncio
async def test_an_ordinary_question_uses_the_latest_scope(search):
    search.hits = {"what is X": [_hit(ID_A, 0.9)], "define X": [_hit(ID_A, 0.8)]}
    llm = FakeLLM(helper={"rewrite": REWRITE_EN}, tokens=[ENGLISH_ANSWER])

    result = _result(await _run(llm))

    assert result.trace["version_scope"] == "latest" and all(scope.mode.value == "latest" for scope in search.scopes)
    assert "never merge two versions" not in _answer_prompt(llm)
