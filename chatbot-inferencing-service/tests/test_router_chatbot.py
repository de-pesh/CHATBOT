import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from app.main import app
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@patch("app.routers.chatbot.get_ollama_client")
@patch("app.routers.chatbot.get_settings")
@patch("app.controllers.chat_controller.process_chat", new_callable=AsyncMock)
def test_chat_route_success(mock_process_chat, mock_get_settings, mock_get_ollama_client, client):
    mock_process_chat.return_value = "This is a mocked response"
    mock_get_ollama_client.return_value = AsyncMock()
    mock_get_settings.return_value = {
        "MONGODB_COLLECTION_NAME": "test_collection",
        "RPC_SERVER": "mock_rpc"
    }

    payload = {
        "chat": ["Hello", "What is the policy for data encryption?"]
    }

    response = client.post("/chat", json=payload)

    assert response.status_code == 200
    assert response.json() == {"response": "This is a mocked response"}
    mock_process_chat.assert_awaited_once()
