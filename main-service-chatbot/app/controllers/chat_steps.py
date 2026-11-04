"""The steps of a chat turn that call out - the model, the search - each small enough to read at a glance.

`chat_controller.process_chat_stream` runs them in order. The decisions between
them (what to keep, what to skip, what to send) are pure and live in
`app/logic/chat_turn.py`, `chat_events.py` and `prompts.py`.
"""
import asyncio
import logging
import time
from collections.abc import Awaitable
from contextlib import contextmanager
from functools import partial

from app.config import Settings
from app.controllers.chat_context import fetch_context
from app.logic.chat_events import diversion_events, no_evidence_events, result_event
from app.logic.chat_turn import (
    history_for_model,
    intent_verdict,
    is_simple_greeting,
    needs_translation,
    query_text_of,
    rerank_shortlist,
    split_search_results,
    usable_rerank,
    usable_rewrite,
    wants_intent_check,
)
from app.logic.language import answered_in_query_script, wrong_script
from app.logic.prompts import (
    ENGLISH,
    INTENT_CHITCHAT,
    INTENT_LANGUAGE_REMINDER,
    TRANSLATE_RETRY_REMINDER,
    fetch_final_prompt,
    fetch_intent_prompt,
    fetch_new_query_prompt,
    fetch_rerank_prompt,
    format_translate_prompt,
    language_reminder,
    parse_new_queries,
    simple_greeting_reply,
)
from app.logic.replies import (
    is_usable_translation,
    parse_json_response,
    with_reminder,
)
from app.logic.rerank import balance_versions, dedupe_candidates, fuse_query_hits, reorder_by_ids, rerank_shortlist_balanced
from app.logic.trace import clipped, hits_summary, top_score_of
from app.logic.versions import LATEST, Mode, detect_scope
from app.services.llm.chat_llm import ChatLLM
from app.services.vector.search_runtime import SearchRuntime
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


class TurnTimer:
    """Milliseconds spent in each stage, reported on the result, so a slow turn can be traced to its stage."""

    def __init__(self):
        self.timings: dict = {}
        self.turn_start = self.stage_start = time.perf_counter()

    def lap(self, name: str) -> None:
        """Records the time since the last lap (or the start) and starts the next stage."""
        self.timings[name] = _ms(self.stage_start)
        self.stage_start = time.perf_counter()

    def since_turn(self, name: str) -> None:
        """Records the time since the turn began, for work that overlaps the stages."""
        self.timings[name] = _ms(self.turn_start)

    def since_stage(self, name: str) -> None:
        self.timings[name] = _ms(self.stage_start)

    @contextmanager
    def gate(self, name: str):
        """Times a wait, then restarts the stage clock so the wait is not counted in what follows."""
        start = time.perf_counter()
        yield
        self.timings[name] = _ms(start)
        self.stage_start = time.perf_counter()


async def helper_json(chat_llm: ChatLLM, messages: list, is_usable, task: str) -> dict | None:
    """JSON from one of the short helper calls (rewrite, rerank, translate).

    These run on the lighter helper model when there is one. If that model fails,
    or returns something `is_usable` rejects - unparseable JSON, a missing field,
    a translation that lost a citation - the call is redone once on the main
    model, so a lighter model can save time but never lowers the quality of the
    result. With no helper model configured the single call is made on the main one.

    `is_usable` guards every result, the main model's included: the main model can
    fail the same way the helper did - it is what put the translate step's schema
    placeholder on screen in place of an Arabic answer - and there is nothing left
    to escalate to, so None is returned and the caller falls back to what it had.
    """
    def checked(data: dict | None, who: str) -> dict | None:
        if data is not None and is_usable(data):
            return data
        logger.warning("%s gave an unusable %s result; discarding it.", who, task)
        return None

    if not chat_llm.uses_helper(task):
        return checked(parse_json_response(await chat_llm.invoke_helper(messages, task)), "Model")
    try:
        data = parse_json_response(await chat_llm.invoke_helper(messages, task))
        if data is not None and is_usable(data):
            return data
        logger.warning("Helper model gave an unusable result; redoing the call on the main model.")
    except Exception as e:
        logger.warning("Helper model call failed (%s); redoing it on the main model.", e)
    return checked(parse_json_response(await chat_llm.invoke_llm(messages)), "Main model")


def script_args_of(settings: Settings) -> dict:
    """The thresholds the script checks in app/logic/language.py take."""
    return {
        "query_min_letters": settings.LANGUAGE_QUERY_MIN_LETTERS,
        "answer_min_letters": settings.LANGUAGE_ANSWER_MIN_LETTERS,
        "script_share": settings.LANGUAGE_SCRIPT_SHARE,
    }


def day_starts_of(settings: Settings) -> tuple[int, int, int, int]:
    """(Settings.GREETING_MORNING/AFTERNOON/EVENING/NIGHT_START_HOUR) - what prompts.time_of_day takes."""
    return (
        settings.GREETING_MORNING_START_HOUR, settings.GREETING_AFTERNOON_START_HOUR,
        settings.GREETING_EVENING_START_HOUR, settings.GREETING_NIGHT_START_HOUR,
    )


async def ask_intent(
    chat_llm: ChatLLM, settings: Settings, chat_history: list, query_text: str,
    catalog_text: str, names: str, local_hour: int | None, user_context: dict | None = None,
) -> tuple | None:
    """(intent, reply) when the message needs no search, else None - see chat_turn.intent_verdict.
    A failed call counts as doubt: the full pipeline runs.

    The reply goes to the user as written, so it is held to the question's language like
    any answer: one in the wrong script is asked for once more with the language spelled
    out, and if that is wrong too the full pipeline answers instead, in the right language."""
    overview = settings.KB_OVERVIEW_ENABLED
    messages = fetch_intent_prompt(
        chat_history, query_text, catalog_text, overview, local_hour,
        settings.INTENT_HISTORY_TURNS, settings.INTENT_HISTORY_CHARS, day_starts_of(settings), user_context,
    )
    script_args = script_args_of(settings)
    verdict = None
    for attempt in (messages, with_reminder(messages, INTENT_LANGUAGE_REMINDER)):
        try:
            raw = await chat_llm.invoke_helper(attempt, "intent")
        except Exception as e:
            logger.warning("Intent step failed (%s); running the full pipeline.", e)
            return None
        verdict = intent_verdict(
            parse_json_response(raw), overview, names, settings.KB_NAME_MENTION_MIN_CHARS,
            settings.NO_OVERVIEW_REPLY,
        )
        if verdict is None or not wrong_script(query_text, verdict[1], **script_args):
            break
        logger.warning("Intent reply came out in the wrong language; asking again.")
    else:
        logger.warning("Intent reply was in the wrong language twice; running the full pipeline.")
        return None
    if verdict and verdict[1] == settings.NO_OVERVIEW_REPLY:
        logger.warning("Intent reply named a document with the overview off; replaced it.")
    return verdict


class ChatTurn:
    """One chat turn: its inputs, its clock, and the steps that call the model or the search."""

    def __init__(
        self, settings: Settings, chat_llm: ChatLLM, chat_history: list, query,
        catalog: Awaitable[tuple] | None, local_hour: int | None, user_context: dict | None = None,
        search: SearchRuntime | None = None,
    ):
        self.settings = settings
        self.llm = chat_llm
        self._search = search
        self.history = history_for_model(chat_history)
        self.query_text = query_text_of(query)
        self.local_hour = local_hour
        self.user_context = user_context  # user_context.user_context: who is asking, for the prompts
        self.overview = settings.KB_OVERVIEW_ENABLED
        self.timer = TurnTimer()
        self._catalog = asyncio.ensure_future(catalog) if catalog is not None else None
        self._script_args = script_args_of(settings)
        self._day_starts = day_starts_of(settings)
        # What this turn did, for dbo.chatbot_trace (app.logic.trace). Filled in as the steps run.
        self.trace: dict = {}
        # Which versions of a document the question is about (logic/versions.py): the latest unless it asks otherwise.
        self.version_scope = detect_scope(self.query_text) if settings.VERSION_DETECTION_ENABLED else LATEST
        self.trace["version_scope"] = str(self.version_scope.mode) + (f":{self.version_scope.token}" if self.version_scope.token else "")

    # --- knowledge-base catalog ---

    async def _load_catalog(self) -> tuple:
        """(catalog text, document names); ("", "") if there is no catalog or it cannot be read - the chat works either way."""
        if self._catalog is None:
            return "", ""
        try:
            return await self._catalog
        except Exception:
            logger.warning("Could not load the knowledge-base catalog for this turn.", exc_info=True)
            return "", ""

    async def catalog_names(self) -> str:
        """Names in a refusal are shown to the user, so only with the overview on."""
        return (await self._load_catalog())[1] if self.overview else ""

    # --- intent ---

    def offers_intent_check(self) -> bool:
        return wants_intent_check(
            self.settings.INTENT_ROUTING_ENABLED, self.query_text, self.settings.INTENT_MAX_WORDS,
        )

    def simple_greeting(self) -> tuple | None:
        """(intent, reply), ready for diversion(), for a bare "hi"/"hey"/"hello" - with no model
        call at all. Tried first (chat_controller.process_chat_stream), before check_intent: the
        single most common message this bot gets is instant and correct even on a turn where the
        intent-classification call itself fails (see ask_intent's own fallback for everything else)."""
        if not is_simple_greeting(self.query_text):
            return None
        return INTENT_CHITCHAT, simple_greeting_reply(self.local_hour, self._day_starts, self.user_context)

    async def check_intent(self) -> tuple | None:
        catalog_text, names = await self._load_catalog()
        diverted = await ask_intent(
            self.llm, self.settings, self.history, self.query_text, catalog_text, names, self.local_hour,
            self.user_context,
        )
        # Measured from the start of the turn: the check overlaps the stages below.
        self.timer.since_turn("intent_ms")
        return diverted

    def diversion(self, verdict: tuple) -> list:
        """The events for a turn the intent step answered without searching."""
        intent, reply = verdict
        logger.info("Intent '%s': answering without searching the knowledge base.", intent)
        self.timer.since_turn("pipeline_ms")
        return diversion_events(intent, reply, self.timer.timings, self.trace)

    # --- rewrite, retrieve, rerank ---

    async def rewrite(self) -> tuple:
        """(search queries, the query's language or None, the standalone reading) - see prompts.parse_new_queries."""
        s = self.settings
        # The catalog is cached and read once a minute per replica, so this is normally no
        # wait. With the overview off it is not read at all: the catalog stays out of every
        # prompt, so no message can talk a model into repeating it.
        catalog_text = (await self._load_catalog())[0] if self.overview else ""
        data = await helper_json(
            self.llm,
            fetch_new_query_prompt(
                self.history, self.query_text, catalog_text,
                s.REWRITE_EXPANSION_BUDGET, s.REWRITE_HISTORY_TURNS, s.REWRITE_HISTORY_CHARS,
            ),
            usable_rewrite, "rewrite",
        )
        logger.info("Rephrased questions: %s", data)
        parsed = parse_new_queries(data, self.query_text, s.REWRITE_MAX_QUERIES, s.REWRITE_EXPANSION_BUDGET)
        self.trace["queries"] = [clipped(q, s.TRACE_QUERY_MAX_CHARS) for q in parsed[0]]
        self.trace["language"] = parsed[1]
        return parsed

    async def retrieve(self, questions: list) -> list:
        """The hits from searching each question at once, fused by rank across the questions; a failed search costs only its own hits."""
        s = self.settings
        results = await asyncio.gather(
            *(fetch_context(
                self._search, collection_name=s.MONGODB_COLLECTION_NAME, search_query=q, limit=s.SEARCH_RESULT_LIMIT,
                version_scope=self.version_scope,
            ) for q in questions),
            return_exceptions=True,
        )
        hit_lists, failures, empty = split_search_results(results)
        for failure in failures:
            logger.error("Context fetch failed for one query: %s", failure, exc_info=failure)
        if empty:
            logger.warning("Context fetch returned no results for %d query(ies).", empty)
        if failures and len(failures) == len(results):
            # Not one search got an answer, so the knowledge base was not consulted at all. Generating
            # from no passages would tell the user "ask about the topics in my knowledge base" about a
            # question it covers - an outage reported as their mistake, and saved to their history.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The knowledge-base search is unavailable right now. Please try again in a moment.",
            )
        self.timer.lap("retrieve_ms")
        # The floor applies to the store's own scores, before the fusion below replaces them with rank
        # scores on a different scale; top_score is the best raw one, so a turn stopped by the floor
        # still shows how close it came.
        self.trace["top_score"] = top_score_of([hit for hits in hit_lists for hit in hits])
        if s.MIN_RELEVANCE_SCORE > 0:
            kept_lists = [[h for h in hits if float(h.get("score") or 0.0) >= s.MIN_RELEVANCE_SCORE] for hits in hit_lists]
            dropped = sum(len(a) - len(b) for a, b in zip(hit_lists, kept_lists))
            if dropped:
                logger.info("Dropped %d hit(s) scoring below MIN_RELEVANCE_SCORE=%s.", dropped, s.MIN_RELEVANCE_SCORE)
            hit_lists = kept_lists
        fused = fuse_query_hits(hit_lists, s.QUERY_FUSION_RRF_K)
        self.trace["retrieved"] = hits_summary(fused, s.TRACE_MAX_HITS)
        return fused

    async def rerank(self, hits: list) -> list:
        """Dedupes the hits, then reorders them by relevance to the original query and drops what the
        model judges off-topic, via one extra LLM call.

        The same chunk can come back from several of the fanned-out searches, and query expansion
        pulls in broader, noisier recall on purpose, so this is the precision pass. Falls back to
        the score order, cut to RERANK_TOP_K, if the call or its JSON fails: a degraded ranking
        beats no answer.

        For ALL-mode (comparison) queries, three extra steps ensure both versions are represented:
        1. The shortlist is balanced so each version gets at least RERANK_COMPARE_MIN_PER_VERSION slots.
        2. The rerank prompt labels each passage with its version and asks the LLM to keep both sides.
        3. A post-rerank balancer fills any version with fewer than the minimum from unused candidates.
        """
        s = self.settings
        deduped = dedupe_candidates(hits)
        comparing = self.version_scope.mode is Mode.ALL
        if comparing:
            candidates = rerank_shortlist_balanced(deduped, s.RERANK_MAX_CANDIDATES, s.RERANK_COMPARE_MIN_PER_VERSION)
        else:
            candidates = rerank_shortlist(deduped, s.RERANK_MAX_CANDIDATES)
        if not candidates:
            self.timer.lap("rerank_ms")
            return candidates
        fallback = candidates[:s.RERANK_TOP_K]
        try:
            data = await helper_json(
                self.llm,
                fetch_rerank_prompt(
                    self.query_text, candidates, s.RERANK_TOP_K, s.RERANK_ID_CHARS, s.RERANK_PASSAGE_CHARS,
                    short_ids=s.RERANK_SHORT_IDS, compare_versions=comparing,
                ),
                usable_rerank, "rerank",
            )
            ranked_ids = data.get("ranked_ids") if data else None
            if ranked_ids:
                result = reorder_by_ids(candidates, ranked_ids, s.RERANK_ID_CHARS, s.RERANK_TOP_K) or fallback
            else:
                logger.warning("Reranker returned no usable ids; falling back to score order.")
                result = fallback
        except Exception as e:
            logger.warning("Reranking failed, falling back to score order: %s", e)
            result = fallback
        if comparing:
            result = balance_versions(result, deduped, s.RERANK_TOP_K, s.RERANK_COMPARE_MIN_PER_VERSION)
        self.timer.lap("rerank_ms")
        self.trace["reranked"] = hits_summary(result, s.TRACE_MAX_HITS)
        return result

    # --- evidence gate ---

    async def no_evidence_reply(self, language: str | None) -> str:
        """NO_EVIDENCE_REPLY, in the question's language when that is not English."""
        reply = self.settings.NO_EVIDENCE_REPLY
        if self.needs_translation(language, reply):
            return await self.translate(reply, language)
        return reply

    def no_evidence(self, reply: str) -> list:
        """The events for a turn the evidence gate answered without calling the model."""
        logger.info("No usable passages for this question: answering with the no-evidence reply.")
        self.timer.since_turn("pipeline_ms")
        return no_evidence_events(reply, [self.settings.MONGODB_COLLECTION_NAME], self.timer.timings, self.trace)

    # --- answer ---

    def answer_messages(self, passages: list, catalog_names: str, language: str | None, standalone: str) -> list:
        return self.history + fetch_final_prompt(
            passages, question=self.query_text, catalog_names=catalog_names,
            standalone_question=standalone, language=(language or "").title(), user_context=self.user_context,
            version_scope=self.version_scope,
        )

    async def stream_answer(self, messages: list):
        """Yields the answer's tokens as the model writes them."""
        first = True
        async for token in self.llm.stream_llm(messages):
            if first:
                # Prompt processing plus any model "thinking" - what the user sits through before the first word.
                self.timer.since_stage("first_token_ms")
                first = False
            yield token

    async def in_query_script(self, messages: list, language: str | None, response: str) -> str:
        """The answer, regenerated once - told the language outright - if it came out in a script
        the question was not in; the result replaces what was streamed."""
        if not wrong_script(self.query_text, response, **self._script_args):
            return response
        logger.warning("Answer came out in the wrong language; regenerating it once.")
        retried = await self.llm.invoke_llm(with_reminder(messages, language_reminder(language)))
        return retried if retried and not wrong_script(self.query_text, retried, **self._script_args) else response

    def needs_translation(self, language: str | None, response: str) -> bool:
        return needs_translation(language, self.query_text, response, **self._script_args)

    async def translate(self, response: str, language: str | None) -> str:
        """The answer in the query's language, or `response` as it stands if no usable translation comes back."""
        try:
            messages = format_translate_prompt(query_text=self.query_text, answer_text=response)
            # Usable: every citation marker came back, and - when the question is known not to
            # be English - the text actually changed (see is_usable_translation).
            # An answer already in the query's script (Urdu written in Arabic letters, say) may
            # come back unchanged when it was right, so only one that is not is required to change.
            must_change = language not in (None, ENGLISH) and not answered_in_query_script(
                self.query_text, response, **self._script_args,
            )
            usable = partial(
                is_usable_translation, original=response.strip(),
                must_change=must_change, min_ratio=self.settings.TRANSLATION_MIN_RATIO,
            )
            data = await helper_json(self.llm, messages, usable, "translate")
            if data is None:
                # The user asked in their language and is owed the answer in it, so it is retried
                # once on the main model - told what came back wrong - before the answer stands.
                logger.warning("Translation was unusable; retrying it once with the rule spelled out.")
                retried = parse_json_response(await self.llm.invoke_llm(with_reminder(messages, TRANSLATE_RETRY_REMINDER)))
                data = retried if retried is not None and usable(retried) else None
            if data and "final_answer" in data:
                return data["final_answer"]
            logger.warning("No usable translation; sending the answer as generated.")
        except Exception as e:
            logger.warning("Language check failed, using original response: %s", e)
        finally:
            self.timer.lap("translate_ms")
        return response

    def finished(self, response: str, sources: list, passages: dict | None = None) -> dict:
        self.timer.since_turn("pipeline_ms")
        return result_event(
            response, [self.settings.MONGODB_COLLECTION_NAME], sources, self.timer.timings, passages=passages,
            trace=self.trace, version_scope=self.version_scope,
        )
