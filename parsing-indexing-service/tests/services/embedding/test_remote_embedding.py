"""RemoteEmbeddingService / AzureEmbeddingService over a faked urlopen: batching, retries, request shape."""
import io
import json
import urllib.error

import numpy as np
import pytest
from app.logic.embeddings import azure_embeddings_url, embeddings_url
from app.services.embedding import remote_embedding_service as remote
from app.services.embedding.azure_embedding_service import AzureEmbeddingService
from app.services.embedding.remote_embedding_service import RemoteEmbeddingService


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b"body"))


@pytest.fixture
def wire(monkeypatch):
    """Scripted urlopen: each call pops the next outcome (a payload, or an exception to raise)."""
    calls, sleeps, script = [], [], []

    def urlopen(request, timeout):
        calls.append((request, timeout))
        outcome = script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    monkeypatch.setattr(remote.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(remote.time, "sleep", sleeps.append)
    return type("Wire", (), {"calls": calls, "sleeps": sleeps, "script": script})


def embeddings_for(batch_size):
    return {"data": [{"index": i, "embedding": [float(i)]} for i in range(batch_size)]}


def make(**kw):
    options = dict(batch_size=64, dimensions=None, timeout=60.0, max_retries=3, backoff_base=2.0,
                   retry_statuses=frozenset({429, 500, 502, 503, 504}))
    return RemoteEmbeddingService(endpoint=embeddings_url("http://host/v1/"), model_name="m", api_key="k", **{**options, **kw})


def test_texts_go_out_in_batches_and_come_back_in_order(wire):
    wire.script += [embeddings_for(2), embeddings_for(1)]
    result = make(batch_size=2).generate_embeddings(["a", "b", "c"])
    assert result.tolist() == [[0.0], [1.0], [0.0]] and result.dtype == np.float32
    assert [json.loads(r.data)["input"] for r, _ in wire.calls] == [["a", "b"], ["c"]]
    request, timeout = wire.calls[0]
    assert request.full_url == "http://host/v1/embeddings" and timeout == 60.0
    assert request.headers["Authorization"] == "Bearer k" and "dimensions" not in json.loads(request.data)


def test_rows_returned_out_of_order_are_put_back_by_index(wire):
    wire.script.append({"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]})
    assert make().generate_embeddings(["a", "b"]).tolist() == [[1.0], [2.0]]


def test_a_single_string_returns_one_vector_and_dimensions_are_sent_when_set(wire):
    wire.script.append(embeddings_for(1))
    assert make(dimensions=256).generate_embeddings("a").tolist() == [0.0]
    assert json.loads(wire.calls[0][0].data)["dimensions"] == 256


def test_a_rate_limit_is_retried_with_exponential_backoff_then_succeeds(wire):
    wire.script += [http_error(429), http_error(503), embeddings_for(1)]
    make(max_retries=3).generate_embeddings("a")
    assert wire.sleeps == [1, 2] and len(wire.calls) == 3


def test_a_network_error_is_retried_and_gives_up_after_max_retries(wire):
    wire.script += [urllib.error.URLError("down")] * 3
    with pytest.raises(RuntimeError, match="Could not reach embedding endpoint"):
        make(max_retries=2).generate_embeddings("a")
    assert len(wire.calls) == 3 and wire.sleeps == [1, 2]


def test_the_backoff_base_and_retryable_statuses_are_configurable(wire):
    wire.script += [http_error(418), embeddings_for(1)]
    make(backoff_base=3.0, max_retries=2, retry_statuses=frozenset({418})).generate_embeddings("a")
    assert wire.sleeps == [1.0]
    wire.script += [http_error(429)]
    with pytest.raises(RuntimeError, match="returned 429"):
        make(retry_statuses=frozenset({418})).generate_embeddings("a")


def test_a_client_error_is_not_retried(wire):
    wire.script.append(http_error(400))
    with pytest.raises(RuntimeError, match="returned 400: body"):
        make().generate_embeddings("a")
    assert len(wire.calls) == 1 and wire.sleeps == []


def test_a_response_without_data_is_an_error(wire):
    wire.script.append({"error": "nope"})
    with pytest.raises(ValueError, match="no 'data'"):
        make().generate_embeddings("a")


@pytest.mark.parametrize("texts", ["", [], [1], 5])
def test_bad_input_is_refused_before_any_request(wire, texts):
    with pytest.raises(ValueError):
        make().generate_embeddings(texts)
    assert wire.calls == []


def test_azure_puts_the_deployment_in_the_url_and_sends_api_key(wire):
    wire.script.append(embeddings_for(1))
    service = AzureEmbeddingService(
        endpoint=azure_embeddings_url("https://r.openai.azure.com/", "dep", "2024-10-21"), model_name="dep", api_key="k",
        batch_size=64, dimensions=None, timeout=60.0, max_retries=3, backoff_base=2.0, retry_statuses=frozenset({429}))
    service.generate_embeddings("a")
    request = wire.calls[0][0]
    assert request.full_url == "https://r.openai.azure.com/openai/deployments/dep/embeddings?api-version=2024-10-21"
    assert request.headers["Api-key"] == "k" and "Authorization" not in request.headers
    assert json.loads(request.data)["model"] == "dep"
