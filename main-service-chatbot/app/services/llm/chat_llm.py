"""ChatLLM: the chat client the pipeline calls, for any backend chat_llm_factory can build.

It wraps the main model (which writes the answer) plus an optional lighter model per helper role
(rewrite, rerank, translate, intent, describe), and sets the reasoning effort on each call.
The backends themselves are the sibling *_llm.py modules; nothing here is specific to one vendor.
"""
import logging
from collections.abc import AsyncIterator

from fastapi import HTTPException, Request, status
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.logic.llm_models import chunk_text

logger = logging.getLogger(__name__)

_MESSAGE_TYPES = {"user": HumanMessage, "system": SystemMessage, "assistant": AIMessage}


def _format_messages(messages: list) -> list:
    """{"role", "content"} dicts as LangChain messages; a message with an unknown role is skipped."""
    formatted = []
    for msg in messages:
        message_type = _MESSAGE_TYPES.get(msg.get("role"))
        if message_type is None:
            logger.warning("Unknown message role encountered: %s", msg.get("role"))
            continue
        formatted.append(message_type(content=msg.get("content")))
    return formatted


class ChatLLM:
    """A LangChain chat model plus the helper models and reasoning-effort settings around it."""

    def __init__(
        self,
        chat_model: BaseChatModel,
        task_clients: dict | None = None,
        helper_reasoning_effort: str | None = None,
        answer_reasoning_effort: str | None = None,
        fallback_model: BaseChatModel | None = None,
    ):
        """
        chat_model: the shared chat model. Writes the answer, and runs any helper step with no model of its own.
        task_clients: optional {helper role: client} for the short structured calls around the answer (see invoke_helper).
        helper_reasoning_effort / answer_reasoning_effort: optional reasoning effort for helper calls / the answer.
        fallback_model: optional second model that writes the answer when the main one fails (LLM_FALLBACK_MODEL).
        """
        self.chat_model = chat_model
        self.task_clients = task_clients or {}
        self.helper_reasoning_effort = helper_reasoning_effort
        self.answer_reasoning_effort = answer_reasoning_effort
        self.fallback_model = fallback_model

    def uses_helper(self, task: str) -> bool:
        """Whether `task` (a helper role: rewrite, rerank, translate, intent, describe) runs on a lighter helper model."""
        return task in self.task_clients

    def _answer_client(self):
        """The main model, at the answer reasoning effort if one is set."""
        if self.answer_reasoning_effort:
            return self.chat_model.bind(reasoning_effort=self.answer_reasoning_effort)
        return self.chat_model

    async def invoke_llm(self, messages: list):
        """One blocking call on the main model, or on the fallback model if that call fails and one is configured."""
        try:
            return await self._invoke(self._answer_client(), messages)
        except HTTPException as e:
            if self.fallback_model is None:
                raise
            logger.warning("Main model failed (%s); answering with the fallback model.", e.detail)
            return await self._invoke(self.fallback_model, messages)

    async def invoke_helper(self, messages: list, task: str):
        """One blocking call for a short structured job (a helper role). Runs on the lighter helper
        model when one is configured for this `task` (LLM_HELPER_MODEL or the task's own setting),
        otherwise on the main model, and applies the helper reasoning effort if one is set.
        The answer never goes through here."""
        client = self.task_clients.get(task, self.chat_model)
        if self.helper_reasoning_effort:
            client = client.bind(reasoning_effort=self.helper_reasoning_effort)
        return await self._invoke(client, messages)

    async def _invoke(self, client, messages: list):
        try:
            response = await client.ainvoke(_format_messages(messages))
            return response.content
        except Exception as e:
            logger.exception(f"Unexpected error querying the chat model: {e}")
            raise HTTPException(status_code=500, detail=f"Unexpected error querying the chat model: {repr(e)}") from e

    async def stream_llm(self, messages: list) -> AsyncIterator[str]:
        """Yields the model's answer incrementally, one token-ish chunk at a time.

        Falls back to a single `invoke_llm` call if the backend refuses to
        stream at all - some Azure OpenAI deployments (notably the o-series and
        other reasoning models) reject `stream=true` until the subscription is
        verified. The fallback only applies when the failure happens *before*
        any chunk arrives; a mid-stream failure cannot be restarted safely
        because the caller has already been handed a partial answer.
        """
        emitted = False
        try:
            async for chunk in self._answer_client().astream(_format_messages(messages)):
                text = chunk_text(chunk)
                if text:
                    emitted = True
                    yield text
        except Exception as e:
            if emitted:
                logger.exception("LLM stream failed after partial output: %s", e)
                raise HTTPException(
                    status_code=500, detail=f"LLM stream failed mid-response: {repr(e)}"
                ) from e
            logger.warning(
                "Streaming unavailable from the LLM backend (%s); falling back to a single call.",
                repr(e),
            )
            content = await self.invoke_llm(messages)
            if content:
                yield content


async def get_chat_llm(request: Request) -> ChatLLM:
    """FastAPI dependency: the chat client built from what the lifespan put on app.state."""
    state = request.app.state
    if getattr(state, "chat_model", None) is None:
        logger.error("Chat model not initialized on app.state.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HTTP client service is not available (not initialized)"
        )
    return ChatLLM(
        chat_model=state.chat_model,
        task_clients=getattr(state, "task_clients", {}),
        helper_reasoning_effort=getattr(state, "helper_reasoning_effort", None),
        answer_reasoning_effort=getattr(state, "answer_reasoning_effort", None),
        fallback_model=getattr(state, "fallback_model", None),
    )
