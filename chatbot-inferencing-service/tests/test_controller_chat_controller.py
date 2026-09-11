import pytest
import json
from unittest.mock import patch, AsyncMock
from app.controllers.chat_controller import process_chat
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

@pytest.mark.asyncio
@patch("app.controllers.chat_controller.fetch_final_prompt", return_value=["final_prompt"])
@patch("app.controllers.chat_controller.fetch_context", return_value=[{"text": "context"}])
@patch("app.controllers.chat_controller.fetch_new_query_prompt", return_value=["rephrase_prompt"])
async def test_process_chat_success(mock_rephrase_prompt, mock_fetch_context, mock_final_prompt):
    mock_ollama = AsyncMock()
    mock_ollama.invoke_llm.side_effect = [
        '```json\n{"new_questions": ["What is the encryption policy?"]}\n```',
        "Final response from LLM"
    ]

    settings = type("Settings", (), {
        "MONGODB_COLLECTION_NAME": "test_collection",
        "RPC_SERVER": "mock_rpc"
    })()

    chat_history = []
    query = "Tell me about encryption"
    result = await process_chat(settings, chat_history, query, mock_ollama)

    assert "Final response from LLM" in result.response
    assert mock_ollama.invoke_llm.call_count == 2
