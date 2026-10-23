"""Re-embeds the knowledge base with the configured embedding model.

Switching EMBEDDING_TYPE / the model means every stored vector was made by the
old one, and vectors from different models cannot be compared. The chunk text is
stored next to each vector, so nothing needs re-parsing or re-uploading: this
reads each chunk's text, embeds it with the current model and writes the new
vector back in place.

    python -m app.tools.reindex --dry-run         # what would change, touches nothing
    python -m app.tools.reindex                   # re-embed what the current model has not done
    python -m app.tools.reindex --force           # re-embed everything regardless
    python -m app.tools.reindex --rebuild-index   # only drop and recreate the Atlas vector index

Resumable and safe to re-run: a chunk is marked with `embeddingModel` once done,
and chunks already marked with the current model are skipped, so an interrupted
run simply continues. Search keeps answering throughout, but mixes old and new
vectors until it finishes, so results are unreliable in between - run it in a
quiet period, and stop uploads while it runs.

This tool talks to MongoDB directly (the vector store interface does not expose
enumerating chunks), so it is specific to VECTOR_STORE=mongo.
"""
import argparse
import asyncio
import logging
import sys

from app.config import get_settings
from app.logic.index_health import reindex_filter
from app.logic.mongo_queries import TEXT_FIELD, VECTOR_FIELD
from app.services.embedding.embedding_factory import (
    create_embedding_service,
    verify_embedding_dimension,
)
from app.services.vector.vector_store_factory import create_vector_store
from pymongo import UpdateOne

logger = logging.getLogger("reindex")


async def reindex(collection_name: str, batch_size: int, dry_run: bool, force: bool) -> int:
    settings = get_settings()
    service = create_embedding_service(settings)
    # Strict here, unlike at server startup: writing vectors of the wrong size
    # into the collection is exactly what this must not do.
    verify_embedding_dimension(service, settings.EMBEDDING_DIMENSION)
    model_id = service.model_id

    async with create_vector_store(settings, collection_name) as store:
        collection = store.db[collection_name]
        before = await store.stored_vector_info(collection_name)
        todo_filter = reindex_filter(VECTOR_FIELD, model_id, settings.LEGACY_EMBEDDING_MODEL_ID, force)
        todo = await collection.count_documents(todo_filter)

        print(f"collection      : {collection_name}")
        print(f"embedding model : {model_id} ({settings.EMBEDDING_DIMENSION} dimensions)")
        print(f"stored vectors  : {before['count']} chunks, {before['dimension']} dimensions, models {before['models'] or 'unrecorded'}")
        print(f"to re-embed     : {todo}")
        if dry_run or todo == 0:
            print("dry run - nothing changed." if dry_run else "nothing to do.")
            return 0

        done = 0
        cursor = collection.find(todo_filter, {"_id": 1, TEXT_FIELD: 1})
        batch = []
        async for doc in cursor:
            if (doc.get(TEXT_FIELD) or "").strip():
                batch.append(doc)
            if len(batch) >= batch_size:
                done += await _flush(service, collection, model_id, batch)
                batch = []
                print(f"  re-embedded {done}/{todo}", flush=True)
        if batch:
            done += await _flush(service, collection, model_id, batch)
        print(f"re-embedded {done} chunks.")

        # An Atlas vector index is fixed-size: recreate it if the size changed.
        if before["dimension"] not in (None, settings.EMBEDDING_DIMENSION):
            print("dimension changed - rebuilding the vector index...")
            print("  done." if await store.rebuild_vector_index(collection_name) else "  no Atlas vector index to rebuild.")
    return 0


async def rebuild_index(collection_name: str) -> int:
    """Drops and recreates the vector index from the current definition (for an
    index built before it declared the isactive filter, or at another dimension)."""
    async with create_vector_store(get_settings(), collection_name) as store:
        rebuilt = await store.rebuild_vector_index(collection_name)
    print("vector index recreated; Atlas builds it in the background." if rebuilt else "no Atlas vector index was rebuilt.")
    return 0


async def _flush(service, collection, model_id, batch) -> int:
    vectors = await asyncio.to_thread(service.generate_embeddings, [d[TEXT_FIELD] for d in batch])
    await collection.bulk_write([
        UpdateOne({"_id": d["_id"]}, {"$set": {VECTOR_FIELD: vec.tolist(), "embeddingModel": model_id}})
        for d, vec in zip(batch, vectors)
    ])
    return len(batch)


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-embed the knowledge base with the configured embedding model.")
    parser.add_argument("--collection", default=None, help="Collection to re-embed (default: VECTOR_COLLECTION_NAME).")
    parser.add_argument("--batch-size", type=int, default=None, help="Chunks embedded per request (default: REINDEX_BATCH_SIZE).")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change and exit.")
    parser.add_argument("--force", action="store_true", help="Re-embed every chunk, even ones the current model already did.")
    parser.add_argument("--rebuild-index", action="store_true", help="Only drop and recreate the Atlas vector index, then exit.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    collection = args.collection or settings.VECTOR_COLLECTION_NAME
    batch_size = args.batch_size or settings.REINDEX_BATCH_SIZE
    if args.rebuild_index:
        return asyncio.run(rebuild_index(collection))
    return asyncio.run(reindex(collection, batch_size, args.dry_run, args.force))


if __name__ == "__main__":
    sys.exit(main())
