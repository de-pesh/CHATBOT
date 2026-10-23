# Chatbot Parsing & Indexing Service

The compute plane of the chatbot stack: a REST service that parses uploaded
documents (text extraction, OCR for images/scanned pages) and indexes them
into a vector store, for `main-service-chatbot`'s `kb_worker` to call during
ingestion. Search itself runs in-process in `main-service-chatbot` (it is the
one call that used to sit on a live chat turn's critical path) - this service
only does what is off that path: Index, Delete, ParseFile. See the repo root
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
   cd parsing-indexing-services-chatbot
   ```
3. Install the dependencies using Poetry:
   ```bash
   poetry install
   ```
4. Configuration is one file for the whole repo: copy `.env.sample` (repo root) to `.env` and fill it in. This folder has no `.env` of its own.

### Running the Application

```bash
poetry run python -m app
```

This starts the REST server on `PORT` (default `8001`; docker-compose maps it
to `8001` on the host too - see `INDEXING_SERVICE_URL` in main-service-chatbot's
the repo-root `.env.sample`). Or run the whole stack via `docker compose up -d` from the
repo root.

## Layout

```
app/
  __main__.py                entry point: uvicorn.run(app)
  api_server.py                FastAPI app: startup, request/response models, the three routes below
  config.py                   Settings (env-driven), every variable is listed in config.py
  logic/                      PURE functions only - no network, DB, disk or clock; everything else imports from here
    text_formats.py             bytes -> text for xlsx/pptx/docx/html/eml/pdf pages, PAGE_BREAK, image prep
    chunking.py                 split_document: text -> chunks (page-aware)
    fusion.py                   reciprocal rank fusion + cosine top-k
    lexical.py                  tokenizer, stemmers, BM25
    mongo_queries.py            pipeline builders, error predicates, batch validation, URI cleaning
    embeddings.py               batching, request/response shapes and retry backoff for HTTP embedders
    storage_paths.py            blob/volume path rules shared by both storage backends
    config_checks.py            require_settings: the startup 'X requires A, B' check the factories share
    prompts.py                  vision-model prompts and reply parsing
    index_health.py             "is the stored index stale?" verdict
  indexing/                   does the work behind POST /index
    handler.py                  download, embed, vector store write
    prepare.py                  prepare_document: chunk (logic/) + embed
  parsing/                    does the work behind POST /parse-file (OCR, LibreOffice, Tika, vision model)
    handler.py                  routes a file to the right extractor
    extractors.py               the extractors that need a subprocess, temp files, network or a model
    vision/                     the vision model, for scanned pages and images only
      service.py                  VisionService: wraps whichever vision LLM is configured
      vision_llm_factory.py       picks the backend - LiteLLM, Azure OpenAI, Azure AI Foundry, Ollama or a plain OpenAI-compatible /v1 endpoint
      *_vision_llm.py             one module per backend
  services/                   backends, one folder per concern, each with an interface + *_factory.py + implementations
    storage/                    file store: local volume, Azure Blob
    embedding/                  embedding model: local, litellm, azure, remote
    vector/                     vector store: MongoDB (mongo_service.py)
  tools/                      reindex.py - re-embed stored chunks after an embedding-model change
```

Adding or swapping a backend (storage, vector store, embedding)? See the
docstring in each `services/<concern>/*_factory.py` and [docs/deploy.md](../docs/deploy.md).

## REST interface

Request/response models are in [`app/api_server.py`](app/api_server.py). There
is no `Search` route here - it runs in-process in main-service-chatbot instead
(see that repo's `app/services/vector/search_runtime.py`), since it was the one
call that used to sit on a live chat turn's critical path; everything below is
document ingestion, off that path, so plain REST/JSON is enough.

* **POST /index** (`IndexRequest` -> `IndexResponse`) - downloads the file at
  `file_path` from the configured store, chunks and embeds it, and writes the
  chunks into `collection_name`.
* **POST /delete** (`DeleteRequest` -> `DeleteResponse`) - soft-deletes
  (deactivates) the chunks belonging to `file_id` in `collection_name`.
* **POST /parse-file** (`ParseFileRequest` -> `ParseFileResponse`) - downloads
  the file at `file_path`, extracts text (OCR via the configured vision LLM
  for images and scanned pages only - every other format needs no LLM),
  writes the extracted text back to the store, and returns its path in
  `blob_path` for a follow-up `/index` call.
* **GET /health** - liveness/readiness check.

#### Supported file types

| Type | How it is read | Pages |
|---|---|---|
| `.pdf` | text layer via PyMuPDF, laid out as Markdown (headings from font size, lists, ruled tables); a scanned PDF goes through the vision LLM merged with tesseract, or tesseract alone when no vision LLM is set. Stored as `.md` | real pages |
| `.docx` | LibreOffice -> PDF when installed, otherwise read directly with python-docx | PDF pages / one page |
| `.doc` | Tika converter (`CONVERTER_URL`), else LibreOffice | one page |
| `.pptx` | python-pptx: slide text, tables and speaker notes (pictures and charts are not read) | one page per slide |
| `.ppt` | Tika converter (`CONVERTER_URL`), else LibreOffice | one page per slide |
| `.xlsx` | openpyxl, every sheet | - |
| `.txt` | decoded, trying UTF-8 / UTF-16 / Latin-1 | - |
| `.html` `.htm` | visible text only (no scripts or styles), declared charset honoured | - |
| `.eml` | headers, body (plain preferred over HTML), attachment names | - |
| `.msg` | extract-msg: headers, body, attachment names | - |
| `.png` `.jpg` `.jpeg` `.webp` | vision LLM: the text in the image (OCR) **and** a description of what it shows - chart values, diagram structure, photo content. Phone photos are turned upright and very large images scaled to 2048 px first | - |

E-mail attachments are listed, not read. `.doc` and `.ppt` go to an Apache Tika
server when `CONVERTER_URL` is set (`http://tika:9998`; compose and
`openshift/09-deployment-tika.yaml` include it - a ~190 MB container of its own,
about 500 MB of RAM once warm). With no converter they need LibreOffice, which
an `OCR_MODE=optional` build skips, and fail with a message saying so. The upload allow-list is
`main-service-chatbot/app/services/kb/kb_service.py`.

## Configuration

Backend selection follows the same one-switch-per-piece-of-infrastructure
pattern as main-service-chatbot - see the module docstring in `app/config.py`:

* `STORAGE_TYPE` (`local` | `azure`) picks the file store - **must match
  main-service's `STORAGE_TYPE`**, since only paths cross the REST boundary
  between the two services.
* `VECTOR_STORE` (`mongo`) picks the vector database.
* `LEXICAL_SEARCH` (`bm25` | `text`) picks the keyword half of hybrid search - see
  "Hybrid search" below.
* `EMBEDDING_TYPE` (`local` | `litellm` | `azure` | `remote`) picks the
  embedding backend: `local` loads a sentence-transformers model from
  `EMBEDDING_MODEL_PATH` (defaults to the `gte-multilingual-base` copy baked
  into the image); `litellm` calls an embeddings alias on a LiteLLM proxy
  (`EMBEDDING_BASE_URL` = the proxy, `EMBEDDING_MODEL_NAME` = the alias,
  `EMBEDDING_API_KEY` = the virtual key); `azure` calls an Azure OpenAI /
  Foundry embeddings deployment directly (`EMBEDDING_BASE_URL` = the resource
  endpoint, `EMBEDDING_MODEL_NAME` = the *deployment* name, `EMBEDDING_API_KEY`,
  optionally `EMBEDDING_API_VERSION`); `remote` is any other OpenAI-compatible
  `/embeddings` endpoint (e.g. a real Ollama server's `/v1`). Foundry's
  non-OpenAI embedding models (Cohere, ...) go through LiteLLM. Either way,
  `EMBEDDING_DIMENSION` must match what the configured model actually
  outputs, or every write and query against the vector index fails - it is
  not auto-detected, so changing the embedding model means updating this and
  reindexing existing documents.

  **main-service-chatbot embeds queries with its own copy of these same four
  settings** (Search runs there, in-process - see its README). They must name
  the same model as this service's, or query vectors and stored vectors are
  not comparable. In docker-compose both services read `EMBEDDING_TYPE` from
  the same repo-root `.env`, but main-service's own `Settings` field rejects
  `"local"` outright (it does not ship torch) - so `EMBEDDING_TYPE=local` is
  only usable in a setup where the two services are configured separately
  (e.g. OpenShift's per-service ConfigMaps) and main-service is still given
  one of `litellm`/`azure`/`remote`, pointed at a *different* embedding
  endpoint than what this service uses locally - which defeats the point of
  a local model. In practice, `EMBEDDING_TYPE=local` here is only realistic
  for a deployment that also swaps main-service's search out for something
  else; the supported path is `litellm`/`azure`/`remote` on both sides.

### Switching the embedding model

1. Set `EMBEDDING_TYPE` (`litellm`, `azure` or `remote`), `EMBEDDING_BASE_URL`,
   `EMBEDDING_MODEL_NAME`, `EMBEDDING_API_KEY`, and `EMBEDDING_DIMENSION` to
   what the model returns. If the model can shorten its output (OpenAI
   `text-embedding-3-*`), `EMBEDDING_SEND_DIMENSIONS=true` asks it to match.
2. **The service checks this at startup** and refuses to start if the model's
   vectors are not `EMBEDDING_DIMENSION` wide.
3. Re-embed the knowledge base once, with the service running:
   `python -m app.tools.reindex --dry-run` to see what would change, then
   `python -m app.tools.reindex`. The chunk text is already stored, so nothing is
   re-parsed or re-uploaded. Every chunk records the model that made its vector
   (`embeddingModel`), so an interrupted run resumes where it stopped, and on
   startup the service warns if the stored vectors came from a different model.
   Stop uploads while it runs; search mixes old and new vectors until it is done.
4. For a **slim image**, build with `--build-arg EMBEDDING_MODE=remote`
   (`docker compose` reads `EMBEDDING_MODE`): torch, transformers and the
   ~600 MB model are left out (about 2.2 GB instead of 4.8 GB, and a much faster
   build). Only useful with an `EMBEDDING_TYPE` other than `local`.

The vision LLM (`ParseFile`'s reading of images and scanned pages) is chosen by
`VISION_LLM_TYPE`, and `vision_llm_factory.py` builds that backend: **litellm**
(`LITELLM_*`, a proxy), **azure** (`AZURE_OPENAI_*`), **foundry**
(`AZURE_AI_FOUNDRY_*` - a model from the Foundry catalog), **ollama**
(`OLLAMA_*`) or **openai** (`VISION_OPENAI_BASE_URL` / `_API_KEY` / `_MODEL` - a
plain OpenAI-compatible `/v1` endpoint such as an Azure AI Foundry
`.../openai/v1`; its own variables so it can sit on a different resource from the
chat model's `LITELLM_*`). `none`, the default, is a valid state: every file type
except images and scanned PDFs parses without it, a scanned PDF is read by
tesseract alone when tesseract and qpdf are installed, and it logs a warning
rather than failing the service to start. Naming a backend whose settings are
incomplete does fail it.

A PDF's extracted text is stored as a `.md` file beside the original (under
`texts/`), so what was indexed can be opened and checked.

## Hybrid search

This describes the search this service's own code implements
(`app/services/vector/mongo_service.py`, `app/logic/fusion.py`, `app/logic/lexical.py`)
- it still runs indexing (`create_vector_index`, `insert_data`) with it, but
the query-time path (`similarity_search_on_query`) itself now runs from
main-service-chatbot's identical copy of these same files, not from here -
see that repo's README and `app/services/vector/search_runtime.py`.

Every search runs two rankings and merges them: cosine similarity over the
embeddings (meaning), and BM25 over the chunk text (exact words, identifiers,
acronyms such as `T3.2.2` or `BYOD`). The merge is weighted Reciprocal Rank
Fusion - the keyword list counts half as much as the vector list
(`HYBRID_LEXICAL_WEIGHT`, `HYBRID_RRF_K`; the math is `app/logic/fusion.py`), and each list is taken 40
deep so a chunk that is only 15th by meaning can still surface. main-service
then reranks the result with a light model.

The BM25 (`app/logic/lexical.py`) is in-process: a small English stemmer
(reporting/reported/reports meet), Arabic normalisation, and identifiers kept
whole. It is rebuilt only when the set of active chunks changes, so an upload
or delete shows up on the next search. `LEXICAL_SEARCH=text` uses MongoDB's
`$text` index instead, which has no stemming and is weaker.

Measured on this knowledge base (305 chunks, 180 generated queries: 60 keyword,
60 natural-language, 60 vaguely worded; the right chunk is the one the query was
written from, so the test is strict):

| | top-1 | top-3 | top-10 | MRR |
|---|---|---|---|---|
| vector only | 55% | 73% | 85% | 0.656 |
| `$text` hybrid, equal weight (previous) | 54% | 77% | 86% | 0.658 |
| BM25 hybrid (current) | 61% | 77% | 86% | 0.698 |

Keyword-style queries gain the most; vaguely worded ones stay at ~70% top-10 for
every method, which is a job for query rewriting rather than for the lexical
side. Differences of a few points are within noise at this sample size.

