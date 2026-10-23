"""MongoEmbeddingStore's search core and index admin, against an in-memory fake of the driver.

The pure parts (pipelines, fusion, BM25) have their own tests in tests/logic; this
pins how the store strings them together: which path a search takes, what it does
when Atlas or an index is missing, and what is shared between concurrent searches.
"""
import asyncio
from types import SimpleNamespace

import pytest
from app.services.vector.mongo_service import MongoEmbeddingStore
from pymongo.errors import OperationFailure

DIM = 3


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, length=None):
        await asyncio.sleep(0)  # a real read yields, which is what lets searches overlap
        return self.docs if length is None else self.docs[:length]


class FakeCollection:
    name = "kb"

    def __init__(self, docs=()):
        self.docs = list(docs)
        self.aggregates, self.finds, self.created_search, self.created_index = [], 0, [], []
        self.aggregate_result = lambda pipeline: []
        self.list_indexes_result = []
        self.create_search_error = None
        self.create_index_error = None

    def aggregate(self, pipeline):
        self.aggregates.append(pipeline)
        return FakeCursor(self.aggregate_result(pipeline))

    def find(self, query, projection=None, **kwargs):
        self.finds += 1
        return FakeCursor(self.docs)

    async def find_one(self, query, projection=None, sort=None):
        return max(self.docs, key=lambda d: d["_id"], default=None)

    async def count_documents(self, query):
        return len(self.docs)

    def list_search_indexes(self, name):
        if isinstance(self.list_indexes_result, Exception):
            raise self.list_indexes_result
        return FakeCursor(self.list_indexes_result)

    async def create_search_index(self, model):
        if self.create_search_error:
            raise self.create_search_error
        self.created_search.append(model)

    async def create_index(self, keys, **kwargs):
        if self.create_index_error:
            raise self.create_index_error
        self.created_index.append((keys, kwargs))

    async def insert_many(self, docs, ordered):
        return type("R", (), {"inserted_ids": list(range(len(docs)))})()

    async def update_many(self, query, update):
        self.updates = getattr(self, "updates", []) + [(query, update)]
        return type("R", (), {"modified_count": 2, "matched_count": 3})()


class FakeDb:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, name):
        return self.collection


def chunk(uuid, text, vector, _id=0):
    return {"_id": _id, "textContent": text, "vectorContent": vector, "metadata": {"uuid": uuid, "isactive": True}}


def hit(uuid, text="t", score=1.0):
    return {"id": uuid, "textContent": text, "metadata": {"uuid": uuid}, "score": score}


def make_store(collection, lexical_search="bm25", **overrides):
    settings = SimpleNamespace(
        MONGO_CONNECTION_STRING=SimpleNamespace(get_secret_value=lambda: "mongodb://localhost"),
        MONGO_DATABASE_NAME="db", VECTOR_SEARCH_EXACT=False, EMBEDDING_DIMENSION=DIM, LEXICAL_SEARCH=lexical_search,
        VECTOR_OVERFETCH_MULTIPLIER=10, HYBRID_FUSION_DEPTH_MULTIPLIER=4, HYBRID_FUSION_DEPTH_MIN=40,
        HYBRID_LEXICAL_WEIGHT=0.5, HYBRID_RRF_K=10, MONGO_SERVER_SELECTION_TIMEOUT_MS=5000, BM25_K1=1.5, BM25_B=0.75,
        VECTOR_INDEX_NAME="vector_index", TEXT_INDEX_NAME="text_search_index",
    )
    for name, value in overrides.items():
        setattr(settings, name, value)
    store = MongoEmbeddingStore(settings, default_collection_name="kb")
    store.db = FakeDb(collection)
    return store


@pytest.fixture(autouse=True)
def atlas_available(monkeypatch):
    monkeypatch.setattr(MongoEmbeddingStore, "_atlas_vector_search_unavailable", False)


def atlas_says_no():
    return OperationFailure("$vectorSearch is only allowed on MongoDB Atlas", code=6047401)


CORPUS = [
    chunk("near", "vacation policy for staff", [1, 0, 0], 1),
    chunk("mid", "expense reports and travel", [0.6, 0.8, 0], 2),
    chunk("far", "office parking rules", [0, 0, 1], 3),
]


# ------------------------------------------------------------ vector search

async def test_without_query_text_only_the_vector_search_runs_and_is_capped_to_limit():
    coll = FakeCollection()
    coll.aggregate_result = lambda p: [hit("a"), hit("b"), hit("c")]
    result = await make_store(coll).similarity_search_on_query([1, 0, 0], "kb", limit=2)
    assert [h["id"] for h in result] == ["a", "b"]
    assert len(coll.aggregates) == 1 and "$vectorSearch" in coll.aggregates[0][0]


async def test_the_atlas_stage_gets_the_configured_index_limit_and_candidate_pool():
    coll = FakeCollection()
    await make_store(coll).similarity_search_on_query([1, 0, 0], "kb", limit=5)
    stage = coll.aggregates[0][0]["$vectorSearch"]
    assert (stage["index"], stage["path"], stage["limit"], stage["numCandidates"]) == ("vector_index", "vectorContent", 5, 50)


async def test_a_query_vector_of_the_wrong_size_returns_nothing_without_asking_the_database():
    coll = FakeCollection()
    assert await make_store(coll).similarity_search_on_query([1, 0], "kb", limit=5) == []
    assert coll.aggregates == []


async def test_a_plain_mongodb_falls_back_to_in_process_cosine_and_remembers_it():
    coll = FakeCollection(CORPUS)

    def refuse(pipeline):
        raise atlas_says_no()

    coll.aggregate_result = refuse
    store = make_store(coll)
    first = await store.similarity_search_on_query([1, 0, 0], "kb", limit=2)
    assert [h["id"] for h in first] == ["near", "mid"]
    assert first[0]["score"] == pytest.approx(1.0) and first[1]["score"] == pytest.approx(0.6)
    assert MongoEmbeddingStore._atlas_vector_search_unavailable is True
    asked = len(coll.aggregates)
    await store.similarity_search_on_query([1, 0, 0], "kb", limit=2)
    assert len(coll.aggregates) == asked  # $vectorSearch is not tried again


async def test_any_other_database_error_yields_no_hits_and_does_not_flip_the_flag():
    coll = FakeCollection(CORPUS)

    def broken(pipeline):
        raise OperationFailure("boom", code=1)

    coll.aggregate_result = broken
    assert await make_store(coll).similarity_search_on_query([1, 0, 0], "kb", limit=2) == []
    assert MongoEmbeddingStore._atlas_vector_search_unavailable is False


async def test_fallback_ignores_rows_whose_vector_is_the_wrong_size_and_an_empty_corpus_is_empty():
    coll = FakeCollection(CORPUS + [chunk("bad", "x", [1, 0], 4), {"_id": 5, "textContent": "no vector", "metadata": {}}])
    coll.aggregate_result = lambda p: (_ for _ in ()).throw(atlas_says_no())
    ids = [h["id"] for h in await make_store(coll).similarity_search_on_query([1, 0, 0], "kb", limit=10)]
    assert sorted(ids) == ["far", "mid", "near"]
    empty = FakeCollection()
    empty.aggregate_result = lambda p: (_ for _ in ()).throw(atlas_says_no())
    assert await make_store(empty).similarity_search_on_query([1, 0, 0], "kb", limit=10) == []


async def test_a_read_error_in_the_cosine_fallback_costs_only_the_vector_half(caplog):
    coll = FakeCollection(CORPUS)

    def route(pipeline):
        if "$vectorSearch" in pipeline[0]:
            raise atlas_says_no()
        return [hit("lexical")]

    coll.aggregate_result = route

    class BrokenCursor:
        async def to_list(self, length=None):
            raise ConnectionError("read timed out")

    coll.find = lambda query, projection=None, **kwargs: BrokenCursor()
    store = make_store(coll, lexical_search="text")
    assert await store.similarity_search_on_query([1, 0, 0], "kb", limit=5) == []
    result = await store.similarity_search_on_query([1, 0, 0], "kb", limit=5, query_text="q")
    assert [h["id"] for h in result] == ["lexical"]
    assert "read timed out" in caplog.text


async def test_the_cosine_fallback_is_engaged_by_a_server_that_does_not_know_the_stage():
    coll = FakeCollection(CORPUS)
    coll.aggregate_result = lambda p: (_ for _ in ()).throw(
        OperationFailure("Unrecognized pipeline stage name: '$vectorSearch'", code=40324))
    result = await make_store(coll).similarity_search_on_query([1, 0, 0], "kb", limit=1)
    assert [h["id"] for h in result] == ["near"]


async def test_concurrent_fallback_searches_read_the_collection_once():
    coll = FakeCollection(CORPUS)
    coll.aggregate_result = lambda p: (_ for _ in ()).throw(atlas_says_no())
    store = make_store(coll)
    await asyncio.gather(*(store.similarity_search_on_query([1, 0, 0], "kb", limit=1) for _ in range(4)))
    assert coll.finds == 1
    await store.similarity_search_on_query([1, 0, 0], "kb", limit=1)
    assert coll.finds == 2  # nothing is kept between turns


# ------------------------------------------------------------ hybrid search

async def test_hybrid_fetches_both_lists_deeper_than_the_limit_and_fuses_them():
    coll = FakeCollection(CORPUS)

    def route(pipeline):
        if "$vectorSearch" in pipeline[0]:
            return [hit("near"), hit("mid")]
        return []

    coll.aggregate_result = route
    store = make_store(coll, lexical_search="text")
    coll.aggregates.clear()
    await store.similarity_search_on_query([1, 0, 0], "kb", limit=3, query_text="vacation")
    depths = {("vec" if "$vectorSearch" in p[0] else "text"): (p[0].get("$vectorSearch") or {}).get("limit", p[-1].get("$limit"))
              for p in coll.aggregates}
    assert depths == {"vec": 40, "text": 40}  # max(3 * 4, 40)
    coll.aggregates.clear()
    await store.similarity_search_on_query([1, 0, 0], "kb", limit=20, query_text="vacation")
    assert {(p[0].get("$vectorSearch") or {}).get("limit") for p in coll.aggregates} == {80, None}


async def test_hybrid_reorders_by_rank_fusion_and_scores_are_fused_scores():
    coll = FakeCollection(CORPUS)

    def route(pipeline):
        if "$vectorSearch" in pipeline[0]:
            return [hit("a"), hit("b")]
        return [hit("b"), hit("c")]

    coll.aggregate_result = route
    result = await make_store(coll, lexical_search="text").similarity_search_on_query(
        [1, 0, 0], "kb", limit=3, query_text="q")
    assert [h["id"] for h in result] == ["b", "a", "c"]
    assert result[0]["score"] == pytest.approx(1 / 12 + 0.5 / 11)


async def test_hybrid_with_no_lexical_hits_is_the_vector_hits_capped_to_limit():
    coll = FakeCollection(CORPUS)
    coll.aggregate_result = lambda p: [hit("a"), hit("b"), hit("c")] if "$vectorSearch" in p[0] else []
    result = await make_store(coll, lexical_search="text").similarity_search_on_query(
        [1, 0, 0], "kb", limit=2, query_text="q")
    assert [h["id"] for h in result] == ["a", "b"]


# ---------------------------------------------------------------- lexical

async def test_bm25_ranks_in_process_and_reuses_its_index_until_the_corpus_changes():
    coll = FakeCollection(CORPUS)
    coll.aggregate_result = lambda p: [] if "$vectorSearch" in p[0] else pytest.fail("$text must not run")
    store = make_store(coll)
    result = await store._lexical("vacation", "kb", 5)
    assert [h["id"] for h in result] == ["near"]
    builds = coll.finds
    await store._lexical("expense", "kb", 5)
    assert coll.finds == builds  # same active count and newest id: no re-read
    coll.docs.append(chunk("new", "vacation days", [0, 1, 0], 9))
    assert {h["id"] for h in await store._lexical("vacation", "kb", 5)} == {"near", "new"}
    assert coll.finds == builds + 1


async def test_concurrent_bm25_searches_build_the_index_once():
    coll = FakeCollection(CORPUS)
    store = make_store(coll)
    await asyncio.gather(*(store._lexical("vacation", "kb", 5) for _ in range(4)))
    assert coll.finds == 1


async def test_a_failing_bm25_falls_back_to_the_text_index():
    coll = FakeCollection(CORPUS)
    coll.find = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("read failed"))
    coll.aggregate_result = lambda p: [hit("via-text")]
    result = await make_store(coll)._lexical("vacation", "kb", 5)
    assert [h["id"] for h in result] == ["via-text"]
    assert "$text" in coll.aggregates[0][0]["$match"]


async def test_lexical_text_mode_goes_straight_to_the_text_index():
    coll = FakeCollection(CORPUS)
    coll.aggregate_result = lambda p: [hit("via-text")]
    assert [h["id"] for h in await make_store(coll, lexical_search="text")._lexical("q", "kb", 5)] == ["via-text"]
    assert coll.finds == 0


@pytest.mark.parametrize("error", [OperationFailure("no text index", code=27), RuntimeError("unexpected")])
async def test_a_text_search_failure_is_no_hits_not_an_error(error):
    coll = FakeCollection()

    def fail(pipeline):
        raise error

    coll.aggregate_result = fail
    assert await make_store(coll, lexical_search="text")._lexical("q", "kb", 5) == []


# -------------------------------------------------------------- connection

async def test_a_failed_connect_closes_the_client_it_opened(monkeypatch):
    from app.services.vector import mongo_service

    clients = []

    class Admin:
        async def command(self, name):
            raise ConnectionError("no server")

    class Client:
        def __init__(self, uri, **kwargs):
            self.admin, self.closed = Admin(), False
            clients.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(mongo_service.motor.motor_asyncio, "AsyncIOMotorClient", Client)
    store = make_store(FakeCollection())
    store.db = None
    assert await store.connect() is False
    assert len(clients) == 1 and clients[0].closed
    assert store._client is None and store.db is None


# ------------------------------------------------------------- index admin

async def test_create_vector_index_submits_the_atlas_index_and_the_text_index():
    coll = FakeCollection()
    assert await make_store(coll).create_vector_index("kb") is True
    (model,) = coll.created_search
    assert model.document["name"] == "vector_index" and model.document["type"] == "vectorSearch"
    assert model.document["definition"]["fields"][0]["numDimensions"] == DIM
    (keys, kwargs), = coll.created_index
    assert keys == [("textContent", "text")] and kwargs == {"name": "text_search_index", "default_language": "none"}


async def test_an_existing_vector_index_is_left_alone():
    coll = FakeCollection()
    coll.list_indexes_result = [{"name": "vector_index"}]
    assert await make_store(coll).create_vector_index("kb") is True
    assert coll.created_search == []


OLD_INDEX = {
    "name": "vector_index", "type": "vectorSearch",
    "latestDefinition": {"fields": [{"type": "vector", "path": "vectorContent", "numDimensions": DIM, "similarity": "cosine"}]},
}


async def test_a_new_index_is_created_with_the_isactive_filter_field():
    coll = FakeCollection()
    await make_store(coll).create_vector_index("kb")
    assert {"type": "filter", "path": "metadata.isactive"} in coll.created_search[0].document["definition"]["fields"]


async def test_an_existing_index_without_the_filter_warns_once_and_is_not_touched(monkeypatch, caplog):
    monkeypatch.setattr(MongoEmbeddingStore, "_warned_missing_filter", set())
    coll = FakeCollection()
    coll.list_indexes_result = [OLD_INDEX]
    store = make_store(coll)
    dropped = []
    coll.drop_search_index = lambda name: dropped.append(name)
    with caplog.at_level("WARNING"):
        assert await store.create_vector_index("kb") is True
        assert await store.create_vector_index("kb") is True  # every Index RPC runs this
    warnings = [r for r in caplog.records if "metadata.isactive" in r.getMessage()]
    assert len(warnings) == 1 and "--rebuild-index" in warnings[0].getMessage()
    assert coll.created_search == [] and dropped == []


async def test_an_existing_index_with_the_filter_field_does_not_warn(monkeypatch, caplog):
    monkeypatch.setattr(MongoEmbeddingStore, "_warned_missing_filter", set())
    coll = FakeCollection()
    coll.list_indexes_result = [
        {**OLD_INDEX, "latestDefinition": {"fields": [*OLD_INDEX["latestDefinition"]["fields"],
                                                      {"type": "filter", "path": "metadata.isactive"}]}}
    ]
    with caplog.at_level("WARNING"):
        assert await make_store(coll).create_vector_index("kb") is True
    assert not [r for r in caplog.records if "metadata.isactive" in r.getMessage()]


async def test_a_deployment_that_cannot_list_search_indexes_still_gets_a_create_attempt():
    coll = FakeCollection()
    coll.list_indexes_result = atlas_says_no()
    assert await make_store(coll).create_vector_index("kb") is True
    assert len(coll.created_search) == 1


@pytest.mark.parametrize("error, expected", [
    (OperationFailure("only allowed on MongoDB Atlas", code=6047401), True),
    (OperationFailure("Index already exists with name vector_index", code=68), True),
    (OperationFailure("nope", code=1), False),
    (RuntimeError("unexpected"), False),
])
async def test_create_search_index_outcomes(error, expected):
    coll = FakeCollection()
    coll.create_search_error = error
    assert await make_store(coll).create_vector_index("kb") is expected


async def test_a_text_index_failure_degrades_to_vector_only_and_still_succeeds():
    coll = FakeCollection()
    coll.create_index_error = RuntimeError("cannot build")
    assert await make_store(coll).create_vector_index("kb") is True


async def test_index_admin_without_a_connection_is_false():
    store = make_store(FakeCollection())
    store.db = None
    assert await store.create_vector_index("kb") is False


# -------------------------------------------------------------------- CRUD

async def test_insert_skips_bad_vectors_and_reports_none_when_nothing_is_valid():
    store = make_store(FakeCollection())
    ids, count = await store.insert_data([chunk("a", "x", [1, 0, 0]), chunk("b", "y", [1])], "kb")
    assert count == 1
    assert await store.insert_data([chunk("b", "y", [1])], "kb") == (None, 0)
    assert await store.insert_data([], "kb") == ([], 0)


async def test_set_latest_returns_how_many_chunks_matched_and_writes_only_the_fields_given():
    collection = FakeCollection()
    store = make_store(collection)
    assert await store.set_latest_by_source_id("file-1", True, "kb") == 3
    assert await store.set_latest_by_source_id("file-1", False, "kb", version_no=2, version_label="2.1", document_key="policy") == 3
    assert collection.updates == [
        ({"metadata.source_file_id": "file-1"}, {"$set": {"metadata.is_latest": True}}),
        ({"metadata.source_file_id": "file-1"}, {"$set": {
            "metadata.is_latest": False, "metadata.version_no": 2, "metadata.version_label": "2.1", "metadata.document_key": "policy"}}),
    ]


async def test_deactivate_returns_how_many_chunks_changed():
    assert await make_store(FakeCollection()).deactivate_data_by_source_id("file-1", "kb") == 2
