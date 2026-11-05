import asyncio
import logging
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

import motor.motor_asyncio
import numpy as np
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from app.config import Settings
from app.logic.fusion import (
    cosine_top_k,
    fusion_depth,
    reciprocal_rank_fusion,
    to_hit,
    usable_docs,
)
from app.logic.index_health import stored_vector_summary
from app.logic.lexical import Bm25Index
from app.logic.mongo_queries import (
    ACTIVE,
    IS_COMPARISON_FIELD,
    IS_LATEST_FIELD,
    TEXT_FIELD,
    VECTOR_FIELD,
    clean_uri,
    dense_corpus_query,
    is_atlas_search_unavailable,
    is_index_already_exists,
    lacks_active_filter,
    lacks_version_filter,
    redact_uri,
    split_valid_items,
    text_corpus_query,
    text_search_pipeline,
    vector_index_definition,
    vector_search_pipeline,
    version_filter_for,
)
from app.logic.versions import Mode, VersionScope
from app.services.vector.vector_store import VectorStore

logger = logging.getLogger(__name__)


def _mql_matches_hit(hit: dict[str, Any], version_filter: dict[str, Any]) -> bool:
    """Evaluate the simple MQL dicts produced by version_filter_for() against a hit.
    Supports equality and {$ne: v} / {$eq: v} — the only operators we emit."""
    meta = hit.get("metadata") or {}
    for key, cond in version_filter.items():
        field = key[len("metadata."):] if key.startswith("metadata.") else key
        val = meta.get(field)
        if isinstance(cond, dict):
            if "$ne" in cond and val == cond["$ne"]:
                return False
            if "$eq" in cond and val != cond["$eq"]:
                return False
        elif val != cond:
            return False
    return True


class MongoEmbeddingStore(VectorStore):
    """
    MongoDB implementation of VectorStore, using Motor.

    Hybrid search: Atlas Vector Search (or an in-process cosine fallback where
    Atlas is not available) fused with a lexical half - in-process BM25 or
    MongoDB's $text index - by Reciprocal Rank Fusion. Every tuning number is a
    Settings field (see config.py); the pipelines, ranking and fusion maths are in
    logic/. See app/services/vector/vector_store.py for the contract another backend
    would have to meet.
    """

    backend_name = "MongoDB"

    # Set once $vectorSearch has been refused as unsupported (plain community
    # MongoDB). It lives on the class because a fresh store can be built per call:
    # an instance attribute would be forgotten each time, and every search would
    # pay for a doomed $vectorSearch round trip and the exception handling before
    # reaching the fallback it always ends up in.
    _atlas_vector_search_unavailable: bool = False

    # True once we confirm the Atlas index declares is_latest and version_no as
    # filter fields. When False, version filtering falls back to post-filtering with
    # over-fetch (the original behaviour). Set in _ensure_vector_search_index.
    _version_filter_in_index: bool = False

    # Collections whose existing vector index was already reported as lacking the
    # isactive filter: create_vector_index runs on every Index call, and the warning
    # is for the operator once, not once per uploaded file.
    _warned_missing_filter: set = set()

    def __init__(self, settings: Settings, default_collection_name: str):
        self.settings = settings
        self.uri = clean_uri(settings.MONGO_CONNECTION_STRING.get_secret_value())
        self.db_name = settings.MONGO_DATABASE_NAME
        self.default_collection_name = default_collection_name
        # The vector index is created with this many dimensions and it must match
        # the configured embedding model - a mismatch fails every write and query.
        self.dimension = settings.EMBEDDING_DIMENSION
        self._client: motor.motor_asyncio.AsyncIOMotorClient | None = None
        self.db: motor.motor_asyncio.AsyncIOMotorDatabase | None = None
        # Work shared between concurrent searches, keyed by what it is for.
        self._corpus_loads: dict[Hashable, asyncio.Future] = {}
        self._bm25_builds: dict[Hashable, asyncio.Future] = {}
        # collection -> (signature, index, documents); rebuilt when the signature moves.
        self._bm25: dict[str, tuple[Any, Bm25Index, list[dict[str, Any]]]] = {}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Opens the connection. Returns True if the server answered."""
        if self.db is not None:
            return True
        log_uri = redact_uri(self.uri)
        try:
            self._client = motor.motor_asyncio.AsyncIOMotorClient(
                self.uri, serverSelectionTimeoutMS=self.settings.MONGO_SERVER_SELECTION_TIMEOUT_MS
            )
            # The ismaster command is cheap and does not require auth.
            await self._client.admin.command("ismaster")
            self.db = self._client[self.db_name]
            logger.info(f"Connected to MongoDB '{self.db_name}' at {log_uri}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB at {log_uri}. Details: {e}", exc_info=True)
            # A client that never answered still owns background monitor threads.
            if self._client is not None:
                self._client.close()
            self._client = None
            self.db = None
            return False

    def close(self):
        if self._client:
            logger.info("Closing MongoDB connection.")
            self._client.close()
            self._client = None
            self.db = None

    def _coll(self, collection_name: str | None = None) -> motor.motor_asyncio.AsyncIOMotorCollection | None:
        """The collection (the default one if no name is given), or None if not
        connected. MongoDB creates a collection on first write."""
        if self.db is None:
            logger.error("Cannot get collection: Not connected to MongoDB. Call connect() first.")
            return None
        return self.db[collection_name or self.default_collection_name]

    # ------------------------------------------------------------------
    # Index admin
    # ------------------------------------------------------------------

    async def create_vector_index(self, collection_name: str | None = None) -> bool:
        """Ensures the Atlas vector index and the text index exist on the collection.

        Runs on every Index call (once per uploaded file), so an index that is
        already there is the normal, silent, successful case. Requires Atlas and a
        user allowed to create search indexes; creation can take a while on a large
        collection. True if the index exists, was submitted, or Atlas is not there
        at all (the in-process fallback applies); False only on a genuine failure.
        """
        collection = self._coll(collection_name)
        if collection is None or not await self._ensure_vector_search_index(collection):
            return False

        # A standard MongoDB text index for the lexical half. Unlike Atlas
        # $vectorSearch it is a core server feature, so hybrid search works on
        # plain community MongoDB too.
        #
        # default_language="none" turns off MongoDB's stemming/stopword removal
        # instead of defaulting to English rules: chunks are not tagged with a
        # language and this store is used with a multilingual embedding model, so
        # English-only stemming would silently mis-handle every other language.
        #
        # create_index is idempotent by name. A genuine failure degrades to
        # vector-only search rather than failing the upload: _text_search already
        # tolerates a missing or unbuilt index at query time.
        try:
            await collection.create_index(
                [(TEXT_FIELD, "text")], name=self.settings.TEXT_INDEX_NAME, default_language="none"
            )
        except Exception as e:
            logger.warning(
                f"Could not ensure text search index '{self.settings.TEXT_INDEX_NAME}' on "
                f"'{collection.name}' - hybrid search will fall back to "
                f"vector-only until this is resolved. Details: {e}"
            )
        return True

    async def _ensure_vector_search_index(self, collection: motor.motor_asyncio.AsyncIOMotorCollection) -> bool:
        """Creates the Atlas $vectorSearch index unless it exists.

        True if it existed, was just created, or this is not Atlas (no search
        index to create); False only on a genuine, unexpected failure.
        """
        name = self.settings.VECTOR_INDEX_NAME
        # Lets the common case (already provisioned) skip the create call. Where the
        # listing itself fails (plain community MongoDB has no such command), fall
        # through to create, which reports "not Atlas" or "already exists" itself.
        try:
            existing = await collection.list_search_indexes(name).to_list(length=1)
        except Exception:
            existing = []
        if existing:
            self._warn_if_filter_missing(existing[0], collection.name)
            if not lacks_version_filter(existing[0]):
                MongoEmbeddingStore._version_filter_in_index = True
            return True

        logger.info(f"Creating Atlas Vector Search index '{name}' on '{self.db_name}.{collection.name}' "
                     f"(field '{VECTOR_FIELD}', dim {self.dimension}, cosine).")
        try:
            await collection.create_search_index(SearchIndexModel(
                definition=vector_index_definition(VECTOR_FIELD, self.dimension), name=name, type="vectorSearch",
            ))
            logger.info(f"Submitted the create command for search index '{name}'. Index building may take time.")
            MongoEmbeddingStore._version_filter_in_index = True
            return True
        except OperationFailure as e:
            if is_atlas_search_unavailable(e):
                logger.warning(
                    f"Atlas Search index creation is not available on this MongoDB deployment "
                    f"(plain community server, not Atlas). Documents are still inserted with "
                    f"embeddings and will be queried via in-process fallback search. Details: {e}"
                )
                return True
            if is_index_already_exists(e):  # raced the existence check above
                return True
            logger.error(f"Could not create search index '{name}' on '{collection.name}'. Details: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during search index creation: {e}", exc_info=True)
            return False

    def _warn_if_filter_missing(self, index: dict[str, Any], collection_name: str) -> None:
        """One warning per collection for a vector index built before the isactive
        filter field was added. It is never dropped here: rebuilding takes the index
        offline while it builds, which is the operator's call."""
        if not lacks_active_filter(index) or collection_name in MongoEmbeddingStore._warned_missing_filter:
            return
        MongoEmbeddingStore._warned_missing_filter.add(collection_name)
        logger.warning(
            f"The vector index '{index.get('name')}' on '{collection_name}' does not declare "
            f"'metadata.isactive' as a filter field, so $vectorSearch is rejected and search runs on "
            f"the lexical half alone. Rebuild it once with: python -m app.tools.reindex --rebuild-index "
            f"--collection {collection_name} (drops and recreates the index; there is no vector half "
            f"until it finishes building)."
        )

    async def rebuild_vector_index(self, collection_name: str | None = None) -> bool:
        """Drops and recreates the vector index at the configured dimension.

        Needed after the dimension changes: an Atlas vector index is created for
        a fixed size and is not updated by re-embedding. On a deployment with no
        Atlas Search there is no index to rebuild, and this is a no-op.
        """
        try:
            await self._coll(collection_name).drop_search_index(self.settings.VECTOR_INDEX_NAME)
        except Exception as e:
            logger.info(f"No vector search index dropped ({e}).")
            return False
        return await self.create_vector_index(collection_name)

    async def stored_vector_info(self, collection_name: str | None = None) -> dict[str, Any]:
        """What the collection currently holds: chunk count, the dimension of a
        sample vector, and which embedding models produced the vectors."""
        collection = self._coll(collection_name)
        has_vector = {VECTOR_FIELD: {"$exists": True}}
        sample = await collection.find_one(has_vector, {VECTOR_FIELD: 1})
        return stored_vector_summary(
            count=await collection.count_documents(has_vector),
            sample_vector=sample[VECTOR_FIELD] if sample else None,
            models=await collection.distinct("embeddingModel"),
            unrecorded=await collection.count_documents({**has_vector, "embeddingModel": {"$exists": False}}),
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert_data(
        self,
        data_items: list[dict[str, Any]],
        collection_name: str | None = None,
    ) -> tuple[list[Any] | None, int]:
        """
        Inserts chunks (each with a 'vectorContent' embedding) into the collection.

        Returns (the inserted '_id's, how many), or (None, 0) on failure. Items
        without a usable vector are skipped and logged.
        """
        collection = self._coll(collection_name)
        if collection is None:
            return None, 0
        if not data_items:
            return [], 0

        valid_documents, skipped = split_valid_items(data_items, self.dimension)
        for message in skipped:
            logger.warning(message)
        if not valid_documents:
            logger.error(f"No valid documents in the batch for '{collection.name}'. {len(skipped)} items skipped.")
            return None, 0

        try:
            result = await collection.insert_many(valid_documents, ordered=False)
        except Exception as e:
            logger.error(f"Batch insert into '{collection.name}' failed. Details: {e}", exc_info=True)
            return None, 0
        logger.info(f"Inserted {len(result.inserted_ids)} of {len(valid_documents)} documents into '{collection.name}'.")
        return result.inserted_ids, len(result.inserted_ids)

    async def deactivate_data_by_source_id(
        self,
        source_file_id: str,
        collection_name: str | None = None,
    ) -> int:
        """Soft delete: sets 'metadata.isactive' to false on every chunk of the
        source file. Returns how many chunks changed; errors propagate."""
        collection = self._coll(collection_name)
        if collection is None:
            return 0
        result = await collection.update_many(
            {"metadata.source_file_id": source_file_id}, {"$set": {"metadata.isactive": False}}
        )
        logger.info(f"Deactivated {result.modified_count} documents in '{collection.name}' "
                     f"with source_file_id: {source_file_id}")
        return result.modified_count

    async def move_chunks_by_source_id(
        self,
        source_file_id: str,
        from_collection: str,
        to_collection: str,
    ) -> int:
        """Copies all chunks for `source_file_id` from `from_collection` into `to_collection`,
        then deletes them from `from_collection`. Returns the number of chunks moved."""
        if self.db is None:
            return 0
        src = self.db[from_collection]
        dst = self.db[to_collection]
        filter_ = {"metadata.source_file_id": source_file_id}
        docs = await src.find(filter_).to_list(length=None)
        if not docs:
            return 0
        for doc in docs:
            doc.pop("_id", None)
        try:
            await dst.insert_many(docs, ordered=False)
        except Exception as e:
            logger.error(f"move_chunks: insert into '{to_collection}' failed for {source_file_id}: {e}", exc_info=True)
            return 0
        result = await src.delete_many(filter_)
        logger.info(f"Moved {result.deleted_count} chunks for {source_file_id} from '{from_collection}' to '{to_collection}'")
        return result.deleted_count

    # ------------------------------------------------------------------
    # Version chunk fetch (for comparison generation)
    # ------------------------------------------------------------------

    async def fetch_chunks_for_version(
        self, source_file_id: str, collection_name: str | None = None, limit: int = 500,
    ) -> list[dict]:
        """All active, non-comparison chunks for one indexed version, ordered by page number.
        Used to gather source text for prebaked comparison generation."""
        collection = self._coll(collection_name)
        if collection is None:
            return []
        query = {
            **ACTIVE,
            "metadata.source_file_id": source_file_id,
            IS_COMPARISON_FIELD: {"$ne": True},
        }
        projection = {TEXT_FIELD: 1, "metadata": 1, "_id": 0}
        return await collection.find(query, projection).sort("metadata.page_number", 1).to_list(length=limit)

    async def deactivate_comparison_chunks(
        self, synthetic_source_id: str, collection_name: str | None = None,
    ) -> int:
        """Soft-deletes comparison chunks stored under a synthetic source_file_id.
        Used to replace stale comparison chunks when force-regenerating."""
        return await self.deactivate_data_by_source_id(synthetic_source_id, collection_name)

    async def comparison_chunks_exist(
        self, synthetic_source_id: str, collection_name: str | None = None,
    ) -> bool:
        """True if at least one active comparison chunk already exists for this version pair."""
        collection = self._coll(collection_name)
        if collection is None:
            return False
        return await collection.find_one({
            **ACTIVE, "metadata.source_file_id": synthetic_source_id, IS_COMPARISON_FIELD: True,
        }, {"_id": 1}) is not None

    # ------------------------------------------------------------------
    # Search: hybrid = vector list + lexical list, fused by rank
    # ------------------------------------------------------------------

    async def similarity_search_on_query(
        self,
        query_vector: list[float],
        collection_name: str,
        limit: int = 10,
        query_text: str | None = None,
        version_scope: VersionScope | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search scoped to `version_scope`.

        When the Atlas index declares version filter fields (_version_filter_in_index):
        - ALL mode: two parallel searches, one per side, fused for balanced coverage
        - Other modes: single search with the version filter pushed into $vectorSearch/$text

        On old indexes without version filter fields, falls back to the original
        post-filter approach (overfetch and keep matching hits)."""
        if version_scope is None:
            return await self._hybrid(query_vector, collection_name, limit, query_text)

        # Only use the in-query filter when the Atlas index supports it and Atlas is up.
        can_filter = MongoEmbeddingStore._version_filter_in_index and not MongoEmbeddingStore._atlas_vector_search_unavailable

        if version_scope.mode is Mode.ALL:
            if can_filter:
                # Three independent passes: latest content, older content, and prebaked
                # comparison chunks (is_comparison=True). Each has its own ANN budget.
                # Comparison chunks excluded from the content passes to avoid double-counting.
                latest_filter = {IS_LATEST_FIELD: {"$ne": False}, IS_COMPARISON_FIELD: {"$ne": True}}
                older_filter = {IS_LATEST_FIELD: False, IS_COMPARISON_FIELD: {"$ne": True}}
                comparison_filter = {IS_COMPARISON_FIELD: True}
                latest_hits, older_hits, comparison_hits = await asyncio.gather(
                    self._hybrid(query_vector, collection_name, limit, query_text, version_filter=latest_filter),
                    self._hybrid(query_vector, collection_name, limit, query_text, version_filter=older_filter),
                    self._hybrid(query_vector, collection_name, limit // 2, query_text, version_filter=comparison_filter),
                )
                from app.logic.rerank import fuse_query_hits
                return fuse_query_hits([latest_hits, older_hits, comparison_hits], k=self.settings.HYBRID_RRF_K)
            else:
                # Old index: single fetch; balance_versions in the reranker compensates.
                return await self._hybrid(query_vector, collection_name, limit, query_text)

        # Non-ALL scope
        if can_filter:
            vf = version_filter_for(version_scope)
            if vf:
                return (await self._hybrid(query_vector, collection_name, limit, query_text, version_filter=vf))[:limit]

        # Old index or no filter produced (shouldn't happen for non-None non-ALL scope,
        # but be safe): overfetch then post-filter.
        fetch = limit * self.settings.VERSION_OVERFETCH_MULTIPLIER
        hits = await self._hybrid(query_vector, collection_name, fetch, query_text)
        return [hit for hit in hits if version_scope.matches(hit.get("metadata") or {})][:limit]

    async def _hybrid(
        self,
        query_vector: list[float],
        collection_name: str,
        limit: int,
        query_text: str | None,
        version_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Cosine similarity over the vector index, fused with lexical relevance via
        Reciprocal Rank Fusion (RRF) when query_text is given.

        The two lists are merged by rank rather than raw score - a cosine
        similarity and a BM25 score are not on comparable scales, but their rank
        orderings are, which is exactly what RRF combines.

        version_filter is an MQL dict pushed into $vectorSearch and $text; callers
        must only pass it when the index declares the filter fields.
        """
        if not query_text:
            return (await self._vector_search(query_vector, collection_name, limit, version_filter))[:limit]

        settings = self.settings
        depth = fusion_depth(limit, settings.HYBRID_FUSION_DEPTH_MULTIPLIER, settings.HYBRID_FUSION_DEPTH_MIN)
        # Independent queries, so they run together rather than back to back.
        vector_hits, text_hits = await asyncio.gather(
            self._vector_search(query_vector, collection_name, depth, version_filter),
            self._lexical(query_text, collection_name, depth, version_filter),
        )
        if not text_hits:
            return vector_hits[:limit]
        return reciprocal_rank_fusion(
            vector_hits, text_hits, limit, k=settings.HYBRID_RRF_K, lexical_weight=settings.HYBRID_LEXICAL_WEIGHT
        )

    async def _shared(self, inflight: dict[Hashable, "asyncio.Future"], key: Hashable, make: Callable[[], Awaitable]):
        """One run of `make()` per key at a time. Callers that arrive while it is
        running wait for that run instead of starting their own; nothing is kept
        once it finishes. Shielded so that one caller giving up does not cancel it
        for the rest."""
        task = inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(make())
            inflight[key] = task
            task.add_done_callback(lambda done: inflight.get(key) is done and inflight.pop(key))
        return await asyncio.shield(task)

    # -- vector half --

    async def _vector_search(self, query_vector: list[float], collection_name: str, limit: int,
                              version_filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Atlas $vectorSearch, or the in-process fallback where Atlas is not available."""
        if len(query_vector) != self.dimension:
            # Fail clearly here rather than let Atlas reject the stage with an
            # opaque error, or numpy raise a bare shape mismatch in the fallback.
            logger.error(
                f"Query embedding has {len(query_vector)} dimensions, expected "
                f"{self.dimension} (EMBEDDING_DIMENSION) in '{collection_name}'."
            )
            return []
        if MongoEmbeddingStore._atlas_vector_search_unavailable:
            return await self._cosine_fallback(query_vector, collection_name, limit)
        try:
            return await self._atlas_vector_search(query_vector, collection_name, limit, version_filter)
        except OperationFailure as e:
            if not is_atlas_search_unavailable(e):
                logger.error(f"Vector search operation failed in '{collection_name}'. Details: {e}", exc_info=True)
                return []
            logger.warning(
                f"Atlas $vectorSearch is not available on this MongoDB deployment "
                f"(plain community server, not Atlas/Atlas-CLI-local). Falling back "
                f"to in-process cosine similarity for '{collection_name}', and "
                f"not trying $vectorSearch again until this service restarts."
            )
            MongoEmbeddingStore._atlas_vector_search_unavailable = True
            return await self._cosine_fallback(query_vector, collection_name, limit)
        except Exception as e:
            logger.error(f"An unexpected error occurred during vector search in '{collection_name}': {e}", exc_info=True)
            return []

    async def _atlas_vector_search(self, query_vector: list[float], collection_name: str, limit: int,
                                   version_filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        settings = self.settings
        pipeline = vector_search_pipeline(
            query_vector,
            index=settings.VECTOR_INDEX_NAME,
            path=VECTOR_FIELD,
            limit=limit,
            exact=settings.VECTOR_SEARCH_EXACT,
            overfetch_multiplier=settings.VECTOR_OVERFETCH_MULTIPLIER,
            extra_filter=version_filter,
        )
        return await self.db[collection_name].aggregate(pipeline).to_list(length=limit)

    async def _cosine_fallback(self, query_vector: list[float], collection_name: str, limit: int) -> list[dict[str, Any]]:
        """Brute-force cosine similarity, used where Atlas $vectorSearch isn't
        available (plain community MongoDB). Fine for local/dev-sized collections;
        not a substitute for a real vector index at production scale.

        A chat turn fans out into several searches at the same moment and each
        needs the whole collection, so they share one read of it. Nothing is kept
        afterwards: the next turn loads afresh, so an upload or a deactivation is
        visible immediately."""
        try:
            corpus = await self._shared(self._corpus_loads, collection_name, lambda: self._read_corpus(collection_name))
        except Exception as e:
            # Like the Atlas path: a read error costs this search its vector half, not the call its lexical half.
            logger.error(f"Reading '{collection_name}' for the in-process vector search failed: {e}", exc_info=True)
            return []
        if corpus is None:
            return []
        docs, matrix, norms = corpus
        return [to_hit(docs[i], score) for i, score in cosine_top_k(matrix, norms, query_vector, limit)]

    async def _read_corpus(self, collection_name: str):
        """(docs, vector matrix, row norms) for every active chunk, or None."""
        query, projection = dense_corpus_query()
        docs = usable_docs(await self.db[collection_name].find(query, projection).to_list(length=None), VECTOR_FIELD, self.dimension)
        if not docs:
            return None
        matrix = np.asarray([d[VECTOR_FIELD] for d in docs], dtype=np.float32)
        return docs, matrix, np.linalg.norm(matrix, axis=1)

    # -- lexical half --

    async def _lexical(self, query_text: str, collection_name: str, limit: int,
                       version_filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """BM25 hits, or the $text index's if LEXICAL_SEARCH says so or BM25 fails."""
        if self.settings.LEXICAL_SEARCH == "bm25":
            try:
                _, index, docs = await self._bm25_snapshot(collection_name)
                fetch_bm25 = limit * 2 if version_filter else limit
                hits = [to_hit(docs[i], score) for i, score in index.search(query_text, fetch_bm25)]
                if version_filter:
                    hits = [h for h in hits if _mql_matches_hit(h, version_filter)][:limit]
                return hits
            except Exception as e:
                logger.warning(f"BM25 search failed, using the $text index instead: {e}")
        return await self._text_search(query_text, collection_name, limit, version_filter)

    async def _bm25_snapshot(self, collection_name: str):
        """The BM25 index for the active chunks, rebuilt only when they change.

        "Changed" is judged by the number of active chunks and the newest chunk's
        id: an upload adds chunks and a delete deactivates them, so either moves
        the pair, and checking it costs two cheap queries instead of re-reading
        and re-tokenising the collection on every search.
        """
        collection = self.db[collection_name]
        newest = await collection.find_one(ACTIVE, {"_id": 1}, sort=[("_id", -1)])
        signature = (await collection.count_documents(ACTIVE), newest and newest["_id"])
        cached = self._bm25.get(collection_name)
        if cached and cached[0] == signature:
            return cached
        return await self._shared(
            self._bm25_builds, (collection_name, signature), lambda: self._build_bm25(collection_name, signature)
        )

    async def _build_bm25(self, collection_name: str, signature: Any):
        query, projection = text_corpus_query()
        docs = await self.db[collection_name].find(query, projection).to_list(length=None)
        index = await asyncio.to_thread(
            Bm25Index.build, [d.get(TEXT_FIELD) or "" for d in docs], k1=self.settings.BM25_K1, b=self.settings.BM25_B
        )
        self._bm25[collection_name] = snapshot = (signature, index, docs)
        return snapshot

    async def _text_search(self, query_text: str, collection_name: str, limit: int,
                           version_filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Lexical search via MongoDB's $text index. Unlike $vectorSearch this is a
        standard community-server feature, so it needs no Atlas fallback - an empty
        result just means the index has nothing to say (e.g. it hasn't finished
        building yet, or the collection is empty).
        """
        try:
            return await self.db[collection_name].aggregate(
                text_search_pipeline(query_text, limit, extra_filter=version_filter)
            ).to_list(length=limit)
        except OperationFailure as e:
            logger.warning(
                f"Text search failed on '{collection_name}' (index missing, still "
                f"building, or query error). Continuing with vector-only results. Details: {e}"
            )
            return []
        except Exception as e:
            logger.error(f"An unexpected error occurred during text search in '{collection_name}': {e}", exc_info=True)
            return []
