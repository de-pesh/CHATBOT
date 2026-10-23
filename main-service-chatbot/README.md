# Chatbot Main Service

The control plane API for the chatbot stack: authentication, chat (RAG over
documents, streamed or blocking), file upload/management, and the knowledge-base
worker that parses and indexes uploaded files via the sibling
`parsing-indexing-services-chatbot` service's REST API. Search itself (the vector
+ BM25 hybrid retrieval a chat turn runs) is in-process here, not a call to that
service - see `app/services/vector/`. See the repo root
[README.md](../README.md) for how this fits into the rest of the stack, and
[docs/guide.md](../docs/guide.md) / [docs/deploy.md](../docs/deploy.md) for
running and configuring it.

## Getting Started

### Prerequisites

* Python 3.12
* Poetry

### Installing

1. Clone the repository:
   ```bash
   git clone <repo-url>
   ```
2. Navigate to this service's directory:
   ```bash
   cd main-service-chatbot
   ```
3. Install the dependencies using Poetry:
   ```bash
   poetry install
   ```
4. Configuration is one file for the whole repo: copy `.env.sample` (repo root) to `.env` and fill it in. This folder has no `.env` of its own.

### Running the Application

```bash
poetry run uvicorn app.main:app --reload
```

Or run the whole stack (this service, the parsing/indexing service, SQL Server,
Redis, Ollama, the frontend) via `docker compose up -d` from the repo root.

## Layout

```
app/
  main.py              FastAPI app setup: middleware, exception handlers, router registration
  config.py            Settings (env-driven), every variable is listed in config.py
  dependencies.py      FastAPI dependencies: auth, current user, service accessors
  security.py          Password hashing, JWT issuing/verification, permission checks
  lifespan_manager.py  Startup/shutdown: wires up the concrete backend for each service
  observability.py     Logging setup + request-context middleware
  routers/             HTTP layer - request/response models, status codes, one module per resource
  controllers/         Business logic behind each router. The chat turn: chat_controller.py is the short
                         sequence, chat_steps.py the steps, chat_context.py (in-process search) and
                         chat_citations.py (citation URLs) the pieces they call
  models/              Pydantic request/response/DB-row models
  logic/               PURE functions only - no network, DB, disk or clock; everything else imports from here
                         prompts, language, rerank, replies, citations, catalog, chat_turn, chat_events,
                         chat_records, kb_jobs, files, llm_models, http, connection_string, sql_scripts, queue_codec,
                         fusion, lexical, mongo_queries, embeddings, config_checks, index_health (hybrid search -
                         same files as parsing-indexing-services-chatbot's copy, see its README)
  services/            Backends, one folder per concern, each with an interface + *_factory.py + implementations:
    llm/                 chat models (azure, litellm, ollama, aws, google) + chat_llm.py, the wrapper the pipeline calls
    db/                  database interface + MSSQL, schema_migrator.py, stored_procedures.py
    storage/             file store: local volume, Azure Blob, Nextcloud
    queue/               upload queue: Redis
    vector/              Search itself: vector_store.py (interface) + vector_store_factory.py + mongo_service.py,
                          and search_runtime.py (the runtime built once at startup, app/lifespan_manager.py)
    embedding/           query-time embedding: embedding_service.py (interface) + embedding_factory.py +
                          remote/azure implementations (litellm | azure | remote only - no local/torch here)
    kb/                  kb_catalog.py (document list) and kb_service.py (delete from the vector store, over REST)
  kb_worker/           Knowledge-base ingestion: the handlers (worker.py), the in-process Redis consumer, and the Celery tasks (celery_tasks.py) that run in the separate kb-worker pod when QUEUE_TYPE=celery; parses/indexes files via the parsing-indexing REST API
  celery_app.py        The Celery app (QUEUE_TYPE=celery): `celery -A app.celery_app worker` - thread pool sized by CELERY_CONCURRENCY
scripts/
  mssql/               Numbered T-SQL schema scripts (DB_TYPE=mssql) - applied by main-service itself at startup (app/services/db/schema_migrator.py, AUTO_APPLY_SCHEMA); scripts/apply_schema.py runs the same code by hand
  apply_schema.py      Same code as the startup schema step, as a command (--status, --baseline, --force)
```

Adding or swapping a backend (database, storage, queue, chat model)? See the docstring in
each `services/<concern>/*_factory.py` and [docs/deploy.md](../docs/deploy.md).

## API Endpoints

Full request/response schemas are at `/docs` (Swagger UI) once the service is
running. Summary:

### Monitoring

* **GET /** - service status
* **GET /health** - health check

### Authentication (`/auth`)

* **POST /auth/authenticate** - log in, get an access token
* **POST /auth/change-password** - change the current user's password
* **POST /auth/re-authenticate** - refresh an access token

### Chat (`/chat`)

* **POST /chat** - process a chat turn, blocking until the full answer is ready
* **POST /chat/stream** - the same pipeline, delivered as Server-Sent Events
* **GET /chat/history** - the caller's own paginated chat history
* **GET /chat/session** - the caller's own paginated chat sessions
* **PATCH /chat/session/{session_id}** - rename a session
* **DELETE /chat/session/{session_id}** - soft-delete a session

### Files (`/files`)

* **POST /files/** - upload files, create file records, and queue them for parsing/indexing
* **GET /files/** - list the caller's file records
* **GET /files/{file_id}** - retrieve one file record
* **GET /files/{file_id}/status** - processing status (queued/parsing/indexed/failed)
* **GET /files/{file_id}/download** - download the original file
* **DELETE /files/{file_id}** - delete a file record
