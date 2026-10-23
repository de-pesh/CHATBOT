"""app/api_server.py's routes - the REST replacement for the old gRPC servicer
methods (Index, Delete, ParseFile). Exercised directly against the route
functions, with fakes standing in for the vector store/embedding/vision clients,
rather than through a live TestClient + lifespan (which would need a reachable
embedding endpoint and a real Mongo)."""
from types import SimpleNamespace

import app.api_server as api_server
from app.api_server import (
    DeleteRequest,
    IndexRequest,
    ParseFileRequest,
    delete,
    index,
    parse_file,
)


class FakeVectorStore:
    def __init__(self, deactivated_count=3):
        self.deactivated_count = deactivated_count
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def deactivate_data_by_source_id(self, source_file_id, collection_name=None):
        self.calls.append((source_file_id, collection_name))
        return self.deactivated_count

    async def set_latest_by_source_id(self, source_file_id, is_latest, collection_name=None, **version):
        self.calls.append(("latest", source_file_id, is_latest, collection_name, version))
        return 4


def _fresh_state():
    """A clean app.state, since api_server.app is a module-level singleton shared
    across tests."""
    api_server.app.state.settings = SimpleNamespace()
    api_server.app.state.storage = object()
    api_server.app.state.embedding = object()
    api_server.app.state.vision = object()


async def test_delete_reports_how_many_chunks_were_deactivated(monkeypatch):
    _fresh_state()
    store = FakeVectorStore(deactivated_count=5)
    monkeypatch.setattr(api_server, "create_vector_store", lambda settings: store)

    response = await delete(DeleteRequest(collection_name="kb", file_id="f1"))

    assert response.success is True
    assert "5" in response.message
    assert store.calls == [("f1", "kb")]


async def test_delete_failure_is_reported_not_raised(monkeypatch):
    _fresh_state()

    class Boom(FakeVectorStore):
        async def deactivate_data_by_source_id(self, *a, **kw):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(api_server, "create_vector_store", lambda settings: Boom())

    response = await delete(DeleteRequest(collection_name="kb", file_id="f1"))

    assert response.success is False and "mongo down" in response.message


async def test_index_calls_index_handler_and_returns_its_file_id(monkeypatch):
    _fresh_state()
    monkeypatch.setattr(api_server, "create_vector_store", lambda settings: FakeVectorStore())

    async def fake_index_handler(**kwargs):
        assert kwargs["collection_name"] == "kb" and kwargs["is_policy"] is True
        return "new-id"

    monkeypatch.setattr(api_server, "index_handler", fake_index_handler)

    response = await index(IndexRequest(collection_name="kb", file_path="p.txt", is_policy=True))

    assert response.success is True and response.file_id == "new-id"


async def test_index_failure_is_reported_not_raised(monkeypatch):
    _fresh_state()
    monkeypatch.setattr(api_server, "create_vector_store", lambda settings: FakeVectorStore())

    async def fake_index_handler(**kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(api_server, "index_handler", fake_index_handler)

    response = await index(IndexRequest(collection_name="kb", file_path="p.txt"))

    assert response.success is False and "insert failed" in response.message


async def test_parse_file_returns_the_blob_path(monkeypatch):
    _fresh_state()

    async def fake_parsing_handler(file_path, storage, vision, max_blank_page_share=1.0):
        return "parsed/path.txt"

    monkeypatch.setattr(api_server, "parsing_handler", fake_parsing_handler)

    response = await parse_file(ParseFileRequest(file_path="raw.pdf"))

    assert response.success is True and response.blob_path == "parsed/path.txt"


async def test_parse_file_failure_names_the_reason(monkeypatch):
    _fresh_state()

    async def fake_parsing_handler(file_path, storage, vision, max_blank_page_share=1.0):
        raise ValueError("no text extracted")

    monkeypatch.setattr(api_server, "parsing_handler", fake_parsing_handler)

    response = await parse_file(ParseFileRequest(file_path="raw.pdf"))

    assert response.success is False and "no text extracted" in response.message


async def test_set_latest_marks_a_documents_chunks_and_reports_the_count(monkeypatch):
    _fresh_state()
    store = FakeVectorStore()
    monkeypatch.setattr(api_server, "create_vector_store", lambda settings: store)

    response = await api_server.set_latest(api_server.SetLatestRequest(collection_name="kb", file_id="f1", is_latest=False))

    assert response.success is True and "4" in response.message
    assert store.calls == [("latest", "f1", False, "kb", {"version_no": None, "version_label": None, "document_key": None})]


async def test_set_latest_passes_the_version_fields_through(monkeypatch):
    _fresh_state()
    store = FakeVectorStore()
    monkeypatch.setattr(api_server, "create_vector_store", lambda settings: store)

    await api_server.set_latest(api_server.SetLatestRequest(
        collection_name="kb", file_id="f1", is_latest=True, version_no=3, version_label="3.2", document_key="policy"))

    assert store.calls[0][4] == {"version_no": 3, "version_label": "3.2", "document_key": "policy"}


async def test_set_latest_failure_is_reported_not_raised(monkeypatch):
    _fresh_state()

    class Boom(FakeVectorStore):
        async def set_latest_by_source_id(self, *a, **kw):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(api_server, "create_vector_store", lambda settings: Boom())

    response = await api_server.set_latest(api_server.SetLatestRequest(collection_name="kb", file_id="f1", is_latest=True))

    assert response.success is False and "mongo down" in response.message
