import logging
from typing import AsyncIterator
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    SystemMessage
)
from fastapi import HTTPException, status, Request


logger = logging.getLogger(__name__)


def _chunk_text(chunk) -> str:
    """Pulls plain text out of one streamed LangChain message chunk.

    `content` is a string for Azure OpenAI and Ollama, but the chat-model
    interface also allows a list of content blocks, so both shapes are handled
    rather than assuming the one the current backend happens to emit.
    """
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


class OllamaService:
    '''
    Service for interacting with a LangChain chat model (Ollama, Azure OpenAI, etc).
    '''

    def __init__(self, ollama_client: BaseChatModel):
        '''
        Initializes the OllamaService with a shared LangChain chat model client.

        Parameters:
            ollama_client (BaseChatModel): The shared, pre-initialized LangChain chat model.
        '''
        if not isinstance(ollama_client, BaseChatModel):
            logger.error("Invalid ollama_client provided: must be a BaseChatModel instance.")
            # This indicates a programming error (dependency setup failed)
            raise TypeError("ollama_client must be a BaseChatModel instance")
        self.ollama_client = ollama_client
        logger.debug("OllamaService wrapper initialized")

    def format_messages(self, messages: list):
        formatted_messages  = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "user":
                formatted_messages .append(HumanMessage(content=content))
            elif role == "system":
                formatted_messages .append(SystemMessage(content=content))
            elif role == "assistant":
                formatted_messages .append(AIMessage(content=content))
            else:
                logger.warning(f"Unknown message role encountered: {role}")
        return formatted_messages 
        
    
    async def invoke_llm(self, messages:list):
        try:
            formatted_messages = self.format_messages(messages)
            response = await self.ollama_client.ainvoke(formatted_messages)
            return response.content
        except Exception as e:
            logger.exception(f"Unexpected error querying Ollama: {e}")
            raise HTTPException(status_code=500, detail=f"Unexpected error querying Ollama: {repr(e)}") from e

    async def stream_llm(self, messages: list) -> AsyncIterator[str]:
        """Yields the model's answer incrementally, one token-ish chunk at a time.

        Falls back to a single `invoke_llm` call if the backend refuses to
        stream at all - some Azure OpenAI deployments (notably the o-series and
        other reasoning models) reject `stream=true` until the subscription is
        verified. The fallback only applies when the failure happens *before*
        any chunk arrives; a mid-stream failure cannot be restarted safely
        because the caller has already been handed a partial answer.
        """
        formatted_messages = self.format_messages(messages)
        emitted = False
        try:
            async for chunk in self.ollama_client.astream(formatted_messages):
                text = _chunk_text(chunk)
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


async def get_ollama_client(request: Request) -> OllamaService:
    """ Dependency to get the Ollama client session. """
    if not hasattr(request.app.state, "ollama_client") or request.app.state.ollama_client is None:
        logger.error("Ollama client session not found or not initialized!")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HTTP client service is not available (not initialized)"
        )
    # return request.app.state.ollama_client
    return OllamaService(ollama_client=request.app.state.ollama_client)
