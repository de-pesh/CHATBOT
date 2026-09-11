import logging
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    SystemMessage
)
from fastapi import HTTPException, status, Request


logger = logging.getLogger(__name__)

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