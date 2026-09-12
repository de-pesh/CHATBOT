# Guide

Two ways to run the stack locally. **Docker is the easy one** - it needs no
Python, Poetry, Node or OCR toolchain on the host.

## Run everything in Docker

```bash
docker compose build     # first time only, and slow: the two rpc servers
                         # each pull torch and a ~600 MB Hugging Face model
docker compose up -d
docker compose ps
```

Open <http://localhost:3006> (it redirects to `/chatbot/`).

```bash
docker compose logs -f main-service   # follow one service
docker compose restart main-service   # restart one
docker compose down                   # stop; add -v to wipe the databases
```

After changing a service's code, rebuild just that image:

```bash
docker compose up -d --build main-service
```

### What the compose file sets up for you

* **MySQL schema** is applied automatically from `main-service/scripts/*.sql`
  on first start - no manual `mysql <` step. It only runs on an empty volume,
  so `docker compose down -v` is how you re-apply it.
* **Mongo** runs as a container too, replacing the hand-started
  `docker-mongo-1` that the old native script assumed.
* **Redis** runs in single-node *cluster* mode, because the code uses
  `redis.asyncio.cluster.RedisCluster` and will not talk to a standalone server.
* **Config**: the per-service `.env` files are excluded by `.dockerignore` and
  never reach the images. Everything is set in `docker-compose.yml`, with the
  Azure credentials substituted in from the `.env` next to it. Service names
  (`redis`, `mysql`, `main-service`, ...) replace the `127.0.0.1` addresses the
  native setup used.

### What runs, and what was trimmed

Six containers, scoped to **chatbot + document ingestion**:

```
chat      FE -> main-service -> Search RPC -> indexing-rpc-server -> vector store
                             -> Azure OpenAI
ingest    FE/API -> main-service -> file store
                                 -> ParseFile RPC -> indexing-rpc-server
                                 -> Index RPC     -> indexing-rpc-server -> vector store
                                 -> database (file record)
list      main-service -> ListDocuments RPC -> indexing-rpc-server -> vector store
delete    main-service -> chatbot_queue -> kb_worker (same process)
                                        -> Delete RPC -> indexing-rpc-server
```

Started at 13. Removed:

* **neo4j** - driver created at startup, never used by any route; `Neo4jService`
  was never imported. Its failure path could block main-service from booting.
* **parsing-service, indexing-service, llm-service** - the assessment pipeline.
  Nothing on the chat or ingestion path touched them. Source removed; recover
  it from git history if the assessment features are ever revived.
* **parsing-rpc-server** - a container, a proto and a network hop for one gRPC
  method, `ParseFile`. Its modules took their dependencies by injection, so they
  moved into `app/parsing/` unchanged.
* **chatbot-kb-management-service** - ran the same parse-then-index work `/kb`
  already did, differing only in trigger (a Redis message) and some MySQL
  bookkeeping. Now an asyncio task inside the inferencing service
  (`app/kb_worker/`), sharing one parser instead of duplicating it.
* **The `Test/MyFunction` gRPC scaffold** indexing-rpc-server served on :50051.
* **The custom cluster-mode Redis image.**

`indexing-rpc-server` deliberately stays separate: it holds the 600 MB embedding
model, and that is the one split in this system that pays for itself.

**Scaling the worker back out**, if ingestion ever competes with chat for the
event loop: run the same image twice - `KB_WORKER_ENABLED=false` on the API
replicas, `true` on a worker replica. No code change, no second codebase.

### Image sizes

| | was | now |
|---|---|---|
| containers | 13 | **6** |
| all images | ~80 GB | **6.6 GB** |
| indexing-rpc-server | 17.2 GB | **4.72 GB** |
| main-service | - | **1.78 GB** |
| chatbot-inferencing-service | - | *gone (merged into main-service)* |
| parsing-rpc-server | 17.2 GB | *gone (folded in)* |

Causes, all fixed:

* `parsing-rpc-server` baked in the 600 MB embedding model and the whole torch
  stack for an `EmbeddingService` that was **never imported**.
* Both installed `torch 2.14.0+cu130` - 3.2 GB of `nvidia-*` wheels - on a
  workload where `torch.cuda.is_available()` is `False`. `indexing-rpc-server`
  now pins torch to the PyTorch CPU index (see its `pyproject.toml`; a closed
  network needs an internal mirror of that index).
* **Poetry shipped inside every image.** Its artifact cache under
  `.cache/pypoetry` is not covered by pip's `--no-cache-dir` and came to 649 MB
  in indexing, 245 MB in inferencing and 63 MB in main-service. Poetry now
  installs into a throwaway venv under `/tmp` and is deleted after resolving
  dependencies, which also keeps its own dependencies out of site-packages.
  Each install ends with an import smoke test so a build fails loudly if
  poetry ever resolves a different target env.
* **`chmod -R g=u` ran last in `indexing-rpc-server`,** after the 600 MB model.
  chmod rewrites every file's metadata, so Docker copied the whole tree into a
  new layer - 1.31 GB of pure duplication. That image is now a two-stage build:
  the toolchain (git, git-lfs, EPEL, poetry) and the chmod happen in a builder
  stage that is discarded, and the runtime stage is a single COPY of the
  finished tree.

### Redis: standalone, not cluster

The client used `redis.asyncio.cluster` against a hand-rolled single-node
"cluster" for one queue. Nothing cluster-specific was used - the five commands
(`bzpopmin`, `zadd`, `setex`, `get`, `ping`) are all standard - so it now runs
plain `redis:7-alpine` on the normal port 6379, and `RedisClusterService` was
renamed `RedisQueueService`.

This also removes the constraint in `../deploy/README.md` ("Redis must run in
cluster mode"), which had made the managed Azure Cache instance unusable.

### The embedding model needs two repos, not one

`gte-multilingual-base`'s `config.json` maps its classes to code in a *different*
repo:

```json
"AutoModel": "Alibaba-NLP/new-impl--modeling.NewModel"
```

`trust_remote_code=True` makes transformers fetch that at load time. The image
cloned only the model repo and set `HF_HUB_OFFLINE=1`, so **embedding always
failed and nothing could ever be indexed**. Two fixes in the Dockerfile:

1. the model is loaded once at build time, which caches the remote code into the
   image and fails the build if it cannot be loaded;
2. `TRANSFORMERS_CACHE` is no longer set - it is deprecated and it overrode the
   hub cache path, sending lookups one directory above where `HF_HOME` had put
   the download.

**A closed-network build needs `Alibaba-NLP/new-impl` mirrored too**, not just
the model repo.

### Known gaps in the local images

* **OCR is not installed.** `tesseract`, `tesseract-langpack-eng`,
  `tesseract-langpack-ara`, `qpdf` and `unpaper` are in none of the repos UBI9 +
  EPEL9 enable, so `OCR_MODE=optional` skips them and scanned PDFs will not
  parse. (`ghostscript` and `pngquant` do install.) Same story as
  `soffice`/LibreOffice, which has never been available in these images.
* **Ingestion is verified end to end**: upload -> parse -> embed -> index ->
  retrieve -> answer. A `policy.txt` test document is left in the `chatbot_policies`
  collection; drop it with `DELETE /kb/{file_id}` or wipe it via `docker compose down -v`.
  stack from PyPI even though nothing here uses a GPU. Installing the CPU-only
  wheels (`--index-url https://download.pytorch.org/whl/cpu`) would cut most of
  that, if disk becomes a problem.
* **Azure Blob and Azure OpenAI are still external**, reached over the
  internet with the credentials in `.env`. Nothing local stands in for them.
* The chat UI calls `http://localhost:8000/chat/stream` directly - that address
  is hardcoded in `FE/src/main.tsx`, which is why port 8000 is published. In
  production `API_ROUTES` comes from the host shell instead; if it does not
  name `streamChatMessage`, the UI derives it from `sendChatMessage + /stream`.
* **The answer streams.** `POST /chat/stream` emits Server-Sent Events:
  `meta` (conversation id), `stage` (rephrasing / retrieving / generating /
  finalising), `delta` (answer chunks), then `done` or `error`. `done.response`
  is authoritative and replaces the concatenated deltas, because citations are
  linkified and a non-English answer is translated after generation. Once the
  stream opens the status is always 200, so failures arrive as an `error`
  event. `POST /chat` still exists and runs the identical pipeline - it drains
  the same generator - for any caller that cannot stream.
* Two things would silently break streaming if changed: the response must keep
  the `text/event-stream` content type (Starlette's `GZipMiddleware` excludes
  only that type, and gzip buffering would hold the chunks back), and any proxy
  in front must honour `X-Accel-Buffering: no`.
* The frontend image is built with `build:local` (`FE/vite.local.ts`), which
  is `vite.qa.ts` minus the Module Federation remote it fetches from a public
  IP. Nothing under `FE/src/` imports that remote.

## Pluggable infrastructure

Every backing service sits behind an interface with a factory in front of it,
so swapping one is an environment variable plus an implementation - no edit to
a startup path or a request handler.

| Setting | Implemented | Interface |
|---|---|---|
| `DB_TYPE` | `mysql` | `main-service/app/services/database_service.py` |
| `QUEUE_TYPE` | `redis` | `main-service/app/services/queue_service.py` |
| `STORAGE_TYPE` | `local`, `azure`, `nextcloud` | `<service>/app/services/storage_service.py` |
| `VECTOR_STORE` | `mongo` | `indexing-rpc-server/app/services/vector_store.py` |

`STORAGE_TYPE=local` is the OpenShift target - a mounted PVC instead of Azure
Blob. `STORAGE_TYPE=local docker compose up -d` runs the whole stack that way,
with a shared `file-store` volume standing in for the PVC. See DEPLOY.md for
the ReadWriteMany and `fsGroup` requirements, and for what a database swap
actually costs (the data layer is stored procedures).

Three things the seams depend on:

* **`STORAGE_TYPE` must agree between the two services.** Only paths cross the
  gRPC boundary, so both have to address the same store. It is set once in
  compose, from `x-backend-env`, for exactly this reason.
* **main-service holds no vector-database client.** Listing used to run a Mongo
  aggregation in this process, which made the vector store pluggable for writes
  only; it is `VectorService.ListDocuments` now.
* **The chat path no longer calls Azure directly.** `citation_helper` used to
  import `generate_blob_sas` from the Azure SDK, so switching `STORAGE_TYPE`
  left chat still talking to Azure. Citation URLs come from
  `get_download_url` on the configured backend, which returns None where there
  is no public URL - the citation then stays plain text.


## Layout

Two application images, a frontend and infrastructure.

```
main-service/app/            CONTROL PLANE - everything the API does
  routers/                   auth.py, chat.py, file.py, kb.py
  controllers/               auth, chat, error, file
  helpers/                   citation, context, prompt (chat retrieval)
  kb_worker/                 chatbot_queue consumer
  services/                  *_factory.py + one file per backend, kb_service, ollama
  models/                    auth, chat, chat_errors, common, files
  security.py                JWT, bcrypt, the Permission enum
  stored_procedures.py       every MySQL stored-proc call
  observability.py           request-ID middleware, filter and logging setup
  indexing_service_pb2*.py   generated stubs (client side)

indexing-rpc-server/app/     COMPUTE PLANE - everything with a model in it
  rpc_server.py              VectorService + ParsingService on one port
  parsing/                   ParseFile: extraction and OCR
  services/embedding_service gte-multilingual-base, 768-dim
  services/vector_store.py   the vector database interface; mongo_service implements it
  services/storage_service.py  the file store interface (a copy of main-service's)
  controllers/               chunking and indexing
  indexing_service.proto     the contract; stubs are generated from it

FE/                          React SPA served under /chatbot/
```

The split is by cost, not by feature. The compute image carries torch, a
600 MB embedding model and the OCR toolchain, and sits at ~1.9 GB resident
once the model loads; main-service holds none of that and runs at ~90 MB.
Folding them together would put both in the process serving /chat and reload
them on every deploy of it.

### What talks to what

```
browser -> :8000 /chat/stream     main-service  (SSE)
                                    -> Search RPC  -> compute -> mongo
                                    -> Azure OpenAI

upload  -> :8000 /files or /kb    main-service
                                    -> blob (the original)
                                    -> ParseFile RPC -> compute -> blob (text)
                                    -> Index RPC     -> compute -> mongo
                                    -> mysql (file record, /files only)

delete  -> :8000 /files/{id}      main-service -> chatbot_queue
                                    -> kb_worker (same process)
                                    -> Delete RPC -> compute
```

Nothing crosses the RPC but paths: both sides reach Azure Blob Storage, so a
document is uploaded once and referred to by blob path thereafter.

### Regenerating the gRPC stubs

`indexing-rpc-server/app/indexing_service.proto` is the source of truth. The
stubs were committed without it for a long time, so the contract could not be
regenerated. From `indexing-rpc-server/`:

```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. \
    app/indexing_service.proto
cp app/indexing_service_pb2*.py ../main-service/app/
```

grpcio-tools is a dev dependency in both services and is not installed into
either image (`poetry install --only main`).

## API surface

Everything is on main-service, port 8000. 13 paths.

```
GET    /health
POST   /auth/authenticate | change-password | re-authenticate
POST   /chat                        ask a question (blocking)
POST   /chat/stream                 same, as SSE - the UI uses this one
GET    /chat/history                past turns for a conversation
GET    /chat/session                list sessions
PATCH  /chat/session/{id}           rename
DELETE /chat/session/{id}           remove
POST   /files/                      upload -> blob + parse + index + SQL record
GET    /files/  /files/{id}         file records
DELETE /files/{id}                  record + blob + KB document
POST   /kb/                         upload -> parse + index, no SQL record
GET    /kb/                         list indexed documents
DELETE /kb/{kb_file_id}             remove one
```

`/files` and `/kb` both ingest. The difference is bookkeeping: `/files` keeps
the original blob and a SQL row, `/kb` only makes the document searchable.

> **Auth does not currently work, and is switched off.** `DISABLE_AUTH=True` in
> both `docker-compose.yml` and `main-service/.env`, so every endpoint above is
> reachable with no token. It could not work as things stand anyway: there is no
> `users` table, and `filter_users`, `upsert_user` and `filter_user_permissions`
> are called by `auth_controller` and `dependencies.has_permission` but are not
> defined in any `.sql` file in this repo. Before exposing this service, add the
> user schema and set `DISABLE_AUTH=False`.

## Database

Four scripts under `main-service/scripts/`, mounted into the MySQL container's
init directory by `docker-compose.yml` and applied only on an empty volume
(`docker compose down -v` re-applies them).

```
01 files_table_and_procedures.sql     files table; upsert_file, filter_files, delete_file
02 files_knowledge_base_column.sql    adds kb_file_id; redefines the two procs
03 get_files_by_entity_procedure.sql  get_files_by_entity
04 chatbot_tables_and_procedures.sql  chatbot_session, chatbot_history + 5 procs
```

3 tables, 9 procedures - every one of them called. It was 13 tables and 36
procedures; `kb_evidence_chain_tables_and_procedures.sql` (frameworks,
assessments, change_requests, the label and asset mappings) and
`versions_table_and_procedures.sql` went with those domains.

> These files were **untracked** until recently: `main-service/.gitignore` had
> a blanket `*.sql` rule, so the schema the whole stack depends on existed only
> on one developer's disk. A fresh clone plus `docker compose down -v` would
> have produced a stack with no tables. `!scripts/*.sql` now excepts them.

## Ports

| | | | |
|---|---|---|---|
| main-service | 8000 | redis | 6379 |
| indexing-rpc-server | 8001 | mongo | 27017 |
| UI | 3006 | mysql | 3306 |
