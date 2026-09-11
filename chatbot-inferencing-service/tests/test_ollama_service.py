import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from app.services.ollama_service import OllamaService
from langchain_ollama.chat_models import ChatOllama
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def test_format_messages():
    ollama = OllamaService(ollama_client=MagicMock(spec=ChatOllama))
    
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
        {"role": "system", "content": "You are a chatbot"},
        {"role": "invalid", "content": "ignore me"},
    ]

    formatted = ollama.format_messages(messages)
    assert len(formatted) == 3  # invalid ignored


@pytest.mark.asyncio
async def test_invoke_llm_success():
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "Mocked response"
    mock_client.ainvoke.return_value = mock_response

    ollama = OllamaService(ollama_client=mock_client)

    messages = [{"role": "user", "content": "What is RAG?"}]
    response = await ollama.invoke_llm(messages)

    assert response == "Mocked response"
    mock_client.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_llm_failure():
    mock_client = AsyncMock()
    mock_client.ainvoke.side_effect = Exception("Boom")

    ollama = OllamaService(ollama_client=mock_client)

    with pytest.raises(HTTPException) as exc_info:
        await ollama.invoke_llm([{"role": "user", "content": "trigger error"}])

    assert exc_info.value.status_code == 500
    assert "Unexpected error querying Ollama" in str(exc_info.value.detail)
