# Deployment

Everything for OpenShift is in `../deploy/` - three files:

    manifests.yaml   the whole deployment in one file
    build.sh         builds and pushes the images
    README.md

    oc apply -n ai-common -f ../deploy/manifests.yaml

`<service>/k8s/legacy-aks/` holds the retired AKS manifests (nginx Ingress,
NodePort Services) for reference only.

> **Stale.** `../deploy/manifests.yaml` still declares NEO4J_* keys and
> Deployments for the five services removed from this repo, and `build.sh`
> still expects their build contexts. Both need reconciling with the three
> images compose now builds: main-service, indexing-rpc-server and the
> frontend. chatbot-inferencing-service has since been merged into
> main-service, so that Deployment must go too.

The Azure DevOps pipelines in each service targeted AKS and ACR and do not
apply to the OpenShift deployment.

## Choosing the infrastructure

Every backing service is behind a factory, so each one is selected with an
environment variable and its credentials. Nothing outside the factory names a
concrete product.

| Setting | Values | Selects | Factory |
|---|---|---|---|
| `DB_TYPE` | `mysql` | relational database | `main-service/app/services/database_factory.py` |
| `QUEUE_TYPE` | `redis` | message queue | `main-service/app/services/queue_factory.py` |
| `STORAGE_TYPE` | `local`, `azure`, `nextcloud` | file store | `<service>/app/services/storage_factory.py` |
| `VECTOR_STORE` | `mongo` | vector database | `indexing-rpc-server/app/services/vector_store_factory.py` |

Only the values listed are implemented. The others are gaps with a written
contract, not stubs - see **Adding a backend** below.

## Replacing Azure Blob with persistent storage

Set `STORAGE_TYPE=local` and point `LOCAL_STORAGE_PATH` at a mounted volume.
The Azure variables can then be dropped entirely. Locally,
`STORAGE_TYPE=local docker compose up -d` runs the whole stack with no Azure
account at all, against a shared `file-store` volume.

Three things about the PVC are load-bearing:

* **It must be ReadWriteMany.** main-service and indexing-rpc-server both mount
  it, because only *paths* cross the gRPC boundary between them: main-service
  writes the upload, the compute service reads it back, parses it and writes
  the extracted text, and main-service indexes that path. RWO binds to one node
  and the second pod will either fail to mount or see a different volume.
* **It must be group-writable.** The pods run under an arbitrary UID in group
  0, so set `fsGroup` in the pod security context. Both images pre-create
  `/data/files` owned `1001:0` and group-writable; startup fails fast with an
  explicit message if the mounted volume is not writable, rather than failing
  on the first upload.
* **`STORAGE_TYPE` must match in both Deployments.** They are configured
  independently but have to be looking at the same store. A mismatch is not
  caught at startup - it surfaces as ParseFile failing to find a file that was
  uploaded seconds earlier.

Sizing: the store keeps the original upload *and* the extracted text for every
document. Deleting a file record removes the original; the extracted text is
not currently reclaimed.

Citations are the one feature that degrades. Azure mints a SAS URL per cited
document; a volume has no public URL, so `get_download_url` returns None and
cited filenames stay as plain text instead of becoming links. Set
`LOCAL_STORAGE_PUBLIC_URL` if something is serving the volume over HTTP.

## Adding a backend

Implement the interface, add one branch to the factory. The interfaces are
`storage_service.py`, `database_service.py`, `queue_service.py` (main-service)
and `vector_store.py` (indexing-rpc-server); each documents what an
implementation has to provide.

Two of them have a catch worth knowing before you plan the work:

* **Database.** `DatabaseService` exposes a single operation, `call_procedure`.
  Every read and write in the service goes through a **stored procedure**, not
  through SQL built in Python. So `DB_TYPE` moves the driver, but it does not
  move the data layer: the nine procedures in `main-service/scripts/*.sql` are
  MySQL syntax and have to be ported to the target dialect before another
  backend serves a single request. That port is the real cost of a database
  swap, and the only way round it is replacing the procedures with an ORM or
  with SQL in the repo - a much larger change.
* **Vector store.** Embeddings are generated before the store sees them
  (768-dimension `gte-multilingual-base`), so a service that insists on
  embedding its own text is not a drop-in. Deletes are soft - a metadata flag -
  so a backend without in-place metadata updates has to emulate
  `deactivate_data_by_source_id` with a delete-by-filter.

`main-service` holds no vector-database client at all: listing, searching,
indexing and deleting all go through the compute service's gRPC surface, so a
vector store swap touches one process.
