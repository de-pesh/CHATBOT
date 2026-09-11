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

Seven containers, scoped to **chatbot + document ingestion**:

```
chat      FE -> chatbot-inferencing-service -> indexing-rpc-server (vector search)
                                            -> mongo, mysql, Azure OpenAI
ingest    main-service -> POST /kb ------> chatbot-inferencing-service
          main-service -> chatbot_queue -> (same service, in-process worker)
                                            -> parses in-process
                                            -> indexing-rpc-server
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
| containers | 13 | **7** |
| all images | ~80 GB | **8.4 GB** |
| indexing-rpc-server | 17.2 GB | **4.4 GB** |
| chatbot-inferencing-service | - | **2.2 GB** |
| main-service | - | **1.68 GB** |
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
* The chat UI calls `http://localhost:8003/chat` directly - that address is
  hardcoded in `FE/src/main.tsx`, which is why port 8003 is published.
* The frontend image is built with `build:local` (`FE/vite.local.ts`), which
  is `vite.qa.ts` minus the Module Federation remote it fetches from a public
  IP. Nothing under `FE/src/` imports that remote.

## Layout

```
main-service/app/            the admin API: auth, file upload, KB management
  routers/                   auth.py, file.py, kb.py - mounted by main.py
  controllers/               auth, error, file
  services/                  mysql, redis, azure blob
  models/                    auth.py, common.py, files.py
  security.py                JWT, bcrypt, the Permission enum
  stored_procedures.py       every MySQL stored-proc call
  observability.py           request-ID middleware, filter and logging setup
chatbot-inferencing-service/app/
  routers/                   /chat and /kb
  parsing/                   document extraction (was parsing-rpc-server)
  kb_worker/                 queue consumer (was chatbot-kb-management-service)
indexing-rpc-server/         gRPC: embed and search, owns the HF model
FE/                          React SPA served under /chatbot/
```

`app/observability.py` is named in `app/logging.yaml` by dotted path
(`app.observability.RequestIDFilter`), so renaming it means editing that YAML
too - nothing imports the filter directly.

## API surface

main-service is down to what the chatbot needs. 35 paths -> 9.

```
GET    /health
POST   /auth/authenticate           |
POST   /auth/change-password        |  see the warning below
POST   /auth/re-authenticate        |
POST   /files/                      upload -> Azure Blob + push to the KB
GET    /files/  /files/{id}         file records
DELETE /files/{id}                  record + blob + KB document
GET    /kb/                         list KB documents (proxies :8003/kb)
DELETE /kb/{kb_file_id}             remove one KB document
```

Removed: `/frameworks`, `/organizations`, `/permissions`, `/roles`,
`/role-permissions`, `/user`, `/user-role-assignments` - the compliance and
RBAC admin domain, 46 of the 58 method+path combinations. The frontend never
called any of it; it only calls `:8003/chat` and `:8003/chat/history`.

> **Auth does not currently work, and is switched off.** `DISABLE_AUTH=True` in
> both `docker-compose.yml` and `main-service/.env`, so every endpoint above is
> reachable with no token. It could not work as things stand anyway: there is no
> `users` table, and `filter_users`, `upsert_user` and `get_user_permissions`
> are called by `auth_controller` and `dependencies.has_permission` but are not
> defined in any `.sql` file in this repo. `GET /user/` used to return 500 for
> exactly that reason. Before exposing this service, add the user schema and set
> `DISABLE_AUTH=False`.

## Ports

| | | | |
|---|---|---|---|
| main-service | 8000 | redis | 6379 |
| indexing-rpc-server | 8001 | mongo | 27017 |
| chatbot-inferencing-service | 8003 | mysql | 3306 |
| UI | 3006 | | |
