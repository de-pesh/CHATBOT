"""The ingest task is safe to run twice: it cleans up after itself instead of leaving orphaned chunks.

Every attempt indexes a fresh knowledge-base document; only the one recorded on the file row is
reachable afterwards (deleting the file deletes just that one). The compute service, database and
vector store are fakes.
"""
import asyncio
import itertools
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.kb_worker import worker as w
from app.models.files import ProcessingStatus
from app.services.db.stored_procedures import StoredProcedures

PAYLOAD = {
    "action_type": "create", "file_id": 7, "file_path": "a/b.pdf", "name": "b.pdf",
    "mimetype": "application/pdf", "user_id": "u@x",
}
SETTINGS = SimpleNamespace(WORKER_ERROR_MESSAGE_MAX_CHARS=1000)


class FakeDB:
    """A one-row files table: filter_files, upsert_file and the status update."""

    def __init__(self, kb_file_id=None, is_active=True, exists=True):
        self.row = {
            "id": 7, "name": "b.pdf", "mimetype": "application/pdf", "file_path": "a/b.pdf", "extracted_text_path": None,
            "vector_store_file_id": kb_file_id, "is_active": is_active, "created_on": datetime(2026, 1, 1), "created_by": "u@x",
        }
        self.exists = exists
        self.statuses = []
        self.upserts = []
        self.upsert_error = None
        self.upsert_gate = None  # awaited inside upsert_file: lets a test hold the task there

    async def call_procedure(self, name, params=None):
        if name == StoredProcedures.FILTER_FILES:
            return [[dict(self.row)] if self.exists else []]
        if name == StoredProcedures.UPDATE_FILE_PROCESSING_STATUS:
            self.statuses.append(params[1])
            return [[]]
        if name == StoredProcedures.UPSERT_FILE:
            if self.upsert_gate is not None:
                await self.upsert_gate.wait()
            if self.upsert_error:
                raise self.upsert_error
            self.upserts.append(params)
            self.row["vector_store_file_id"] = params[6]
            return [[]]
        raise AssertionError(name)


class World:
    """Fake compute service and vector store: `active` is the set of chunk sets a search would find."""

    def __init__(self, monkeypatch, db, index_error=None, on_indexed=None, delete_error=None):
        self.db = db
        self.active = set()
        self.deleted = []
        self.indexed = []
        self.metadata = []
        self.parsed = 0
        self.on_indexed = on_indexed
        ids = itertools.count(1)

        async def parse(settings, blob_path):
            self.parsed += 1
            return f"parsed/{self.parsed}.txt"

        async def index(settings, parsed_file_path, metadata):
            if index_error:
                raise index_error
            self.metadata.append(metadata)
            kb_id = f"kb-{next(ids)}"
            self.active.add(kb_id)
            self.indexed.append(kb_id)
            if self.on_indexed:
                self.on_indexed(kb_id)
            return kb_id

        async def delete_document(settings, kb_file_id):
            if delete_error:
                raise delete_error
            self.deleted.append(kb_file_id)
            self.active.discard(kb_file_id)
            return "ok"

        monkeypatch.setattr(w, "parse_file_via_rpc", parse)
        monkeypatch.setattr(w.IndexingRPCService, "indexing_add_file", staticmethod(index))
        monkeypatch.setattr(w.kb_service, "delete_document", delete_document)

    async def ingest(self, catalog=None, payload=None):
        await w.add_file_handler(dict(payload or PAYLOAD), SETTINGS, self.db, catalog=catalog)


class Catalog:
    def __init__(self):
        self.refreshes = 0

    async def refresh(self):
        self.refreshes += 1


@pytest.mark.asyncio
async def test_a_first_ingest_records_the_new_document_and_deletes_nothing(monkeypatch):
    db = FakeDB()
    world = World(monkeypatch, db)
    catalog = Catalog()

    await world.ingest(catalog)

    assert db.row["vector_store_file_id"] == "kb-1" and world.active == {"kb-1"}
    assert world.deleted == []
    assert db.statuses == [ProcessingStatus.PROCESSING.value, ProcessingStatus.COMPLETED.value]
    assert catalog.refreshes == 1


@pytest.mark.asyncio
async def test_a_rerun_after_success_leaves_exactly_one_reachable_document(monkeypatch):
    db = FakeDB()
    world = World(monkeypatch, db)

    await world.ingest()
    await world.ingest()  # a task taken back from an expired lease, or delivered twice

    assert db.row["vector_store_file_id"] == "kb-2"
    assert world.deleted == ["kb-1"]
    assert world.active == {"kb-2"}
    # deleting the file deletes what the row points at - and nothing is left behind
    world.active.discard(db.row["vector_store_file_id"])
    assert world.active == set()


@pytest.mark.asyncio
async def test_a_failure_after_indexing_removes_this_attempts_document_and_reraises(monkeypatch):
    db = FakeDB(kb_file_id="kb-old")
    db.upsert_error = RuntimeError("db down")
    world = World(monkeypatch, db)
    world.active.add("kb-old")

    with pytest.raises(RuntimeError, match="db down"):
        await world.ingest()

    assert world.deleted == ["kb-1"]  # this attempt's, not the one the row still points at
    assert world.active == {"kb-old"}
    assert db.row["vector_store_file_id"] == "kb-old"


@pytest.mark.asyncio
async def test_a_cancellation_after_indexing_still_removes_this_attempts_document(monkeypatch):
    db = FakeDB()
    db.upsert_gate = asyncio.Event()  # the task is held inside the write-back
    world = World(monkeypatch, db)
    task = asyncio.create_task(world.ingest())
    while not world.indexed:
        await asyncio.sleep(0)
    await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert world.deleted == ["kb-1"] and world.active == set()


@pytest.mark.asyncio
async def test_a_file_deleted_while_it_was_ingested_stays_deleted_and_its_chunks_are_removed(monkeypatch):
    db = FakeDB()
    world = World(monkeypatch, db, on_indexed=lambda kb_id: db.row.update(is_active=False))
    catalog = Catalog()

    await world.ingest(catalog)  # not an error: nothing to retry

    assert db.upserts == []  # upsert_file would have made it active again
    assert world.deleted == ["kb-1"] and world.active == set()
    assert ProcessingStatus.COMPLETED.value not in db.statuses
    assert catalog.refreshes == 0


@pytest.mark.asyncio
async def test_a_file_whose_row_vanished_while_it_was_ingested_is_treated_the_same(monkeypatch):
    db = FakeDB()
    world = World(monkeypatch, db, on_indexed=lambda kb_id: setattr(db, "exists", False))

    await world.ingest()

    assert db.upserts == [] and world.deleted == ["kb-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("db", [FakeDB(is_active=False), FakeDB(exists=False)], ids=["inactive", "missing"])
async def test_a_file_already_deleted_is_not_ingested_at_all(monkeypatch, db):
    world = World(monkeypatch, db)

    await world.ingest()

    assert world.parsed == 0 and world.indexed == [] and db.upserts == [] and db.statuses == []


@pytest.mark.asyncio
async def test_the_document_a_rerun_replaces_is_removed_and_a_newer_one_it_meets_too(monkeypatch):
    """Two attempts overlapped: the other one recorded kb-x after this one started."""
    db = FakeDB(kb_file_id="kb-old")
    world = World(monkeypatch, db, on_indexed=lambda kb_id: db.row.update(vector_store_file_id="kb-x"))
    world.active.update({"kb-old", "kb-x"})

    await world.ingest()

    assert db.row["vector_store_file_id"] == "kb-1"
    assert sorted(world.deleted) == ["kb-old", "kb-x"]
    assert world.active == {"kb-1"}


@pytest.mark.asyncio
async def test_a_failed_cleanup_is_swallowed_after_a_success(monkeypatch):
    db = FakeDB(kb_file_id="kb-old")
    world = World(monkeypatch, db, delete_error=RuntimeError("vector store down"))

    await world.ingest()  # does not raise

    assert db.row["vector_store_file_id"] == "kb-1"
    assert db.statuses[-1] == ProcessingStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_a_failed_cleanup_does_not_replace_the_error_that_failed_the_task(monkeypatch):
    db = FakeDB()
    db.upsert_error = RuntimeError("db down")
    world = World(monkeypatch, db, delete_error=RuntimeError("vector store down"))

    with pytest.raises(RuntimeError, match="db down"):
        await world.ingest()


@pytest.mark.asyncio
async def test_a_failure_before_indexing_has_nothing_to_clean_up(monkeypatch):
    db = FakeDB()
    world = World(monkeypatch, db, index_error=RuntimeError("index failed"))

    with pytest.raises(RuntimeError, match="index failed"):
        await world.ingest()

    assert world.deleted == []


@pytest.mark.asyncio
async def test_a_task_without_a_file_id_is_rejected_not_ingested_into_an_arbitrary_row(monkeypatch):
    db = FakeDB()
    world = World(monkeypatch, db)

    with pytest.raises(ValueError, match="file_id"):
        await w.add_file_handler({**PAYLOAD, "file_id": None}, SETTINGS, db)

    assert world.parsed == 0


# --- the catalog refresh runs outside the task ---

@pytest.mark.asyncio
async def test_a_catalog_refresh_returns_at_once_and_runs_in_the_background():
    release = asyncio.Event()

    class SlowCatalog:
        runs = 0

        async def refresh(self):
            SlowCatalog.runs += 1
            await release.wait()

    background = w.BackgroundCatalogRefresh(SlowCatalog())
    await asyncio.wait_for(background.refresh(), timeout=1)  # not blocked by the slow describer
    await asyncio.sleep(0)
    assert SlowCatalog.runs == 1

    release.set()
    await background._task
    await background.close()


@pytest.mark.asyncio
async def test_at_most_one_refresh_runs_and_a_request_made_meanwhile_earns_one_more_pass():
    gate = asyncio.Event()
    running, peak, runs = 0, 0, 0

    class Catalog:
        async def refresh(self):
            nonlocal running, peak, runs
            running += 1
            peak = max(peak, running)
            runs += 1
            await gate.wait()
            running -= 1

    background = w.BackgroundCatalogRefresh(Catalog())
    await background.refresh()
    await asyncio.sleep(0)
    await background.refresh()
    await background.refresh()  # several requests while one runs make one more pass, not several
    gate.set()
    await background._task

    assert runs == 2 and peak == 1


@pytest.mark.asyncio
async def test_a_failing_refresh_is_logged_not_raised():
    class Broken:
        async def refresh(self):
            raise RuntimeError("llm down")

    background = w.BackgroundCatalogRefresh(Broken())
    await background.refresh()
    await background._task  # finishes normally
    await background.close()


@pytest.mark.asyncio
async def test_closing_stops_a_refresh_still_running():
    class Hung:
        async def refresh(self):
            await asyncio.sleep(60)

    background = w.BackgroundCatalogRefresh(Hung())
    await background.refresh()
    await asyncio.sleep(0)
    await background.close()

    assert background._task.done()


# --- the worker connects to the queue however long it takes ---

@pytest.mark.asyncio
async def test_the_worker_retries_the_first_connection_instead_of_dying(monkeypatch):
    import redis.exceptions as redis_exceptions

    attempts = []
    sleeps = []

    class Queue:
        async def initialize(self):
            attempts.append(1)
            if len(attempts) == 1:
                raise redis_exceptions.ConnectionError("refused")
            if len(attempts) == 2:
                raise redis_exceptions.AuthenticationError("WRONGPASS")

        async def run_worker(self, **kwargs):
            self.ran = True

        async def close(self):
            self.closed = True

    async def fake_sleep(delay):
        sleeps.append(delay)

    queue = Queue()
    monkeypatch.setattr(w, "create_queue_service", lambda settings: queue)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    settings = SimpleNamespace(
        WORKER_LEASE_SECONDS=900, RPC_TIMEOUT_SECONDS=60, CHATBOT_QUEUE_NAME="q", WORKER_BRPOP_TIMEOUT=5,
        WORKER_ERROR_RETRY_DELAY=7, WORKER_ERROR_MESSAGE_MAX_CHARS=1000,
    )

    await w.run_kb_worker(settings, db_service=None, catalog=None)

    assert len(attempts) == 3 and sleeps == [7, 7]
    assert queue.ran and queue.closed


@pytest.mark.asyncio
async def test_cancelling_the_worker_while_it_waits_for_the_queue_stops_it(monkeypatch):
    class Queue:
        closed = False

        async def initialize(self):
            raise ConnectionError("refused")

        async def close(self):
            self.closed = True

    queue = Queue()
    monkeypatch.setattr(w, "create_queue_service", lambda settings: queue)
    settings = SimpleNamespace(
        WORKER_LEASE_SECONDS=900, RPC_TIMEOUT_SECONDS=60, CHATBOT_QUEUE_NAME="q", WORKER_BRPOP_TIMEOUT=5,
        WORKER_ERROR_RETRY_DELAY=3600, WORKER_ERROR_MESSAGE_MAX_CHARS=1000,
    )
    task = asyncio.create_task(w.run_kb_worker(settings, db_service=None))
    await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert queue.closed


VERSIONED = {**PAYLOAD, "document_key": "b", "version_no": 2, "version_label": "2.0"}


@pytest.mark.asyncio
async def test_a_versioned_ingest_indexes_not_latest_then_elects_the_latest_once_recorded(monkeypatch):
    synced = []

    async def sync(db, settings, document_key):
        synced.append((document_key, db.row["vector_store_file_id"]))  # the row is already recorded when this runs

    monkeypatch.setattr(w, "sync_document", sync)
    db = FakeDB()
    world = World(monkeypatch, db)

    await world.ingest(payload=VERSIONED)

    assert world.metadata[0]["is_latest"] is False and world.metadata[0]["document_key"] == "b"
    assert world.metadata[0]["version_no"] == 2 and world.metadata[0]["stored_file_path"] == "a/b.pdf"
    assert synced == [("b", "kb-1")]


@pytest.mark.asyncio
async def test_a_failed_election_fails_the_task_so_it_retries_rather_than_leave_the_file_invisible(monkeypatch):
    async def sync(db, settings, document_key):
        raise RuntimeError("compute service down")

    monkeypatch.setattr(w, "sync_document", sync)
    world = World(monkeypatch, FakeDB())

    with pytest.raises(RuntimeError, match="compute service down"):
        await world.ingest(payload=VERSIONED)


@pytest.mark.asyncio
async def test_an_unversioned_ingest_needs_no_election(monkeypatch):
    async def sync(db, settings, document_key):
        assert document_key is None

    monkeypatch.setattr(w, "sync_document", sync)
    world = World(monkeypatch, FakeDB())

    await world.ingest()

    assert "is_latest" not in world.metadata[0]
