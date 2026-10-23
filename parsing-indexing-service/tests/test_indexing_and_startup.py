"""index_handler end to end with fakes, and the import-time promises of the server."""
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from app.indexing.handler import index_handler
from app.logic.text_formats import PAGE_BREAK
from app.services.embedding.embedding_service import EmbeddingService
from app.services.vector.vector_store import VectorStore


class FakeEmbedder(EmbeddingService):
    model_id = "fake-model"

    def generate_embeddings(self, texts, show_progress_bar=False):
        return np.ones((len(texts), 3), dtype=np.float32)


class FakeStorage:
    def __init__(self, text):
        self.text = text

    async def download_file(self, path):
        return BytesIO(self.text.encode("utf-8"))


class FakeStore(VectorStore):
    def __init__(self, connects=True, inserted=True, index_ok=True, index_error=None, deactivate_error=None):
        self.connects, self.inserted, self.index_ok = connects, inserted, index_ok
        self.index_error, self.deactivate_error = index_error, deactivate_error
        self.items, self.closed, self.deactivated = [], False, []

    async def connect(self):
        return self.connects

    def close(self):
        self.closed = True

    async def create_vector_index(self, collection_name=None):
        if self.index_error:
            raise self.index_error
        return self.index_ok

    async def insert_data(self, data_items, collection_name=None):
        self.items = data_items
        return (["id"] * len(data_items), len(data_items)) if self.inserted else (None, 0)

    async def similarity_search_on_query(self, *args, **kwargs):
        return []

    async def deactivate_data_by_source_id(self, source_file_id, collection_name=None):
        self.deactivated.append((source_file_id, collection_name))
        if self.deactivate_error:
            raise self.deactivate_error
        return 0


SETTINGS = SimpleNamespace(CHUNK_SIZE=1000, CHUNK_OVERLAP=100)


async def run(text, store, is_policy=False, metadata=None):
    return await index_handler(
        file_path="dir/file.txt",
        collection_name="kb",
        metadata=metadata or {"file_name": "f.pdf", "isactive": "overridden"},
        storage_service=FakeStorage(text),
        vector_store=store,
        embedding_service=FakeEmbedder(),
        settings=SETTINGS,
        is_policy=is_policy,
    )


async def test_chunks_are_enriched_and_written():
    store = FakeStore()
    source_id = await run("some text", store)
    (item,) = store.items
    assert item["textContent"] == "some text" and item["embeddingModel"] == "fake-model"
    assert item["vectorContent"] == [1.0, 1.0, 1.0]
    meta = item["metadata"]
    assert meta["source_file_id"] == source_id and meta["original_file_path"] == "dir/file.txt"
    assert meta["isactive"] is True and meta["file_name"] == "f.pdf" and meta["uuid"]
    assert "page_number" not in meta


async def test_is_policy_keeps_the_page_number_next_to_the_added_metadata():
    store = FakeStore()
    await run(PAGE_BREAK.join(["one", "two"]), store, is_policy=True)
    assert [i["metadata"]["page_number"] for i in store.items] == [1, 2]
    assert len({i["metadata"]["uuid"] for i in store.items}) == 2


async def test_each_call_gets_a_fresh_source_file_id():
    assert await run("a", FakeStore()) != await run("a", FakeStore())


async def test_nothing_to_index_is_not_an_error_and_writes_nothing():
    store = FakeStore()
    await run("   ", store)  # whitespace only: splits into zero chunks
    assert store.items == []


async def test_a_failed_insert_raises():
    with pytest.raises(RuntimeError, match="Failed to insert chunks into collection 'kb'"):
        await run("text", FakeStore(inserted=False))


async def test_a_failed_index_raises():
    with pytest.raises(RuntimeError, match="Failed to ensure vector index"):
        await run("text", FakeStore(index_ok=False))


async def test_a_failure_after_the_insert_deactivates_that_attempts_chunks_and_still_raises():
    store = FakeStore(index_error=ConnectionError("atlas down"))
    with pytest.raises(ConnectionError, match="atlas down"):
        await run("text", store)
    (source_file_id, collection), = store.deactivated
    assert collection == "kb" and source_file_id == store.items[0]["metadata"]["source_file_id"]


async def test_a_false_index_result_and_a_failed_insert_also_deactivate():
    for store in (FakeStore(index_ok=False), FakeStore(inserted=False)):
        with pytest.raises(RuntimeError):
            await run("text", store)
        assert len(store.deactivated) == 1


async def test_a_failing_cleanup_never_hides_the_original_error(caplog):
    store = FakeStore(index_ok=False, deactivate_error=RuntimeError("mongo gone"))
    with pytest.raises(RuntimeError, match="Failed to ensure vector index"):
        await run("text", store)
    assert "mongo gone" in caplog.text


async def test_a_successful_index_deactivates_nothing():
    store = FakeStore()
    await run("text", store)
    assert store.deactivated == []


async def test_the_store_context_manager_raises_when_it_cannot_connect():
    store = FakeStore(connects=False)
    with pytest.raises(ConnectionError, match="Failed to connect to the vector store"):
        async with store:
            pass


async def test_the_store_context_manager_closes_on_exit():
    store = FakeStore()
    async with store:
        pass
    assert store.closed


def test_importing_the_server_loads_no_models_and_no_ocr_stack():
    code = (
        "import sys, app.api_server\n"
        "heavy = [m for m in ('sentence_transformers', 'ocrmypdf', 'pikepdf', 'torch') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
        "assert app.api_server.app is not None\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    assert result.returncode == 0, result.stderr
