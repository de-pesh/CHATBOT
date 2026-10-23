# Ingestion Flow

How a file goes from an admin upload → searchable chunks in the vector store.

---

## Overview

```
Admin POST /files  →  blob storage + SQL record  →  queue message
                                                           ↓
                                               parsing-indexing-service worker
                                                    /         \
                                               parsing       indexing
                                                    \         /
                                              SQL update + vector store
```

Two services are involved:

| Service | Role |
|---|---|
| `main-service-chatbot` | Receives the upload, writes SQL record, pushes queue message |
| `parsing-indexing-service` | Worker that dequeues, parses, embeds, writes to MongoDB |

---

## Step 1 — Upload (main-service-chatbot)

**Entry point:** [main-service-chatbot/app/routers/file.py](main-service-chatbot/app/routers/file.py) `POST /`

1. Extension check against `ALLOWED_UPLOAD_EXTENSIONS`; size check against `MAX_UPLOAD_SIZE_MB`.
2. SHA-256 hash → duplicate check (`FIND_FILE_BY_CONTENT_HASH`). Refuses with 409 if identical file already exists (unless `allow_duplicate=true`).
3. Uploads raw bytes to blob storage (`FileStorageService.upload_file`).
4. Writes a SQL file record via `FileController.create_file` — `kb_file_id` and `parsed_path` are **null** at this point.
5. Stamps a version: `stamp_upload` (handles `_v3` suffix in filename, `version_label`, `effective_date`).
6. Pushes a `{"action_type": "create", "file_id": ..., "file_path": ...}` message to the queue (`MessageQueueService.send_message`).

**See:** [file.py:152](main-service-chatbot/app/routers/file.py#L152) — queue push  
**See:** [file.py:129](main-service-chatbot/app/routers/file.py#L129) — SHA-256 duplicate guard

---

## Step 2 — Worker picks up the job (parsing-indexing-service)

**Entry point:** [parsing-indexing-service/app/worker/worker.py](parsing-indexing-service/app/worker/worker.py) `process_message → add_file_handler`

The worker (`celery_tasks.py` or `redis_queue_service`) dequeues and calls `add_file_handler`.

Before doing real work it:
- Reads the current SQL row for the file.
- If the file was deleted in the meantime → skips silently (idempotent).
- Marks the file `PROCESSING`.

---

## Step 3 — Parsing

**Entry point:** [parsing-indexing-service/app/parsing/handler.py](parsing-indexing-service/app/parsing/handler.py) `parsing_handler`

1. Downloads the raw file from blob storage.
2. Optional Tika MIME check (TIKA_URL) — warns on mismatch, never blocks.
3. Routes to an extractor by file extension:

| Extension | Extractor |
|---|---|
| `.pdf .docx .pptx .xlsx .html` | `docling_helper` — Docling converts to structured JSON |
| `.png .jpg .jpeg .webp` | `image_helper` — Vision LLM describes the image as Markdown |
| `.txt` | inline decode |
| `.eml .msg` | email-to-text parsers |

4. Blank-page safety check: if most pages have no text, raises an error (suggests enabling Vision LLM).
5. Uploads the extracted text as a new blob (`.json` for Docling, `.md` for images, `.txt` otherwise).
6. Returns the new blob path.

**See:** [parsing/handler.py:44](parsing-indexing-service/app/parsing/handler.py#L44) — EXTRACTORS map  
**See:** [parsing/handler.py:102](parsing-indexing-service/app/parsing/handler.py#L102) — `parsing_handler` function

---

## Step 4 — Indexing (chunk + embed + store)

**Entry point:** [parsing-indexing-service/app/indexing/handler.py](parsing-indexing-service/app/indexing/handler.py) `index_handler`

1. Downloads the extracted-text blob.
2. Calls `prepare_document` in a thread (CPU-bound; would block the async event loop inline):
   - `split_document` — splits text into chunks (respects `CHUNK_SIZE`, `CHUNK_OVERLAP`; for policy files splits on page boundaries first).
   - `embedding_service.generate_embeddings` — one batch call per document to the embedding model.
   - Returns `[{"textContent", "vectorContent", "embeddingModel", "metadata"}]`.
3. Stamps each chunk with a fresh `source_file_id` UUID + file metadata.
4. `vector_store.insert_data` → bulk-inserts into MongoDB collection.
5. `vector_store.create_vector_index` — ensures the ANN index exists.
6. On failure: deactivates any partial chunks written under that `source_file_id` before re-raising.

**See:** [indexing/handler.py:16](parsing-indexing-service/app/indexing/handler.py#L16) — `index_handler`  
**See:** [indexing/prepare.py:7](parsing-indexing-service/app/indexing/prepare.py#L7) — `prepare_document`

---

## Step 5 — Write results back + version sync

Back in `add_file_handler` ([worker.py:127](parsing-indexing-service/app/worker/worker.py#L127)):

1. Re-checks the SQL row (could have been deleted during indexing — if so, discards chunks and returns).
2. Calls `UPSERT_FILE` stored procedure to write `kb_file_id` and `parsed_path` back onto the SQL record.
3. Marks the file `COMPLETED`.
4. `sync_document` — elects the latest active version across all uploads of the same document and mirrors the `is_latest` flag onto all vector chunks.
5. Triggers `catalog.refresh()` on main-service so the KB overview is up to date.

---

## Failure / retry behaviour

- A dead-letter handler marks the SQL row `FAILED` with a truncated error message.
- Each retry gets a fresh `source_file_id`; partial chunks from the failed attempt are deactivated immediately on failure so they cannot be searched.
- The whole flow is idempotent: running it twice leaves only the most recent `source_file_id` active.

---

## Key services (all behind interfaces + factories)

| Interface | Concrete implementations |
|---|---|
| `FileStorageService` | Azure Blob, local disk |
| `EmbeddingService` | Azure OpenAI, local sentence-transformers, remote HTTP |
| `VectorStore` | MongoDB Atlas Vector Search |
| `VisionService` | Azure Vision LLM, Foundry, LiteLLM, Ollama, OpenAI |
| `MessageQueueService` | Redis Streams, Celery |
