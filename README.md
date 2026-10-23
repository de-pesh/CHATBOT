# Chatbot

A document chat stack: upload files, they get parsed/embedded/indexed, then
answered over with citations. Three deployable pieces plus infrastructure.

```
FE/                                   React SPA - chat UI, served under /chatbot/
main-service-chatbot/                 CONTROL PLANE - the API (auth, chat, files, kb_worker)
parsing-indexing-services-chatbot/    COMPUTE PLANE - parsing, embedding, document ingestion (REST)
openshift/                            OpenShift manifests (secrets, configmap, PVC, deployments, routes)
docs/                                 architecture.md, guide.md, pipeline.md, deploy.md, openshift-deploy.md, study-1/2
docker-compose.yml                    full local stack - every service as a container
```

## Start here

* **Changing the code** -> [docs/architecture.md](docs/architecture.md): the layers, the rules,
  and a "where do I change..." table. Read it before your first edit.
* **How ingestion and chat work, end to end** -> [docs/study-1-ingestion.md](docs/study-1-ingestion.md),
  [docs/study-2-chat.md](docs/study-2-chat.md)

* **Running locally** -> [docs/guide.md](docs/guide.md) (Docker), or
  [docs/local-setup.md](docs/local-setup.md) (everything on your own machine, no Docker)
* **How the pipeline actually works** (parsing, chunking, embedding, retrieval,
  rerank, answer generation - stage by stage, with the exact env var and file
  behind each one) -> [docs/pipeline.md](docs/pipeline.md)
* **Configuring/swapping a backend** (database, queue, storage, vector store) ->
  [docs/deploy.md](docs/deploy.md)
* **Deploying to OpenShift** -> [docs/openshift-deploy.md](docs/openshift-deploy.md)

**One rule for reading the code:** inside each service, `app/logic/` holds pure
functions only (no network, DB, disk or clock - text in, text out); every other
folder does the actual work and imports from it.

Everything is configured in ONE file, `.env` (start from `.env.sample`, which lists every
setting of both services with its description and default - grouped by topic, and generated from the
code by `python scripts/gen_env_sample.py`). Docker ports, image tags, container names and build
registries are in its last section too.

Quickest path to a running stack:

```bash
cp .env.sample .env      # once: then fill in your model keys (the ONE config file)
docker compose build   # first time only, and slow: the two Python services
                        # each pull torch and a ~600 MB Hugging Face model
docker compose up -d
```

Then open <http://localhost:3006>.

Prefer your own SQL Server, Redis and MongoDB over containers (no Docker)? Copy
`.env.sample` to `.env`, edit it, then on Windows run `.\run-local.ps1` (installs,
checks, starts everything). Elsewhere: `python local.py install`, `check`, `run`
(and `ui`) - see [docs/local-setup.md](docs/local-setup.md).

The database and tables are created by main-service itself on its first start - in
Docker, natively and on OpenShift alike.
