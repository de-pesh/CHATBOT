import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.helpers.context_helper import fetch_context
import grpc
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

@pytest.mark.asyncio
@patch("app.helpers.context_helper.vectorapi_pb2_grpc.VectorServiceStub")
@patch("app.helpers.context_helper.grpc.aio.insecure_channel")
async def test_fetch_context_success(mock_insecure_channel, mock_vector_stub):
    # Setup
    mock_channel = AsyncMock()
    mock_insecure_channel.return_value.__aenter__.return_value = mock_channel

    mock_stub_instance = AsyncMock()
    mock_vector_stub.return_value = mock_stub_instance

    fake_result = MagicMock()
    fake_result_list = [fake_result]
    mock_stub_instance.Search.return_value.results = fake_result_list

    results = await fetch_context("test_collection", "test query", "localhost:50051")
    
    assert results == fake_result_list
    mock_stub_instance.Search.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.helpers.context_helper.vectorapi_pb2_grpc.VectorServiceStub")
@patch("app.helpers.context_helper.grpc.aio.insecure_channel")
async def test_fetch_context_rpc_failure(mock_insecure_channel, mock_vector_stub, caplog):
    mock_channel = AsyncMock()
    mock_insecure_channel.return_value.__aenter__.return_value = mock_channel

    mock_stub_instance = AsyncMock()
    mock_vector_stub.return_value = mock_stub_instance

    # Simulate RPC error
    mock_stub_instance.Search.side_effect = grpc.aio.AioRpcError(
        grpc.StatusCode.UNAVAILABLE, "Service Unavailable"
    )

    with caplog.at_level("ERROR"):
        result = await fetch_context("collection", "query", "localhost:8000")
        assert result is None or result == []  # function returns nothing on error
        assert "RPC error during Search" in caplog.text
